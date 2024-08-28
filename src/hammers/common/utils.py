"""Common utilities for hammers."""

import datetime
import json
from collections import OrderedDict
from collections.abc import Generator
from urllib.parse import urlencode

from openstack import resource
from openstack.baremetal.v1.node import Node as IronicNode
from openstack.connection import Connection
from openstack.reservation.v1.host import Host as BlazarHost


def pp(item: dict) -> None:
    """Pretty print a dict."""
    print(json.dumps(item))


# List of ironic provision states that a healthy blazar host may be in
RESERVABLE_PROVISION_STATES = [
    "active",
    "available",
    "wait call-back",
    "deploying",
    "deleting",
]

# List of ironic provision states from which inspection may be initiated.
# `available` here is a special case, we must first move the node to `manageable`
# and move it back when inspection is complete
INSPECTABLE_PROVISION_STATES = [
    "available",
    "manageable",
    "inspect failed",
]


class ReservableNode(IronicNode):
    """Ironic Node object with additional fields for inspection."""

    uuid = resource.Body("uuid")
    inspection_started_at = resource.Body("inspection_started_at")
    inspection_finished_at = resource.Body("inspection_finished_at")

    blazar_reserved = resource.Computed("blazar_reserved")

    inspection_interval = datetime.timedelta(days=30)

    def needs_inspection(self, inspection_interval: datetime.timedelta = None) -> bool:
        """Return true if last inspected older than threshold."""
        if not inspection_interval:
            inspection_interval = self.inspection_interval

        if not self.inspection_finished_at:
            # property is null, therefore either either node has never been
            # inspected, or most recent inspection failed to complete
            return True

        last_inspected_date = datetime.datetime.fromisoformat(
            self.inspection_finished_at,
        )
        now = datetime.datetime.now(tz=datetime.UTC)
        return (now - last_inspected_date) > inspection_interval

    def needs_bootmode_set(self) -> bool:
        """Return true if `boot_mode` not in capabilities."""
        capabilities = self.properties.get("capabilities")
        if not capabilities:
            return True

        return "boot_mode" not in capabilities


def ironic_nodes_with_last_inspected(
    connection: Connection,
) -> Generator[ReservableNode]:
    """Return list of ironic nodes."""
    bm_proxy = connection.baremetal
    query_string = urlencode(
        OrderedDict(
            fields="uuid,name,provision_state,power_state,maintenance,inspect_interface,properties,inspection_finished_at",
        ),
    )
    uri = "nodes/?" + query_string

    result = bm_proxy.get(url=uri, microversion="1.82")

    for node in result.json().get("nodes"):
        node_ref = ReservableNode(**node)
        yield node_ref


def unreserved_blazar_hosts(connection: Connection) -> Generator[BlazarHost]:
    """Return generator of blazar hosts which have an empty reservations list."""
    res_proxy = connection.reservation

    unreserved_allocations = res_proxy.host_allocations(reservations=[])
    for alloc in unreserved_allocations:
        yield res_proxy.get_host(alloc.resource_id)


def ironic_nodes_with_reservation_status(connection: Connection) -> Generator[dict]:
    """Yield ironic hosts annotated with `blazar_reserved=True` if reserved."""
    # for efficiency, get selection of fields from all ironic nodes in one query
    ironic_nodes_cache = ironic_nodes_with_last_inspected(connection)

    # get all blazar hosts where the allocation has an empty reservations array
    unreserved_node_ids = [
        n.hypervisor_hostname for n in unreserved_blazar_hosts(connection)
    ]

    for n in ironic_nodes_cache:
        if n.uuid in unreserved_node_ids:
            n.blazar_reserved = False
        else:
            n.blazar_reserved = True
        yield n
