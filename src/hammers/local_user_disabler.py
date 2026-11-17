"""Disable keystone local users that are not service accounts."""

import argparse
import fnmatch
import logging
import sys
from collections.abc import Generator

import openstack
from openstack.connection import Connection
from openstack.identity.v3.user import User

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
openstack.enable_logging(debug=False)
LOG = logging.getLogger(__name__)

# chi-in-a-box names its tempest accounts local-testuser-NN.
DEFAULT_SKIP_USERS = ["admin", "tempest*", "local-testuser-*", "testuser-*"]


def find_users_to_disable(
    conn: Connection,
    domain_id: str,
    service_project_id: str,
    skip_users: set[str],
) -> Generator[User]:
    """Yield enabled users in the domain that are not service accounts."""
    service_user_ids = {
        a.user["id"]
        for a in conn.identity.role_assignments(
            scope_project_id=service_project_id, effective=True
        )
    }
    for user in conn.identity.users(domain_id=domain_id):
        if not user.is_enabled:
            LOG.debug("skipping user %s:%s, already disabled", user.id, user.name)
            continue
        if user.id in service_user_ids:
            LOG.debug("skipping user %s:%s, has role on service project", user.id, user.name)
            continue
        if user.id == conn.current_user_id:
            LOG.debug("skipping user %s:%s, running this hammer", user.id, user.name)
            continue
        if any(fnmatch.fnmatchcase(user.name, pattern) for pattern in skip_users):
            LOG.debug("skipping user %s:%s, matches skip list", user.id, user.name)
            continue
        yield user


def disable_user(conn: Connection, user: User) -> None:
    LOG.info("disabling user %s:%s", user.id, user.name)
    conn.identity.update_user(user, is_enabled=False)


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
        help="print out which users would be disabled, instead of disabling them.",
    )
    parser.add_argument("--debug", action="store_true", help="increase log verbosity.")
    parser.add_argument(
        "--domain",
        default="default",
        help="name or ID of the domain holding local users.",
    )
    parser.add_argument(
        "--service-project",
        default="service",
        help="users with a role on this project are service accounts, and are skipped.",
    )
    parser.add_argument(
        "--skip-user",
        action="append",
        default=DEFAULT_SKIP_USERS,
        help="name or shell-style pattern of an additional account to skip, may be "
        "repeated. Always skipped: %(default)s",
    )
    return parser.parse_args(args)


def main(arg_list: list[str]) -> None:
    args = parse_args(arg_list)

    if args.debug:
        LOG.setLevel(logging.DEBUG)

    conn = openstack.connect(cloud=args.cloud)

    domain = conn.identity.find_domain(args.domain, ignore_missing=False)
    service_project = conn.identity.find_project(
        args.service_project, domain_id=domain.id, ignore_missing=False
    )

    users = list(
        find_users_to_disable(conn, domain.id, service_project.id, set(args.skip_user))
    )
    LOG.info("Found %s enabled non-service user(s) in domain %s", len(users), domain.name)

    for user in users:
        if args.dry_run:
            LOG.info("DRY-RUN: would disable user %s:%s", user.id, user.name)
            continue
        try:
            disable_user(conn, user)
        except Exception as ex:
            LOG.warning("failed to disable user %s:%s: %s", user.id, user.name, ex)


def launch_main():
    main(sys.argv[1:])


if __name__ == "__main__":
    launch_main()
