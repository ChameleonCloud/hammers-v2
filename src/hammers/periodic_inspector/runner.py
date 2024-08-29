"""Periodic inspector script."""

import argparse
import concurrent.futures
import logging
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import timedelta

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
    expire_days: int = 31,
    dry_run: bool = True,  # noqa: FBT001
    provide_manageable: bool = False,
    inspect_reserved: bool = False,
    reinspect_failed: bool = False,
) -> Future:
    """Queue a node for inspection, and handle interruptions."""

    inspection_timedelta = timedelta(days=expire_days)

    ### Readonly checks
    if node.needs_bootmode_set():
        LOG.warning("boot_mode capability is unset: node %s:%s", node.uuid, node.name)

    if (
        not node.is_maintenance
        and node.provision_state == "manageable"
        and not node.needs_inspection(inspection_timedelta)
    ):
        if provide_manageable and not dry_run:
            LOG.warning(
                "setting node %s:%s to available, inspection may have been interrupted.",
                node.uuid,
                node.name,
            )
            connection.baremetal.set_node_provision_state(node.uuid, "provide")
        else:
            LOG.warning("Please run provide for node: node %s:%s", node.uuid, node.name)

    inspectable_provision_states = utils.INSPECTABLE_PROVISION_STATES
    if reinspect_failed:
        inspectable_provision_states.append("inspect failed")

    ### Check if safe to modify
    if node.needs_inspection(inspection_timedelta):
        if (
            (inspect_reserved or not node.blazar_reserved)
            and not node.is_maintenance
            and node.provision_state in inspectable_provision_states
        ):
            if dry_run:
                LOG.info(
                    "DRY-RUN: starting inspection for node %s:%s",
                    node.uuid,
                    node.name,
                )
                node = connection.baremetal.get_node(node.uuid)
            else:
                LOG.info("starting inspection for node %s:%s", node.uuid, node.name)
                node = connection.inspect_machine(node.uuid, wait=True, timeout=900)
        else:
            LOG.debug("skipping: inspection for node %s:%s", node.uuid, node.name)
            return None

        return node

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
        "--inspect-reserved",
        action="store_true",
        help="also inspected nodes which are reserved but not active",
    )
    parser.add_argument(
        "--reinspect-failed",
        action="store_true",
        help="re-inspect nodes in state 'inspect failed'",
    )
    parser.add_argument(
        "--expire-days",
        help="If previous inspection is older than this many days, re-inspect.'",
        type=int,
        default=31,
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
                expire_days=args.expire_days,
                dry_run=args.dry_run,
                provide_manageable=args.provide_manageable,
                inspect_reserved=args.inspect_reserved,
                reinspect_failed=args.reinspect_failed,
            )
            future_to_inspected_node[inspection_future] = node

        for future in concurrent.futures.as_completed(future_to_inspected_node):
            node = future_to_inspected_node[future]
            if future.exception() is not None:
                LOG.warning(
                    "node %s:%s failed to inspect with error %s",
                    node.id,
                    node.name,
                    future.exception(),
                )
            else:
                inspected_node = future.result()
                if inspected_node:
                    LOG.info(
                        "finished inspection for node %s:%s",
                        inspected_node.id,
                        inspected_node.name,
                    )


if __name__ == "__main__":
    main()
