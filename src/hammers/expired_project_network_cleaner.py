"""Module to clean up resources by expired projects"""

import argparse
from collections import defaultdict
import logging
import sys
import openstack
from datetime import timedelta as TimeDelta

from hammers.utils import project_is_expired


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
        "--dry-run",
        action="store_true",
        help="print out which servers would be shelved, instead of shelving them.",
    )
    parser.add_argument("--debug", action="store_true", help="increase log verbosity.")
    parser.add_argument(
        "--grace-days",
        type=int,
        default=0,
        help="How many days does a resource need to be unused before we'll clean it up",
    )
    parser.add_argument(
        "--portal-api-token",
        type=str,
        required=True,
        help="API token for portal",
    )
    parser.add_argument(
        "--ignore-pending",
        action="store_true",
        help="Ignore servers from a project with a pending allocation."
    )
    return parser.parse_args(args)


def main(arg_list: list[str]) -> None:
    args = parse_args(arg_list)

    if args.debug:
        LOG.setLevel(logging.DEBUG)

    grace_period = TimeDelta(days=args.grace_days)
    api_token = args.portal_api_token
    dry_run = args.dry_run
    ignore_pending = args.ignore_pending

    if args.debug:
        LOG.setLevel(logging.DEBUG)

    conn = openstack.connect(cloud=args.cloud)
    projects_by_id = {p.id: p for p in conn.identity.projects()}
    

    networks_by_project = defaultdict(list)
    for network in conn.network.networks(is_shared=False):
        if network.project_id:
            networks_by_project[network.project_id].append(network)

    for project_id, networks in networks_by_project.items():
        # Some old KVM projects have `charge_code` set, but new ones use `name`
        charge_code = projects_by_id[project_id].get("charge_code")
        if not charge_code:
            charge_code = projects_by_id[project_id].name
        if project_is_expired(charge_code, grace_period, ignore_pending, api_token, LOG):
            for network in networks:
                ports = conn.network.ports(network_id=network.id)                
                for port in ports:
                    if port.device_owner=="network:router_interface":
                        # Need to remove the router interface before deleting the port
                        router_id = port.device_id
                        subnet_id = port.fixed_ips[0]['subnet_id']
                        if dry_run:
                            LOG.info(f"Would remove router interface from router {router_id} for subnet {subnet_id}")
                        else:
                            LOG.info(f"Deletingrouter interface from router {router_id} for subnet {subnet_id}")
                            conn.network.remove_interface_from_router(router_id, subnet_id=subnet_id)
                    if dry_run:
                        LOG.info(f"Would delete port {port.id} on network {network.id}")
                    else:
                        LOG.info(f"Deleting port {port.id} on network {network.id}")
                        conn.network.delete_port(port.id)
                if dry_run:
                    LOG.info(f"Would delete network {network.id}")
                else:
                    LOG.info(f"Deleting network {network.id}")
                    conn.network.delete_network(network.id)

    routers_by_project = defaultdict(list)
    for router in conn.network.routers():
        if router.project_id:
            routers_by_project[router.project_id].append(router)

    for project_id, routers in routers_by_project.items():
        # Some old KVM projects have `charge_code` set, but new ones use `name`
        charge_code = projects_by_id[project_id].get("charge_code")
        if not charge_code:
            charge_code = projects_by_id[project_id].name
        if project_is_expired(charge_code, grace_period, ignore_pending, api_token, LOG):
            for router in routers:
                if dry_run:
                    LOG.info(f"Would delete router {router.id}, {router.name}")
                else:
                    LOG.info(f"Deleting router {router.id}, {router.name}")
                    conn.network.delete_router(router.id)


def launch_main():
    main(sys.argv[1:])


if __name__ == "__main__":
    launch_main()
