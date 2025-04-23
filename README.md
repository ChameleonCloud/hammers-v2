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

Instally optional dependencies if desired:
```
pip install '.[dev]'
```

# To Dos

- Add tests for image deployer
