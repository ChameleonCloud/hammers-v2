"""Read-only detector for Ironic nodes stuck in unusable states.

Writes Prometheus metrics (node-exporter textfile format). It never changes
anything in OpenStack.
"""

import argparse
import logging
import os
import re
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone

import openstack
from openstack import exceptions

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
openstack.enable_logging(debug=False)
LOG = logging.getLogger(__name__)

NODE_FIELDS = [
    "uuid",
    "name",
    "provision_state",
    "target_provision_state",
    "maintenance",
    "maintenance_reason",
    "instance_uuid",
    "provision_updated_at",
    "reservation",
    "last_error",
]
IRONIC_MICROVERSION = "1.82"
PAGE_SIZE = 100

FAILED_STATES = frozenset(
    {
        "error",
        "deploy failed",
        "clean failed",
        "inspect failed",
        "rescue failed",
        "unrescue failed",
        "adopt failed",
    }
)
TRANSITION_STATES = frozenset(
    {
        "wait call-back",
        "deploying",
        "cleaning",
        "clean wait",
        "deleting",
        "inspecting",
        "inspect wait",
        "rescuing",
    }
)

# Result of looking up a node's instance in Nova.
EXISTS = "exists"
DELETED = "deleted"  # Nova GET is 404 and Nova has a deleted record
UNPROVEN = "unproven"  # Nova GET is 404 but no deleted record
UNKNOWN = "unknown"  # lookup errored, say nothing about the instance

LAST_SUCCESS_METRIC = "chi_detector_last_success_timestamp_seconds"


def list_nodes(conn) -> list[dict]:
    """Return all ironic nodes as dicts with NODE_FIELDS."""
    nodes = []
    marker = None
    while True:
        params = {"fields": ",".join(NODE_FIELDS), "limit": PAGE_SIZE}
        if marker:
            params["marker"] = marker
        resp = conn.baremetal.get(
            "/nodes", params=params, microversion=IRONIC_MICROVERSION
        )
        exceptions.raise_from_response(resp)
        page = resp.json()["nodes"]
        nodes.extend(page)
        if len(page) < PAGE_SIZE:
            return nodes
        marker = page[-1]["uuid"]


def check_admin_visibility(conn) -> None:
    """Raise if we cannot list servers across all projects."""
    next(iter(conn.compute.servers(all_projects=True, limit=1)), None)


def lookup_instance(conn, instance_uuid: str) -> str:
    """Ask Nova whether a server exists, is provably deleted, or is unknown."""
    try:
        conn.compute.get_server(instance_uuid)
        return EXISTS
    except exceptions.NotFoundException:
        pass
    except Exception as exc:
        LOG.warning("nova lookup of %s failed: %s", instance_uuid, exc)
        return UNKNOWN

    # 404: only call it deleted if Nova still has a deleted record for it.
    try:
        resp = conn.compute.get(
            "/servers",
            params={"all_tenants": "true", "deleted": "true", "uuid": instance_uuid},
        )
        exceptions.raise_from_response(resp)
        # check the id ourselves in case Nova ignored the uuid filter
        if any(s["id"] == instance_uuid for s in resp.json()["servers"]):
            return DELETED
    except Exception as exc:
        LOG.warning("nova deleted-list lookup of %s failed: %s", instance_uuid, exc)
        return UNKNOWN
    return UNPROVEN


def classify(node: dict, instance: str | None) -> str | None:
    """Return the one case a node falls in, or None if it looks fine."""
    state = node.get("provision_state")
    maintenance = bool(node.get("maintenance"))

    if instance == DELETED:
        return "deleted_instance"
    if not maintenance and state in FAILED_STATES:
        return "failed_state"
    if not maintenance and state in TRANSITION_STATES:
        return "stuck_transition"
    if instance == UNPROVEN:
        return "instance_missing_unproven"
    if maintenance and not (node.get("maintenance_reason") or "").strip():
        return "parked_no_reason"
    if state == "available" and node.get("instance_uuid"):
        return "available_with_instance"
    return None


def issue_age_seconds(node: dict, now: datetime) -> float:
    """Seconds since provision_updated_at, or 0 if unknown."""
    try:
        updated = datetime.fromisoformat(node["provision_updated_at"])
    except (KeyError, TypeError, ValueError):
        return 0
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    return max((now - updated).total_seconds(), 0)


def find_issues(
    conn, nodes: list[dict], now: datetime, max_failure_fraction: float
) -> tuple[list[dict], int, bool]:
    """Return (flagged nodes, nova lookup failures, run succeeded)."""
    ok = True
    lookups = failures = 0
    instances = {}

    try:
        check_admin_visibility(conn)
    except Exception as exc:
        LOG.error("cannot list servers in all projects, skipping nova checks: %s", exc)
        ok = False
    else:
        for node in nodes:
            if node.get("instance_uuid"):
                lookups += 1
                instances[node["uuid"]] = lookup_instance(conn, node["instance_uuid"])
        failures = list(instances.values()).count(UNKNOWN)
        if lookups and failures / lookups > max_failure_fraction:
            LOG.error("%d of %d nova lookups failed", failures, lookups)
            ok = False

    issues = []
    for node in nodes:
        case = classify(node, instances.get(node["uuid"]))
        if case:
            issues.append(
                {
                    "node": node.get("name") or "",
                    "uuid": node["uuid"],
                    "provision_state": node.get("provision_state") or "",
                    "case": case,
                    "age": issue_age_seconds(node, now),
                }
            )
    return issues, failures, ok


def escape_label(value) -> str:
    """Escape a label value for the Prometheus text format."""
    return (
        str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    )


def sample(name: str, labels: dict, value) -> str:
    pairs = ",".join(f'{k}="{escape_label(v)}"' for k, v in labels.items())
    return f"{name}{{{pairs}}} {value:.0f}" if pairs else f"{name} {value:.0f}"


def render_metrics(
    nodes: list[dict],
    issues: list[dict],
    lookup_failures: int,
    last_success: float | None,
) -> str:
    census = Counter(
        (n.get("provision_state") or "", str(bool(n.get("maintenance"))).lower())
        for n in nodes
    )
    lines = [
        "# HELP chi_node_census Number of ironic nodes by provision state and maintenance flag.",
        "# TYPE chi_node_census gauge",
    ]
    for (state, maintenance), count in sorted(census.items()):
        labels = {"provision_state": state, "maintenance": maintenance}
        lines.append(sample("chi_node_census", labels, count))

    lines += [
        "# HELP chi_node_issue_age_seconds Seconds since the flagged node last changed provision state.",
        "# TYPE chi_node_issue_age_seconds gauge",
    ]
    for issue in sorted(issues, key=lambda i: (i["case"], i["uuid"])):
        labels = {k: issue[k] for k in ("node", "uuid", "provision_state", "case")}
        lines.append(sample("chi_node_issue_age_seconds", labels, issue["age"]))

    if last_success is not None:
        lines += [
            f"# HELP {LAST_SUCCESS_METRIC} Unix time of the last fully successful detector run.",
            f"# TYPE {LAST_SUCCESS_METRIC} gauge",
            sample(LAST_SUCCESS_METRIC, {}, last_success),
        ]
    lines += [
        "# HELP chi_detector_lookup_failures Nova instance lookups that errored in the last run.",
        "# TYPE chi_detector_lookup_failures gauge",
        sample("chi_detector_lookup_failures", {}, lookup_failures),
    ]
    return "\n".join(lines) + "\n"


def previous_success(path: str | None) -> float | None:
    """Read the last-success timestamp left by an earlier run, if any."""
    if not path:
        return None
    try:
        with open(path) as f:
            match = re.search(rf"^{LAST_SUCCESS_METRIC} (\S+)$", f.read(), re.MULTILINE)
        return float(match.group(1)) if match else None
    except (OSError, ValueError):
        return None


def write_textfile(path: str, text: str) -> None:
    """Atomically replace `path`, so node-exporter never reads a partial file."""
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".stuck_node.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.chmod(tmp_path, 0o644)  # mkstemp makes 0600, node-exporter may be another user
        os.replace(tmp_path, path)
    except BaseException:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cloud",
        help="item in clouds.yaml to connect to, same as OS_CLOUD",
    )
    parser.add_argument(
        "--textfile",
        help="write metrics to this file (atomically) instead of stdout. "
        "Use a .prom file in node-exporter's textfile directory.",
    )
    parser.add_argument(
        "--max-lookup-failure-fraction",
        type=float,
        default=0.2,
        help="fail the run if more than this fraction of nova lookups error",
    )
    return parser.parse_args()


def main() -> None:
    """Report stuck nodes."""
    args = parse_args()
    conn = openstack.connect(cloud=args.cloud)
    now = datetime.now(tz=timezone.utc)

    nodes = list_nodes(conn)
    issues, failures, ok = find_issues(
        conn, nodes, now, args.max_lookup_failure_fraction
    )
    for issue in issues:
        LOG.info(
            "flagged node %s (%s) state=%s case=%s age=%ds",
            issue["node"],
            issue["uuid"],
            issue["provision_state"],
            issue["case"],
            issue["age"],
        )
    LOG.info(
        "checked %d nodes, flagged %d, nova lookup failures %d, run %s",
        len(nodes),
        len(issues),
        failures,
        "succeeded" if ok else "FAILED",
    )

    # a failed run keeps the old last-success time, so the alert can see staleness
    last_success = now.timestamp() if ok else previous_success(args.textfile)
    text = render_metrics(nodes, issues, failures, last_success)
    if args.textfile:
        write_textfile(args.textfile, text)
    else:
        sys.stdout.write(text)
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
