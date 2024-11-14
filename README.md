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

## Floating IP (and router) Reaper

CHI has two ways to get public IPs, reservable and ad-hoc.
Although quotas limit how many ad-hoc IPs a project may allocate, nothing cleans them up automatically, even if not used.

This script will delete floating IPs IFF:

1. they are not a blazar reservable IP
1. they are not of status `ACTIVE`
1. `updated_at` is older than `--grace-days` days

This script will additionally remove unused routers to free up their addresses, IFF:

1. their public IP is not a blazar reservable IP
1. they are not attached to any neutron networks (e.g. have only a public IP, but no allocated interfaces)
1. `updated_at` is older than `--grace-days` days


### Arguments

- cloud: which entry in clouds.yaml to run against
- dry-run: prints out what addresses would be deleted, instead of deleting them
- grace-days: how long an address needs to be unused before we clean it up
