"""Module to clean up unused ip addresses."""

import argparse
import logging
import sys
from collections.abc import Generator
from datetime import datetime as DateTime
from datetime import timedelta as TimeDelta
from datetime import timezone as TimeZone

import iso8601
import openstack
from openstack.connection import Connection
from openstack.network.v2.floating_ip import FloatingIP
from openstack.network.v2.router import Router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
openstack.enable_logging(debug=False)
LOG = logging.getLogger(__name__)


def grace_period_expired(updated_str: str, grace_period: TimeDelta) -> bool:
    """Return true if resource hasn't been updated in longer than grace period."""
    last_updated = iso8601.parse_date(updated_str)
    now = DateTime.now(tz=TimeZone.utc)

    # explicitly time of last update is older than expiry time
    return last_updated < (now - grace_period)


def find_idle_floating_ips(conn: Connection, grace_period) -> Generator[FloatingIP]:
    floating_ips = conn.list_floating_ips()

    for fip in floating_ips:
        if "blazar" in fip.tags:
            LOG.debug("skipping FIP %s, managed by blazar", fip.floating_ip_address)
            continue
        if fip.status == "ACTIVE":
            LOG.debug("skipping FIP %s, is active", fip.floating_ip_address)
            continue
        if not grace_period_expired(fip.updated_at, grace_period):
            LOG.debug("skipping FIP %s, still in grace period", fip.floating_ip_address)
            continue

        yield fip


def find_idle_routers(
    conn: Connection,
    grace_period,
    ip_whitelist: set = None,
) -> Generator[Router]:
    routers = conn.list_routers()

    for router in routers:
        interfaces = conn.list_router_interfaces(
            router=router,
            interface_type="internal",
        )
        if len(interfaces) > 0:
            LOG.debug("skipping router %s, has active ports", router.id)
            continue

        if not grace_period_expired(router.updated_at, grace_period):
            LOG.debug("skipping router %s, still in grace period", router.id)
            continue

        uses_whitelisted_ip = False
        if router.external_gateway_info:
            external_ips = [
                e.get("ip_address")
                for e in router.external_gateway_info.get("external_fixed_ips", [])
            ]
            for ip in external_ips:
                if ip in ip_whitelist:
                    uses_whitelisted_ip = True
                    break

        if uses_whitelisted_ip:
            LOG.debug("skipping router %s, uses whitelisted ip %s", router.id, ip)
            continue

        yield router


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
        help="print out which IPs would be removed, instead of deleting them.",
    )
    parser.add_argument("--debug", action="store_true", help="increase log verbosity.")
    parser.add_argument(
        "--grace-days",
        type=int,
        default=7,
        help="How many days does a resource need to be unused before we'll clean it up",
    )
    return parser.parse_args(args)


def main(arg_list: list[str]) -> None:
    args = parse_args(arg_list)

    if args.debug:
        LOG.setLevel(logging.DEBUG)

    grace_period = TimeDelta(days=args.grace_days)

    conn = openstack.connect(cloud=args.cloud)
    reservable_ips = {r.floating_ip_address for r in conn.reservation.floatingips()}

    fips = find_idle_floating_ips(conn=conn, grace_period=grace_period)
    for f in fips:
        if args.dry_run:
            LOG.info("DRY-RUN: remove floating IP %s:%s", f.id, f.floating_ip_address)

        if not args.dry_run:
            LOG.info("deleting unused floating IP %s:%s", f.id, f.floating_ip_address)
            conn.delete_floating_ip(f.id)

    routers = find_idle_routers(
        conn=conn, grace_period=grace_period, ip_whitelist=reservable_ips
    )
    for r in routers:
        if args.dry_run:
            LOG.info("DRY-RUN: remove router %s:%s", r.id, r.name)

        if not args.dry_run:
            LOG.info("deleting unused router %s:%s", r.id, r.name)
            conn.delete_router(r.id)


def launch_main():
    main(sys.argv[1:])


if __name__ == "__main__":
    launch_main()
