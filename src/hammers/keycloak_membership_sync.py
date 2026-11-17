"""Remove keystone project memberships that no longer exist in keycloak."""

import argparse
import logging
import os
import sys
import time
from collections import defaultdict
from collections.abc import Generator

import openstack
import requests
from openstack.connection import Connection

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
openstack.enable_logging(debug=False)
LOG = logging.getLogger(__name__)

KEYCLOAK_PAGE_SIZE = 500


class KeycloakClient:
    """Read-only client for the keycloak admin API, using a service account."""

    def __init__(self, url: str, realm: str, client_id: str, client_secret: str):
        self.admin_url = f"{url}/admin/realms/{realm}"
        self.token_url = f"{url}/realms/{realm}/protocol/openid-connect/token"
        self.credentials = {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        }
        self.session = requests.Session()
        self.token_expires_at = 0.0

    def _get(self, path: str, **params) -> list[dict]:
        if time.monotonic() >= self.token_expires_at:
            res = requests.post(self.token_url, data=self.credentials)
            res.raise_for_status()
            token = res.json()
            self.session.headers["Authorization"] = f"Bearer {token['access_token']}"
            # renew early so the token can't expire while a request is in flight
            self.token_expires_at = time.monotonic() + token["expires_in"] - 30
        res = self.session.get(f"{self.admin_url}/{path}", params=params)
        res.raise_for_status()
        return res.json()

    def _get_all(self, path: str) -> Generator[dict]:
        first = 0
        while True:
            page = self._get(
                path, first=first, max=KEYCLOAK_PAGE_SIZE, briefRepresentation="true"
            )
            yield from page
            if len(page) < KEYCLOAK_PAGE_SIZE:
                return
            first += KEYCLOAK_PAGE_SIZE

    def group_ids_by_name(self) -> dict[str, str]:
        """Return the ids of all top-level groups, keyed by group name."""
        return {g["name"]: g["id"] for g in self._get_all("groups")}

    def group_member_usernames(self, group_id: str) -> set[str]:
        """Return the usernames of the direct members of a group."""
        return {u["username"] for u in self._get_all(f"groups/{group_id}/members")}


def find_stale_memberships(
    conn: Connection, keycloak: KeycloakClient, domain_id: str, role_id: str
) -> Generator[tuple[dict, dict]]:
    """Yield (project, user) for each role assignment in the domain whose user
    is no longer a member of the keycloak group with the project's name."""
    members_by_project = defaultdict(list)
    projects = {}
    for assignment in conn.identity.role_assignments(
        role_id=role_id, include_names=True
    ):
        project = assignment.scope.get("project")
        user = assignment.user
        if not (project and user):
            continue
        if project["domain"]["id"] != domain_id or user["domain"]["id"] != domain_id:
            continue
        projects[project["name"]] = project
        members_by_project[project["name"]].append(user)

    group_ids = keycloak.group_ids_by_name()
    for project_name, users in members_by_project.items():
        if project_name not in group_ids:
            LOG.warning("skipping project %s, no matching keycloak group", project_name)
            continue
        members = keycloak.group_member_usernames(group_ids[project_name])
        for user in users:
            if user["name"] not in members:
                yield projects[project_name], user


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
        help="print out which memberships would be removed, instead of removing them.",
    )
    parser.add_argument("--debug", action="store_true", help="increase log verbosity.")
    parser.add_argument(
        "--domain",
        default="chameleon",
        help="keystone domain that federated users and projects are created in",
    )
    parser.add_argument(
        "--role",
        default="member",
        help="keystone role that the federation mapping assigns on projects",
    )
    parser.add_argument(
        "--keycloak-url",
        default="https://auth.chameleoncloud.org/auth",
        help="keycloak base URL",
    )
    parser.add_argument("--keycloak-realm", default="chameleon")
    parser.add_argument(
        "--keycloak-client-id",
        required=True,
        help="service account client with view-users and query-groups roles",
    )
    parser.add_argument(
        "--keycloak-client-secret",
        default=os.environ.get("KEYCLOAK_CLIENT_SECRET"),
        help="defaults to $KEYCLOAK_CLIENT_SECRET",
    )
    return parser.parse_args(args)


def main(arg_list: list[str]) -> None:
    args = parse_args(arg_list)

    if args.debug:
        LOG.setLevel(logging.DEBUG)

    conn = openstack.connect(cloud=args.cloud)
    keycloak = KeycloakClient(
        args.keycloak_url,
        args.keycloak_realm,
        args.keycloak_client_id,
        args.keycloak_client_secret,
    )
    domain = conn.identity.find_domain(args.domain, ignore_missing=False)
    role = conn.identity.find_role(args.role, ignore_missing=False)

    for project, user in find_stale_memberships(conn, keycloak, domain.id, role.id):
        if args.dry_run:
            LOG.info(
                "DRY-RUN: would remove user %s from project %s",
                user["name"],
                project["name"],
            )
            continue
        LOG.info("removing user %s from project %s", user["name"], project["name"])
        try:
            conn.identity.unassign_project_role_from_user(
                project["id"], user["id"], role.id
            )
        except Exception as ex:
            LOG.warning(
                "failed to remove user %s from project %s: %s",
                user["name"],
                project["name"],
                ex,
            )


def launch_main():
    main(sys.argv[1:])


if __name__ == "__main__":
    launch_main()
