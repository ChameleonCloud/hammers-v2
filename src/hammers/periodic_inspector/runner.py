"""Periodic inspector script."""

import concurrent.futures

import openstack
from oslo_log import log as logging

from hammers.common import utils

openstack.enable_logging(debug=False)
LOG = logging.getLogger(__name__)


# CONFIG PARAMS
# --site: same param as OS_CLOUD
# --dry-run: if true, only print out which nodes would be inspected
# --allow-reserved: if true, allow inspecting nodes if they are in a reservation, but not active
# --parallel-inspections: how many nodes can be in an inspecting state at once
# --stale-inspection-days


def main() -> None:
    """Drop the hammer."""
    conn = openstack.connect()

    ironic_nodes_cache = utils.ironic_nodes_with_reservation_status(connection=conn)
    nodes_to_inspect = [
        n for n in ironic_nodes_cache if n.needs_inspection() and not n.blazar_reserved
    ]
    inspection_futures = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        for n in nodes_to_inspect:
            print(
                f"queuing inspection for node {n.name}: {n.blazar_reserved} {n.needs_inspection()}"
            )
            inspection_future = executor.submit(conn.inspect_machine(n.uuid))
            inspection_futures[n.uuid] = inspection_future


if __name__ == "__main__":
    main()
