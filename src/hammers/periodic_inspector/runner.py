"""Periodic inspector script."""

import argparse
import concurrent.futures
import logging
from concurrent.futures import Future, ThreadPoolExecutor

import openstack
from openstack.connection import Connection

from hammers.common import utils

logging.basicConfig(level=logging.INFO)
openstack.enable_logging(debug=False)
LOG = logging.getLogger(__name__)


def inspect_a_node(
    connection: Connection,
    node: utils.ReservableNode,
    dry_run: bool = True,  # noqa: FBT001
) -> Future:
    """Queue a node for inspection, and handle interruptions."""
    # if extra.inspect.target_provision_state is not set
    # set extra.inspect.target_provision_state == provision_state
    # do inspection stuff
    # when finished, move node to extra.inspect.target_provision_state
    #   if success, unset extra.inspect.target_provision_state
    # goal is to handle moving nodes back to active if inspection interrupted in the middle.

    if node.needs_bootmode_set():
        LOG.warning("boot_mode capability is unset: node %s:%s", node.uuid, node.name)

    if dry_run:
        LOG.info("DRY-RUN: starting inspection for node %s:%s", node.uuid, node.name)
        return node
    LOG.info("starting inspection for node %s:%s", node.uuid, node.name)
    return connection.inspect_machine(node.uuid, wait=True, timeout=900)


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
        "-p",
        "--parallel",
        help="How many nodes can be inspecting at once.",
        default=5,
    )
    return parser.parse_args()


def main() -> None:
    """Drop the hammer."""
    args = parse_args()
    conn = openstack.connect(cloud=args.cloud)

    LOG.info("Collecting node and reservation info for cloud %s", args.cloud)
    ironic_nodes_cache = utils.ironic_nodes_with_reservation_status(connection=conn)
    nodes_to_inspect = [
        n for n in ironic_nodes_cache if n.needs_inspection() and not n.blazar_reserved
    ]
    LOG.info("Finished collecting node and reservation info for cloud %s", args.cloud)

    future_to_inspected_node = {}
    with ThreadPoolExecutor(max_workers=args.parallel) as executor:
        for node in nodes_to_inspect:
            inspection_future = executor.submit(
                inspect_a_node,
                connection=conn,
                node=node,
                dry_run=args.dry_run,
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
            LOG.info(
                "finished inspection for node %s:%s",
                inspected_node.uuid,
                inspected_node.name,
            )


if __name__ == "__main__":
    main()
