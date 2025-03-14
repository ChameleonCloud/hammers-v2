"""Common utilities for hammers."""

import datetime
import json
from collections import OrderedDict
from collections.abc import Generator
from urllib.parse import urlencode
from datetime import datetime as DateTime
from datetime import timezone as TimeZone
from datetime import timedelta as TimeDelta

import iso8601
from openstack import resource
from openstack.baremetal.v1.node import Node as IronicNode
from openstack.connection import Connection
from openstack.reservation.v1.host import Host as BlazarHost
import requests


def pp(item: dict) -> None:
    """Pretty print a dict."""
    print(json.dumps(item))


# Don't touch a node if the next reservation starts in under 4 hours, gives time to recover
# from failures.
MINIMUM_BUFFER_SECONDS = datetime.timedelta(seconds=3600 * 4)

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

    allocations = res_proxy.host_allocations()
    now = datetime.datetime.now(tz=datetime.UTC)
    for alloc in allocations:
        in_reservation = False
        reservations = alloc.reservations
        if reservations:
            min_start_date = None
            for res in reservations:
                start_date = iso8601.parse_date(res.start_date)
                end_date = iso8601.parse_date(res.end_date)
                if now >= start_date and now <= end_date:
                    in_reservation = True
                    break
                # get start of earliest reservation
                if not min_start_date:
                    min_start_date = start_date
                else:
                    min_start_date = min(min_start_date, start_date)
            if in_reservation:
                continue
            else:
                time_to_res = min_start_date - now
                if time_to_res <= MINIMUM_BUFFER_SECONDS:
                    print(
                        f"Skipping {alloc.resource_id}: next reservation starts in {time_to_res}"
                    )

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


def grace_period_expired(last_alloc_str: str, grace_period: TimeDelta) -> bool:
    """Return true if resource hasn't been updated in longer than grace period."""
    last_updated = iso8601.parse_date(last_alloc_str)
    now = DateTime.now(tz=TimeZone.utc)

    # explicitly time of last update is older than expiry time
    return last_updated < (now - grace_period)


def project_is_expired(charge_code, grace_period, ignore_pending, api_token, log):
    """Return true if the project is expired, given grace period and pending allocation criteria"""
    api_url = f"https://chameleoncloud.org/admin/allocations/api/view/{charge_code}/?token={api_token}"
    res = requests.get(api_url)
    try:
        res.raise_for_status()
        alloc_json = res.json()
    except (requests.HTTPError, requests.exceptions.JSONDecodeError):
        # For these exceptions, we assume the project has not expired
        log.debug("Error fetching allocation data for %s. Status %s", charge_code, res.status_code)
        return False
    if alloc_json["is_active"]:
        log.debug("Project %s is active", charge_code)
        return False
    if ignore_pending and alloc_json["has_pending_allocation"]:
        log.debug("Project %s has pending allocation", charge_code)
        return False
    if not grace_period_expired(alloc_json["expiration_date"], grace_period):
        log.debug("Project %s within grace period", charge_code)
        return False
    log.debug("Project %s has expired", charge_code)
    return True
