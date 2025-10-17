# coding: utf-8
'''

Checks ironic nodes for a set of bad states with known workarounds, and resets
those states if applicable.

Currently checks for:
- Serial Console not Enabled
- Node in Provision state "ERROR"
'''

import argparse
import logging

import openstack
from openstack.connection import Connection
from openstack.baremetal.v1 import _proxy as ironic_proxy
from openstack.baremetal.v1.node import Node
from collections.abc import Generator
from openstack.exceptions import BadRequestException

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
openstack.enable_logging(debug=False)
LOG = logging.getLogger(__name__)

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
    return parser.parse_args()


def reset_node_state(ironic_client: ironic_proxy.Proxy, node_uuid: str, dry_run=False) -> Node|None:
    """
    Resets ironic node state via "undeploy" verb.

    Caution: The undeploy verb both resets nodes in "error" state, but will also
    delete any active instance on that node.
    To avoid this case, the method takes node UUID as an argument, fetches an
    up-to-date view of the node, and returns early if there is an associated 
    instance, or the state is not "error".

    Returns None in the case it exited early, otherwise returns a Node object
    reflecting the new state.
    """

    # get a new reference to the node, ensures we're up to date
    node = ironic_client.get_node(node=node_uuid)
    if node.instance_id:
        LOG.warning("Node %s has instance %s skipping", node.id, node.instance_id)
        return

    if node.provision_state != "error":
        LOG.warning("Node %s in state %s, not Error!", node.id, node.provision_state)
        return

    if dry_run:
        LOG.info("DRY-RUN: resetting error state for node %s, last error was %s", node.id, node.last_error)
    else:
        LOG.info("resetting error state for node %s, last error was %s", node.id, node.last_error)
        result = ironic_client.set_node_provision_state(node=node.id, target="undeploy")
        return result

def list_error_nodes(ironic_client: ironic_proxy.Proxy) -> Generator[Node]:
    """
    Wraps call to ironic list nodes, ensuring we list only nodes that are "safe"
    to touch. This improves efficiency, but the checks should be repeated in 
    actions that mutate these nodes, in case the state has changed by the time 
    we get through the list.
    """

    return ironic_client.nodes(
        details=True,               # we need this to get last_error_state
        is_maintenance=False,       # if a node is in maintenance, it's probably being worked on
        associated=False,           # don't touch any nodes with instances on them, for safety
        provision_state="error",    # only touch node with the "error" state.
    )

def enable_serial_consoles(ironic_client: ironic_proxy.Proxy, dry_run=False) -> None:
    console_disabled_nodes = ironic_client.nodes(
        details=True,               # we need this to get last_error_state
        is_console_enabled=False    # for all nodes with console disabled, set back to enabled.
    )

    for node in console_disabled_nodes:
        if dry_run:
            LOG.info("Would enable serial console on Node %s", node.id)
        else:
            try:
                ironic_client.enable_node_console(node=node)
            except BadRequestException as exc:
                LOG.warning(exc)

def main() -> None:
    args = parse_args()
    conn: Connection = openstack.connect(cloud=args.cloud)
    ironic_client: ironic_proxy.Proxy = conn.baremetal  # type: ignore

    enable_serial_consoles(ironic_client=ironic_client, dry_run=args.dry_run)

    for node in list_error_nodes(ironic_client=ironic_client):
        reset_node_state(ironic_client=ironic_client, node_uuid=node.id, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
