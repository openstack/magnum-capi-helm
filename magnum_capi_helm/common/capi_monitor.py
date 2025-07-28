#    Licensed under the Apache License, Version 2.0 (the "License");
#    you may not use this file except in compliance with the License.
#    You may obtain a copy of the License at
#
#        http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS,
#    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#    See the License for the specific language governing permissions and
#    limitations under the License.

from magnum.conductor import monitors
from magnum.i18n import _
from magnum.objects import fields as m_fields
from magnum_capi_helm import driver_utils
from magnum_capi_helm import kubernetes


MONITOR_STATE_READY = _("Ready")


class CAPIMonitor(monitors.MonitorBase):
    def __init__(self, context, cluster):
        super(CAPIMonitor, self).__init__(context, cluster)
        self.__k8s_client = None
        self.data = {}
        self.data["nodes"] = []
        self.data["pods"] = []

    @property
    def _k8s_client(self):
        if not self.__k8s_client:
            self.__k8s_client = kubernetes.Client.load()
        return self.__k8s_client

    @property
    def metrics_spec(self):
        # TODO(dalees): Not yet implemented for CAPI helm driver
        return super().metrics_spec

    def pull_data(self):
        # TODO(dalees): Not yet implemented for CAPI helm driver
        return super().pull_data()

    def poll_health_status(self):
        """Poll health status of API and nodes for given cluster from CAPI

        Design Policy:
        1.  Magnum should not require direct access to workload clusters.
            This means all health status must come from Cluster API's
            management cluster resources. These already provide health info.

        2.  Calculating the overall health status.
            We depend on Cluster and OpenstackCluster resources having updated
            'status'.
            From these, we can retrieve overall health and report back.

            Some errors surface only in OpenstackCluster state, under
            FailureMessage and FailureReason. Surface this to users, as it can
            be helpful (eg. Neutron 409's when deleting a network that has
            ports)

            If we find any Cluster.status.conditions field *not* Status:True,
            then list and set Unhealthy.

            If we find OpenstackCluster.status.ready *not* true, report back
            and include FailureReason and FailureMessage if set.

            Iterate all nodegroup MachineDeployment and report back on
            status.conditions.

        3.  The data structure of health_status_reason:

            As an attribute of the cluster, the health_status_reason has to
            use the field type from
            oslo.versionedobjects/blob/master/oslo_versionedobjects/fields.py

        :return: None.

        The class variable data is updated with current status and reason.

        """
        # Start with a good state for everything
        status = m_fields.ClusterHealthStatus.HEALTHY
        reason = {}

        for monitor_type, monitor_func in [
            ("cluster", self._poll_cluster),
            ("infrastructure", self._poll_infra),
            ("controlplane", self._poll_controlplane),
            ("nodegroup", self._poll_nodegroups),
        ]:
            reason[monitor_type] = monitor_func()
            if reason[monitor_type] != MONITOR_STATE_READY:
                status = m_fields.ClusterHealthStatus.UNHEALTHY

        self.data["health_status"] = status
        self.data["health_status_reason"] = reason

    def _poll_cluster(self):
        """Get status from Cluster.

        This has most status info available as it bubbles up.
        """
        namespace = driver_utils.cluster_namespace(self.cluster)
        resource_name = driver_utils.get_k8s_resource_name(self.cluster, None)
        resource_cluster = self._k8s_client.get_capi_cluster(
            resource_name, namespace
        )
        if not resource_cluster:
            return "Cluster resource not found."

        cluster_conditions = [
            c.get("type")
            for c in resource_cluster.get("status", {}).get("conditions", {})
            if c.get("status") != "True"
        ]
        if cluster_conditions:
            return f"Waiting on {cluster_conditions}"

        return MONITOR_STATE_READY

    def _poll_infra(self):
        """Analyse OpenStackCluster resource

        This represents the CAPO Infrastructure component.
        """
        namespace = driver_utils.cluster_namespace(self.cluster)
        resource_name = driver_utils.get_k8s_resource_name(self.cluster, None)
        resource_capo = self._k8s_client.get_capi_openstackcluster(
            resource_name, namespace
        )
        if not resource_capo:
            return "Infrastructure resource not found."

        capo_status = resource_capo.get("status", {})
        capo_ready = capo_status.get("ready", False)
        capo_message = capo_status.get("failureMessage", "")
        capo_reason = capo_status.get("failureReason", None)

        if capo_reason:
            return f"{capo_reason}: {capo_message}"

        if not capo_ready:
            return "Infrastructure not ready."

        return MONITOR_STATE_READY

    def _poll_controlplane(self):
        """Analyse KubeAdmControlPlane resource

        This CAPI controller manages the control plane machines
        """
        namespace = driver_utils.cluster_namespace(self.cluster)
        resource_name = driver_utils.get_k8s_resource_name(
            self.cluster, "control-plane"
        )
        resource_kcp = self._k8s_client.get_k8s_control_plane(
            resource_name, namespace
        )
        if not resource_kcp:
            return "Control plane resource not found."

        conditions = [
            c.get("type")
            for c in resource_kcp.get("status", {}).get("conditions", {})
            if c.get("status") != "True"
        ]
        if conditions:
            return f"Waiting on {conditions}"

        return MONITOR_STATE_READY

    def _poll_nodegroups(self):
        """Analyse MachineDeployment resources

        There is one machinedeployment per nodegroup (which holds
        information on machineset and machine resources)
        """
        namespace = driver_utils.cluster_namespace(self.cluster)
        nodegroup_reasons = []
        for nodegroup in self.cluster.nodegroups:
            if nodegroup.role == "master":
                continue
            resource_name = driver_utils.get_k8s_resource_name(
                self.cluster, nodegroup.name
            )
            resource_md = self._k8s_client.get_machine_deployment(
                resource_name, namespace
            )

            if not resource_md:
                nodegroup_reasons.append(
                    f"{nodegroup.name} resource not found."
                )
                continue

            conditions = [
                c.get("type")
                for c in resource_md.get("status", {}).get("conditions", {})
                if c.get("status") != "True"
            ]
            if conditions:
                nodegroup_reasons.append(
                    f"{nodegroup.name} waiting on {conditions}"
                )
        if nodegroup_reasons:
            return ",".join(nodegroup_reasons)

        return MONITOR_STATE_READY

    def _is_cluster_accessible(self):
        # This monitor does not directly access Kubernetes clusters
        # as it obtains all information from CAPI Management cluster.
        # So all clusters are accessible, if CAPI is functional.
        return True
