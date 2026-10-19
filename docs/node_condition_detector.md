# Node Condition Detector

A Prometheus exporter that reports ironic nodes in a bad condition. Read-only: it never changes a node.

It runs every check every `--interval` seconds and serves the latest results on `<addr>:<port>/metrics`. Prometheus scrapes it as job `node_condition`.

## Checks

| check | condition | means | runbook |
|---|---|---|---|
| `orphaned_instance` | `orphaned_instance` | The node has an `instance_uuid`, and nova returns 404 for that server. Nova has lost the server but ironic still holds the node, so it can't be scheduled. `instance_uuid` is the missing server. See nova bugs [2169776](https://bugs.launchpad.net/nova/+bug/2169776), [2169779](https://bugs.launchpad.net/nova/+bug/2169779), [2169200](https://bugs.launchpad.net/nova/+bug/2169200). | [IronicNodeOrphanInstance](https://github.com/ChameleonCloud/chi-in-a-box/blob/chameleoncloud/2025.1/docs/runbooks/IronicNodeOrphanInstance.md) |

To add a check, write a function that returns its `Condition`s and add it to `CHECKS`.

Nodes in failed or stuck provision states (`deploy failed`, `error`, `wait call-back`, ...) are covered by the `IronicNodeStuckFailed` and `IronicNodeStuckTransitional` alerts on `openstack_ironic_node`, not here.

## Metrics

```
chameleon_node_condition{check, condition, node_uuid, node_name, provision_state, instance_uuid} 1
chameleon_node_check_last_success_timestamp_seconds{check}
```

If a check fails, it keeps serving its last results, and its last-success time goes stale. Other checks are unaffected. A check that has never succeeded reports a last-success time of 0.

## Arguments

- `--cloud`: entry in clouds.yaml to use, same as `OS_CLOUD`
- `--addr`: address to serve `/metrics` on (default `0.0.0.0`, every interface). Use the internal API address on a host with a public interface.
- `--port`: port to serve `/metrics` on (default 9199)
- `--interval`: seconds between check runs (default 600)
- `--once`: run the checks once, print the metrics and exit; exits 1 if a check failed

## Alert rules

`NodeCondition` covers every condition. A condition doesn't alert while its check is stale (`NodeCheckStale` fires instead), or for nodes with maintenance=true or no longer in ironic. If the exporter is down, `PrometheusAllTargetsMissing` fires for job `node_condition`.

```yaml
groups:
  - name: node-conditions
    rules:
      - alert: NodeCondition
        expr: |
          chameleon_node_condition
            and on (kolla_external_fqdn, check)
              (time() - chameleon_node_check_last_success_timestamp_seconds < 3600)
            and on (kolla_external_fqdn, node_uuid)
              label_replace(openstack_ironic_node{maintenance="false"}, "node_uuid", "$1", "id", "(.*)")
        for: 30m
        labels:
          severity: warning
        annotations:
          summary: "{{ $labels.node_name }} at {{ $labels.kolla_external_fqdn }}: {{ $labels.condition }} ({{ $labels.provision_state }}){{ with $labels.instance_uuid }}, instance {{ . }}{{ end }}"
          # one runbook per condition, listed in the Checks table
          runbook_url: "https://github.com/ChameleonCloud/hammers-v2/blob/main/docs/node_condition_detector.md#checks"
      - alert: NodeCheckStale
        expr: time() - chameleon_node_check_last_success_timestamp_seconds > 3600
        for: 10m
        labels:
          severity: warning
        annotations:
          summary: "Node check {{ $labels.check }} at {{ $labels.kolla_external_fqdn }} hasn't succeeded in 1h; see the node_condition_detector logs"
```
