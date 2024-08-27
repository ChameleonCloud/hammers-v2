"""Periodic inspector script."""

import concurrent.futures
import datetime
import json
from collections import OrderedDict
from urllib.parse import urlencode

import openstack
import openstack.baremetal
import openstack.baremetal.v1
import openstack.baremetal.v1.node
import openstack.proxy
from oslo_log import log as logging

openstack.enable_logging(debug=False)
LOG = logging.getLogger(__name__)


RESERVABLE_PROVISION_STATES = [
    "active",
    "available",
]

INSPECTABLE_PROVISION_STATES = [
    "available",
    "manageable",
    "inspect failed",
]


stale_inspect_days = 31
inspection_interval = datetime.timedelta(days=stale_inspect_days)


def pp(item):
    print(json.dumps(item))


def _get_nodes_with_last_inspected(conn: openstack.connection.Connection):
    bm_proxy = conn.baremetal
    query_string = urlencode(
        OrderedDict(
            fields="name,uuid,provision_state,power_state,maintenance,inspect_interface,properties,inspection_finished_at",
        )
    )
    uri = "nodes/?" + query_string

    result = bm_proxy.get(url=uri, microversion="1.82")

    return result.json().get("nodes")


def _get_unreserved_nodes(conn: openstack.connection.Connection):
    res_proxy = conn.reservation

    unreserved_allocations = res_proxy.host_allocations(reservations=[])
    for alloc in unreserved_allocations:
        yield res_proxy.get_host(alloc.resource_id)


def node_needs_inspection(node, inspection_interval: datetime.timedelta) -> bool:
    """Return true if last inspected older than threshold."""
    last_inspected_string = node.get("inspection_finished_at")
    if not last_inspected_string:
        # property is null, therefore either either node has never been
        # inspected, or most recent inspection failed to complete
        return True

    last_inspected_date = datetime.datetime.fromisoformat(last_inspected_string)
    now = datetime.datetime.now(tz=datetime.UTC)
    return (now - last_inspected_date) > inspection_interval


def node_needs_bootmode(node) -> bool:
    properties = node.get("properties", {})
    capabilities = properties.get("capabilities")

    return "bios" in capabilities


def _ironic_nodes_with_reservation_status(conn):
    # for efficiency, get selection of fields from all ironic nodes in one query
    ironic_nodes_cache = _get_nodes_with_last_inspected(conn)

    # get all blazar hosts where the allocation has an empty reservations array
    unreserved_node_ids = [n.hypervisor_hostname for n in _get_unreserved_nodes(conn)]

    for n in ironic_nodes_cache:
        if n.get("uuid") in unreserved_node_ids:
            n["blazar_reserved"] = False
        else:
            n["blazar_reserved"] = True

        yield n


def main():
    conn = openstack.connect()

    ironic_nodes_cache = _ironic_nodes_with_reservation_status(conn)

    future_to_inspected_node = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        for n in ironic_nodes_cache:
            name = n.get("name")
            node_uuid = n.get("uuid")
            provision_state = n.get("provision_state")
            maintenance = n.get("maintenance")
            needs_inspect = node_needs_inspection(n, inspection_interval)
            needs_bootmode = node_needs_bootmode(n)
            is_reserved = n.get("blazar_reserved")

            if not maintenance and provision_state not in RESERVABLE_PROVISION_STATES:
                print(f"node {name} needs provide run!")

            # if is_reserved and (needs_inspect or needs_bootmode):
            #     print(f"schedule maintenance for {name}")

            if (
                provision_state in INSPECTABLE_PROVISION_STATES
                and not is_reserved
                and not maintenance
                and needs_inspect
            ):
                print(
                    f"queuing inspection for {node_uuid}:{name}: state: {provision_state}, reserved: {is_reserved}, needs_inspect: {needs_inspect}, needs_bootmode: {needs_bootmode}"
                )
                inspection_future = executor.submit(
                    conn.inspect_machine,
                    name_or_id=node_uuid,
                    timeout=900,
                )
                future_to_inspected_node[inspection_future] = node_uuid

    print("queued all inspections, waiting for completion")
    for future in concurrent.futures.as_completed(future_to_inspected_node):
        node_id = future_to_inspected_node[future]
        if future.exception() is not None:
            print(f"node {node_id} failed to inspect with error {future.exception()}")
        else:
            inspected_node = future.result()
            print(f"node {inspected_node.id} finished inspecting")


if __name__ == "__main__":
    main()
