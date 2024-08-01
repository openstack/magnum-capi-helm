#!/bin/bash
#
# These instructions assume an Ubuntu-based host or VM for running devstack.
# Please note that if you are running this in a VM, it is vitally important
# that the underlying hardware have nested virtualization enabled or you will
# experience very poor amphora performance.
#
# Heavily based on:
# https://opendev.org/openstack/octavia/src/branch/master/devstack/contrib/new-octavia-devstack.sh

set -ex

# Set up the packages we need. Ubuntu package manager is assumed.
sudo apt-get update
sudo apt-get install git vim apparmor apparmor-utils jq -y

# Clone the devstack repo
sudo mkdir -p /opt/stack
if [ ! -f /opt/stack/stack.sh ]; then
    sudo chown -R ${USER}. /opt/stack
    git clone https://git.openstack.org/openstack-dev/devstack /opt/stack
fi

default_interface=$(ip route show default | awk 'NR==1 {print $5}')

HOSTNAME=$(ip addr show "$default_interface" | grep -oP '(?<=inet\s)\d+(\.\d+){3}')

cat <<EOF > /opt/stack/local.conf
[[local|localrc]]

HOST_IP=$(echo $HOSTNAME)

DATABASE_PASSWORD=secretdatabase
RABBIT_PASSWORD=secretrabbit
ADMIN_PASSWORD=secretadmin
SERVICE_PASSWORD=secretservice
SERVICE_TOKEN=111222333444

# Keystone config
KEYSTONE_ADMIN_ENDPOINT=true

# Glance config
GLANCE_LIMIT_IMAGE_SIZE_TOTAL=20000

# Logging
# -------

# By default ``stack.sh`` output only goes to the terminal where it runs.  It can
# be configured to additionally log to a file by setting ``LOGFILE`` to the full
# path of the destination log file.  A timestamp will be appended to the given name.
LOGFILE=$DEST/logs/stack.sh.log

# Old log files are automatically removed after 7 days to keep things neat.  Change
# the number of days by setting ``LOGDAYS``.
LOGDAYS=2

# Nova logs will be colorized if ``SYSLOG`` is not set; turn this off by setting
# ``LOG_COLOR`` false.
#LOG_COLOR=False

# Enable OVN
Q_AGENT=ovn
Q_ML2_PLUGIN_MECHANISM_DRIVERS=ovn,logger
Q_ML2_PLUGIN_TYPE_DRIVERS=local,flat,vlan,geneve
Q_ML2_TENANT_NETWORK_TYPE="geneve"

# Enable OVN services
enable_service ovn-northd
enable_service ovn-controller
enable_service q-ovn-metadata-agent

# Use Neutron
enable_service q-svc

# Disable Neutron agents not used with OVN.
# disable_service q-agt
# disable_service q-l3
# disable_service q-dhcp
# disable_service q-meta

# Enable services, these services depend on neutron plugin.
enable_plugin neutron https://opendev.org/openstack/neutron
enable_service q-trunk
enable_service q-dns
#enable_service q-qos
FIXED_RANGE=10.1.0.0/24

# Enable octavia tempest plugin tests
enable_plugin octavia-tempest-plugin https://opendev.org/openstack/octavia-tempest-plugin

# Horizon config
disable_service horizon

# Cinder (OpenStack Block Storage) is disabled by default to speed up
# DevStack a bit. You may enable it here if you would like to use it.
enable_service cinder c-sch c-api c-vol

# A UUID to uniquely identify this system.  If one is not specified, a random
# one will be generated and saved in the file 'ovn-uuid' for re-use in future
# DevStack runs.
#OVN_UUID=

# If using the OVN native layer-3 service, choose a router scheduler to
# manage the distribution of router gateways on hypervisors/chassis.
# Default value is leastloaded.
#OVN_L3_SCHEDULER=leastloaded

# The DevStack plugin defaults to using the ovn branch from the official ovs
# repo.  You can optionally use a different one.  For example, you may want to
# use the latest patches in blp's ovn branch (and see OVN_BUILD_FROM_SOURCE):
#OVN_REPO=https://github.com/blp/ovs-reviews.git
#OVN_BRANCH=ovn

# NOTE: When specifying the branch, as shown above, you must also enable this!
# By default, OVN will be installed from packages. In order to build OVN from
# source, set OVN_BUILD_FROM_SOURCE=True
#OVN_BUILD_FROM_SOURCE=False

# If the admin wants to enable this chassis to host gateway routers for
# external connectivity, then set ENABLE_CHASSIS_AS_GW to True.
# Then devstack will set ovn-cms-options with enable-chassis-as-gw
# in Open_vSwitch table's external_ids column.
# If this option is not set on any chassis, all the of them with bridge
# mappings configured will be eligible to host a gateway.
#ENABLE_CHASSIS_AS_GW=True

# If you wish to use the provider network for public access to the cloud,
# set the following
#Q_USE_PROVIDERNET_FOR_PUBLIC=True

# Create public bridge
#OVN_L3_CREATE_PUBLIC_NETWORK=True

# This needs to be equalized with Neutron devstack
PUBLIC_NETWORK_GATEWAY="172.24.4.1"

# Nova config
LIBVIRT_TYPE=kvm

# Octavia configuration
OCTAVIA_NODE="api"
DISABLE_AMP_IMAGE_BUILD=True
enable_plugin barbican https://opendev.org/openstack/barbican
enable_plugin octavia https://opendev.org/openstack/octavia
enable_plugin octavia-dashboard https://opendev.org/openstack/octavia-dashboard
LIBS_FROM_GIT+=python-octaviaclient
enable_service octavia
enable_service o-api
enable_service o-hk
enable_service o-da
enable_service o-cw
enable_service o-hm

# OVN octavia provider plugin
enable_plugin ovn-octavia-provider https://opendev.org/openstack/ovn-octavia-provider

# Magnum
enable_plugin magnum https://opendev.org/openstack/magnum

[[post-config|$NOVA_CONF]]
[scheduler]
discover_hosts_in_cells_interval = 2
EOF

# Fix permissions on current tty so screens can attach
sudo chmod go+rw `tty`

# Stack that stack!
/opt/stack/stack.sh

#
# Install this checkout and restart the Magnum services
#
SELF_PATH="$(realpath "${BASH_SOURCE[0]:-${(%):-%x}}")"
REPO_PATH="$(dirname "$(dirname "$(dirname "$SELF_PATH")")")"
python3 -m pip install -e "$REPO_PATH"
sudo systemctl restart devstack@magnum-api devstack@magnum-cond

new_path="/home/ubuntu/.local/bin"

source ~/.bashrc

# Check if the path is already in the PATH variable
if [[ ":$PATH:" != *":$new_path:"* ]]; then
  # If it's not in the PATH, add it
  echo 'export PATH="$PATH:'"$new_path"'"' >> ~/.bashrc
fi

# Get latest capi-helm-charts tag
LATEST_TAG=$(curl -fsL https://api.github.com/repos/azimuth-cloud/capi-helm-charts/tags | jq -r '.[0].name')
# Curl the dependencies URL
DEPENDENCIES_JSON=$(curl -fsL https://github.com/azimuth-cloud/capi-helm-charts/releases/download/$LATEST_TAG/dependencies.json)

# Parse JSON into bash variables
ADDON_PROVIDER=$(echo $DEPENDENCIES_JSON | jq -r '.["addon-provider"]')
AZIMUTH_IMAGES_TAG=$(echo $DEPENDENCIES_JSON | jq -r '.["azimuth-images"]')
CLUSTER_API=$(echo $DEPENDENCIES_JSON | jq -r '.["cluster-api"]')
CLUSTER_API_JANITOR_OPENSTACK=$(echo $DEPENDENCIES_JSON | jq -r '.["cluster-api-janitor-openstack"]')
CLUSTER_API_PROVIDER_OPENSTACK=$(echo $DEPENDENCIES_JSON | jq -r '.["cluster-api-provider-openstack"]')
CERT_MANAGER=$(echo $DEPENDENCIES_JSON | jq -r '.["cert-manager"]')
HELM=$(echo $DEPENDENCIES_JSON | jq -r '.["helm"]')
SONOBUOY=$(echo $DEPENDENCIES_JSON | jq -r '.["sonobuoy"]')

# # Install `kubectl` CLI
curl -fsLo /tmp/kubectl "https://dl.k8s.io/release/$(curl -fsL https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl"
sudo install -o root -g root -m 0755 /tmp/kubectl /usr/local/bin/kubectl

# Install k3s
curl -fsL https://get.k3s.io | sudo bash -s - --disable traefik

# copy kubeconfig file into standard location
mkdir -p $HOME/.kube
sudo cp /etc/rancher/k3s/k3s.yaml $HOME/.kube/config
sudo chown $USER $HOME/.kube/config

# Install helm
curl -fsL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash

# Install cert manager
{
    helm upgrade cert-manager cert-manager \
      --install \
      --namespace cert-manager \
      --create-namespace \
      --repo https://charts.jetstack.io \
      --version $CERT_MANAGER \
      --set installCRDs=true \
      --wait \
      --timeout 10m
} || {
    kubectl -n cert-manager get pods |  awk '$1 && $1!="NAME" { print $1 }' | xargs -n1 kubectl -n cert-manager logs
    exit
}

# Install clusterctl
curl -L https://github.com/kubernetes-sigs/cluster-api/releases/download/$CLUSTER_API/clusterctl-linux-amd64 -o clusterctl
sudo install -o root -g root -m 0755 clusterctl /usr/local/bin/clusterctl

# Install Cluster API resources
# using the matching tested values here:
# https://github.com/azimuth-cloud/capi-helm-charts/blob/main/dependencies.json
clusterctl init \
    --core cluster-api:$CLUSTER_API \
    --bootstrap kubeadm:$CLUSTER_API \
    --control-plane kubeadm:$CLUSTER_API \
    --infrastructure openstack:$CLUSTER_API_PROVIDER_OPENSTACK

# Install addon manager
helm upgrade cluster-api-addon-provider cluster-api-addon-provider \
  --install \
  --repo https://azimuth-cloud.github.io/cluster-api-addon-provider \
  --version $ADDON_PROVIDER \
  --namespace capi-addon-system \
  --create-namespace \
  --wait \
  --timeout 10m

# Install janitor
helm upgrade cluster-api-janitor-openstack cluster-api-janitor-openstack \
  --install \
  --repo https://azimuth-cloud.github.io/cluster-api-janitor-openstack \
  --version $CLUSTER_API_JANITOR_OPENSTACK \
  --namespace capi-janitor-system \
  --create-namespace \
  --wait \
  --timeout 10m

pip install python-magnumclient

# Configure OpenStack auth
source /opt/stack/openrc admin admin

# Create a flavor that is *just* big enough for Kubernetes
openstack flavor create ds2G20 --ram 2048 --disk 20 --id d5 --vcpus 2 --public

# Curl the manifest
AZIMUTH_IMAGES=$(curl -fsL "https://github.com/azimuth-cloud/azimuth-images/releases/download/$AZIMUTH_IMAGES_TAG/manifest.json")

# Get the keys of the Kubernetes images
K8S_IMAGE_KEYS="$(echo "$AZIMUTH_IMAGES" | jq -r '. | with_entries(select(.value | has("kubernetes_version"))) | keys | sort | .[]')"

# For each Kubernetes image, upload the image and create a corresponding COE template
for K8S_IMAGE_KEY in $K8S_IMAGE_KEYS; do
    K8S_IMAGE_NAME="$(echo "$AZIMUTH_IMAGES" | jq -r ".[\"$K8S_IMAGE_KEY\"].name")"
    K8S_IMAGE_URL="$(echo "$AZIMUTH_IMAGES" | jq -r ".[\"$K8S_IMAGE_KEY\"].url")"
    K8S_IMAGE_VERSION="$(echo "$AZIMUTH_IMAGES" | jq -r ".[\"$K8S_IMAGE_KEY\"].kubernetes_version")"

    # Download the image and upload it to Glance
    curl -fsSLo "$K8S_IMAGE_NAME.qcow2" "$K8S_IMAGE_URL"
    openstack image create "$K8S_IMAGE_NAME" \
      --file "$K8S_IMAGE_NAME.qcow2" \
      --disk-format qcow2 \
      --container-format bare \
      --public
    rm "$K8S_IMAGE_NAME.qcow2"
    openstack image set "$K8S_IMAGE_NAME" --os-distro ubuntu --os-version 22.04
    openstack image set "$K8S_IMAGE_NAME" --property kube_version="$K8S_IMAGE_VERSION"
    K8S_IMAGE_ID=$(openstack image show $K8S_IMAGE_NAME -c id -f value)

    # Create a COE template for the image
    openstack coe cluster template create "k8s-${K8S_IMAGE_VERSION//./-}" \
      --coe kubernetes \
      --image "$K8S_IMAGE_ID" \
      --labels \
capi_helm_chart_version="$LATEST_TAG",\
octavia_provider=ovn,\
monitoring_enabled=true,\
kube_dashboard_enabled=true \
      --external-network public \
      --master-flavor ds2G20 \
      --flavor ds2G20 \
      --public \
      --master-lb-enabled
done

# You can test it like this:
#  openstack coe cluster create devstacktest \
#    --cluster-template k8s-v1-28-5 \
#    --master-count 1 \
#    --node-count 2
#  openstack coe cluster list
#  openstack coe cluster config devstacktest
