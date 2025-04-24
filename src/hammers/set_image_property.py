"""
set_image_property.py

A small CLI tool to batch-update OpenStack image metadata
(e.g., mark images as supported or deprecated).
"""
import sys
import argparse
import logging
import yaml
import openstack


def get_openstack_connection(cloud_name: str):
    return openstack.connect(cloud=cloud_name)


def load_values_from_file(file_path: str) -> list[tuple[str, str]]:
    """
    Read a file with lines in 'UUID:value' format and return a list
    of (uuid, value) tuples.
    """
    values: list[tuple[str, str]] = []
    with open(file_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if ":" not in line:
                logging.error(
                    f"Invalid format in line: '{line}' "
                    "(expected 'UUID:value'). Skipping."
                )
                continue
            uuid, val = line.split(':', 1)
            values.append((uuid.strip(), val.strip()))
    return values


def get_values(args: argparse.Namespace) -> list[tuple[str, str]]:
    """
    Determine the list of (uuid, value) pairs based on CLI args,
    either loading from a file or using a single value.
    """
    if args.values_file:
        try:
            return load_values_from_file(args.values_file)
        except FileNotFoundError:
            logging.error(f"File not found: {args.values_file}")
            sys.exit(1)
    if not args.single_value:
        logging.error(
            "--single-value is required when not using"
            " --values-file."
        )
        sys.exit(1)
    if ":" not in args.single_value:
        logging.error(
            "Invalid format for --single-value"
            " (expected 'UUID:value')."
        )
        sys.exit(1)
    uuid, val = args.single_value.split(':', 1)
    return [(uuid.strip(), val.strip())]


def tag_image(conn, uuid: str, value: str, field: str, dry_run: bool) -> None:
    """
    Apply the metadata field=value to a single image, or log if dry-run.
    """
    action = f"{field}={value}"
    if dry_run:
        logging.info(f"DRY-RUN: would set '{action}' on image {uuid}")
        return
    logging.info(f"Setting '{action}' on image {uuid}...")
    try:
        conn.compute.set_image_metadata(uuid, **{field: value})
        logging.info(f"{action} successfully set on image {uuid}")
    except Exception as e:
        logging.error(f"Error tagging image {uuid}: {e}")


def parse_args(args: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Mark images with a given metadata property (e.g., "
            "deprecated or supported)."
        )
    )
    parser.add_argument(
        "--site-yaml", required=True, type=str,
        help="A YAML file with site information for where the images reside."
    )
    parser.add_argument(
        "--metadata-field", required=True, type=str,
        dest="metadata_field",
        help=(
            "Metadata field name to set."
        )
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Perform a dry run without making any changes."
    )
    parser.add_argument(
        "--debug", action="store_true",
        help="Enable debug logging."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--values-file",
        dest="values_file",
        help=(
            "Path to a text file listing UUID:value pairs, "
            "one per line."
        )
    )
    group.add_argument(
        "--single-value",
        help="Single image UUID and value in the format 'UUID:value'."
    )
    return parser.parse_args(args)


def main(arg_list: list[str]) -> None:
    args = parse_args(arg_list)

    log_level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(level=log_level)

    try:
        with open(args.site_yaml, "r") as f:
            site = yaml.safe_load(f)
    except Exception as e:
        logging.error(
            f"Failed to load site YAML '{args.site_yaml}': {e}"
        )
        sys.exit(1)

    cloud_name = site.get('image_store_cloud')
    if not cloud_name:
        logging.error("Required 'image_store_cloud' key not found in site YAML")
        sys.exit(1)

    conn = get_openstack_connection(cloud_name)

    values = get_values(args)
    for uuid, val in values:
        tag_image(conn, uuid, val, args.metadata_field, args.dry_run)


def launch_main():
    main(sys.argv[1:])


if __name__ == "__main__":
    launch_main()
