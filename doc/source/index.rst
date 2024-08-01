..
      Copyright 2014-2015 OpenStack Foundation
      All Rights Reserved.

      Licensed under the Apache License, Version 2.0 (the "License"); you may
      not use this file except in compliance with the License. You may obtain
      a copy of the License at

          http://www.apache.org/licenses/LICENSE-2.0

      Unless required by applicable law or agreed to in writing, software
      distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
      WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
      License for the specific language governing permissions and limitations
      under the License.

=======================================================
Welcome to the Magnum CAPI Helm Driver's documentation!
=======================================================

Magnum CAPI Helm is an OpenStack Magnum driver which uses Helm to create
Kubernetes (k8s) clusters with Cluster API.

The driver uses a standard set of Helm charts to create the k8s resources
required to provision and manage a k8s cluster using Cluster API,
including various useful add-ons like a CNI and a monitoring stack.

These Helm charts currently live at https://github.com/azimuth-cloud/capi-helm-charts.

The Helm charts are intended to be a way to share a reference method to
create k8s clusters on OpenStack. The charts are not expected or intended to
be specific to Magnum. The hope is they can also be used by ArgoCD, Flux or
Azimuth to create clusters outside of Magnum if desired.

* **Free software:** under the `Apache license <http://www.apache.org/licenses/LICENSE-2.0>`_
* **Source:** https://opendev.org/openstack/magnum-capi-helm
* **Blueprints:** https://blueprints.launchpad.net/magnum
* **Bugs: (use magnum-capi-helm tag)** https://bugs.launchpad.net/magnum
* **Magnum Source:** https://opendev.org/openstack/magnum
* **Magnum REST Client:** https://opendev.org/openstack/python-magnumclient

Installation Guide
------------------

.. toctree::
   :maxdepth: 2

   Installation Guide <install/index>

Configuration Reference
-----------------------
.. toctree::
   :maxdepth: 2

   configuration/index

Contributor Guide
-----------------

.. toctree::
   :maxdepth: 2

   contributor/index

