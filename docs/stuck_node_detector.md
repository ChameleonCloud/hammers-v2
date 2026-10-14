# Stuck Node Detector

Finds Ironic nodes stuck in states they will not leave by themselves, and reports them as Prometheus metrics. It is read-only: it never changes a node, a server, or a port, and has no `--apply` flag.

For what to do about a flagged node, see the `IronicNodeStuck` runbook in chi-in-a-box.

## What it flags

Each node falls in at most one case:

| case | meaning |
|---|---|
| `deleted_instance` | the node holds an `instance_uuid`, Nova returns 404 for it, and Nova lists it as deleted. The server is provably gone, in any provision state |
| `failed_state` | `error`, `deploy failed`, `clean failed`, `inspect failed`, `rescue failed`, `unrescue failed` or `adopt failed`, and not in maintenance |
| `stuck_transition` | `wait call-back`, `deploying`, `cleaning`, `clean wait`, `deleting`, `inspecting`, `inspect wait` or `rescuing`, and not in maintenance. The detector only reports the age; the alert picks the threshold |
| `instance_missing_unproven` | Nova returns 404 for the node's instance, but has no deleted record. Never treat this as gone |
| `parked_no_reason` | in maintenance with an empty reason |
| `available_with_instance` | `available` but still holds an `instance_uuid` |

Nova lookups fail safe: any error other than a 404 means "unknown", and the node gets no instance case. Before any lookup the detector checks it can list servers in all projects. If that fails, or more than `--max-lookup-failure-fraction` of lookups fail, the run counts as failed. It still writes the census and the Ironic-only cases, exits 1, and does not move the last-success time forward.

## Metrics

| metric | labels | value |
|---|---|---|
| `chi_node_census` | `provision_state`, `maintenance` | number of nodes |
| `chi_node_issue_age_seconds` | `node`, `uuid`, `provision_state`, `case` | seconds since the node last changed provision state |
| `chi_detector_last_success_timestamp_seconds` | | Unix time of the last successful run |
| `chi_detector_lookup_failures` | | Nova lookups that errored in the last run |

## Running it

```shell
stuck_node_detector --cloud <cloud> --textfile /var/lib/node_exporter/textfile/stuck_nodes.prom
```

Without `--textfile` it prints the metrics to stdout. The file is replaced atomically, so node-exporter never reads a partial file. Run it from a systemd timer every 5 to 15 minutes, with admin credentials. A non-admin cannot see other projects' servers and would get false 404s.

## Example alerts

```promql
# failed state for 30 minutes
chi_node_issue_age_seconds{case="failed_state"} > 1800
# stuck transition for 2 hours
chi_node_issue_age_seconds{case="stuck_transition"} > 7200
# any node holding a provably deleted instance
chi_node_issue_age_seconds{case="deleted_instance"}
# the detector itself stopped working (3 missed 15-minute runs)
time() - chi_detector_last_success_timestamp_seconds > 2700
```

## Limits

- No Blazar check: it does not know if a flagged node is under a lease.
- No Placement or Neutron port checks.
- If Nova's deleted rows have been archived or purged, a deleted server shows as `instance_missing_unproven`.
- It makes one Nova lookup per node that holds an instance.
