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
    servers_by_project = defaultdict(list)
    for server in conn.compute.servers(all_projects=True):
        if server.status == "ACTIVE":
            servers_by_project[server.project_id].append(server)

    projects_by_id = {p.id: p for p in conn.identity.projects()}

    for project_id, servers in servers_by_project.items():
        # Some old KVM projects have `charge_code` set, but new ones use `name`
        charge_code = projects_by_id[project_id].get("charge_code")
        if not charge_code:
            charge_code = projects_by_id[project_id].name
        LOG.info(f"Checking project {charge_code}")
        if project_is_expired(charge_code, grace_period, ignore_pending, api_token, LOG):
            for server in servers:
                if dry_run:
                    LOG.info("DRY-RUN: Shelving server %s:%s", server.id, server.name)
                else:
                    LOG.info("Shelving server %s:%s", server.id, server.name)
                    server.shelve()


def launch_main():
    main(sys.argv[1:])


if __name__ == "__main__":
    launch_main()
