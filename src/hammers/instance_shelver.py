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

        if instance.project_id in [
            "570aad8999f7499db99eae22fe9b29bb",
            "f6c7696906c04b3c89fc3bda9a1b8be0",
        ]:
            LOG.info(
                "Skipping admin instance %s %s %s",
                instance.name,
                instance.id,
                instance.project_id,
            )
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
        LOG.debug(
            "Snapshot %s %s already exists for instance %s, skipping snapshot creation.",
            snap.name,
            snap.status,
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
            wait=True,
        )
        connection.image.update_image(snapshot_image, owner=instance.project_id)
        return snapshot_image


def retire_instance(
    connection: Connection,
    instance: Server,
    dry_run: bool,
) -> Server:
    """Retire non-reservable instances by shelving them.

    1. lock the instance (shelved and locked counts towards quota)
    2. shut down the instance
    3. snapshot the instance, ensure owned by the project
    4. shelve the instance (must wait for snapshot to complete)
    """

    # LOG.info("Locking instance %s %s", instance.name, instance.status)

    # if instance.status in ["ACTIVE"]:
    #    LOG.info("Shutting down instance %s %s", instance.name, instance.status)

    snapshotted = False
    if instance.status in ["SHUTOFF"]:
        snapshotted = ensure_instance_is_snapshotted(
            connection=connection,
            instance=instance,
            dry_run=dry_run,
        )
        if snapshotted:
            if dry_run:
                LOG.info(
                    "Would shelve instance %s %s %s",
                    instance.name,
                    instance.id,
                    instance.status,
                )
            else:
                LOG.info("Shelving instance %s %s", instance.name, instance.status)
                # connection.compute.shelve_server(instance)

    return instance


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
    parser.add_argument(
        "--parallel",
        type=int,
        default=5,
    )
    return parser.parse_args()


def main() -> None:
    """Drop the hammer."""
    args = parse_args()
    conn = openstack.connect(cloud=args.cloud)

    LOG.info("Collecting instance and reservation info for cloud %s", args.cloud)

    instances_to_retire = get_instances_to_retire(conn)

    with ThreadPoolExecutor(max_workers=args.parallel) as executor:
        future_to_retired_instance = {
            executor.submit(
                retire_instance,
                connection=conn,
                instance=instance,
                dry_run=args.dry_run,
            ): instance
            for instance in instances_to_retire
        }
        for future in as_completed(future_to_retired_instance):
            instance = future_to_retired_instance[future]
            try:
                retired_instance = future.result()
                LOG.info(
                    "Finished! instance %s %s %s",
                    retired_instance.name,
                    retired_instance.id,
                    retired_instance.status,
                )
            except Exception as exc:
                LOG.error(
                    "Error retiring instance %s %s: %s",
                    instance.name,
                    instance.id,
                    exc,
                )


if __name__ == "__main__":
    main()
