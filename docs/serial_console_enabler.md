# Serial Console Enabler

Ensures every ironic node has the serial console turned on, so that the horizon console works for end-users.

This script will enable the serial console for an ironic node IFF:

1. `console_enabled` is false
1. the node is not in maintenance
1. the node's `console_interface` is not `no-console` (driver doesn't support it)
1. the node's provision state is one of `active`, `available`, or `manageable` (avoids touching nodes mid-deploy, cleaning, or in error)

Nodes where the driver claims to support a console but the API call fails (e.g., non-compliant IPMI) will log a warning and be retried on the next run.

## Arguments

- cloud: which entry in clouds.yaml to run against
- dry-run: prints out which nodes would be changed, instead of changing them
- debug: increase log verbosity
