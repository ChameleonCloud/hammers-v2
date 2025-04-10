import argparse
import collections
import yaml
import sys
import iso8601
import openstack
from datetime import timedelta


def parse_args(args: list[str]) -> argparse.Namespace:
    """Handle CLI arguments."""
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--file",
        type=str,
        required=True,
        help="File",
    )
    return parser.parse_args(args)


def merge_timeframes(timeframes):
    # Timeframes is a list of (start, end) tuples
    if not timeframes:
        return []
    timeframes.sort()
    merged = [timeframes[0]]
    for start, end in timeframes[1:]:
        last_start, last_end = merged[-1]
        # print(last_end, start + timedelta(minutes=10))
        if last_end <= start + timedelta(minutes=10):
            merged[-1] = (last_start, end)
        else:
            merged.append((start, end))
    return merged


def main(arg_list: list[str]) -> None:
    args = parse_args(arg_list)

    with open(args.file, "r") as f:
        data = yaml.safe_load(f)

    changes_by_site = {}
    hosts_by_id = {}

    # Hacky project id stuff. This only works with one project now
    project_id = None
    for cloud_site, uuids in data.items():
        changes_by_project = {}
        conn = openstack.connect(cloud=cloud_site)

        for host in conn.reservation.hosts():
            hosts_by_id[host.id] = host
        allocations = list(conn.reservation.host_allocations())
        allocs_by_lease_id = collections.defaultdict(list)
        for alloc in allocations:
            for reservation in alloc.reservations:
                allocs_by_lease_id[reservation["lease_id"]].append(alloc.resource_id)

        for uuid in uuids:
            lease = conn.reservation.get_lease(uuid)
            project_id = lease.project_id
            s = iso8601.parse_date(lease.start_date)
            e = iso8601.parse_date(lease.end_date)
            lease_allocs = allocs_by_lease_id[uuid]
            changes_by_host = changes_by_project.get(project_id, collections.defaultdict(list))
            for alloc in lease_allocs:
                changes_by_host[alloc].append((s, e))
            changes_by_project[project_id] = changes_by_host
        changes_by_site[cloud_site] = changes_by_project

    commands_by_date = collections.defaultdict(list)
    for site, changes_by_project in changes_by_site.items():
        print(site)
        for project, host_dict in changes_by_project.items():
            for host, times in host_dict.items():
                print(host, project)
                h = hosts_by_id[host]
                print(f"\t{h.properties['node_type']} - {h.properties['node_name']} ({h.id})")
                merged_times = merge_timeframes(times)
                for time_tuple in merged_times:
                    start, end = time_tuple
                    restricted_reason = f"Node is being used for education project until {end.date()}"
                    set_cmd = f'OS_CLOUD={site} openstack reservation host set --extra authorized_projects={project} --extra restricted_reason="{restricted_reason}" {h.id}'
                    unset_cmd = f"OS_CLOUD={site} openstack reservation host unset --extra authorized_projects --extra restricted_reason {h.id}"

                    commands_by_date[start].append(set_cmd)
                    commands_by_date[end].append(unset_cmd)

                    print(f"\t\t{start} - {end}")

    for date, commands in sorted(commands_by_date.items()):
        print(date.astimezone().isoformat())
        for cmd in commands:
            print(f"\t{cmd}")


def launch_main():
    main(sys.argv[1:])


if __name__ == "__main__":
    launch_main()
