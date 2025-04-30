"""Module to clean up resources by expired projects"""

from collections import defaultdict
import logging
import argparse
import re
import os
import sys
from datetime import timedelta as TimeDelta
from kubernetes import client, config

from hammers.utils import get_user_groups, project_is_expired


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
LOG = logging.getLogger(__name__)


def get_volumes_by_username(namespace, kube_config_path: str = None) -> dict[str, list[str]]:
    if kube_config_path:
        config.load_kube_config(config_file="/path/to/your/kubeconfig")
    else:
        config.load_incluster_config()

    v1 = client.CoreV1Api()

    volumes_by_username = defaultdict(list)
    pvcs = v1.list_namespaced_persistent_volume_claim(namespace="jupyter").items
    for pvc in pvcs:
        username = pvc.metadata.annotations.get("hub.jupyter.org/username")
        pvc_name = pvc.metadata.name
        # If the PVC was not bound since migrations, it won't have the annotation.
        if not username:
            # Remove both claim- prefix and 6 digit hex suffix. Not 100% accurate, but since
            # most usernames end with .edu, and u is not a hex digit, it mostly works.
            partial_name = re.sub(r'^claim-', '', pvc_name)
            username = re.sub(r'-[a-f0-9]{6,}$', '', partial_name)
        if username:
            volumes_by_username[username].append(pvc_name)
    return volumes_by_username


def parse_args(args: list[str]) -> argparse.Namespace:
    """Handle CLI arguments."""
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=os.environ.get('HAMMERS_DRY_RUN'),
        help="print out which servers would be shelved, instead of shelving them.",
    )
    parser.add_argument("--debug", action="store_true", help="increase log verbosity.")
    parser.add_argument(
        "--portal-api-token",
        type=str,
        default=os.environ.get('HAMMERS_PORTAL_API_TOKEN'),
        help="API token for portal",
    )
    parser.add_argument(
        "--ignore-pending",
        action="store_true",
        help="Ignore servers from a project with a pending allocation.",
    )
    parser.add_argument(
        "--keycloak-url",
        type=str,
        default=os.environ.get("HAMMERS_KEYCLOAK_URL", "https://auth.chameleoncloud.org/auth"),
        help="The cloud to use for OpenStack connection.",
    )
    parser.add_argument(
        "--keycloak-client-id",
        type=str,
        default=os.environ.get("HAMMERS_KEYCLOAK_CLIENT_ID", "portal-admin"),
        help="Keycloak admin client id.",
    )
    parser.add_argument(
        "--keycloak-client-secret",
        type=str,
        default=os.environ.get("HAMMERS_KEYCLOAK_CLIENT_SECRET"),
        help="Keycloak admin client secret.",
    )
    parser.add_argument(
        "--kube-config-path",
        type=str,
        help="Path to kubeconfig file.",
    )
    parser.add_argument(
        "--kube-namespace",
        type=str,
        default=os.environ.get("HAMMERS_KUBE_NAMESPACE"),
        help="Kubernetes namespace to use.",
    )
    return parser.parse_args(args)


def main(arg_list: list[str]) -> None:
    args = parse_args(arg_list)

    if args.debug:
        LOG.setLevel(logging.DEBUG)

    api_token = args.portal_api_token
    dry_run = args.dry_run
    ignore_pending = args.ignore_pending

    if args.debug:
        LOG.setLevel(logging.DEBUG)

    volumes_by_username = get_volumes_by_username(args.kube_namespace, args.kube_config_path)
    for username, volumes in volumes_by_username.items():
        try:
            projects = get_user_groups(
                username,
                args.keycloak_url,
                args.keycloak_client_id,
                args.keycloak_client_secret,
            )
            grace_period = TimeDelta(days=365)
            has_active_project = False
            for charge_code in projects:
                # NOTE Some projects may be "admin" projects, but in that case project_is_expired returns True.
                if not project_is_expired(
                    charge_code, grace_period, api_token=api_token, ignore_pending=ignore_pending, log=LOG
                ):
                    has_active_project = True
            if not has_active_project:
                for volume in volumes:
                    LOG.info("Should delete volume '%s' for user '%s'", volume, username)
                    if not dry_run:
                        # TODO add delete code once we are confident this is working.
                        pass
            else:
                LOG.info("User %s has an active project, skipping", username)
        except Exception as e:
            LOG.error("Could not get projects for user %s", username)
            LOG.error(e)


def launch_main():
    main(sys.argv[1:])


if __name__ == "__main__":
    launch_main()
