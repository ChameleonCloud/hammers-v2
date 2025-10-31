import argparse
from collections import defaultdict
import collections
import logging
import sys
import openstack

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
openstack.enable_logging(debug=False)
LOG = logging.getLogger(__name__)


def parse_args(args: list[str]) -> argparse.Namespace:
    """Handle CLI arguments."""
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--cloud",
        help="item in clouds.yaml to connect to, same as OS_CLOUD",
    )
    parser.add_argument(
        "--lease-id",
        type=str,
        required=True,
    )
    return parser.parse_args(args)


def main(arg_list: list[str]) -> None:
    args = parse_args(arg_list)

    conn = openstack.connect(cloud=args.cloud)

    # look up lease data for resrevation id
    lease = conn.reservation.get_lease(args.lease_id)

    # look up allocations
    allocations = list(conn.reservation.host_allocations())
    allocs_by_lease_id = collections.defaultdict(list)
    for alloc in allocations:
        for reservation in alloc.reservations:
            allocs_by_lease_id[reservation["lease_id"]].append(alloc)
    print("lease:")
    print(f"\t- id: {lease.id}")
    print(f"\t- name: {lease.name}")
    print(f"\t- project_id: {lease.project_id}")
    print(f"\t- start: {lease.start_date}")
    print(f"\t- end: {lease.end_date}")

    hostnames = set()
    count_by_hostname = defaultdict(int)
    for alloc in allocs_by_lease_id[lease.id]:
        count_by_hostname[
            conn.reservation.get_host(alloc.resource_id).hypervisor_hostname
        ] += 1
        hostnames.add(
            conn.reservation.get_host(alloc.resource_id).hypervisor_hostname
        )

    print("allocations_per_host")
    for hostname, count in count_by_hostname.items():
        print(f"\t- {hostname}: {count}")

    # look up resource provider for blazar host (child of hypervisor RP)
    rps = []
    for hostname in hostnames:
        for rp in conn.placement.resource_providers():
            if hostname in rp.name and "blazar" in rp.name:
                rps.append(rp)

    print("resource_inventory_per_host:")
    for rp in rps:
        for rpi in conn.placement.resource_provider_inventories(rp):
            for res in lease.reservations:
                if res.id.replace("-", "_").upper() in rpi.resource_class:
                    print(f'\t- {rp.name[len("blazar_"):]}')
                    print(f"\t\t- {rpi.resource_class}: {rpi.total}")

    # look up any instances by flavor
    print("instances_by_flavor:")
    for reservation in lease.reservations:
        print(f"\t- reservation id: {reservation.id}")
        for server in conn.compute.servers(flavor=reservation.id, all_projects=True):
            print(f"\t\t- {server.name} - {server.status} ({server.id})")


def launch_main():
    main(sys.argv[1:])


if __name__ == "__main__":
    launch_main()
