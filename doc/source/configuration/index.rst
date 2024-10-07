===================
Configuration Guide
===================

Features
========

The driver currently supports create, delete and upgrade operations as well
as updates to node groups and their sizes.

The Kubernetes versions against which the CAPI Helm charts are currently being tested
can be found `here <https://github.com/azimuth-cloud/capi-helm-charts/blob/main/.github/workflows/ensure-capi-images.yaml#L9>`_.

The driver respects the following cluster and template properties:

* image_id
* keypair
* fixed_network, fixed_subnet (if missing, a new one is created)
* external_network_id
* dns_nameserver

The driver supports the following labels:

* monitoring_enabled: default is off, change to "true" to enable
* kube_dashboard_enabled: default is on, change to "false" to disable
* octavia_provider: default is "amphora", "ovn" is also an option
* fixed_subnet_cidr: default is "10.0.0.0/24"
* extra_network_name: default is "", which can be useful if using
  Manila with the CephFS Native driver.

**TODO: Add more recently supported labels here.**

Currently, all clusters use the Calico CNI. While Cilium is also supported
in the Helm charts, it is not currently regularly tested.

We have found that cluster upgrades with ClusterAPI don't work well without
using a load balancer, even with a single node control plane, so we currently
ignore the "master-lb-enabled" flag.
