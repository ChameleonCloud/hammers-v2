"""Ensure ironic nodes have the serial console enabled."""

import argparse
import logging
import sys
from collections.abc import Generator

import openstack
from openstack.baremetal.v1.node import Node
from openstack.connection import Connection

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
openstack.enable_logging(debug=False)
LOG = logging.getLogger(__name__)


# Provision states where it is safe to toggle the console.
CONSOLE_TOGGLEABLE_STATES = [
    "active",
    "available",
    "manageable",
]


def find_nodes_missing_console(conn: Connection) -> Generator[Node]:
    """Yield ironic nodes that do not have the serial console enabled."""
    nodes = conn.baremetal.nodes(
        fields=[
            "uuid",
            "name",
            "console_enabled",
            "console_interface",
            "provision_state",
            "maintenance",
        ],
    )
    for node in nodes:
        if node.is_console_enabled:
            LOG.debug("skipping node %s:%s, console already enabled", node.id, node.name)
            continue
        if node.is_maintenance:
            LOG.debug("skipping node %s:%s, in maintenance", node.id, node.name)
            continue
        if node.console_interface == "no-console":
            LOG.debug("skipping node %s:%s, driver has no console", node.id, node.name)
            continue
        if node.provision_state not in CONSOLE_TOGGLEABLE_STATES:
            LOG.debug(
                "skipping node %s:%s, provision state %s",
                node.id,
                node.name,
                node.provision_state,
            )
            continue
        yield node


def enable_console(conn: Connection, node: Node) -> None:
    LOG.info("enabling console for node %s:%s", node.id, node.name)
    conn.baremetal.enable_node_console(node)


def parse_args(args: list[str]) -> argparse.Namespace:
    """Handle CLI arguments."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--cloud",
        help="item in clouds.yaml to connect to, same as OS_CLOUD",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print out which nodes would have their console enabled, instead of changing them.",
    )
    parser.add_argument("--debug", action="store_true", help="increase log verbosity.")
    return parser.parse_args(args)


def main(arg_list: list[str]) -> None:
    args = parse_args(arg_list)

    if args.debug:
        LOG.setLevel(logging.DEBUG)

    conn = openstack.connect(cloud=args.cloud)

    nodes = list(find_nodes_missing_console(conn))
    LOG.info("Found %s ironic node(s) with serial console disabled", len(nodes))

    for node in nodes:
        if args.dry_run:
            LOG.info("DRY-RUN: would enable console for node %s:%s", node.id, node.name)
            continue
        try:
            enable_console(conn, node)
        except Exception as ex:
            LOG.warning(
                "failed to enable console for node %s:%s: %s",
                node.id,
                node.name,
                ex,
            )


def launch_main():
    main(sys.argv[1:])


if __name__ == "__main__":
    launch_main()
