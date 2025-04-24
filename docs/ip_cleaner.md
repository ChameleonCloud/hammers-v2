# Floating IP (and router) Reaper

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


## Arguments

- cloud: which entry in clouds.yaml to run against
- dry-run: prints out what addresses would be deleted, instead of deleting them
- grace-days: how long an address needs to be unused before we clean it up
