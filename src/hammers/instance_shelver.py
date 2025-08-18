"""Periodic inspector script."""

import argparse
import logging
from collections.abc import Generator
from concurrent.futures import Future, ThreadPoolExecutor, as_completed

import openstack
from openstack.compute.v2.server import Server
from openstack.connection import Connection

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
openstack.enable_logging(debug=False)
LOG = logging.getLogger(__name__)


def get_instances_to_retire(connection: Connection) -> Generator[Server]:
    instances = connection.compute.servers(
        all_projects=True,
    )  # type: ignore
    for instance in instances:
        if not instance.flavor.original_name.startswith("m1"):
            continue

        if instance.compute_host:
            yield instance


def ensure_instance_is_snapshotted(
    connection: Connection,
    instance: Server,
    dry_run: bool,
) -> None:
    snapshot_name = f"{instance.id}-snapshot"

    admin_snapshots = connection.image.images(
        name=snapshot_name,
        owner=connection.current_project_id,
    )
    for snap in admin_snapshots:
        connection.image.update_image(snap, owner=instance.project_id)

    existing_snapshots = connection.image.images(
        name=snapshot_name,
        owner=instance.project_id,
    )
    for snap in existing_snapshots:
        LOG.info(
            "Snapshot %s already exists for instance %s, skipping snapshot creation.",
            snapshot_name,
            instance.name,
        )
        return snap

    if dry_run:
        LOG.info("Would snapshot instance %s %s", instance.name, instance.id)
    else:
        LOG.info("snapshotting instance %s %s", instance.name, instance.status)
        snapshot_image = connection.create_image_snapshot(
            name=snapshot_name,
            server=instance,
            wait=False,
        )
        return snapshot_image


def retire_instance(
    connection: Connection,
    instance: Server,
    dry_run: bool,
) -> None:
    """Retire non-reservable instances by shelving them.

    1. shut down the instance
    2. snapshot the instance, ensure owned by the project
    3. shelve the instance (must wait for snapshot to complete)
    4. lock the instance (shelved and locked counts towards quota)
    """

    LOG.info("Locking instance %s %s", instance.name, instance.status)

    if instance.status in ["ACTIVE"]:
        LOG.info("Shutting down instance %s %s", instance.name, instance.status)

    snapshotted = False
    if instance.status in ["SHUTOFF"]:
        snapshotted = ensure_instance_is_snapshotted(
            connection=connection,
            instance=instance,
            dry_run=dry_run,
        )

    if snapshotted:
        LOG.info("Shelving instance %s %s", instance.name, instance.status)
        # TODO shelve the instance


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--cloud",
        help="item in clouds.yaml to connect to, same as OS_CLOUD",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
    )
    return parser.parse_args()


def main() -> None:
    """Drop the hammer."""
    args = parse_args()
    conn = openstack.connect(cloud=args.cloud)

    LOG.info("Collecting instance and reservation info for cloud %s", args.cloud)

    instances_to_retire = get_instances_to_retire(conn)

    for i in instances_to_retire:
        retire_instance(
            connection=conn,
            instance=i,
            dry_run=args.dry_run,
        )

    # future_to_retired_instance = {}
    # with ThreadPoolExecutor(max_workers=args.parallel) as executor:
    #     for instance in instances_to_retire:
    #         shelving_future = executor.submit(
    #             retire_instance,
    #             connection=conn,
    #             instance=instance,
    #             dry_run=args.dry_run,
    #         )
    #         future_to_retired_instance[shelving_future] = instance


if __name__ == "__main__":
    main()
