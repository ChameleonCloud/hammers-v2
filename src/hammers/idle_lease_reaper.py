import argparse
from collections import defaultdict
import collections
import logging
import sys
import openstack
from datetime import datetime, timedelta, timezone
from . import utils
import json
import re
from . import notifications

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
openstack.enable_logging(debug=False)
LOG = logging.getLogger(__name__)

DEFAULT_WARN_HOURS = 6
DEFAULT_GRACE_HOURS = 9
EXCLUDED_PROJECT_IDS = [
    # Chameleon (TACC)
    'f6c7696906c04b3c89fc3bda9a1b8be0',
    # Chameleon (UC)
    '4140e5f9f65545dbb9f0bdc90ef68d23',
    # Maintenance
    '4ffe61cf850d4b45aef86b46411d33e1']


def parse_args(args: list[str]) -> argparse.Namespace:
    """Handle CLI arguments."""
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--cloud",
        help="item in clouds.yaml to connect to, same as OS_CLOUD",
    )
    parser.add_argument(
        '-w', '--warn-hours', type=int,
        help='Number of hours after which to warn user.',
        default=DEFAULT_WARN_HOURS)
    parser.add_argument(
        '-r', '--grace-hours', type=int,
        help='Number of hours after which to remove lease.',
        default=DEFAULT_GRACE_HOURS)
    parser.add_argument(
        'action', choices=['info', 'delete'],
        help='Just display info or actually delete them?')
    parser.add_argument(
        '--utilization-threshold', type=float, default=1.0,
        help='Fraction of reserved instances that must be active to not be considered idle (default 1.0)')
    parser.add_argument(
        '--flavor-name-regex', type=str, default="^g1",
        help='Regex to filter flavors against, only leases with matching flavors will be considered (default "^g1")')
    parser.add_argument(
        '--sender',
        type=str,
        help='Email address of sender.',
        default='noreply@chameleoncloud.org')
    parser.add_argument('--slack', type=str, help=(
        'JSON file with Slack webhook information to send a notification to'))
    return parser.parse_args(args)

def parse_time(time_str: str, alt_format: bool = False) -> datetime:
    """Parse openstack timestamps to UTC timezone aware objects."""
    # The API returns e.g., '2026-02-05T09:52:00.000000'
    # Handle the 'T' vs ' ' differences gracefully
    time_str = time_str.replace(' ', 'T')
    time_str = time_str.split('+')[0].split('.')[0]
    dt_fmt = '%Y-%m-%dT%H:%M:%S'

    return datetime.strptime(time_str, dt_fmt).replace(tzinfo=timezone.utc)

def send_notification(conn, lease, sender, warn_period, termination_period,
                      subject, email_body):

    user = conn.identity.get_user(lease.user_id)
    html = notifications.render_template(
        email_body,
        vars=dict(username = user.name,
                  lease_name=lease.name,
                  lease_id=lease.id,
                  warn_period=warn_period,
                  termination_period=termination_period))
    notifications.send_email(notifications.get_host(), user.email, sender, subject, html)

def main(arg_list: list[str]) -> None:
    args = parse_args(arg_list)
    sender = args.sender

    conn = openstack.connect(cloud=args.cloud)

    now = datetime.now(tz=timezone.utc)

    warn_threshold_time = now - timedelta(hours=args.warn_hours)
    grace_threshold_time = now - timedelta(hours=args.grace_hours)

    violating_leases_warn = []
    violating_leases_delete = []

    res = conn.reservation.get("/leases?all_tenants=True")
    leases_data = res.json().get("leases", [])

    leases = []
    for l in leases_data:
        try:
             leases.append(conn.reservation.get_lease(l['id']))
        except Exception as e:
             LOG.warning(f"Could not fetch full details for lease {l['id']}: {e}")

    for lease in leases:
        if lease.status != "ACTIVE":
            continue

        lease_start_time = parse_time(lease.start_date, alt_format=True)

        is_warn = lease_start_time <= warn_threshold_time
        is_grace = lease_start_time <= grace_threshold_time

        if not is_warn and not is_grace:
            continue

        underutilized = False

        # Resolve reserved vs used capacity per reservation
        for reservation in lease.reservations:
            if reservation.resource_type != "flavor:instance":
                continue

            reservation_dict = dict(reservation).get("properties", {})
            reserved_capacity = reservation_dict.get("amount", 0)
            rp_json = json.loads(reservation_dict.get("resource_properties"))
            flavor_name = rp_json.get("name")

            if args.flavor_name_regex:
                if not re.search(args.flavor_name_regex, flavor_name):
                    continue

            # Count the instances deployed under this reservation id/flavor
            used_capacity = 0
            for server in conn.compute.servers(flavor=reservation.id, all_projects=True):
                # Note: Including ERROR instances, as users are trying to use the reservation still
                if server.status not in ["DELETED"]:
                    used_capacity += 1

            if reserved_capacity > 0:
                utilization = used_capacity / reserved_capacity
                if utilization < args.utilization_threshold:
                    LOG.info(f"{lease.id}: Reservation {reservation.id} for flavor {reserved_capacity}x{flavor_name} underutilized. Used: {used_capacity}, Reserved: {reserved_capacity}")
                    underutilized = True

        if underutilized:
             if is_grace:
                  violating_leases_delete.append(lease)
             elif is_warn:
                  violating_leases_warn.append(lease)

    if args.action == "info":
        print(f"\nFound {len(violating_leases_warn)} leases in warn period:")
        for l in violating_leases_warn:
            print(f"  - {l.id} ({l.name}) started at {l.start_date}")
        print(f"\nFound {len(violating_leases_delete)} leases in grace period (would be deleted):")
        for l in violating_leases_delete:
            print(f"  - {l.id} ({l.name}) started at {l.start_date}")
    elif args.action == "delete":
        print(f"\nFound {len(violating_leases_warn)} leases in warn period (will only warn, not delete yet):")
        for l in violating_leases_warn:
            print(f"  - {l.id} ({l.name})")
            send_notification(
                conn, l, sender, args.warn_hours, args.grace_hours,
                "Your lease {} is idle and may be terminated.".format(l.name),
                notifications.IDLE_LEASE_WARNING_EMAIL_BODY)
        print(f"\nFound {len(violating_leases_delete)} leases in grace period (deleting now):")
        for l in violating_leases_delete:
            print(f"  - {l.id} ({l.name})")
            send_notification(
                conn, l, sender, args.warn_hours, args.grace_hours,
                "Your lease {} has been terminated.".format(l.name),
                notifications.IDLE_LEASE_TERMINATION_EMAIL_BODY)
            try:
                conn.reservation.delete_lease(l.id)
                print(f"Deleted lease {l.id}")
            except Exception as e:
                print(f"Failed to delete lease {l.id}: {e}")

        if args.slack:
            slack = Slackbot(args.slack, script_name='unutilized-leases-reaper')
        else:
            slack = None


def launch_main():
    main(sys.argv[1:])


if __name__ == "__main__":
    launch_main()
