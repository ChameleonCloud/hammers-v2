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

- [Periodic Node Inspector](docs/periodic_node_inspector.md)
- [Floating IP (and router) Reaper](docs/ip_cleaner.md)
- [Image Deployer](docs/image_deployer.md)
- [Set Image Property](docs/set_image_property.md)
- [Serial Console Enabler](docs/serial_console_enabler.md)

# Running Hammers

Create a virtual environment and install the dependencies:
```
python -m venv .venv
source .venv/bin/activate
pip install .
```

Then you can reference the hammers directly:
```
$ image_deployer -h
```

# Running Tests

To run the tests you'll need to install optional dependencies:
```
pip install '.[dev]'
```

You can run the tests with tox:
```
tox
```

# Building and Deploying

Sites run these tools from one container image, `ghcr.io/chameleoncloud/chameleon_site_tools`. It holds hammers v2, the image deployer, the periodic inspector and the reference repo generator.

There are two CI workflows:
- `.github/workflows/test.yaml` runs the tests on pull requests and on pushes to `main`.
- `.github/workflows/build-image.yaml` builds and pushes the image on pushes to `main` and of `v*` tags, and when started manually from the Actions tab ("Run workflow"). It does not wait for the tests.

Which image tags get pushed depends on the trigger:

| Trigger | Image tags pushed |
|---|---|
| push to `main` | `main`, `sha-<short commit>` |
| push of tag `vX.Y` | `vX.Y`, `sha-<short commit>`, `latest` |
| manual run on a branch | `<branch>`, `sha-<short commit>` |

Sites deploy a pinned `vX.Y` tag, set in chi-in-a-box.

## Releasing

1. Merge your change to `main`, and check that the Test run on `main` passed.
2. Tag the release and push the tag:
   ```
   git tag v0.3
   git push origin v0.3
   ```
3. Check that the Build image run for the tag passed, and that the new tag appears on the [package page](https://github.com/orgs/ChameleonCloud/packages/container/package/chameleon_site_tools).
4. In [chi-in-a-box](https://github.com/ChameleonCloud/chi-in-a-box), set `chameleon_site_tools_tag: "v0.3"` in `kolla/defaults.yml`. Open a PR against each release branch that sites deploy from (currently `chameleoncloud/2025.1`).
5. Deploy at each site, as described below.

## Deploying to a site

On the site's deploy host, pull the chi-in-a-box change that bumped the tag. Then run post-deploy, which deploys all four tools:
```
./cc-ansible --site /opt/site-config post-deploy
```
Or run only the playbook for the tool that changed:
```
./cc-ansible --site /opt/site-config --playbook playbooks/hammers.yml
./cc-ansible --site /opt/site-config --playbook playbooks/chameleon_image_tools.yml
./cc-ansible --site /opt/site-config --playbook playbooks/chameleon_periodic_inspector.yml
./cc-ansible --site /opt/site-config --playbook playbooks/chameleon_reference_repo.yml
```
The tools run from systemd timers on the control node, using `docker run`. The timers never pull a newer image, so a site keeps running the old version until one of these playbooks runs.

To try an unreleased build on a dev site, start a manual CI run on your branch. Then set `chameleon_site_tools_tag: "sha-<short commit>"` in that site's `defaults.yml` and run the playbook.
