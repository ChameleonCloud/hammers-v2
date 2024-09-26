import argparse
import datetime
import logging
from datetime import timedelta

import iso8601
import openstack

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
        help="print out which IPs would be removed, instead of deleting them.",
    )
    return parser.parse_args()


def _compare_fip(item: openstack.network.v2.floating_ip.FloatingIP):
    return item.updated_at


def main() -> None:
    args = parse_args()
    conn = openstack.connect(cloud=args.cloud)

    query = {
        "status": "DOWN",
        "not-tags": "blazar",
    }
    fips_list = list(conn.network.ips(**query))
    fips_list.sort(key=_compare_fip)
    ip: openstack.network.v2.floating_ip.FloatingIP
    for ip in fips_list:
        last_updated = iso8601.parse_date(ip.updated_at)
        if (datetime.datetime.now(tz=datetime.UTC) - last_updated) >= timedelta(days=7):
            if args.dry_run:
                LOG.info(
                    "DRY-RUN: FIP %s was last updated at %s, would be deleted",
                    ip.floating_ip_address,
                    ip.updated_at,
                )
            else:
                conn.network.delete_ip(ip)
                LOG.info("Deleted floating IP %s due to age", ip.floating_ip_address)


if __name__ == "__main__":
    main()
