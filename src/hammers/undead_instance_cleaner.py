#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "openstacksdk",
#     "ccauth @ git+https://github.com/chameleoncloud/ccauth",
# ]
# ///

"""Clean up ironic nodes still claimed by a nova instance that no longer exists."""

import argparse
import logging
from collections.abc import Generator

import openstack
from openstack.baremetal.v1.node import Node
from openstack.connection import Connection
from openstack.exceptions import NotFoundException

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
openstack.enable_logging(debug=False)
LOG = logging.getLogger(__name__)

NODE_LIST_FIELDS = [
    "uuid",
    "name",
    "instance_uuid",
    "provision_state",
    "target_provision_state",
    "maintenance",
]


def instance_exists(conn: Connection, instance_id) -> bool:
    # Double check that the server is *really* gone
    try:
        conn.compute.get_server(instance_id)
    except NotFoundException:
        return False

    return True


def find_undead_nodes(conn: Connection) -> Generator[Node]:
    """Yield ironic nodes whose nova instance returns 404."""
    candidate_nodes = [
        node
        for node in conn.baremetal.nodes(fields=NODE_LIST_FIELDS)
        if node.instance_id
    ]
    known = {s.id for s in conn.compute.servers(all_projects=True)}

    for node in candidate_nodes:
        if node.instance_id in known:
            continue

        if instance_exists(conn, node.instance_id):
            LOG.warning(
                "instance %s missing from nova list, but found in get",
                node.instance_id,
            )
            continue

        if node.target_provision_state:
            LOG.warning("SKIP: Node %s still moving from state %s to %s", node.id, node.provision_state, node.target_provision_state)
            continue

        if node.is_maintenance:
            LOG.warning("SKIP: Node %s in maintenance with reason %s", node.id, node.maintenance_reason)
            continue

        if node.reservation:
            LOG.warning("SKIP: Node %s is locked by a conductor: %s", node.id, node.reservation)
            continue

        yield node


def undeploy(conn: Connection, node: Node) -> None:
    LOG.info("undeploying node %s %s", node.name, node.id)

    # not sure how we got here, but nodes can't move from available -> deleted
    if node.provision_state == "available":
        LOG.warning("node %s in unexpected state 'available', using adopt to workaround", node.id)
        conn.baremetal.set_node_provision_state(node, "manage", wait=True)
        conn.baremetal.set_node_provision_state(node, "adopt", wait=True)
        conn.baremetal.set_node_provision_state(node, "deleted",wait=True)
    else:
        conn.baremetal.set_node_provision_state(node, "deleted",wait=True)


def main():

    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()


    conn = openstack.connect()

    for node in find_undead_nodes(conn):
        LOG.warning(
            "node %s claims instance %s, but not found in nova",
            node.id,
            node.instance_id,
        )
        if args.apply:
            undeploy(conn=conn, node=node)


if __name__ == "__main__":
    main()
