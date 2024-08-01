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
        default="0.10.1",
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
    cfg.StrOpt(
        "csi_cinder_reclaim_policy",
        default="Retain",
        help=(
            "Policy for reclaiming dynamically created "
            "persistent volumes. Can be 'Retain' or 'Delete'."
        ),
    ),
    cfg.BoolOpt(
        "csi_cinder_allow_volume_expansion",
        default=True,
        help=(
            "Allows the users to resize the volume by "
            "editing the corresponding PVC object."
        ),
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
        "csi_cinder_fstype",
        default="ext4",
        help=("Filesystem type for persistent volumes."),
    ),
    cfg.StrOpt(
        "csi_cinder_volume_binding_mode",
        default="Immediate",
        help=(
            "The volumeBindingMode field controls when "
            "volume binding and dynamic provisioning should occur."
        ),
    ),
    cfg.StrOpt(
        "csi_cinder_availability_zone",
        default="nova",
        help=("The default availability zone to use for Cinder volumes."),
    ),
    cfg.StrOpt(
        "app_cred_interface_type",
        default="public",
        help=(
            "The value to use in the interface field of "
            "generated application credentials."
        ),
    ),
]

CONF = cfg.CONF
CONF.register_group(capi_helm_group)
CONF.register_opts(capi_helm_opts, group=capi_helm_group)
