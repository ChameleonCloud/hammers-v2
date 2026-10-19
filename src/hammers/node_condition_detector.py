"""Read-only Prometheus exporter: report ironic nodes in a bad condition as chameleon_node_condition.

Checks: orphaned_instance (the node's instance_uuid is a server nova returns 404 for).
"""

import argparse
import logging
import sys
import time
from typing import Callable, NamedTuple

import openstack
from openstack import exceptions
from openstack.baremetal.v1.node import Node
from openstack.connection import Connection
from prometheus_client import CollectorRegistry, generate_latest, start_http_server
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.registry import Collector

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
openstack.enable_logging(debug=False)
LOG = logging.getLogger(__name__)

# fields= drops every attribute not named here, so name each one we read.
# The SDK exposes uuid as node.id and instance_uuid as node.instance_id.
NODE_FIELDS = ["uuid", "name", "provision_state", "instance_uuid"]


class Condition(NamedTuple):
    condition: str
    node_uuid: str
    node_name: str
    provision_state: str
    # the server nova returns 404 for; empty for conditions that aren't about an instance
    instance_uuid: str = ""


def find_orphans(conn: Connection) -> list[Node]:
    """Return nodes whose instance nova answers 404 for. Any other error raises."""
    nodes = [n for n in conn.baremetal.nodes(fields=NODE_FIELDS) if n.instance_id]
    server_ids = {s.id for s in conn.compute.servers(details=False, all_projects=True)}

    orphans = []
    for node in nodes:
        if node.instance_id in server_ids:
            continue
        # the list is a snapshot, so a server created since would look missing.
        # Confirm with a GET, and only count a 404.
        try:
            conn.compute.get_server(node.instance_id)
        except exceptions.NotFoundException:
            LOG.warning("orphan: node %s:%s instance %s", node.id, node.name, node.instance_id)
            orphans.append(node)
    return orphans


def check_orphaned_instance(conn: Connection) -> list[Condition]:
    return [
        Condition("orphaned_instance", n.id, n.name or "", n.provision_state, n.instance_id)
        for n in find_orphans(conn)
    ]


# check name -> function returning that check's conditions. Add a check here.
CHECKS: dict[str, Callable[[Connection], list[Condition]]] = {
    "orphaned_instance": check_orphaned_instance,
}


class NodeConditionCollector(Collector):
    """Serves the latest result of each check.

    A check that fails keeps its last results and last-success time, so the time
    goes stale and NodeCheckStale fires. A check that has never succeeded reports
    a last-success time of 0, so it alerts too.
    """

    def __init__(self, checks: dict[str, Callable[[Connection], list[Condition]]]):
        self.checks = checks
        self.conditions: dict[str, list[Condition]] = {name: [] for name in checks}
        self.last_success: dict[str, float] = {name: 0 for name in checks}

    def run(self, conn: Connection) -> bool:
        """Run every check once. Return False if any check failed."""
        all_ok = True
        for name, check in self.checks.items():
            try:
                conditions = check(conn)
            except Exception:
                LOG.exception("check %s failed, keeping its last results", name)
                all_ok = False
                continue
            self.conditions[name] = conditions
            self.last_success[name] = time.time()
            LOG.info("check %s: %d node(s)", name, len(conditions))
        return all_ok

    def collect(self):
        condition = GaugeMetricFamily(
            "chameleon_node_condition",
            "Node is in a bad condition, named by the condition label.",
            labels=["check", "condition", "node_uuid", "node_name", "provision_state", "instance_uuid"],
        )
        last_success = GaugeMetricFamily(
            "chameleon_node_check_last_success_timestamp_seconds",
            "Unix time of the last run of this check that completed without errors.",
            labels=["check"],
        )
        for name in self.checks:
            for c in self.conditions[name]:
                condition.add_metric([name, *c], 1)
            last_success.add_metric([name], self.last_success[name])
        yield condition
        yield last_success


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cloud", help="item in clouds.yaml to connect to, same as OS_CLOUD")
    parser.add_argument("--port", type=int, default=9199, help="port to serve /metrics on (default 9199)")
    parser.add_argument("--interval", type=int, default=600, help="seconds between check runs (default 600)")
    parser.add_argument("--once", action="store_true", help="run the checks once, print the metrics and exit; exits 1 if a check failed")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    conn = openstack.connect(cloud=args.cloud)
    collector = NodeConditionCollector(CHECKS)

    if args.once:
        ok = collector.run(conn)
        registry = CollectorRegistry()
        registry.register(collector)
        sys.stdout.write(generate_latest(registry).decode())
        sys.exit(0 if ok else 1)

    registry = CollectorRegistry()
    registry.register(collector)
    start_http_server(args.port, registry=registry)
    LOG.info("serving /metrics on port %d, running checks every %ds", args.port, args.interval)
    while True:
        collector.run(conn)
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
