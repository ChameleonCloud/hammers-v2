=======================
Bag o' Hammers
=======================

    *Percussive maintenance.*

Collection of various tools to keep things ship-shape. Not particularly bright tools, but good for a first-pass.


Currently, this "V2" attempt only contains the `periodic_inspector`, which loops over all registered ironic nodes, 
selects those with no instances, and that aren't in a reservation, and finally runs an ironic inspect if it's been too long since the last one.

Since this loops over all ironic nodes and modifies the state, it will eventually integrate the following other hammer's functionality:

1. resetting the state of ironic hosts from `error`, usually from neutron port timeouts, or ipmi failures
1. cleaning nodes to set bios boot configuration, or updating firmware
1. syncing metadata from referenceapi to blazar
1. checking if the ironic inspection data is unexpectly bad, such as if a disk is missing
1. other maintenance tasks that would require a "lock" on a given node (at least until we tell doni to take this over)