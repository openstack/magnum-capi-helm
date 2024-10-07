==================
Installation Guide
==================

For a Kayobe-based deployment, you can follow
`this <https://stackhpc-kayobe-config.readthedocs.io/en/stackhpc-2023.1/configuration/magnum-capi.html>`__ guide.
The relevant sub-sections of the same guide can also be adapted for
Kolla-Ansible-based deployments.

If you install this Python package within your Magnum virtual environment,
it should be picked up by Magnum::

  git clone https://opendev.org/openstack/magnum-capi-helm.git
  cd magnum-capi-helm
  pip install -e .

We currently run the unit tests against the 2023.1 version of Magnum.

The driver requires access to a Cluster API management cluster.
For more information, please see:
https://cluster-api.sigs.k8s.io/user/quick-start

To access the above Cluster API management cluster, you need to add Magnum
configuration to tell the driver where the management cluster's kubeconfig
file lives::

  [capi_helm]
  kubeconfig_file = /etc/magnum/kubeconfig

Once the driver installation is complete, to create a cluster you
first need an image that has been built to include Kubernetes.
There are community-maintained packer build pipelines here:
https://image-builder.sigs.k8s.io/capi/capi.html

Alternatively, you can grab pre-built images from StackHPC's
`Azimuth image releases <https://github.com/stackhpc/azimuth-images/releases/latest>`__.
Images are available in the `manifest.json` file and are named in the format
`ubuntu-<ubuntu release>-<kube version>-<date and time of build>`.

Since Magnum distinguishes which driver to use based on the properties
of the images used in the cluster template, the above image needs to
have the correct os-distro property set when uploaded to Glance. For example::

  curl -fo ubuntu.qcow 'https://object.arcus.openstack.hpc.cam.ac.uk/azimuth-images/ubuntu-jammy-kube-v1.28.3-231030-1102.qcow2?AWSAccessKeyId=c5bd0fa15bae4e08b305a52aac97c3a6&Expires=1730200795&Signature=gs9Fk7y06cpViQHP04TmHDtmkWE%3D'
  openstack image create ubuntu-jammy-kube-v1.28.3 \
    --file ubuntu.qcow2  \
    --disk-format qcow2 \
    --container-format bare \
    --public
  openstack image set ubuntu-jammy-kube-v1.28.3 --os-distro ubuntu --os-version 22.04

After uploading a suitable image, you can now create a Magnum cluster template
and then a cluster based on this template::

  openstack coe cluster template create new_driver \
    --coe kubernetes \
    --label octavia_provider=ovn \
    --image $(openstack image show ubuntu-jammy-kube-v1.28.3 -c id -f value) \
    --external-network public \
    --master-flavor ds2G20 \
    --flavor ds2G20 \
    --public \
    --master-lb-enabled

  openstack coe cluster create test-cluster \
    --cluster-template new_driver \
    --master-count 1 \
    --node-count 2
  openstack coe cluster list


Once the cluster has been created, you can get the cluster's kubeconfig file
and (optionally) run Sonoboy to test the created cluster::

  openstack coe cluster config test-cluster
  export KUBECONFIG=~/config
  kubectl get nodes
  sonobuoy run --mode quick --wait
