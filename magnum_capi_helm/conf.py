# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy
# of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from oslo_config import cfg

capi_helm_group = cfg.OptGroup(
    name="capi_helm", title="Helm Cluster API Driver configuration"
)

capi_helm_opts = [
    cfg.StrOpt(
        "kubeconfig_file",
        default="",
        help=(
            "Path to a kubeconfig file for a management cluster,"
            "for use in the Cluster API driver. "
            "Defaults to the environment variable KUBECONFIG, "
            "or if not defined ~/.kube/config"
        ),
    ),
    cfg.StrOpt(
        "namespace_prefix",
        default="magnum",
        help=(
            "Resources for each openstack cluster are created in a "
            "separate namespace within the CAPI Management cluster "
            "specified by the configuration: [capi_helm]/kubeconfig_file "
            "You should modify this prefix when two magnum deployments "
            "want to share a single CAPI management cluster."
        ),
    ),
    cfg.StrOpt(
        "helm_chart_repo",
        default="https://azimuth-cloud.github.io/capi-helm-charts",
        help=(
            "Reference to the helm chart repository for "
            "the cluster API driver. "
            "Note that if helm_chart_name starts with oci:// "
            "you will want this to set this to the empty string."
        ),
    ),
    cfg.StrOpt(
        "helm_chart_name",
        default="openstack-cluster",
        help=(
            "Name of the helm chart to use from the repo specified "
            "by the config: capi_driver.helm_chart_repo"
        ),
    ),
    cfg.StrOpt(
        "default_helm_chart_version",
        default="0.26.0",
        help=(
            "Version of the helm chart specified "
            "by the config: capi_driver.helm_chart_repo "
            "and capi_driver.helm_chart_name. "
            "A cluster label can override this."
        ),
    ),
    cfg.IntOpt(
        "minimum_flavor_ram",
        default=2048,
        help=("Minimum RAM for flavor used to " "create a Kubernetes node."),
    ),
    cfg.IntOpt(
        "minimum_flavor_vcpus",
        default=2,
        help=("Minimum VCPUS for flavor used to " "create a Kubernetes node."),
    ),
    cfg.StrOpt(
        "csi_cinder_default_volume_type",
        help=("Default StorageClass volume type for persistent volumes."),
    ),
    cfg.ListOpt(
        "csi_cinder_allowed_topologies",
        default=[],
        help=(
            "Select the Nodes where the application "
            "Pods may be scheduled based on Node labels."
        ),
    ),
    cfg.StrOpt(
        "app_cred_interface_type",
        default="public",
        help=(
            "The value to use in the interface field of "
            "generated application credentials."
        ),
    ),
    cfg.StrOpt(
        "api_resources",
        default={},
        help=(
            """

            Dictionary of cluster api resources to modify api_version
            and plural names in string format.


            Example::

                '{
                    "K8sControlPlane": {
                      "api_version": "controlplane.cluster.x-k8s.io/v1beta1",
                      "plural_name": "kubeadmcontrolplanes"
                    },
                    "OpenstackCluster": {
                      "api_version": "infrastructure.cluster.x-k8s.io/v1beta1",
                    },
                }'


            """
        ),
    ),
    cfg.ListOpt(
        "k8s_control_plane_resource_conditions",
        default=[
            "MachinesReady",
            "Ready",
            "EtcdClusterHealthy",
            "ControlPlaneComponentsHealthy",
        ],
        help=(
            "List of conditions to check for kubernetes control plane "
            "resource to consider as ready."
        ),
    ),
    cfg.IntOpt(
        "helm_lock_timeout",
        default=300,
        help=(
            "Maximum time, in seconds, to wait to acquire the per-release "
            "Helm lock before giving up, when a concurrent Helm operation "
            "is already in progress for the same cluster."
        ),
    ),
    cfg.IntOpt(
        "helm_timeout",
        default=300,
        help=(
            "Timeout, in seconds, passed to the Helm CLI for "
            "install/upgrade/uninstall operations. The per-release Helm "
            "lock's lease duration is derived from this value, so it "
            "always outlives the Helm command it protects."
        ),
    ),
]

capi_helm_cluster_labels_group = cfg.OptGroup(
    name="capi_helm_cluster_labels",
    title="Default values for cluster labels",
)

capi_helm_cluster_labels_opts = [
    # etcd
    cfg.IntOpt(
        "etcd_blockdevice_size",
        default=0,
        help=(
            "Size of the etcd block device in GB. "
            "0 means use the default ephemeral disk."
        ),
    ),
    cfg.StrOpt(
        "etcd_blockdevice_type",
        default="volume",
        choices=["volume", "local"],
        help=(
            "Storage type for the etcd block device. "
            "'volume' uses a Cinder volume; 'local' uses an ephemeral disk."
        ),
    ),
    cfg.StrOpt(
        "etcd_blockdevice_volume_type",
        default="",
        help="Cinder volume type for the etcd block device.",
    ),
    cfg.StrOpt(
        "etcd_blockdevice_volume_az",
        default="",
        help="Availability zone for the etcd Cinder volume.",
    ),
    cfg.IntOpt(
        "etcd_volume_size",
        default=0,
        deprecated_opts=[
            cfg.DeprecatedOpt("etcd_volume_size", group="capi_helm")
        ],
        help="Deprecated: use etcd_blockdevice_size instead.",
    ),
    cfg.StrOpt(
        "etcd_volume_type",
        default="",
        deprecated_opts=[
            cfg.DeprecatedOpt("etcd_volume_type", group="capi_helm")
        ],
        help="Deprecated: use etcd_blockdevice_volume_type instead.",
    ),
    # addons
    cfg.BoolOpt(
        "monitoring_enabled",
        default=False,
        help="Enable the monitoring addon on the cluster.",
    ),
    cfg.BoolOpt(
        "kube_dashboard_enabled",
        default=True,
        help="Enable the Kubernetes Dashboard addon.",
    ),
    cfg.BoolOpt(
        "auto_healing_enabled",
        default=True,
        help="Enable auto-healing for cluster nodes.",
    ),
    cfg.BoolOpt(
        "auto_scaling_enabled",
        default=False,
        help="Enable the cluster autoscaler.",
    ),
    cfg.IntOpt(
        "min_node_count",
        default=None,
        help=(
            "Minimum node count for autoscaling. "
            "Defaults to the nodegroup node_count when unset."
        ),
    ),
    cfg.IntOpt(
        "max_node_count",
        default=None,
        help=(
            "Maximum node count for autoscaling. "
            "Defaults to the nodegroup node_count when unset."
        ),
    ),
    # auth
    cfg.BoolOpt(
        "keystone_auth_enabled",
        default=False,
        help="Enable the Keystone authentication webhook.",
    ),
    # CSI Cinder — moved from [capi_helm], old location still accepted
    cfg.StrOpt(
        "csi_cinder_availability_zone",
        default="nova",
        deprecated_opts=[
            cfg.DeprecatedOpt(
                "csi_cinder_availability_zone", group="capi_helm"
            )
        ],
        help="Default availability zone for Cinder volumes.",
    ),
    cfg.StrOpt(
        "csi_cinder_reclaim_policy",
        default="Retain",
        choices=["Retain", "Delete"],
        deprecated_opts=[
            cfg.DeprecatedOpt("csi_cinder_reclaim_policy", group="capi_helm")
        ],
        help=(
            "Reclaim policy for dynamically provisioned persistent volumes. "
            "Can be 'Retain' or 'Delete'."
        ),
    ),
    cfg.StrOpt(
        "csi_cinder_volume_binding_mode",
        default="WaitForFirstConsumer",
        choices=["WaitForFirstConsumer", "Immediate"],
        deprecated_opts=[
            cfg.DeprecatedOpt(
                "csi_cinder_volume_binding_mode", group="capi_helm"
            )
        ],
        help=(
            "Controls when volume binding and dynamic provisioning occur. "
            "Can be 'WaitForFirstConsumer' or 'Immediate'."
        ),
    ),
    cfg.StrOpt(
        "csi_cinder_fstype",
        default="ext4",
        deprecated_opts=[
            cfg.DeprecatedOpt("csi_cinder_fstype", group="capi_helm")
        ],
        help="Filesystem type for persistent volumes.",
    ),
    cfg.BoolOpt(
        "csi_cinder_allow_volume_expansion",
        default=True,
        deprecated_opts=[
            cfg.DeprecatedOpt(
                "csi_cinder_allow_volume_expansion", group="capi_helm"
            )
        ],
        help="Allow users to resize persistent volumes by editing the PVC.",
    ),
    # Octavia load balancer
    cfg.StrOpt(
        "octavia_provider",
        default="amphora",
        help="Octavia load balancer provider (e.g. 'amphora' or 'ovn').",
    ),
    cfg.StrOpt(
        "octavia_lb_algorithm",
        default="",
        help=(
            "Load balancer algorithm. "
            "When unset, defaults to SOURCE_IP_PORT for the ovn provider "
            "and ROUND_ROBIN for all others."
        ),
    ),
    cfg.BoolOpt(
        "octavia_lb_healthcheck",
        default=True,
        help="Enable health checks on the load balancer.",
    ),
    # networking
    cfg.BoolOpt(
        "master_lb_floating_ip_enabled",
        default=True,
        help="Associate a floating IP with the API load balancer.",
    ),
    cfg.StrOpt(
        "fixed_subnet_cidr",
        default="10.0.0.0/24",
        help="CIDR for the node network when no fixed_subnet is specified.",
    ),
    cfg.StrOpt(
        "pod_network_cidr",
        default="10.100.0.0/16",
        help=(
            "CIDR for the pod network. Must not overlap with OpenStack "
            "provider or external network subnets."
        ),
    ),
    cfg.StrOpt(
        "service_network_cidr",
        default="172.24.0.0/13",
        help=(
            "CIDR for the Kubernetes service network. Must not overlap with "
            "OpenStack provider or external network subnets."
        ),
    ),
    cfg.StrOpt(
        "api_master_lb_allowed_cidrs",
        default="",
        help=(
            "Comma-separated list of CIDRs allowed to reach the API load "
            "balancer. Empty means all CIDRs are allowed."
        ),
    ),
    cfg.StrOpt(
        "extra_network_names",
        default="",
        deprecated_opts=[
            cfg.DeprecatedOpt(
                "extra_network_name", group="capi_helm_cluster_labels"
            )
        ],
        help=(
            "Space separated list of names of additional Neutron networks "
            "to attach to each node."
        ),
    ),
    # boot volume
    cfg.StrOpt(
        "boot_volume_type",
        default=None,
        help=(
            "Root volume type. "
            "Falls back to the cinder config option "
            "default_boot_volume_type when unset."
        ),
    ),
    cfg.IntOpt(
        "boot_volume_size",
        default=None,
        help=(
            "Root volume size in GB. "
            "Falls back to the cinder config option "
            "default_boot_volume_size when unset."
        ),
    ),
    # chart version (template-only — cannot be overridden per cluster)
    cfg.StrOpt(
        "capi_helm_chart_version",
        default="",
        help=(
            "Helm chart version to use. Can only be set via the cluster "
            "template label, not overridden per cluster. Falls back to "
            "capi_helm.default_helm_chart_version when unset."
        ),
    ),
]

CONF = cfg.CONF
CONF.register_group(capi_helm_group)
CONF.register_opts(capi_helm_opts, group=capi_helm_group)
CONF.register_group(capi_helm_cluster_labels_group)
CONF.register_opts(
    capi_helm_cluster_labels_opts, group=capi_helm_cluster_labels_group
)


def list_capi_opts():
    return [(capi_helm_group, [o]) for o in capi_helm_opts]


def list_capi_cluster_label_opts():
    return [
        (capi_helm_cluster_labels_group, [o])
        for o in capi_helm_cluster_labels_opts
    ]
