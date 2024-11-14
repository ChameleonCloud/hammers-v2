"""Periodic inspector script."""

import argparse
import concurrent.futures
import logging
import random
from collections.abc import Generator
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import timedelta

import openstack
from openstack.connection import Connection

from hammers import utils

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
openstack.enable_logging(debug=False)
LOG = logging.getLogger(__name__)


def get_nodes_to_inspect(
    connection: Connection,
    nodes: list[utils.ReservableNode],
    expire_days: int = 31,
    dry_run: bool = True,  # noqa: FBT001
    provide_manageable: bool = False,
    inspect_reserved: bool = False,
    reinspect_failed: bool = False,
) -> Generator[utils.ReservableNode, None, None]:
    """Queue a node for inspection, and handle interruptions."""
    inspection_timedelta = timedelta(days=expire_days)

    for node in nodes:
        ### Readonly checks
        if node.needs_bootmode_set():
            LOG.warning(
                "boot_mode capability is unset: node %s:%s", node.uuid, node.name
            )

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
                LOG.warning(
                    "Please run provide for node: node %s:%s", node.uuid, node.name
                )

        inspectable_provision_states = utils.INSPECTABLE_PROVISION_STATES
        if reinspect_failed:
            inspectable_provision_states.append("inspect failed")

        ### Check if safe to modify
        if (
            node.needs_inspection(inspection_timedelta)
            and (inspect_reserved or not node.blazar_reserved)
            and not node.is_maintenance
            and node.provision_state in inspectable_provision_states
        ):
            yield node

        LOG.debug("skipping: inspection for node %s:%s", node.uuid, node.name)


def start_inspection(
    connection: Connection,
    node: utils.ReservableNode,
    dry_run: bool,
) -> Future:
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
    return node


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
        help="Maximum number of nodes to inspect in parallel.",
        type=int,
        default=1,
    )
    parser.add_argument(
        "--limit",
        help="Maximum number of nodes to inspect. No queue if <= `parallel`",
        type=int,
        default=1,
    )
    return parser.parse_args()


def main() -> None:
    """Drop the hammer."""
    args = parse_args()
    conn = openstack.connect(cloud=args.cloud)

    LOG.info("Collecting node and reservation info for cloud %s", args.cloud)

    nodes_to_inspect = list(
        get_nodes_to_inspect(
            connection=conn,
            nodes=utils.ironic_nodes_with_reservation_status(connection=conn),
            expire_days=args.expire_days,
            dry_run=args.dry_run,
            provide_manageable=args.provide_manageable,
            inspect_reserved=args.inspect_reserved,
            reinspect_failed=args.reinspect_failed,
        ),
    )
    random.shuffle(nodes_to_inspect)

    num_nodes_to_inspect = min(args.limit, len(nodes_to_inspect))
    LOG.info(
        "Found %s nodes to inspect for cloud %s, processing %s",
        len(nodes_to_inspect),
        args.cloud,
        num_nodes_to_inspect,
    )

    future_to_inspected_node = {}
    with ThreadPoolExecutor(max_workers=args.parallel) as executor:
        for node in nodes_to_inspect[:num_nodes_to_inspect]:
            inspection_future = executor.submit(
                start_inspection,
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
