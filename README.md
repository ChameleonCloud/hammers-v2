# Bag o' Hammers

*Percussive maintenance.*

Collection of various tools to keep things ship-shape. Not particularly bright tools, but good for a first-pass.

This is a "v2" rewrite of https://github.com/chameleoncloud/hammers, with the following goals:

1. only interact with openstack via the API, never directly editing files or accessing the DB
1. work correctly with standard openstack auth mechanisms, both clouds.yaml and openrc, and allow use of app credentials
1. leverage openstacksdk rather than re-implementing all APIs
1. don't hard-code policy decisions, allow this to be configured per-hammer
1. have unit-tests for logic in each hammer, but don't test the API, rely on upstream for that

As for deployment, the plan is to run this in parallel with hammers v1, and incrementally migrate hammers to the new format, one at a time.

# Current Hammers


## Periodic Node Inspector

Ensures ironic-inspector runs against all nodes every so often.

Since this loops over all ironic nodes and modifies the state, it will eventually integrate the following other hammer's functionality:

1. resetting the state of ironic hosts from `error`, usually from neutron port timeouts, or ipmi failures
1. cleaning nodes to set bios boot configuration, or updating firmware
1. syncing metadata from referenceapi to blazar
1. checking if the ironic inspection data is unexpectly bad, such as if a disk is missing
1. other maintenance tasks that would require a "lock" on a given node (at least until we tell doni to take this over)

