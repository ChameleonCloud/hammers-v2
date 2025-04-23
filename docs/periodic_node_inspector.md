# Periodic Node Inspector

Ensures ironic-inspector runs against all nodes every so often.

Since this loops over all ironic nodes and modifies the state, it will eventually integrate the following other hammer's functionality:

1. resetting the state of ironic hosts from `error`, usually from neutron port timeouts, or ipmi failures
1. cleaning nodes to set bios boot configuration, or updating firmware
1. syncing metadata from referenceapi to blazar
1. checking if the ironic inspection data is unexpectly bad, such as if a disk is missing
1. other maintenance tasks that would require a "lock" on a given node (at least until we tell doni to take this over)
