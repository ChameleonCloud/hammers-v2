"""Periodic inspector script."""

import argparse
import concurrent.futures
import logging
from concurrent.futures import Future, ThreadPoolExecutor

import openstack
from openstack.connection import Connection

from hammers.common import utils

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
openstack.enable_logging(debug=False)
LOG = logging.getLogger(__name__)


def inspect_a_node(
    connection: Connection,
    node: utils.ReservableNode,
    dry_run: bool = True,  # noqa: FBT001
    provide_manageable: bool = False,
) -> Future:
    """Queue a node for inspection, and handle interruptions."""
    ### Readonly checks
    if node.needs_bootmode_set():
        LOG.warning("boot_mode capability is unset: node %s:%s", node.uuid, node.name)

    if (
        not node.is_maintenance
        and node.provision_state == "manageable"
        and not node.needs_inspection()
    ):
        if dry_run:
            LOG.warning("Please run provide for node: node %s:%s", node.uuid, node.name)
        elif provide_manageable:
            LOG.warning(
                "setting node %s:%s to available, inspection may have been interrupted.",
                node.uuid,
                node.name,
            )
            connection.baremetal.set_node_provision_state(node.uuid, "provide")

    ### Check if safe to modify
    if node.needs_inspection():
        if (
            not node.blazar_reserved
            and not node.is_maintenance
            and node.provision_state in utils.INSPECTABLE_PROVISION_STATES
        ):
            if dry_run:
                LOG.info(
                    "DRY-RUN: starting inspection for node %s:%s",
                    node.uuid,
                    node.name,
                )
                return node
            else:
                LOG.info("starting inspection for node %s:%s", node.uuid, node.name)
                return connection.inspect_machine(node.uuid, wait=True, timeout=900)
        else:
            LOG.debug("skipping: inspection for node %s:%s", node.uuid, node.name)
    return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--cloud",
        help="item in clouds.yaml to connect to, same as OS_CLOUD",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print out which nodes will be inspected, but take no action.",
    )
    parser.add_argument(
        "--provide-manageable",
        action="store_true",
        help="set nodes found in manageable state to available",
    )
    parser.add_argument(
        "-p",
        "--parallel",
        help="How many nodes can be inspecting at once.",
        type=int,
        default=1,
    )
    return parser.parse_args()


def main() -> None:
    """Drop the hammer."""
    args = parse_args()
    conn = openstack.connect(cloud=args.cloud)

    LOG.info("Collecting node and reservation info for cloud %s", args.cloud)
    ironic_nodes_cache = utils.ironic_nodes_with_reservation_status(connection=conn)
    LOG.info("Finished collecting node and reservation info for cloud %s", args.cloud)

    future_to_inspected_node = {}
    with ThreadPoolExecutor(max_workers=args.parallel) as executor:
        for node in ironic_nodes_cache:
            inspection_future = executor.submit(
                inspect_a_node,
                connection=conn,
                node=node,
                dry_run=args.dry_run,
                provide_manageable=args.provide_manageable,
            )
            future_to_inspected_node[inspection_future] = node

    for future in concurrent.futures.as_completed(future_to_inspected_node):
        node = future_to_inspected_node[future]
        if future.exception() is not None:
            LOG.warning(
                "node %s:%s failed to inspect with error %s",
                node.uuid,
                node.name,
                future.exception(),
            )
        else:
            inspected_node = future.result()
            if inspected_node:
                LOG.info(
                    "finished inspection for node %s:%s",
                    inspected_node.uuid,
                    inspected_node.name,
                )


if __name__ == "__main__":
    main()
