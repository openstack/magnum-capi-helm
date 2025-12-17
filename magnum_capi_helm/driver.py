# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.
import enum
import ipaddress
import re

import requests
import yaml

from magnum.api import utils as api_utils
from magnum.common import clients
from magnum.common import exception
from magnum.common import neutron
from magnum.common import short_id
from magnum.drivers.common import driver
from magnum.objects import fields
from oslo_log import log as logging
from oslo_utils import strutils
from oslo_utils import uuidutils

from magnum_capi_helm.common import app_creds
from magnum_capi_helm.common import ca_certificates
from magnum_capi_helm.common import capi_monitor
from magnum_capi_helm import conf
from magnum_capi_helm import driver_utils
from magnum_capi_helm import helm
from magnum_capi_helm import kubernetes

LOG = logging.getLogger(__name__)
CONF = conf.CONF
NODE_GROUP_ROLE_CONTROLLER = "master"
SCALE_TO_ZERO_MIN_CHART_VERSION = (0, 26, 0)


def _gather_label_names():
    regular_label_names = set(
        opt.name for opt in conf.capi_helm_cluster_labels_opts
    )
    deprecated_label_names = []
    for opt in conf.capi_helm_cluster_labels_opts:
        for dep_opt in opt.deprecated_opts:
            deprecated_label_names.append(dep_opt.name)
    return frozenset(regular_label_names.union(set(deprecated_label_names)))


_REGISTERED_LABEL_NAMES = _gather_label_names()


class NodeGroupState(enum.Enum):
    NOT_PRESENT = 1
    PENDING = 2
    READY = 3
    FAILED = 4


class Driver(driver.Driver):
    needs_trust = False

    def __init__(self):
        self._helm_client = helm.Client()
        self.__k8s_client = None

    @property
    def _k8s_client(self):
        if not self.__k8s_client:
            self.__k8s_client = kubernetes.Client.load()
        return self.__k8s_client

    @property
    def provides(self):
        return [
            {
                "server_type": "vm",
                # NOTE(johngarbutt) we don't depend on a specific OS,
                # we depend on kubeadm images with cloud-init
                "os": "ubuntu",
                "coe": "kubernetes",
            },
            {
                "server_type": "vm",
                "os": "flatcar",
                "coe": "kubernetes",
            },
        ]

    def validate_master_resize(self, node_count):
        # This driver supports resizing to the same values
        # as initial create, so re-use the Base class validation.
        return self.validate_master_size(node_count)

    def _update_control_plane_nodegroup_status(self, cluster, nodegroup):
        # The status of the master nodegroup is determined by the Cluster API
        # control plane object
        kcp = self._k8s_client.get_k8s_control_plane(
            driver_utils.get_k8s_resource_name(cluster, "control-plane"),
            driver_utils.cluster_namespace(cluster),
        )

        ng_state = NodeGroupState.NOT_PRESENT
        if kcp:
            ng_state = NodeGroupState.PENDING

        kcp_spec = kcp.get("spec", {}) if kcp else {}
        kcp_status = kcp.get("status", {}) if kcp else {}

        # The control plane object is what controls the Kubernetes version
        # If it is known, report it
        kube_version = kcp_status.get("version", kcp_spec.get("version"))
        if cluster.coe_version != kube_version:
            cluster.coe_version = kube_version
            cluster.save()

        kcp_true_conditions = {
            cond["type"]
            for cond in kcp_status.get("conditions", [])
            if cond["status"] == "True"
        }
        kcp_ready = all(
            cond in kcp_true_conditions
            for cond in CONF.capi_helm.k8s_control_plane_resource_conditions
        )
        target_replicas = kcp_spec.get("replicas")
        current_replicas = kcp_status.get("replicas")
        updated_replicas = kcp_status.get("updatedReplicas")
        ready_replicas = kcp_status.get("readyReplicas")
        if (
            kcp_ready
            and target_replicas == current_replicas
            and current_replicas == updated_replicas
            and updated_replicas == ready_replicas
        ):
            ng_state = NodeGroupState.READY

        # TODO(mkjpryor) Work out a way to determine FAILED state
        return self._update_nodegroup_status(cluster, nodegroup, ng_state)

    def _update_worker_nodegroup_status(self, cluster, nodegroup):
        # The status of a worker nodegroup is determined by the corresponding
        # Cluster API machine deployment
        md = self._k8s_client.get_machine_deployment(
            driver_utils.get_k8s_resource_name(cluster, nodegroup.name),
            driver_utils.cluster_namespace(cluster),
        )

        ng_state = NodeGroupState.NOT_PRESENT
        if md:
            ng_state = NodeGroupState.PENDING

        # When a machine deployment is deleted, it disappears straight
        # away even when there are still machines belonging to it that
        # are deleting
        # In that case, we want to keep the nodegroup as DELETE_IN_PROGRESS
        # until all the machines for the node group are gone
        if (
            not md
            and nodegroup.status.startswith("DELETE_")
            and self._nodegroup_machines_exist(cluster, nodegroup)
        ):
            LOG.debug(
                f"Node group {nodegroup.name} "
                f"for cluster {cluster.uuid} "
                "machine deployment gone, but machines still found."
            )
            ng_state = NodeGroupState.PENDING

        md_status = md.get("status", {}) if md else {}
        md_phase = md_status.get("phase")
        if md_phase:
            if md_phase == "Running":
                ng_state = NodeGroupState.READY
            elif md_phase in {"Failed", "Unknown"}:
                ng_state = NodeGroupState.FAILED

        return self._update_nodegroup_status(cluster, nodegroup, ng_state)

    def _update_nodegroup_status(self, cluster, nodegroup, ng_state):
        # For delete we are waiting for not present
        if nodegroup.status.startswith("DELETE_"):
            if ng_state == NodeGroupState.NOT_PRESENT:
                if not nodegroup.is_default:
                    # Conductor will delete default nodegroups
                    # when cluster is deleted, but non default
                    # node groups should be deleted here.
                    nodegroup.destroy()
                LOG.debug(
                    f"Node group deleted: {nodegroup.name} "
                    f"for cluster {cluster.uuid} "
                    f"which is_default: {nodegroup.is_default}"
                )
                # signal the node group has been deleted
                return None

            LOG.debug(
                f"Node group not yet delete: {nodegroup.name} "
                f"for cluster {cluster.uuid}"
            )
            return nodegroup

        is_update_operation = nodegroup.status.startswith("UPDATE_")
        is_create_operation = nodegroup.status.startswith("CREATE_")
        if not is_update_operation and not is_create_operation:
            LOG.warning(
                f"Node group: {nodegroup.name} in unexpected "
                f"state: {nodegroup.status} in cluster {cluster.uuid}"
            )
        elif ng_state == NodeGroupState.READY:
            nodegroup.status = (
                fields.ClusterStatus.UPDATE_COMPLETE
                if is_update_operation
                else fields.ClusterStatus.CREATE_COMPLETE
            )
            LOG.debug(
                f"Node group ready: {nodegroup.name} "
                f"in cluster {cluster.uuid}"
            )
            nodegroup.save()

        elif ng_state == NodeGroupState.FAILED:
            nodegroup.status = (
                fields.ClusterStatus.UPDATE_FAILED
                if is_update_operation
                else fields.ClusterStatus.CREATE_FAILED
            )
            LOG.debug(
                f"Node group failed: {nodegroup.name} "
                f"in cluster {cluster.uuid}"
            )
            nodegroup.save()
        elif ng_state == NodeGroupState.NOT_PRESENT:
            LOG.debug(
                f"Node group not yet found: {nodegroup.name} "
                f"state:{nodegroup.status} in cluster {cluster.uuid}"
            )
        else:
            LOG.debug(
                f"Node group still pending: {nodegroup.name} "
                f"state:{nodegroup.status} in cluster {cluster.uuid}"
            )

        return nodegroup

    def _nodegroup_machines_exist(self, cluster, nodegroup):
        cluster_name = driver_utils.chart_release_name(cluster)
        nodegroup_name = driver_utils.sanitized_name(nodegroup.name)
        machines = self._k8s_client.get_all_machines_by_label(
            {
                "capi.stackhpc.com/cluster": cluster_name,
                "capi.stackhpc.com/component": "worker",
                "capi.stackhpc.com/node-group": nodegroup_name,
            },
            driver_utils.cluster_namespace(cluster),
        )
        return bool(machines)

    def _update_cluster_api_address(self, cluster, capi_cluster):
        # As soon as we know the API address, we should set it
        # This means users can access the API even if the create is
        # not complete, which could be useful for debugging failures,
        # e.g. with addons
        if not capi_cluster:
            # skip update if cluster not yet created
            return

        if cluster.status not in [
            fields.ClusterStatus.CREATE_IN_PROGRESS,
            fields.ClusterStatus.UPDATE_IN_PROGRESS,
        ]:
            # only update api-address when updating or creating
            return

        api_endpoint = capi_cluster["spec"].get("controlPlaneEndpoint")
        if api_endpoint:
            api_address = (
                f"https://{api_endpoint['host']}:{api_endpoint['port']}"
            )
            if cluster.api_address != api_address:
                cluster.api_address = api_address
                cluster.save()
                LOG.debug(f"Found api_address for {cluster.uuid}")

    def _update_status_updating(self, cluster, capi_cluster):
        # If the cluster is not yet ready then the create/update
        # is still in progress
        true_conditions = {
            cond["type"]
            for cond in capi_cluster.get("status", {}).get("conditions", [])
            if cond["status"] == "True"
        }
        for cond in ("InfrastructureReady", "ControlPlaneReady", "Ready"):
            if cond not in true_conditions:
                return

        is_update_operation = cluster.status.startswith("UPDATE_")

        # Check the status of the addons
        addons = self._k8s_client.get_addons_by_label(
            {
                "addons.stackhpc.com/cluster": driver_utils.chart_release_name(
                    cluster
                )
            },
            driver_utils.cluster_namespace(cluster),
        )
        for addon in addons:
            addon_phase = addon.get("status", {}).get("phase")
            if addon_phase and addon_phase in {"Failed", "Unknown"}:
                # If the addon is failed, mark the cluster as failed
                cluster.status = (
                    fields.ClusterStatus.UPDATE_FAILED
                    if is_update_operation
                    else fields.ClusterStatus.CREATE_FAILED
                )
                cluster.save()
                return
            elif addon_phase and addon_phase == "Deployed":
                # If the addon is deployed, move on to the next one
                continue
            else:
                # If there are any addons that are not deployed or failed,
                # wait for the next invocation to check again
                LOG.debug(
                    f"addon {addon['metadata']['name']} not yet deployed "
                    f"for {cluster.uuid}"
                )
                return

        # If we get this far, the cluster has completed successfully
        cluster.status = (
            fields.ClusterStatus.UPDATE_COMPLETE
            if is_update_operation
            else fields.ClusterStatus.CREATE_COMPLETE
        )
        cluster.save()

    def _update_status_deleting(self, context, cluster):
        # Fetch the current app cred ID before we delete all secrets
        app_cred_id = self._get_app_cred_id(cluster)

        namespace = driver_utils.cluster_namespace(cluster)

        # Once the Cluster API cluster is gone, we need to clean up
        # the secrets we created
        self._k8s_client.delete_all_secrets_by_label(
            "magnum.openstack.org/cluster-uuid",
            cluster.uuid,
            namespace,
        )

        cluster.status_reason = None

        # Clean up the app cred
        if app_cred_id:
            try:
                app_creds.delete_app_cred(cluster, app_cred_id)
            except app_creds.ApplicationCredentialError as e:
                # NOTE(northcottmt): We don't want this to be a failure
                # condition as it may prevent cleanup of broken clusters,
                # e.g. if cluster creation fails before the appcred is created
                # or cluster deletion fails after the appcred is deleted
                error_msg = (
                    "Failed to delete application credential for "
                    f"cluster {cluster.uuid}: {e}"
                )
                cluster.status_reason = error_msg
                LOG.warning(error_msg)

        # The Helm lock's lease is normally cleaned up when its holding
        # HelmLock releases it, but clean up here too in case one was
        # left behind (e.g. a conductor crashing mid-operation), so
        # leases don't accumulate in this (per-project) namespace. Skip
        # this if the lease is still actively held - it may belong to a
        # genuinely in-flight Helm operation for this cluster, and
        # deleting it would let a second operation acquire the lock
        # concurrently. Any such lease still self-heals via its own TTL.
        lease_name = driver_utils.helm_lock_lease_name(cluster)
        if not helm.is_release_locked(self._k8s_client, lease_name, namespace):
            try:
                self._k8s_client.delete_lease(lease_name, namespace)
            except requests.exceptions.HTTPError as exc:
                if exc.response.status_code != 404:
                    raise

        cluster.status = fields.ClusterStatus.DELETE_COMPLETE
        cluster.save()

    def _get_capi_cluster(self, cluster):
        release_name = driver_utils.chart_release_name(cluster)
        if release_name:
            return self._k8s_client.get_capi_cluster(
                release_name,
                driver_utils.cluster_namespace(cluster),
            )

    def _update_all_nodegroups_status(self, cluster):
        """Returns True if any node group still in progress."""
        nodegroups = []
        for nodegroup in cluster.nodegroups:
            if nodegroup.role == NODE_GROUP_ROLE_CONTROLLER:
                updated_nodegroup = (
                    self._update_control_plane_nodegroup_status(
                        cluster, nodegroup
                    )
                )
            else:
                updated_nodegroup = self._update_worker_nodegroup_status(
                    cluster, nodegroup
                )
            if updated_nodegroup:
                nodegroups.append(updated_nodegroup)

        # Return True if any are still in progress
        for nodegroup in nodegroups:
            if nodegroup.status.endswith("_IN_PROGRESS"):
                return True
        return False

    def update_cluster_status(self, context, cluster):
        # NOTE(mkjpryor)
        # Because Kubernetes operators are built around reconciliation loops,
        # Cluster API clusters don't really go into an error state
        # Hence we only currently handle transitioning from IN_PROGRESS
        # states to COMPLETE

        # TODO(mkjpryor) Add a timeout for create/update/delete

        if driver_utils.is_helm_locked(self._k8s_client, cluster):
            # A Helm update for this cluster is still waiting for, or
            # holding, the lock - the CAPI resources may not yet reflect
            # whatever is currently being applied, so don't trust their
            # state for status purposes this cycle.
            LOG.debug(
                "Helm lock currently held for %s; skipping status "
                "update until it is released",
                cluster.uuid,
            )
            return

        capi_cluster = self._get_capi_cluster(cluster)

        if capi_cluster:
            # Update the cluster API address if it is known
            # so users can get their coe credentials
            self._update_cluster_api_address(cluster, capi_cluster)

            # Update the nodegroups first
            # to ensure API never returns an inconsistent state
            nodegroups_in_progress = self._update_all_nodegroups_status(
                cluster
            )

        if cluster.status in {
            fields.ClusterStatus.CREATE_IN_PROGRESS,
            fields.ClusterStatus.UPDATE_IN_PROGRESS,
        }:
            LOG.debug("Checking on an update for %s", cluster.uuid)
            # If the cluster does not exist yet,
            # create is still in progress
            if not capi_cluster:
                LOG.debug(f"capi_cluster not yet created for {cluster.uuid}")
                return
            if nodegroups_in_progress:
                LOG.debug(f"Node groups are not all ready for {cluster.uuid}")
                return
            self._update_status_updating(cluster, capi_cluster)

        elif cluster.status == fields.ClusterStatus.DELETE_IN_PROGRESS:
            LOG.debug("Checking on a delete for %s", cluster.uuid)
            # If the Cluster API cluster still exists,
            # the delete is still in progress
            if capi_cluster:
                LOG.debug(f"capi_cluster still found for {cluster.uuid}")
                return
            self._update_status_deleting(context, cluster)

    def get_monitor(self, context, cluster):
        return capi_monitor.CAPIMonitor(context, cluster)

    def _k8s_resource_labels(self, cluster):
        # TODO(johngarbutt) need to check these are safe labels
        name = driver_utils.chart_release_name(cluster)
        return {
            "magnum.openstack.org/project-id": cluster.project_id[:63],
            "magnum.openstack.org/user-id": cluster.user_id[:63],
            "magnum.openstack.org/cluster-uuid": cluster.uuid[:63],
            "cluster.x-k8s.io/cluster-name": name,
        }

    def _create_appcred_secret(self, context, cluster):
        app_cred = app_creds.create_app_cred(context, cluster)
        string_data = app_creds.get_app_cred_string_data(context, app_cred)
        name = self._get_app_cred_secret_name(cluster)
        self._k8s_client.apply_secret(
            name,
            {
                "metadata": {"labels": self._k8s_resource_labels(cluster)},
                "stringData": string_data,
            },
            driver_utils.cluster_namespace(cluster),
        )
        return app_cred

    def _ensure_certificate_secrets(self, context, cluster):
        # Magnum creates CA certs for each of the Kubernetes components that
        # must be trusted by the cluster
        # In particular, this is required for "openstack coe cluster config"
        # to work, as that doesn't communicate with the driver and instead
        # relies on the correct CA being trusted by the cluster

        # Cluster API looks for specific named secrets for each of the CAs,
        # and generates them if they don't exist, so we create them here
        # with the correct certificates in
        for (
            name,
            data,
        ) in ca_certificates.get_certificate_string_data(
            context, cluster
        ).items():
            self._k8s_client.apply_secret(
                driver_utils.get_k8s_resource_name(cluster, name),
                {
                    "metadata": {"labels": self._k8s_resource_labels(cluster)},
                    "type": "cluster.x-k8s.io/secret",
                    "stringData": data,
                },
                driver_utils.cluster_namespace(cluster),
            )

    def _label(self, cluster, key, default):
        if key not in _REGISTERED_LABEL_NAMES:
            raise ValueError(
                f"Cluster label '{key}' is not registered in "
                "capi_helm_cluster_labels. Add it to conf.py first."
            )
        all_labels = helm.mergeconcat(
            cluster.cluster_template.labels, cluster.labels
        )
        if not all_labels:
            return default
        raw = all_labels.get(key, default)
        # NOTE(johngarbutt): filtering untrusted user input
        return re.sub(r"[^a-zA-Z0-9\.\-\/ _]+", "", raw)

    def _get_label_bool(self, cluster, label, default):
        cluster_label = self._label(cluster, label, "")
        return strutils.bool_from_string(cluster_label, default=default)

    def _get_label_int(self, cluster, label, default):
        cluster_label = self._label(cluster, label, "")
        if not cluster_label:
            return default
        try:
            return int(cluster_label)
        except ValueError:
            return default

    def _get_label_csv(self, cluster, label, default):
        """Return a cluster label as a filtered list split on commas."""
        if label not in _REGISTERED_LABEL_NAMES:
            raise ValueError(
                f"Cluster label '{label}' is not registered in "
                "capi_helm_cluster_labels. Add it to conf.py first."
            )
        all_labels = helm.mergeconcat(
            cluster.cluster_template.labels, cluster.labels
        )
        raw = (all_labels or {}).get(label, "") or default
        if not raw:
            return []
        return [
            re.sub(r"[^a-zA-Z0-9\.\-\/\:]+", "", entry.strip())
            for entry in str(raw).split(",")
            if entry.strip()
        ]

    def _get_chart_version(self, cluster):
        version = cluster.cluster_template.labels.get(
            "capi_helm_chart_version",
            CONF.capi_helm.default_helm_chart_version,
        )
        # NOTE(johngarbutt): filtering untrusted user input
        return re.sub(r"[^a-z0-9\.\-\+]+", "", version)

    def _parse_chart_version(self, version_str):
        """Return (major, minor, patch) tuple, ignore pre-release suffixes."""
        nums = []
        for part in re.split(r"[.\-]", version_str)[:3]:
            if part.isdigit():
                nums.append(int(part))
            else:
                break
        return tuple(nums)

    def _chart_supports_scale_to_zero(self, cluster):
        version = self._get_chart_version(cluster)
        return (
            self._parse_chart_version(version)
            >= SCALE_TO_ZERO_MIN_CHART_VERSION
        )

    def _get_kube_version(self, image):
        # The image should have a property containing the Kubernetes version.
        # image.get() uses dict.get() which bypasses the overridden __getitem__
        # in openstacksdk Resource objects (data lives in _body, not the dict).
        # Unknown properties are packed into image.properties by openstacksdk.
        kube_version = image.get("kube_version") or getattr(
            image, "properties", {}
        ).get("kube_version")
        if not kube_version:
            raise exception.MagnumException(
                message=f"Image {image.id} does not "
                "have a kube_version property."
            )
        raw = kube_version.lstrip("v")
        # TODO(johngarbutt) more validation required?
        return re.sub(r"[^0-9\.]+", "", raw)

    def _get_os_distro(self, image):
        # image.get() uses dict.get() which bypasses the overridden __getitem__
        # in openstacksdk Resource objects. os_distro is a declared Body field
        # in openstacksdk Image, so getattr() reaches it via the descriptor.
        os_distro = image.get("os_distro") or getattr(image, "os_distro", None)
        if not os_distro:
            raise exception.MagnumException(
                message=f"Image {image.id} does not "
                "have an os_distro property."
            )
        return re.sub(r"[^a-zA-Z0-9\.\-\/ ]+", "", os_distro)

    def _get_image_details(self, context, image_identifier):
        glance = clients.OpenStackClients(context).glance()
        if hasattr(glance.images, "get"):
            # glanceclient
            image = api_utils.get_openstack_resource(
                glance.images, image_identifier, "images"
            )
        else:
            # openstacksdk
            image = glance.find_image(image_identifier)
        return (
            image.id,
            self._get_kube_version(image),
            self._get_os_distro(image),
        )

    def _get_app_cred_secret_name(self, cluster):
        return driver_utils.get_k8s_resource_name(cluster, "cloud-credentials")

    def _get_app_cred_id(self, cluster):
        # determine the existing application credential secret
        secret_name = self._get_app_cred_secret_name(cluster)
        if not secret_name:
            return None
        secret_namespace = driver_utils.cluster_namespace(cluster)

        # fetch the existing secret and unpack the application credential ID
        try:
            clouds_dict_str = self._k8s_client.get_secret_value(
                secret_name, secret_namespace, "clouds.yaml"
            )
            if clouds_dict_str:
                clouds_dict = yaml.safe_load(clouds_dict_str)
                auth_dict = clouds_dict["clouds"]["openstack"]["auth"]
                return auth_dict["application_credential_id"]
            else:
                msg = "secret containing credential does not exist"
        except (KeyError, yaml.YAMLError) as e:
            msg = str(e)

        LOG.error(
            "Failed to fetch application credential for cluster "
            f"{cluster.uuid}: {msg}"
        )

    def _get_etcd_config(self, cluster):
        lconf = CONF.capi_helm_cluster_labels
        # Support new-style and legacy labels for volume size and type, with
        # new-style labels taking precedence
        etcd_size = self._get_label_int(
            cluster,
            "etcd_blockdevice_size",
            self._get_label_int(
                cluster,
                "etcd_volume_size",
                lconf.etcd_blockdevice_size,
            ),
        )
        if etcd_size > 0:
            etcd_block_device = {"size": etcd_size}
            # The block device type can be either local or volume
            etcd_bd_type = self._label(
                cluster,
                "etcd_blockdevice_type",
                lconf.etcd_blockdevice_type,
            )
            if etcd_bd_type == "local":
                etcd_block_device["type"] = "Local"
            else:
                etcd_block_device["type"] = "Volume"

                etcd_volume_type = self._label(
                    cluster,
                    "etcd_blockdevice_volume_type",
                    self._label(
                        cluster,
                        "etcd_volume_type",
                        lconf.etcd_blockdevice_volume_type,
                    ),
                )
                if etcd_volume_type:
                    etcd_block_device["volumeType"] = etcd_volume_type

                etcd_volume_az = self._label(
                    cluster,
                    "etcd_blockdevice_volume_az",
                    lconf.etcd_blockdevice_volume_az,
                )
                if etcd_volume_az:
                    etcd_block_device["availabilityZone"] = etcd_volume_az
            return {"blockDevice": etcd_block_device}
        else:
            return {}

    def _get_dns_nameservers(self, cluster):
        dns_nameserver = cluster.cluster_template.dns_nameserver
        if dns_nameserver:
            return strutils.split_by_commas(dns_nameserver)
        else:
            return None

    def _get_monitoring_enabled(self, cluster):
        return self._get_label_bool(
            cluster,
            "monitoring_enabled",
            CONF.capi_helm_cluster_labels.monitoring_enabled,
        )

    def _get_kube_dash_enabled(self, cluster):
        return self._get_label_bool(
            cluster,
            "kube_dashboard_enabled",
            CONF.capi_helm_cluster_labels.kube_dashboard_enabled,
        )

    def _get_autoheal_enabled(self, cluster):
        return self._get_label_bool(
            cluster,
            "auto_healing_enabled",
            CONF.capi_helm_cluster_labels.auto_healing_enabled,
        )

    def _get_autoscale_enabled(self, cluster):
        return self._get_label_bool(
            cluster,
            "auto_scaling_enabled",
            CONF.capi_helm_cluster_labels.auto_scaling_enabled,
        )

    def _get_autoscale_values(self, cluster, nodegroup):
        auto_scale = self._get_autoscale_enabled(cluster)
        min_nodes, max_nodes = self._validate_allowed_node_counts(
            cluster, nodegroup
        )
        auto_scale_args = {}
        if auto_scale and min_nodes != max_nodes:
            auto_scale_args["autoscale"] = "true"
            auto_scale_args["machineCountMin"] = min_nodes
            auto_scale_args["machineCountMax"] = max_nodes
        return auto_scale_args

    def _get_k8s_keystone_auth_enabled(self, cluster):
        return self._get_label_bool(
            cluster,
            "keystone_auth_enabled",
            CONF.capi_helm_cluster_labels.keystone_auth_enabled,
        )

    def _get_fixed_network_id(self, context, cluster):
        network = cluster.fixed_network
        if not network:
            return
        if network and uuidutils.is_uuid_like(network):
            return network
        else:
            return neutron.get_network(
                context, network, source="name", target="id", external=False
            )

    def _validate_cidr_no_overlap(self, context, cluster):
        """Raise MagnumException if pod/service CIDRs overlap."""
        pod_cidr_str = self._label(
            cluster, "pod_network_cidr", "10.100.0.0/16"
        )
        service_cidr_str = self._label(
            cluster, "service_network_cidr", "172.24.0.0/13"
        )

        try:
            pod_net = ipaddress.ip_network(pod_cidr_str, strict=False)
        except ValueError:
            raise exception.MagnumException(
                message=(
                    f"pod_network_cidr '{pod_cidr_str}' is not a valid CIDR."
                )
            )
        try:
            service_net = ipaddress.ip_network(service_cidr_str, strict=False)
        except ValueError:
            raise exception.MagnumException(
                message=(
                    f"service_network_cidr '{service_cidr_str}'"
                    " is not a valid CIDR."
                )
            )

        if pod_net.overlaps(service_net):
            raise exception.MagnumException(
                message=(
                    f"Pod CIDR {pod_cidr_str} overlaps with service CIDR "
                    f"{service_cidr_str}. Use the 'pod_network_cidr' and "
                    "'service_network_cidr' labels to set non-overlapping"
                    " CIDRs."
                )
            )

        nc = clients.OpenStackClients(context).neutron()
        subnets_to_check = []

        def _iter_subnets(nid):
            if hasattr(nc, "list_subnets"):
                # neutronclient (Magnum stable branches)
                for s in nc.list_subnets(network_id=nid).get("subnets", []):
                    yield s.get("cidr"), (s.get("name") or s.get("id", "?"))
            else:
                # openstacksdk (Magnum master)
                for s in nc.subnets(network_id=nid):
                    yield getattr(s, "cidr", None), (
                        getattr(s, "name", None) or getattr(s, "id", "?")
                    )

        network_id = self._get_fixed_network_id(context, cluster)
        if network_id:
            for cidr, name in _iter_subnets(network_id):
                if cidr:
                    subnets_to_check.append(
                        (cidr, f"fixed network subnet '{name}'")
                    )

        ext_net_id = neutron.get_external_network_id(
            context, cluster.cluster_template.external_network_id
        )
        if ext_net_id:
            for cidr, name in _iter_subnets(ext_net_id):
                if cidr:
                    subnets_to_check.append(
                        (cidr, f"external network subnet '{name}'")
                    )

        for cidr_str, label in subnets_to_check:
            try:
                net = ipaddress.ip_network(cidr_str, strict=False)
            except ValueError:
                continue
            if net.version != pod_net.version:
                continue

            if pod_net.overlaps(net):
                raise exception.MagnumException(
                    message=(
                        f"Pod CIDR {pod_cidr_str} overlaps with {label} "
                        f"({cidr_str}). Use the 'pod_network_cidr' label "
                        "to set a non-overlapping CIDR"
                        " (e.g. '10.100.0.0/16')."
                    )
                )
            if service_net.overlaps(net):
                raise exception.MagnumException(
                    message=(
                        f"Service CIDR {service_cidr_str} overlaps with"
                        f" {label} ({cidr_str}). Use the"
                        " 'service_network_cidr' label to set a"
                        " non-overlapping CIDR (e.g. '10.96.0.0/12')."
                    )
                )

    def _validate_allowed_flavor(self, context, requested_flavor):
        # Compare requested flavor with allowed for Kubernetes node
        nova = clients.OpenStackClients(context).nova()
        if hasattr(nova.flavors, "list"):
            # novaclient
            flavors = nova.flavors.list(
                min_ram=CONF.capi_helm.minimum_flavor_ram
            )
        else:
            # openstacksdk compute proxy
            flavors = nova.flavors(min_ram=CONF.capi_helm.minimum_flavor_ram)
        for flavor in flavors:
            vcpus = flavor.vcpus
            LOG.debug(
                f"Checking if {requested_flavor} matches "
                f"{flavor.id} or {flavor.name}"
            )
            if requested_flavor in [flavor.id, flavor.name]:
                if vcpus < CONF.capi_helm.minimum_flavor_vcpus:
                    raise exception.MagnumException(
                        message=f"Flavor {requested_flavor} does not "
                        f"have enough CPU to run Kubernetes. "
                        f"Minimum {CONF.capi_helm.minimum_flavor_vcpus} "
                        "vcpus required."
                    )
                return
        raise exception.MagnumException(
            message=f"Flavor {requested_flavor} does not "
            f"have enough RAM to run Kubernetes. "
            f"Minimum {CONF.capi_helm.minimum_flavor_ram} MB required."
        )

    def _is_default_worker_nodegroup(self, cluster, nodegroup):
        return cluster.default_ng_worker.id == nodegroup.id

    def _get_node_counts(self, cluster, nodegroup):

        # NOTE(scott): In CAPI MachineDeployment resources created by the
        # capi-helm-charts, the `replicas` field is omitted when autoscaling
        # is enabled (since we're relinquishing control over node count to
        # the autoscaler) so if a user creates a nodegroup where only
        # node_count is provided and min/max are not, we need to be careful
        # about setting the default min/max values to equal node_count.
        min_nodes = nodegroup.node_count
        max_nodes = nodegroup.node_count

        if nodegroup.min_node_count is not None:
            min_nodes = nodegroup.min_node_count
        if nodegroup.max_node_count is not None:
            max_nodes = nodegroup.max_node_count

        # If min/max node counts are not defined on the default
        # worker group then fall back to equivalent cluster labels
        if self._is_default_worker_nodegroup(cluster, nodegroup):
            # NOTE(scott): Magnum seems to set min_node_count = 1
            # on the default group so treat this as if it were None
            if nodegroup.min_node_count == 1:
                min_nodes = nodegroup.node_count

            # We still want to be able to override the default node
            # group values with labels for consistent behaviour with
            # Magnum Heat driver.
            conf_min = CONF.capi_helm_cluster_labels.min_node_count
            conf_max = CONF.capi_helm_cluster_labels.max_node_count
            min_nodes = self._get_label_int(
                cluster,
                "min_node_count",
                conf_min if conf_min is not None else min_nodes,
            )
            max_nodes = self._get_label_int(
                cluster,
                "max_node_count",
                conf_max if conf_max is not None else max_nodes,
            )

        return min_nodes, max_nodes

    def _validate_allowed_node_counts(self, cluster, nodegroup):
        min_nodes, max_nodes = self._get_node_counts(cluster, nodegroup)

        LOG.debug(
            f"Checking if node group {nodegroup.name} has valid "
            f"node count parameters (count, min, max) = "
            f"{(nodegroup.node_count, min_nodes, max_nodes)}"
        )

        if min_nodes is not None:
            if min_nodes < 0:
                raise exception.NodeGroupInvalidInput(
                    message="Min node count must be greater than "
                    "or equal to 0 for all node groups."
                )
            if min_nodes == 0 and not self._chart_supports_scale_to_zero(
                cluster
            ):
                raise exception.NodeGroupInvalidInput(
                    message=(
                        "Scale to zero requires capi-helm-charts >= 0.26.0. "
                        "Set the capi_helm_chart_version label or update "
                        "default_helm_chart_version in magnum.conf."
                    )
                )
            if max_nodes is not None and max_nodes < min_nodes:
                raise exception.NodeGroupInvalidInput(
                    message="Max node count must be greater than "
                    "or equal to min node count"
                )
            if min_nodes > nodegroup.node_count:
                raise exception.NodeGroupInvalidInput(
                    message="Min node count must be less than "
                    "or equal to current node count"
                )

        return min_nodes, max_nodes

    def _get_csi_cinder_availability_zone(self, cluster):
        return self._label(
            cluster,
            "csi_cinder_availability_zone",
            CONF.capi_helm_cluster_labels.csi_cinder_availability_zone,
        )

    def _get_csi_cinder_reclaim_policy(self, cluster):
        return self._label(
            cluster,
            "csi_cinder_reclaim_policy",
            CONF.capi_helm_cluster_labels.csi_cinder_reclaim_policy,
        )

    def _get_csi_cinder_volume_binding_mode(self, cluster):
        return self._label(
            cluster,
            "csi_cinder_volume_binding_mode",
            CONF.capi_helm_cluster_labels.csi_cinder_volume_binding_mode,
        )

    def _get_csi_cinder_fstype(self, cluster):
        return self._label(
            cluster,
            "csi_cinder_fstype",
            CONF.capi_helm_cluster_labels.csi_cinder_fstype,
        )

    def _get_csi_cinder_allow_volume_expansion(self, cluster):
        return self._get_label_bool(
            cluster,
            "csi_cinder_allow_volume_expansion",
            CONF.capi_helm_cluster_labels.csi_cinder_allow_volume_expansion,
        )

    def _get_octavia_provider(self, cluster):
        return self._label(
            cluster,
            "octavia_provider",
            CONF.capi_helm_cluster_labels.octavia_provider,
        )

    def _get_cni_type(self, cluster):
        network_driver = cluster.cluster_template.network_driver
        if not network_driver:
            return None
        # NOTE: filtering untrusted user input
        return re.sub(r"[^a-zA-Z0-9\.\-_]+", "", network_driver)

    def _get_octavia_lb_algorithm(self, cluster):
        provider = self._get_octavia_provider(cluster)
        conf_default = CONF.capi_helm_cluster_labels.octavia_lb_algorithm
        dynamic_default = (
            "SOURCE_IP_PORT" if provider.lower() == "ovn" else "ROUND_ROBIN"
        )
        return self._label(
            cluster,
            "octavia_lb_algorithm",
            conf_default or dynamic_default,
        )

    def _get_allowed_cidrs(self, cluster):
        cidrs = self._get_label_csv(
            cluster,
            "api_master_lb_allowed_cidrs",
            CONF.capi_helm_cluster_labels.api_master_lb_allowed_cidrs,
        )
        LOG.debug(f"CIDR list {cidrs}")
        return cidrs or False

    def _storageclass_definitions(self, context, cluster):
        """Query cinder API to retrieve list of available volume types.

        @return dict(dict,list(dict)) containing storage classes
        """
        LOG.debug("Retrieve volume types from cinder for StorageClasses.")
        availability_zone = self._get_csi_cinder_availability_zone(cluster)
        c_client = clients.OpenStackClients(context).cinder()
        if hasattr(c_client, "volume_types"):
            # cinderclient
            volume_types = [i.name for i in c_client.volume_types.list()]
        else:
            # openstacksdk block storage proxy exposes types(),
            # not volume_types
            volume_types = [i.name for i in c_client.types()]
        # Use the default volume type if defined. Otherwise use the first
        # type returned by cinder.
        default_volume_type = CONF.capi_helm.csi_cinder_default_volume_type
        LOG.debug(
            f"Default volume type: {default_volume_type}"
            f" Volume types: {volume_types}"
        )
        if not default_volume_type:
            default_volume_type = volume_types[0]
            LOG.warning(
                f"Default volume type not defined."
                f" Using {default_volume_type}."
            )
        elif default_volume_type not in volume_types:
            # If default does not exist throw an error.
            raise exception.MagnumException(
                message=f"{default_volume_type} is not a"
                " valid Cinder volume type."
            )
        default_storage_class = {}
        additional_storage_classes = []
        allow_expansion = self._get_csi_cinder_allow_volume_expansion(cluster)
        reclaim_policy = self._get_csi_cinder_reclaim_policy(cluster)
        binding_mode = self._get_csi_cinder_volume_binding_mode(cluster)
        allowed_topologies = CONF.capi_helm.csi_cinder_allowed_topologies
        fstype = self._get_csi_cinder_fstype(cluster)

        for volume_type in volume_types:
            storage_class = {
                "name": driver_utils.sanitized_name(volume_type),
                "reclaimPolicy": reclaim_policy,
                "allowVolumeExpansion": allow_expansion,
                "volumeBindingMode": binding_mode,
                "availabilityZone": availability_zone,
                "volumeType": volume_type,
                "allowedTopologies": allowed_topologies,
                "fstype": fstype,
                "enabled": True,
            }
            if volume_type == default_volume_type:
                default_storage_class = storage_class
            else:
                additional_storage_classes.append(storage_class)
        return dict(
            defaultStorageClass=default_storage_class,
            additionalStorageClasses=additional_storage_classes,
        )

    def _process_node_groups(self, cluster, nodegroups):
        nodegroup_set = []
        for ng in nodegroups:
            if ng.role != NODE_GROUP_ROLE_CONTROLLER:
                nodegroup_item = dict(
                    name=driver_utils.sanitized_name(ng.name),
                    machineFlavor=ng.flavor_id,
                    machineCount=ng.node_count,
                )
                if self._get_autoscale_enabled(cluster):
                    values = self._get_autoscale_values(cluster, ng)
                    nodegroup_item = helm.mergeconcat(nodegroup_item, values)
                nodegroup_set.append(nodegroup_item)
        return nodegroup_set

    # Nodegroups in any of these statuses are already gone, or on their
    # way out, and must never be included in the Helm values.
    _NODEGROUP_SKIP_STATUSES = (
        fields.ClusterStatus.DELETE_IN_PROGRESS,
        fields.ClusterStatus.DELETE_FAILED,
        fields.ClusterStatus.DELETE_COMPLETE,
    )

    def _update_helm_release(
        self, context, cluster, exclude_nodegroup_name=None
    ):
        lconf = CONF.capi_helm_cluster_labels

        release_name = driver_utils.chart_release_name(cluster)
        namespace = driver_utils.cluster_namespace(cluster)
        with helm.HelmLock(release_name, namespace) as lock:
            # NOTE(scott): We refresh the cluster here to avoid missing
            # any updates to the DB which occurred while we waiting for
            # the lock
            cluster.refresh()

            nodegroups = [
                ng
                for ng in cluster.nodegroups
                if ng.name != exclude_nodegroup_name
                and ng.status not in self._NODEGROUP_SKIP_STATUSES
            ]

            image_id, kube_version, os_distro = self._get_image_details(
                context, cluster.cluster_template.image_id
            )

            network_id = self._get_fixed_network_id(context, cluster)
            subnet_id = neutron.get_fixed_subnet_id(
                context, cluster.fixed_subnet
            )

            values = {
                "kubernetesVersion": kube_version,
                "machineImageId": image_id,
                "machineSSHKeyName": cluster.keypair or None,
                "cloudCredentialsSecretName": self._get_app_cred_secret_name(
                    cluster
                ),
                "etcd": self._get_etcd_config(cluster),
                "apiServer": {
                    "associateFloatingIP": self._get_label_bool(
                        cluster,
                        "master_lb_floating_ip_enabled",
                        lconf.master_lb_floating_ip_enabled,
                    ),
                    # Disabling load balancers is not supported
                    # Please have a look at:
                    # https://docs.openstack.org/magnum-capi-helm/latest/configuration/index.html#tip-tricks
                    "enableLoadBalancer": True,
                    "loadBalancerProvider": self._get_octavia_provider(
                        cluster
                    ),
                },
                "clusterNetworking": {
                    "dnsNameservers": self._get_dns_nameservers(cluster),
                    "externalNetworkId": neutron.get_external_network_id(
                        context, cluster.cluster_template.external_network_id
                    ),
                    "internalNetwork": {
                        "networkFilter": (
                            {"id": network_id} if network_id else None
                        ),
                        "subnetFilter": (
                            {"id": subnet_id} if subnet_id else None
                        ),
                        # This is only used if a fixed network is not specified
                        "nodeCidr": self._label(
                            cluster,
                            "fixed_subnet_cidr",
                            lconf.fixed_subnet_cidr,
                        ),
                    },
                },
                "kubeNetwork": {
                    "pods": {
                        "cidrBlocks": [
                            self._label(
                                cluster, "pod_network_cidr", "10.100.0.0/16"
                            )
                        ]
                    },
                    "services": {
                        "cidrBlocks": [
                            self._label(
                                cluster,
                                "service_network_cidr",
                                "172.24.0.0/13",
                            )
                        ]
                    },
                },
                "osDistro": os_distro,
                "controlPlane": {
                    "machineFlavor": cluster.master_flavor_id,
                    "machineCount": cluster.master_count,
                    "healthCheck": {
                        "enabled": self._get_autoheal_enabled(cluster),
                    },
                },
                "nodeGroupDefaults": {
                    "healthCheck": {
                        "enabled": self._get_autoheal_enabled(cluster),
                    },
                },
                "nodeGroups": self._process_node_groups(cluster, nodegroups),
                "addons": {
                    "openstack": {
                        "csiCinder": self._storageclass_definitions(
                            context, cluster
                        ),
                        "cloudConfig": {
                            "LoadBalancer": {
                                "lb-provider": self._get_octavia_provider(
                                    cluster
                                ),
                                "lb-method": self._get_octavia_lb_algorithm(
                                    cluster
                                ),
                                "create-monitor": self._get_label_bool(
                                    cluster,
                                    "octavia_lb_healthcheck",
                                    lconf.octavia_lb_healthcheck,
                                ),
                            }
                        },
                    },
                    "monitoring": {
                        "enabled": self._get_monitoring_enabled(cluster)
                    },
                    "kubernetesDashboard": {
                        "enabled": self._get_kube_dash_enabled(cluster)
                    },
                    # TODO(mkjpryor): can't enable ingress until code exists to
                    #                 remove the load balancer
                    "ingress": {"enabled": False},
                },
            }

            # Add boot disk details, if defined in config file.
            # Helm chart defaults to ephemeral disks, if unset.
            boot_volume_type = self._label(
                cluster,
                "boot_volume_type",
                lconf.boot_volume_type or CONF.cinder.default_boot_volume_type,
            )
            if boot_volume_type:
                disk_type_details = {
                    "controlPlane": {
                        "machineRootVolume": {
                            "volumeType": boot_volume_type,
                        }
                    },
                    "nodeGroupDefaults": {
                        "machineRootVolume": {
                            "volumeType": boot_volume_type,
                        }
                    },
                }
                values = helm.mergeconcat(values, disk_type_details)

            conf_boot_size = lconf.boot_volume_size
            boot_volume_size_gb = self._get_label_int(
                cluster,
                "boot_volume_size",
                (
                    conf_boot_size
                    if conf_boot_size is not None
                    else CONF.cinder.default_boot_volume_size
                ),
            )
            if boot_volume_size_gb:
                disk_size_details = {
                    "controlPlane": {
                        "machineRootVolume": {
                            "diskSize": boot_volume_size_gb,
                        }
                    },
                    "nodeGroupDefaults": {
                        "machineRootVolume": {
                            "diskSize": boot_volume_size_gb,
                        }
                    },
                }
                values = helm.mergeconcat(values, disk_size_details)

            # Sometimes you need to add an extra network
            # for things like Cinder CSI CephFS Native
            # NOTE(mattcrees): extra_network_name is deprecated, so
            # extra_network_names takes precedence if both are set.
            # extra_network_name should be removed in a future release.
            extra_network_name = self._label(
                cluster,
                "extra_network_name",
                "",
            )
            extra_network_names = self._label(
                cluster,
                "extra_network_names",
                lconf.extra_network_names,
            )
            if extra_network_name and not extra_network_names:
                extra_network_names = extra_network_name
            if extra_network_names:
                ports = [{}]
                for network in extra_network_names.split(" "):
                    ports.append(
                        {
                            "network": {
                                "name": network,
                            },
                            "securityGroups": [],
                        }
                    )
                network_details = {
                    "nodeGroupDefaults": {
                        "machineNetworking": {
                            "ports": ports,
                        },
                    },
                }
                values = helm.mergeconcat(values, network_details)

            if self._get_k8s_keystone_auth_enabled(cluster):
                k8s_keystone_auth_config = {
                    "authWebhook": "k8s-keystone-auth",
                    "addons": {
                        "openstack": {
                            "k8sKeystoneAuth": {
                                # addon subchart configuration
                                "enabled": True,
                                "values": {
                                    "openstackAuthUrl": context.auth_url,
                                    "projectId": context.project_id,
                                },
                            }
                        }
                    },
                }
                values = helm.mergeconcat(values, k8s_keystone_auth_config)
                LOG.debug(
                    "Enable K8s keystone auth webhook for"
                    f" project: {context.project_id} auth url: "
                    f"{context.auth_url}"
                )

            api_lb_allowed_cidrs = self._get_allowed_cidrs(cluster)
            if isinstance(api_lb_allowed_cidrs, list):
                allowed_cidrs_config = {
                    "apiServer": {"allowedCidrs": api_lb_allowed_cidrs}
                }
                values = helm.mergeconcat(values, allowed_cidrs_config)

            cni_type = self._get_cni_type(cluster)
            if cni_type:
                cni_config = {"addons": {"cni": {"type": cni_type}}}
                values = helm.mergeconcat(values, cni_config)

            # Reset the lease's TTL clock now, right before the one step
            # the lock actually needs to protect - everything above this
            # point (DB/Glance/Neutron/Cinder lookups) is unbounded and
            # must not eat into the time budget the Helm command itself
            # is allotted.
            lock.renew()

            self._helm_client.install_or_upgrade(
                release_name,
                CONF.capi_helm.helm_chart_name,
                values,
                repo=CONF.capi_helm.helm_chart_repo,
                version=self._get_chart_version(cluster),
                namespace=namespace,
            )

    def _generate_release_name(self, cluster):
        if cluster.stack_id:
            return

        # Make sure no duplicate names
        # by generating 12 character random id
        random_bit = short_id.generate_id()
        base_name = driver_utils.sanitized_name(cluster.name)
        # valid release names are 53 chars long
        # and stack_id is 12 characters
        # but we also use this to derive hostnames
        trimmed_name = base_name[:30]
        # Save the full name, so users can rename in the API
        cluster.stack_id = f"{trimmed_name}-{random_bit}".lower()
        # be sure to save this before we use it
        cluster.save()

    def create_cluster(self, context, cluster, cluster_create_timeout):
        LOG.info("Starting to create cluster %s", cluster.uuid)

        self._validate_allowed_flavor(context, cluster.master_flavor_id)
        nodegroups = cluster.nodegroups
        for ng in nodegroups:
            self._validate_allowed_flavor(context, ng.flavor_id)
        self._validate_cidr_no_overlap(context, cluster)
        # we generate this name (on the initial create call only)
        # so we hit no issues with duplicate cluster names
        # and it makes renaming clusters in the API possible
        self._generate_release_name(cluster)

        # NOTE(johngarbutt) all node groups should already
        # be in the CREATE_IN_PROGRESS state
        self._k8s_client.ensure_namespace(
            driver_utils.cluster_namespace(cluster)
        )
        self._create_appcred_secret(context, cluster)
        self._ensure_certificate_secrets(context, cluster)

        self._update_helm_release(context, cluster)

    def update_cluster(
        self, context, cluster, scale_manager=None, rollback=False
    ):
        # we get here if cluster was patched with new node_count
        self._update_helm_release(context, cluster)

    def delete_cluster(self, context, cluster):
        LOG.info("Starting to delete cluster %s", cluster.uuid)

        # Copy the helm driver by marking all node groups
        # as delete in progress here, as note done by conductor
        # We do this before calling uninstall_release because
        # update_cluster_status can get called before we return
        for ng in cluster.nodegroups:
            ng.status = fields.ClusterStatus.DELETE_IN_PROGRESS
            ng.save()

        release_name = driver_utils.chart_release_name(cluster)
        # Only attempt deletion of CAPI resources if they were created in
        # the first place e.g. if trust creation fails during cluster create
        # then no CAPI resources will have been created.
        if release_name:
            namespace = driver_utils.cluster_namespace(cluster)
            # Begin the deletion of the cluster resources by uninstalling the
            # Helm release.
            # Note that this just marks the resources for deletion,
            # it does not wait for the resources to be deleted.
            # Hold the same lock as install/upgrade so this can't race
            # with a concurrent Helm operation on the same release.
            with helm.HelmLock(release_name, namespace):
                self._helm_client.uninstall_release(
                    release_name,
                    namespace=namespace,
                )

    def resize_cluster(
        self,
        context,
        cluster,
        resize_manager,
        node_count,
        nodes_to_remove,
        nodegroup=None,
    ):
        if nodes_to_remove:
            LOG.warning("Removing specific nodes is not currently supported")
        self._update_helm_release(context, cluster)

    def upgrade_cluster(
        self,
        context,
        cluster,
        cluster_template,
        max_batch_size,
        nodegroup,
        scale_manager=None,
        rollback=False,
    ):
        # TODO(mkjpryor) check that the upgrade is viable
        # e.g. not a downgrade, not an upgrade by more than one minor version

        # Updating the template will likely apply for all nodegroups
        # So mark them all as having an update in progress
        for nodegroup in cluster.nodegroups:
            nodegroup.status = fields.ClusterStatus.UPDATE_IN_PROGRESS
            self._validate_allowed_flavor(context, nodegroup.flavor_id)
            nodegroup.save()

        # Move the cluster to the new template
        cluster.cluster_template_id = cluster_template.uuid
        cluster.status = fields.ClusterStatus.UPDATE_IN_PROGRESS
        cluster.save()
        cluster.refresh()

        self._update_helm_release(context, cluster)

    def create_nodegroup(self, context, cluster, nodegroup):
        nodegroup.status = fields.ClusterStatus.CREATE_IN_PROGRESS
        self._validate_allowed_flavor(context, nodegroup.flavor_id)
        if self._get_autoscale_enabled(cluster):
            self._validate_allowed_node_counts(cluster, nodegroup)
        nodegroup.save()

        self._update_helm_release(context, cluster)

    def update_nodegroup(self, context, cluster, nodegroup):
        nodegroup.status = fields.ClusterStatus.UPDATE_IN_PROGRESS
        self._validate_allowed_flavor(context, nodegroup.flavor_id)
        if self._get_autoscale_enabled(cluster):
            self._validate_allowed_node_counts(cluster, nodegroup)
        nodegroup.save()

        self._update_helm_release(context, cluster)

    def delete_nodegroup(self, context, cluster, nodegroup):
        nodegroup.status = fields.ClusterStatus.DELETE_IN_PROGRESS
        nodegroup.save()

        # Remove the nodegroup being deleted from the nodegroups for the
        # Helm release. The exclusion is applied against the cluster's
        # nodegroups as refreshed from the DB after the Helm lock is
        # acquired, not this pre-lock snapshot, so it can't go stale if
        # another nodegroup change lands while we are waiting for the
        # lock.
        self._update_helm_release(
            context,
            cluster,
            exclude_nodegroup_name=nodegroup.name,
        )

    def rotate_credential(self, context, cluster):
        # Current cluster owner to revert to if rotation fails
        old_user_id = cluster.user_id

        # Template cluster name for log messages
        cluster_name = (
            f"'{cluster.name}' ({cluster.uuid})"
            if cluster.name
            else cluster.uuid
        )

        LOG.info(f"Rotating application credential for cluster {cluster_name}")

        # Set new owner now to ensure resource labels are applied correctly
        cluster.user_id = context.user_id
        cluster.status_reason = None

        # Fetch the existing application credential ID to check against later
        old_app_cred_id = self._get_app_cred_id(cluster)

        # Tuple of possible exceptions raised when creating new app cred
        exceptions = (
            app_creds.ApplicationCredentialError,
            exception.AuthorizationFailure,  # Raised on Keystone error
            requests.exceptions.RequestException,
            yaml.YAMLError,
        )
        error_msg = None

        try:
            # Create and apply application credential to cluster
            new_app_cred = self._create_appcred_secret(context, cluster)
        except exceptions as e:
            error_msg = str(e)
            LOG.warning(
                "Failed to rotate application credential for cluster "
                f"{cluster_name}: {e}"
            )
        else:
            if not new_app_cred:
                error_msg = "Application credential secret is unset."
                LOG.critical(
                    "Failed to rotate application credential for cluster "
                    f"{cluster_name}: {error_msg}"
                )
            elif new_app_cred.id == old_app_cred_id:
                error_msg = "Application credential secret failed to apply."
                LOG.error(
                    "Failed to rotate application credential for cluster "
                    f"{cluster_name}: {error_msg}"
                )
            # Update user-id label on existing secrets if it has changed
            elif cluster.user_id != old_user_id:
                try:
                    self._ensure_certificate_secrets(context, cluster)
                except requests.exceptions.RequestException as e:
                    error_msg = str(e)
                    LOG.error(
                        f"Failed to transfer ownership of cluster "
                        f"{cluster_name} from {old_user_id} to "
                        f"{cluster.user_id}: {e}"
                    )
                else:
                    LOG.info(
                        f"Ownership of cluster {cluster_name} transferred "
                        f"from {cluster.user_id} to {context.user_id}"
                    )

        if error_msg:
            cluster.status = fields.ClusterStatus.UPDATE_FAILED
            cluster.status_reason = error_msg
            cluster.user_id = old_user_id
            cluster.save()
            raise exception.MagnumException(message=error_msg)

        # Remove the old credential now that the new one is working
        if old_app_cred_id:
            try:
                app_creds.delete_app_cred(cluster, old_app_cred_id)
            except app_creds.ApplicationCredentialError as e:
                # NOTE(northcottmt): Not a failure condition as the cluster is
                # functional, but report the old app cred could not be deleted
                cluster.status_reason = (
                    "A new application credential was applied successfully "
                    "but the previous application credential could not be "
                    f"deleted: {e}"
                )
                LOG.warning(
                    "Failed to delete application credential for "
                    f"cluster {cluster_name}: {e}"
                )

        cluster.status = fields.ClusterStatus.UPDATE_COMPLETE
        cluster.save()

    def create_federation(self, context, federation):
        raise NotImplementedError("Will not implement 'create_federation'")

    def update_federation(self, context, federation):
        raise NotImplementedError("Will not implement 'update_federation'")

    def delete_federation(self, context, federation):
        raise NotImplementedError("Will not implement 'delete_federation'")
