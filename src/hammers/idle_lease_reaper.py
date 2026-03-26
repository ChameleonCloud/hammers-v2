import argparse
import logging
import sys
import openstack
from datetime import datetime, timedelta, timezone
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
    # Chameleon (KVM)
    '975c0a94b784483a885f4503f70af655',
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


def parse_time(time_str: str) -> datetime:
    """Parse openstack timestamps to UTC timezone aware objects."""
    # The API returns e.g., '2026-02-05T09:52:00.000000'
    # Handle the 'T' vs ' ' differences gracefully
    time_str = time_str.replace(' ', 'T')
    time_str = time_str.split('+')[0].split('.')[0]
    dt_fmt = '%Y-%m-%dT%H:%M:%S'

    return datetime.strptime(time_str, dt_fmt).replace(tzinfo=timezone.utc)


def send_notification(conn, lease, sender, warn_period, termination_period,
                      subject, email_body):

    user = conn.identity.get_user(lease["user_id"])
    html = notifications.render_template(
        email_body,
        vars=dict(username = user.name,
                  lease_name=lease["name"],
                  lease_id=lease["id"],
                  warn_period=warn_period,
                  termination_period=termination_period))
    notifications.send_email(notifications.get_host(), user.email, sender, subject, html)


def get_active_leases(conn):
    """Fetch all active leases."""
    res = conn.reservation.get("/leases?all_tenants=True")
    for lease in res.json().get("leases", []):
        if lease["status"] == "ACTIVE":
            yield lease


def classify_leases(leases, warn_time, grace_time):
    """Bucket leases into warn vs delete lists by start time."""
    leases_to_warn, leases_to_delete = [], []
    # assert that grace_time < warn_time
    for lease in leases:
        start = parse_time(lease["start_date"])
        if start <= grace_time:
            leases_to_delete.append(lease)
        elif start <= warn_time:
            leases_to_warn.append(lease)
    return leases_to_warn, leases_to_delete


def is_underutilized(conn, lease, flavor_regex, threshold):
    underutilized = False

    # Resolve reserved vs used capacity per reservation
    for reservation in lease.get("reservations", []):
        if lease["project_id"] in EXCLUDED_PROJECT_IDS:
            continue

        if reservation.get("resource_type") != "flavor:instance":
            continue

        reserved_capacity = reservation.get("amount", 0)
        rp = reservation.get("resource_properties", "{}")
        rp_json = json.loads(rp) if isinstance(rp, str) else rp
        flavor_name = rp_json.get("name")

        if flavor_regex:
            if not re.search(flavor_regex, str(flavor_name)):
                continue

        # Count the instances deployed under this reservation id/flavor
        used_capacity = 0
        for server in conn.compute.servers(flavor=reservation["id"], all_projects=True):
            # Note: Including ERROR instances, as users are trying to use the reservation still
            if server.status not in ["DELETED"]:
                used_capacity += 1

        if reserved_capacity > 0:
            utilization = used_capacity / reserved_capacity
            if utilization < threshold:
                LOG.info(f"{lease['id']}: Reservation {reservation['id']} for flavor {reserved_capacity}x{flavor_name} underutilized. Used: {used_capacity}, Reserved: {reserved_capacity}")
                underutilized = True

    return underutilized


def main(arg_list: list[str]) -> None:
    args = parse_args(arg_list)
    sender = args.sender

    try:
        conn = openstack.connect(cloud=args.cloud)

        now = datetime.now(tz=timezone.utc)

        active_leases = get_active_leases(conn)
        
        # We first filter by time, then by utilization
        warn_candidates, delete_candidates = classify_leases(
            active_leases,
            now - timedelta(hours=args.warn_hours),
            now - timedelta(hours=args.grace_hours),
        )
        is_idle = lambda l: is_underutilized(
                conn, l, args.flavor_name_regex, args.utilization_threshold)

        violating_leases_warn = [l for l in warn_candidates if is_idle(l)]
        violating_leases_delete = [l for l in delete_candidates if is_idle(l)]

        def _print_lease(l):
            print(f"  - {l['id']} ({l['name']}) started at {l['start_date']}")

        if args.action == "info":
            print(f"\nFound {len(violating_leases_warn)} leases in warn period:")
            for l in violating_leases_warn:
                _print_lease(l)
            print(f"\nFound {len(violating_leases_delete)} leases in grace period (would be deleted):")
            for l in violating_leases_delete:
                _print_lease(l)
        if args.action == "delete":
            print(f"\nSending warnings to {len(violating_leases_warn)} leases in warn period:")
            for l in violating_leases_warn:
                _print_lease(l)
                send_notification(
                    conn, l, sender, args.warn_hours, args.grace_hours,
                    "Your lease {} is idle and may be terminated.".format(l['name']),
                    notifications.IDLE_LEASE_WARNING_EMAIL_BODY)
            print(f"\nDeleting {len(violating_leases_delete)} leases in grace period:")
            for l in violating_leases_delete:
                _print_lease(l)
                send_notification(
                    conn, l, sender, args.warn_hours, args.grace_hours,
                    "Your lease {} has been terminated.".format(l['name']),
                    notifications.IDLE_LEASE_TERMINATION_EMAIL_BODY)
                try:
                    conn.reservation.delete_lease(l['id'])
                    print(f"Deleted lease {l['id']}")
                except Exception as e:
                    print(f"Failed to delete lease {l['id']}: {e}")

            if args.slack and (violating_leases_warn or violating_leases_delete):
                slack = notifications.Slackbot(args.slack, script_name='unutilized-leases-reaper')
                message = (
                    'Warned deletion of *{} idle leases* '
                    'Commanded deletion of *{} idle leases* '
                    '(Unutilized lease violation)'
                    .format(len(violating_leases_warn), len(violating_leases_delete))
                )
                slack.message(message)
    except Exception as e:
        raise e


def launch_main():
    main(sys.argv[1:])


if __name__ == "__main__":
    launch_main()
