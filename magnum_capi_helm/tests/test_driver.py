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
from unittest import mock
from uuid import uuid4

from magnum.common import exception
from magnum.common import neutron
from magnum.objects import fields
from magnum.tests.unit.db import base
from magnum.tests.unit.objects import utils as obj_utils

from magnum_capi_helm.common import app_creds
from magnum_capi_helm.common import ca_certificates
from magnum_capi_helm.common import capi_monitor
from magnum_capi_helm import conf
from magnum_capi_helm import driver
from magnum_capi_helm import driver_utils
from magnum_capi_helm import helm
from magnum_capi_helm import kubernetes

CONF = conf.CONF


class ClusterAPIDriverTest(base.DbTestCase):
    def setUp(self):
        super(ClusterAPIDriverTest, self).setUp()
        self.driver = driver.Driver()
        self.cluster_obj = obj_utils.create_test_cluster(
            self.context,
            name="cluster_example_$A",
            master_flavor_id="flavor_small",
            flavor_id="flavor_medium",
            stack_id="cluster-example-a-111111111111",
        )
        # add in missing node group flavor
        for ng in self.cluster_obj.nodegroups:
            if ng.role != "master":
                ng.flavor_id = "flavor_medium"
                ng.save()

    def test_provides(self):
        self.assertEqual(
            [
                {
                    "server_type": "vm",
                    "os": "ubuntu",
                    "coe": "kubernetes",
                },
                {
                    "server_type": "vm",
                    "os": "flatcar",
                    "coe": "kubernetes",
                },
            ],
            self.driver.provides,
        )

    @mock.patch.object(driver.Driver, "_update_status_deleting")
    @mock.patch.object(driver.Driver, "_update_status_updating")
    @mock.patch.object(driver.Driver, "_update_all_nodegroups_status")
    @mock.patch.object(driver.Driver, "_get_capi_cluster")
    def test_update_cluster_status_creating(
        self, mock_capi, mock_ng, mock_update, mock_delete
    ):
        mock_ng.return_value = True
        mock_capi.return_value = {"spec": {}}
        self.cluster_obj.status = fields.ClusterStatus.CREATE_IN_PROGRESS

        self.driver.update_cluster_status(self.context, self.cluster_obj)

        mock_ng.assert_called_once_with(self.cluster_obj)
        mock_update.assert_not_called()
        mock_delete.assert_not_called()

    @mock.patch.object(driver.Driver, "_update_status_deleting")
    @mock.patch.object(driver.Driver, "_update_status_updating")
    @mock.patch.object(driver.Driver, "_update_all_nodegroups_status")
    @mock.patch.object(driver.Driver, "_get_capi_cluster")
    def test_update_cluster_status_creating_not_found(
        self, mock_capi, mock_ng, mock_update, mock_delete
    ):
        mock_ng.return_value = True
        mock_capi.return_value = None
        self.cluster_obj.status = fields.ClusterStatus.CREATE_IN_PROGRESS

        self.driver.update_cluster_status(self.context, self.cluster_obj)

        mock_ng.assert_not_called()
        mock_update.assert_not_called()
        mock_delete.assert_not_called()

    @mock.patch.object(driver.Driver, "_update_status_deleting")
    @mock.patch.object(driver.Driver, "_update_status_updating")
    @mock.patch.object(driver.Driver, "_update_all_nodegroups_status")
    @mock.patch.object(driver.Driver, "_get_capi_cluster")
    def test_update_cluster_status_created(
        self, mock_capi, mock_ng, mock_update, mock_delete
    ):
        mock_ng.return_value = False
        mock_capi.return_value = {"spec": {}}
        self.cluster_obj.status = fields.ClusterStatus.CREATE_IN_PROGRESS

        self.driver.update_cluster_status(self.context, self.cluster_obj)

        mock_ng.assert_called_once_with(self.cluster_obj)
        mock_update.assert_called_once_with(self.cluster_obj, {"spec": {}})
        mock_delete.assert_not_called()

    @mock.patch.object(driver.Driver, "_update_status_deleting")
    @mock.patch.object(driver.Driver, "_update_status_updating")
    @mock.patch.object(driver.Driver, "_update_all_nodegroups_status")
    @mock.patch.object(driver.Driver, "_get_capi_cluster")
    def test_update_cluster_status_deleted(
        self, mock_capi, mock_ng, mock_update, mock_delete
    ):
        mock_capi.return_value = None
        self.cluster_obj.status = fields.ClusterStatus.DELETE_IN_PROGRESS

        self.driver.update_cluster_status(self.context, self.cluster_obj)

        mock_ng.assert_not_called()
        mock_update.assert_not_called()
        mock_delete.assert_called_once_with(self.context, self.cluster_obj)

    @mock.patch.object(driver.Driver, "_update_status_deleting")
    @mock.patch.object(driver.Driver, "_update_status_updating")
    @mock.patch.object(driver.Driver, "_update_all_nodegroups_status")
    @mock.patch.object(driver.Driver, "_get_capi_cluster")
    def test_update_cluster_status_deleting(
        self, mock_capi, mock_ng, mock_update, mock_delete
    ):
        mock_capi.return_value = {"spec": {}}
        self.cluster_obj.status = fields.ClusterStatus.DELETE_IN_PROGRESS

        self.driver.update_cluster_status(self.context, self.cluster_obj)

        mock_ng.assert_called_once_with(self.cluster_obj)
        mock_update.assert_not_called()
        mock_delete.assert_not_called()

    @mock.patch.object(driver.Driver, "_update_status_deleting")
    @mock.patch.object(driver.Driver, "_update_status_updating")
    @mock.patch.object(driver.Driver, "_update_all_nodegroups_status")
    @mock.patch.object(driver.Driver, "_get_capi_cluster")
    def test_update_cluster_status_create_complete(
        self, mock_capi, mock_ng, mock_update, mock_delete
    ):
        mock_capi.return_value = {"spec": {}}
        self.cluster_obj.status = fields.ClusterStatus.CREATE_COMPLETE

        self.driver.update_cluster_status(self.context, self.cluster_obj)

        mock_ng.assert_called_once_with(self.cluster_obj)
        mock_update.assert_not_called()
        mock_delete.assert_not_called()

    @mock.patch.object(driver.Driver, "_update_worker_nodegroup_status")
    @mock.patch.object(driver.Driver, "_update_control_plane_nodegroup_status")
    def test_update_all_nodegroups_status_not_in_progress(
        self, mock_cp, mock_w
    ):
        control_plane = [
            ng
            for ng in self.cluster_obj.nodegroups
            if ng.role == driver.NODE_GROUP_ROLE_CONTROLLER
        ][0]
        control_plane.status = fields.ClusterStatus.CREATE_COMPLETE
        mock_cp.return_value = control_plane
        mock_w.return_value = None

        result = self.driver._update_all_nodegroups_status(self.cluster_obj)

        self.assertFalse(result)
        control_plane = [
            ng
            for ng in self.cluster_obj.nodegroups
            if ng.role == driver.NODE_GROUP_ROLE_CONTROLLER
        ][0]
        mock_cp.assert_called_once_with(self.cluster_obj, mock.ANY)
        self.assertEqual(
            control_plane.obj_to_primitive(),
            mock_cp.call_args_list[0][0][1].obj_to_primitive(),
        )
        mock_w.assert_called_once_with(self.cluster_obj, mock.ANY)
        worker = [
            ng
            for ng in self.cluster_obj.nodegroups
            if ng.role != driver.NODE_GROUP_ROLE_CONTROLLER
        ][0]
        self.assertEqual(
            worker.obj_to_primitive(),
            mock_w.call_args_list[0][0][1].obj_to_primitive(),
        )

    @mock.patch.object(driver.Driver, "_update_worker_nodegroup_status")
    @mock.patch.object(driver.Driver, "_update_control_plane_nodegroup_status")
    def test_update_all_nodegroups_status_in_progress(self, mock_cp, mock_w):
        control_plane = [
            ng
            for ng in self.cluster_obj.nodegroups
            if ng.role == driver.NODE_GROUP_ROLE_CONTROLLER
        ][0]
        control_plane.status = fields.ClusterStatus.CREATE_IN_PROGRESS
        mock_cp.return_value = control_plane
        mock_w.return_value = None

        result = self.driver._update_all_nodegroups_status(self.cluster_obj)

        self.assertTrue(result)
        mock_cp.assert_called_once_with(self.cluster_obj, mock.ANY)
        mock_w.assert_called_once_with(self.cluster_obj, mock.ANY)

    @mock.patch.object(driver.Driver, "_update_nodegroup_status")
    @mock.patch.object(kubernetes.Client, "load")
    def test_update_worker_nodegroup_status_empty(
        self, mock_load, mock_update
    ):
        mock_client = mock.MagicMock(spec=kubernetes.Client)
        mock_load.return_value = mock_client
        nodegroup = mock.MagicMock()
        nodegroup.name = "workers"
        nodegroup.status = fields.ClusterStatus.CREATE_IN_PROGRESS
        md = {"status": {}}
        mock_client.get_machine_deployment.return_value = md

        self.driver._update_worker_nodegroup_status(
            self.cluster_obj, nodegroup
        )

        mock_client.get_machine_deployment.assert_called_once_with(
            "cluster-example-a-111111111111-workers", "magnum-fakeproject"
        )
        mock_update.assert_called_once_with(
            self.cluster_obj, nodegroup, driver.NodeGroupState.PENDING
        )

    @mock.patch.object(driver.Driver, "_update_nodegroup_status")
    @mock.patch.object(kubernetes.Client, "load")
    def test_update_worker_nodegroup_status_scaling_up(
        self, mock_load, mock_update
    ):
        mock_client = mock.MagicMock(spec=kubernetes.Client)
        mock_load.return_value = mock_client
        nodegroup = mock.MagicMock()
        nodegroup.name = "workers"
        md = {"status": {"phase": "ScalingUp"}}
        mock_client.get_machine_deployment.return_value = md

        self.driver._update_worker_nodegroup_status(
            self.cluster_obj, nodegroup
        )

        mock_client.get_machine_deployment.assert_called_once_with(
            "cluster-example-a-111111111111-workers", "magnum-fakeproject"
        )
        mock_update.assert_called_once_with(
            self.cluster_obj, mock.ANY, driver.NodeGroupState.PENDING
        )

    @mock.patch.object(driver.Driver, "_update_nodegroup_status")
    @mock.patch.object(kubernetes.Client, "load")
    def test_update_worker_nodegroup_status_failed(
        self, mock_load, mock_update
    ):
        mock_client = mock.MagicMock(spec=kubernetes.Client)
        mock_load.return_value = mock_client
        nodegroup = mock.MagicMock()
        nodegroup.name = "workers"
        nodegroup.status = fields.ClusterStatus.CREATE_IN_PROGRESS
        md = {"status": {"phase": "Failed"}}
        mock_client.get_machine_deployment.return_value = md

        self.driver._update_worker_nodegroup_status(
            self.cluster_obj, nodegroup
        )

        mock_client.get_machine_deployment.assert_called_once_with(
            "cluster-example-a-111111111111-workers", "magnum-fakeproject"
        )
        mock_update.assert_called_once_with(
            self.cluster_obj, nodegroup, driver.NodeGroupState.FAILED
        )

    @mock.patch.object(driver.Driver, "_update_nodegroup_status")
    @mock.patch.object(kubernetes.Client, "load")
    def test_update_worker_nodegroup_status_not_present_creating(
        self, mock_load, mock_update
    ):
        mock_client = mock.MagicMock(spec=kubernetes.Client)
        mock_load.return_value = mock_client
        nodegroup = mock.MagicMock()
        nodegroup.name = "workers"
        nodegroup.status = fields.ClusterStatus.CREATE_IN_PROGRESS
        mock_client.get_machine_deployment.return_value = None
        mock_client.get_all_machines_by_label.return_value = None

        self.driver._update_worker_nodegroup_status(
            self.cluster_obj, nodegroup
        )

        mock_client.get_machine_deployment.assert_called_once_with(
            "cluster-example-a-111111111111-workers", "magnum-fakeproject"
        )
        mock_update.assert_called_once_with(
            self.cluster_obj, nodegroup, driver.NodeGroupState.NOT_PRESENT
        )
        mock_client.get_all_machines_by_label.assert_not_called()
        nodegroup.destroy.assert_not_called()
        nodegroup.save.assert_not_called()

    @mock.patch.object(driver.Driver, "_update_nodegroup_status")
    @mock.patch.object(kubernetes.Client, "load")
    def test_update_worker_nodegroup_status_not_present_deleting(
        self, mock_load, mock_update
    ):
        mock_client = mock.MagicMock(spec=kubernetes.Client)
        mock_load.return_value = mock_client
        nodegroup = mock.MagicMock()
        nodegroup.name = "workers"
        nodegroup.status = fields.ClusterStatus.DELETE_IN_PROGRESS
        machine = {"status": {}}
        mock_client.get_machine_deployment.return_value = None
        mock_client.get_all_machines_by_label.return_value = machine

        self.driver._update_worker_nodegroup_status(
            self.cluster_obj, nodegroup
        )

        mock_client.get_machine_deployment.assert_called_once_with(
            "cluster-example-a-111111111111-workers", "magnum-fakeproject"
        )
        mock_client.get_all_machines_by_label.assert_called_once_with(
            {
                "capi.stackhpc.com/cluster": "cluster-example-a-111111111111",
                "capi.stackhpc.com/component": "worker",
                "capi.stackhpc.com/node-group": "workers",
            },
            "magnum-fakeproject",
        )
        mock_update.assert_called_once_with(
            self.cluster_obj, nodegroup, driver.NodeGroupState.PENDING
        )

    @mock.patch.object(kubernetes.Client, "load")
    def test_update_worker_nodegroup_status_machines_missing_non_default(
        self, mock_load
    ):
        mock_client = mock.MagicMock(spec=kubernetes.Client)
        mock_load.return_value = mock_client
        nodegroup = mock.MagicMock()
        nodegroup.name = "workers"
        nodegroup.status = fields.ClusterStatus.DELETE_IN_PROGRESS
        nodegroup.is_default = False
        mock_client.get_machine_deployment.return_value = None
        mock_client.get_all_machines_by_label.return_value = None

        self.driver._update_worker_nodegroup_status(
            self.cluster_obj, nodegroup
        )

        mock_client.get_machine_deployment.assert_called_once_with(
            "cluster-example-a-111111111111-workers", "magnum-fakeproject"
        )
        mock_client.get_all_machines_by_label.assert_called_once_with(
            {
                "capi.stackhpc.com/cluster": "cluster-example-a-111111111111",
                "capi.stackhpc.com/component": "worker",
                "capi.stackhpc.com/node-group": "workers",
            },
            "magnum-fakeproject",
        )
        nodegroup.destroy.assert_called_once_with()
        nodegroup.save.assert_not_called()

    @mock.patch.object(driver.Driver, "_update_nodegroup_status")
    @mock.patch.object(kubernetes.Client, "load")
    def test_update_worker_nodegroup_status_running(
        self, mock_load, mock_update
    ):
        mock_client = mock.MagicMock(spec=kubernetes.Client)
        mock_load.return_value = mock_client
        nodegroup = mock.MagicMock()
        nodegroup.name = "workers"
        md = {"status": {"phase": "Running"}}
        mock_client.get_machine_deployment.return_value = md

        self.driver._update_worker_nodegroup_status(
            self.cluster_obj, nodegroup
        )

        mock_client.get_machine_deployment.assert_called_once_with(
            "cluster-example-a-111111111111-workers", "magnum-fakeproject"
        )
        mock_update.assert_called_once_with(
            self.cluster_obj, mock.ANY, driver.NodeGroupState.READY
        )

    @mock.patch.object(driver.Driver, "_update_nodegroup_status")
    @mock.patch.object(kubernetes.Client, "load")
    def test_update_control_plane_nodegroup_status_empty(
        self, mock_load, mock_update
    ):
        mock_client = mock.MagicMock(spec=kubernetes.Client)
        mock_load.return_value = mock_client
        nodegroup = mock.MagicMock()
        nodegroup.name = "masters"
        mock_client.get_kubeadm_control_plane.return_value = None

        self.driver._update_control_plane_nodegroup_status(
            self.cluster_obj, nodegroup
        )

        mock_client.get_kubeadm_control_plane.assert_called_once_with(
            "cluster-example-a-111111111111-control-plane",
            "magnum-fakeproject",
        )
        mock_update.assert_called_once_with(
            self.cluster_obj, mock.ANY, driver.NodeGroupState.NOT_PRESENT
        )

    @mock.patch.object(driver.Driver, "_update_nodegroup_status")
    @mock.patch.object(kubernetes.Client, "load")
    def test_update_control_plane_nodegroup_status_condition_false(
        self, mock_load, mock_update
    ):
        mock_client = mock.MagicMock(spec=kubernetes.Client)
        mock_load.return_value = mock_client
        nodegroup = mock.MagicMock()
        nodegroup.name = "masters"
        kcp = {
            "spec": {
                "replicas": 3,
            },
            "status": {
                "conditions": [
                    {"type": "MachinesReady", "status": "True"},
                    {"type": "Ready", "status": "True"},
                    {"type": "EtcdClusterHealthy", "status": "True"},
                    {
                        "type": "ControlPlaneComponentsHealthy",
                        "status": "False",
                    },
                ],
                "replicas": 3,
                "updatedReplicas": 3,
                "readyReplicas": 3,
            },
        }
        mock_client.get_kubeadm_control_plane.return_value = kcp

        self.driver._update_control_plane_nodegroup_status(
            self.cluster_obj, nodegroup
        )

        mock_client.get_kubeadm_control_plane.assert_called_once_with(
            "cluster-example-a-111111111111-control-plane",
            "magnum-fakeproject",
        )
        mock_update.assert_called_once_with(
            self.cluster_obj, mock.ANY, driver.NodeGroupState.PENDING
        )

    @mock.patch.object(driver.Driver, "_update_nodegroup_status")
    @mock.patch.object(kubernetes.Client, "load")
    def test_update_control_plane_nodegroup_status_mismatched_replicas(
        self, mock_load, mock_update
    ):
        mock_client = mock.MagicMock(spec=kubernetes.Client)
        mock_load.return_value = mock_client
        nodegroup = mock.MagicMock()
        nodegroup.name = "masters"
        kcp = {
            "spec": {
                "replicas": 3,
            },
            "status": {
                "conditions": [
                    {"type": "MachinesReady", "status": "True"},
                    {"type": "Ready", "status": "True"},
                    {"type": "EtcdClusterHealthy", "status": "True"},
                    {
                        "type": "ControlPlaneComponentsHealthy",
                        "status": "True",
                    },
                ],
                "replicas": 3,
                "updatedReplicas": 2,
                "readyReplicas": 2,
            },
        }
        mock_client.get_kubeadm_control_plane.return_value = kcp

        self.driver._update_control_plane_nodegroup_status(
            self.cluster_obj, nodegroup
        )

        mock_client.get_kubeadm_control_plane.assert_called_once_with(
            "cluster-example-a-111111111111-control-plane",
            "magnum-fakeproject",
        )
        mock_update.assert_called_once_with(
            self.cluster_obj, mock.ANY, driver.NodeGroupState.PENDING
        )

    @mock.patch.object(driver.Driver, "_update_nodegroup_status")
    @mock.patch.object(kubernetes.Client, "load")
    def test_update_control_plane_nodegroup_status_ready(
        self, mock_load, mock_update
    ):
        mock_client = mock.MagicMock(spec=kubernetes.Client)
        mock_load.return_value = mock_client
        nodegroup = mock.MagicMock()
        nodegroup.name = "masters"
        kcp = {
            "spec": {
                "replicas": 3,
            },
            "status": {
                "conditions": [
                    {"type": "MachinesReady", "status": "True"},
                    {"type": "Ready", "status": "True"},
                    {"type": "EtcdClusterHealthy", "status": "True"},
                    {
                        "type": "ControlPlaneComponentsHealthy",
                        "status": "True",
                    },
                ],
                "replicas": 3,
                "updatedReplicas": 3,
                "readyReplicas": 3,
            },
        }
        mock_client.get_kubeadm_control_plane.return_value = kcp

        self.driver._update_control_plane_nodegroup_status(
            self.cluster_obj, nodegroup
        )

        mock_client.get_kubeadm_control_plane.assert_called_once_with(
            "cluster-example-a-111111111111-control-plane",
            "magnum-fakeproject",
        )
        mock_update.assert_called_once_with(
            self.cluster_obj, mock.ANY, driver.NodeGroupState.READY
        )

    @mock.patch.object(kubernetes.Client, "load")
    def test_nodegroup_machines_exist(self, mock_load):
        mock_client = mock.MagicMock(spec=kubernetes.Client)
        mock_load.return_value = mock_client
        mock_client.get_all_machines_by_label.return_value = ["item1"]
        nodegroup = obj_utils.create_test_nodegroup(self.context)

        result = self.driver._nodegroup_machines_exist(
            self.cluster_obj, nodegroup
        )

        self.assertTrue(result)
        mock_client.get_all_machines_by_label.assert_called_once_with(
            {
                "capi.stackhpc.com/cluster": "cluster-example-a-111111111111",
                "capi.stackhpc.com/component": "worker",
                "capi.stackhpc.com/node-group": "nodegroup1",
            },
            "magnum-fakeproject",
        )

    @mock.patch.object(capi_monitor, "CAPIMonitor")
    def test_get_monitor(self, mock_mon):
        self.driver.get_monitor(self.context, self.cluster_obj)
        mock_mon.assert_called_once_with(self.context, self.cluster_obj)

    @mock.patch.object(kubernetes.Client, "load")
    def test_get_capi_cluster(self, mock_load):
        mock_client = mock.MagicMock(spec=kubernetes.Client)
        mock_load.return_value = mock_client

        self.driver._get_capi_cluster(self.cluster_obj)

        mock_client.get_capi_cluster.assert_called_once_with(
            "cluster-example-a-111111111111", "magnum-fakeproject"
        )

    @mock.patch.object(app_creds, "delete_app_cred")
    @mock.patch.object(kubernetes.Client, "load")
    def test_update_status_deleting(self, mock_load, mock_delete):
        mock_client = mock.MagicMock(spec=kubernetes.Client)
        mock_load.return_value = mock_client

        self.driver._update_status_deleting(self.context, self.cluster_obj)

        self.assertEqual("DELETE_COMPLETE", self.cluster_obj.status)
        mock_delete.assert_called_once_with(self.context, self.cluster_obj)
        mock_client.delete_all_secrets_by_label.assert_called_once_with(
            "magnum.openstack.org/cluster-uuid",
            self.cluster_obj.uuid,
            "magnum-fakeproject",
        )

    def test_update_status_updating_not_ready(self):
        self.cluster_obj.status = fields.ClusterStatus.CREATE_IN_PROGRESS
        capi_cluster = {}

        self.driver._update_status_updating(self.cluster_obj, capi_cluster)

        self.assertEqual(
            fields.ClusterStatus.CREATE_IN_PROGRESS, self.cluster_obj.status
        )

    @mock.patch.object(kubernetes.Client, "load")
    def test_update_status_updating_condition_false(self, mock_load):
        mock_client = mock.MagicMock(spec=kubernetes.Client)
        mock_client.get_addons_by_label.return_value = []
        mock_load.return_value = mock_client

        self.cluster_obj.status = fields.ClusterStatus.CREATE_IN_PROGRESS
        capi_cluster = {
            "status": {
                "conditions": [
                    dict(type="InfrastructureReady", status="True"),
                    dict(type="ControlPlaneReady", status="True"),
                    dict(type="Ready", status="False"),
                ]
            }
        }

        self.driver._update_status_updating(self.cluster_obj, capi_cluster)

        self.assertEqual(
            fields.ClusterStatus.CREATE_IN_PROGRESS, self.cluster_obj.status
        )

    @mock.patch.object(kubernetes.Client, "load")
    def test_update_status_updating_ready_created(self, mock_load):
        mock_client = mock.MagicMock(spec=kubernetes.Client)
        mock_client.get_addons_by_label.return_value = []
        mock_load.return_value = mock_client

        self.cluster_obj.status = fields.ClusterStatus.CREATE_IN_PROGRESS
        capi_cluster = {
            "status": {
                "conditions": [
                    dict(type="InfrastructureReady", status="True"),
                    dict(type="ControlPlaneReady", status="True"),
                    dict(type="Ready", status="True"),
                ]
            }
        }

        self.driver._update_status_updating(self.cluster_obj, capi_cluster)

        self.assertEqual(
            fields.ClusterStatus.CREATE_COMPLETE, self.cluster_obj.status
        )

    @mock.patch.object(kubernetes.Client, "load")
    def test_update_status_updating_addons_unknown(self, mock_load):
        mock_client = mock.MagicMock(spec=kubernetes.Client)
        mock_client.get_addons_by_label.return_value = [
            {
                "metadata": {"name": "cni"},
                "status": {},
            },
            {
                "metadata": {"name": "monitoring"},
                "status": {},
            },
        ]
        mock_load.return_value = mock_client

        self.cluster_obj.status = fields.ClusterStatus.CREATE_IN_PROGRESS
        capi_cluster = {
            "status": {
                "conditions": [
                    dict(type="InfrastructureReady", status="True"),
                    dict(type="ControlPlaneReady", status="True"),
                    dict(type="Ready", status="True"),
                ]
            }
        }

        self.driver._update_status_updating(self.cluster_obj, capi_cluster)

        self.assertEqual(
            fields.ClusterStatus.CREATE_IN_PROGRESS, self.cluster_obj.status
        )

    @mock.patch.object(kubernetes.Client, "load")
    def test_update_status_updating_addons_installing(self, mock_load):
        mock_client = mock.MagicMock(spec=kubernetes.Client)
        mock_client.get_addons_by_label.return_value = [
            {
                "metadata": {"name": "cni"},
                "status": {"phase": "Deployed"},
            },
            {
                "metadata": {"name": "monitoring"},
                "status": {"phase": "Installing"},
            },
        ]
        mock_load.return_value = mock_client

        self.cluster_obj.status = fields.ClusterStatus.CREATE_IN_PROGRESS
        capi_cluster = {
            "status": {
                "conditions": [
                    dict(type="InfrastructureReady", status="True"),
                    dict(type="ControlPlaneReady", status="True"),
                    dict(type="Ready", status="True"),
                ]
            }
        }

        self.driver._update_status_updating(self.cluster_obj, capi_cluster)

        self.assertEqual(
            fields.ClusterStatus.CREATE_IN_PROGRESS, self.cluster_obj.status
        )

    @mock.patch.object(kubernetes.Client, "load")
    def test_update_status_updating_addons_deployed(self, mock_load):
        mock_client = mock.MagicMock(spec=kubernetes.Client)
        mock_client.get_addons_by_label.return_value = [
            {
                "metadata": {"name": "cni"},
                "status": {"phase": "Deployed"},
            },
            {
                "metadata": {"name": "monitoring"},
                "status": {"phase": "Deployed"},
            },
        ]
        mock_load.return_value = mock_client

        self.cluster_obj.status = fields.ClusterStatus.CREATE_IN_PROGRESS
        capi_cluster = {
            "status": {
                "conditions": [
                    dict(type="InfrastructureReady", status="True"),
                    dict(type="ControlPlaneReady", status="True"),
                    dict(type="Ready", status="True"),
                ]
            }
        }

        self.driver._update_status_updating(self.cluster_obj, capi_cluster)

        self.assertEqual(
            fields.ClusterStatus.CREATE_COMPLETE, self.cluster_obj.status
        )

    @mock.patch.object(kubernetes.Client, "load")
    def test_update_status_updating_addons_failed(self, mock_load):
        mock_client = mock.MagicMock(spec=kubernetes.Client)
        mock_client.get_addons_by_label.return_value = [
            {
                "metadata": {"name": "cni"},
                "status": {"phase": "Deployed"},
            },
            {
                "metadata": {"name": "monitoring"},
                "status": {"phase": "Failed"},
            },
        ]
        mock_load.return_value = mock_client

        self.cluster_obj.status = fields.ClusterStatus.CREATE_IN_PROGRESS
        capi_cluster = {
            "status": {
                "conditions": [
                    dict(type="InfrastructureReady", status="True"),
                    dict(type="ControlPlaneReady", status="True"),
                    dict(type="Ready", status="True"),
                ]
            }
        }

        self.driver._update_status_updating(self.cluster_obj, capi_cluster)

        self.assertEqual(
            fields.ClusterStatus.CREATE_FAILED, self.cluster_obj.status
        )

    @mock.patch.object(kubernetes.Client, "load")
    def test_update_status_updating_ready_updated(self, mock_load):
        mock_client = mock.MagicMock(spec=kubernetes.Client)
        mock_client.get_addons_by_label.return_value = []
        mock_load.return_value = mock_client

        self.cluster_obj.status = fields.ClusterStatus.UPDATE_IN_PROGRESS
        capi_cluster = {
            "status": {
                "conditions": [
                    dict(type="InfrastructureReady", status="True"),
                    dict(type="ControlPlaneReady", status="True"),
                    dict(type="Ready", status="True"),
                ]
            }
        }

        self.driver._update_status_updating(self.cluster_obj, capi_cluster)

        self.assertEqual(
            fields.ClusterStatus.UPDATE_COMPLETE, self.cluster_obj.status
        )

    def test_update_cluster_api_address(self):
        capi_cluster = {
            "spec": {"controlPlaneEndpoint": {"host": "foo", "port": 6443}}
        }

        self.driver._update_cluster_api_address(self.cluster_obj, capi_cluster)

        self.assertEqual("https://foo:6443", self.cluster_obj.api_address)

    def test_update_cluster_api_address_skip(self):
        self.cluster_obj.api_address = "asdf"
        capi_cluster = {"spec": {"foo": "bar"}}

        self.driver._update_cluster_api_address(self.cluster_obj, capi_cluster)

        self.assertEqual("asdf", self.cluster_obj.api_address)

    def test_update_cluster_api_address_skip_on_delete(self):
        self.cluster_obj.status = fields.ClusterStatus.DELETE_IN_PROGRESS
        self.cluster_obj.api_address = "asdf"
        capi_cluster = {
            "spec": {"controlPlaneEndpoint": {"host": "foo", "port": 6443}}
        }

        self.driver._update_cluster_api_address(self.cluster_obj, capi_cluster)

        self.assertEqual("asdf", self.cluster_obj.api_address)

    def test_update_nodegroup_status_create_complete(self):
        nodegroup = obj_utils.create_test_nodegroup(self.context)
        nodegroup.status = fields.ClusterStatus.CREATE_IN_PROGRESS

        updated = self.driver._update_nodegroup_status(
            self.cluster_obj, nodegroup, driver.NodeGroupState.READY
        )

        self.assertEqual(fields.ClusterStatus.CREATE_COMPLETE, updated.status)

    def test_update_nodegroup_status_update_complete(self):
        nodegroup = obj_utils.create_test_nodegroup(self.context)
        nodegroup.status = fields.ClusterStatus.UPDATE_IN_PROGRESS

        updated = self.driver._update_nodegroup_status(
            self.cluster_obj, nodegroup, driver.NodeGroupState.READY
        )

        self.assertEqual(fields.ClusterStatus.UPDATE_COMPLETE, updated.status)

    def test_update_nodegroup_status_create_failed(self):
        nodegroup = obj_utils.create_test_nodegroup(self.context)
        nodegroup.status = fields.ClusterStatus.CREATE_IN_PROGRESS

        updated = self.driver._update_nodegroup_status(
            self.cluster_obj, nodegroup, driver.NodeGroupState.FAILED
        )

        self.assertEqual(fields.ClusterStatus.CREATE_FAILED, updated.status)

    def test_update_nodegroup_status_update_failed(self):
        nodegroup = obj_utils.create_test_nodegroup(self.context)
        nodegroup.status = fields.ClusterStatus.UPDATE_IN_PROGRESS

        updated = self.driver._update_nodegroup_status(
            self.cluster_obj, nodegroup, driver.NodeGroupState.FAILED
        )

        self.assertEqual(fields.ClusterStatus.UPDATE_FAILED, updated.status)

    def test_update_nodegroup_status_create_in_progress(self):
        nodegroup = obj_utils.create_test_nodegroup(self.context)
        nodegroup.status = fields.ClusterStatus.CREATE_IN_PROGRESS

        updated = self.driver._update_nodegroup_status(
            self.cluster_obj, nodegroup, driver.NodeGroupState.PENDING
        )

        self.assertEqual(
            fields.ClusterStatus.CREATE_IN_PROGRESS, updated.status
        )

    def test_update_nodegroup_status_delete_in_progress(self):
        nodegroup = obj_utils.create_test_nodegroup(self.context)
        nodegroup.status = fields.ClusterStatus.DELETE_IN_PROGRESS

        updated = self.driver._update_nodegroup_status(
            self.cluster_obj, nodegroup, driver.NodeGroupState.PENDING
        )

        self.assertEqual(
            fields.ClusterStatus.DELETE_IN_PROGRESS, updated.status
        )
        self.assertEqual(nodegroup.as_dict(), updated.as_dict())

    def test_update_nodegroup_creating_but_not_found(self):
        nodegroup = obj_utils.create_test_nodegroup(self.context)
        nodegroup.status = fields.ClusterStatus.CREATE_IN_PROGRESS

        updated = self.driver._update_nodegroup_status(
            self.cluster_obj, nodegroup, driver.NodeGroupState.NOT_PRESENT
        )

        self.assertEqual(
            fields.ClusterStatus.CREATE_IN_PROGRESS, updated.status
        )

    def test_update_nodegroup_status_delete_return_none(self):
        nodegroup = obj_utils.create_test_nodegroup(self.context)
        nodegroup.status = fields.ClusterStatus.DELETE_IN_PROGRESS

        result = self.driver._update_nodegroup_status(
            self.cluster_obj, nodegroup, driver.NodeGroupState.NOT_PRESENT
        )

        self.assertIsNone(result)

    def test_update_nodegroup_status_delete_non_default_destroy(self):
        nodegroup = mock.MagicMock()
        nodegroup.status = fields.ClusterStatus.DELETE_IN_PROGRESS
        nodegroup.is_default = False

        result = self.driver._update_nodegroup_status(
            self.cluster_obj, nodegroup, driver.NodeGroupState.NOT_PRESENT
        )

        self.assertIsNone(result)
        nodegroup.destroy.assert_called_once_with()

    def test_update_nodegroup_status_delete_unexpected_state(self):
        nodegroup = obj_utils.create_test_nodegroup(self.context)
        nodegroup.status = fields.ClusterStatus.ROLLBACK_IN_PROGRESS

        updated = self.driver._update_nodegroup_status(
            self.cluster_obj, nodegroup, driver.NodeGroupState.NOT_PRESENT
        )

        self.assertEqual(
            fields.ClusterStatus.ROLLBACK_IN_PROGRESS, updated.status
        )
        self.assertEqual(nodegroup.as_dict(), updated.as_dict())

    def test_namespace(self):
        self.cluster_obj.project_id = "123-456F"

        namespace = driver_utils.cluster_namespace(self.cluster_obj)

        self.assertEqual("magnum-123456f", namespace)

    def test_label_return_default(self):
        self.cluster_obj.labels = dict()
        self.cluster_obj.cluster_template.labels = dict()

        result = self.driver._label(self.cluster_obj, "foo", "bar")

        self.assertEqual("bar", result)

    def test_label_return_template(self):
        self.cluster_obj.cluster_template.labels = dict(foo=42)

        result = self.driver._label(self.cluster_obj, "foo", "bar")

        self.assertEqual("42", result)

    def test_label_return_cluster(self):
        self.cluster_obj.labels = dict(foo=41)
        self.cluster_obj.cluster_template.labels = dict(foo=42)

        result = self.driver._label(self.cluster_obj, "foo", "bar")

        self.assertEqual("41", result)

    def test_sanitized_name_no_suffix(self):
        self.assertEqual(
            "123-456fab", driver_utils.sanitized_name("123-456Fab")
        )

    def test_sanitized_name_with_suffix(self):
        self.assertEqual(
            "123-456-fab-1-asdf",
            driver_utils.sanitized_name("123-456_Fab!!_1!!", "asdf"),
        )
        self.assertEqual(
            "123-456-fab-1-asdf",
            driver_utils.sanitized_name("123-456_Fab-1", "asdf"),
        )

    def test_get_kube_version_raises(self):
        mock_image = mock.Mock()
        mock_image.get.return_value = None
        mock_image.id = "myid"

        e = self.assertRaises(
            exception.MagnumException,
            self.driver._get_kube_version,
            mock_image,
        )

        self.assertEqual(
            "Image myid does not have a kube_version property.", str(e)
        )
        mock_image.get.assert_called_once_with("kube_version")

    def test_get_kube_version_works(self):
        mock_image = mock.Mock()
        mock_image.get.return_value = "v1.27.9"

        result = self.driver._get_kube_version(mock_image)

        self.assertEqual("1.27.9", result)
        mock_image.get.assert_called_once_with("kube_version")

    @mock.patch("magnum.common.clients.OpenStackClients", autospec=True)
    @mock.patch("magnum.api.utils.get_openstack_resource", autospec=True)
    def test_get_image_details_ubuntu(self, mock_get, mock_osc):
        mock_image = mock.Mock()
        image_metadata = {
            "os_distro": "ubuntu",
            "kube_version": "1.27.9",
        }

        def image_side_effect(arg):
            return image_metadata[arg]

        mock_image.get.side_effect = image_side_effect
        mock_image.id = "myid"
        mock_get.return_value = mock_image

        id, version, distro = self.driver._get_image_details(
            self.context, "myimagename"
        )

        self.assertEqual("1.27.9", version)
        self.assertEqual("myid", id)
        self.assertEqual("ubuntu", distro)
        mock_image.get.assert_any_call("kube_version")
        mock_image.get.assert_any_call("os_distro")
        mock_get.assert_called_once_with(mock.ANY, "myimagename", "images")

    @mock.patch("magnum.common.clients.OpenStackClients", autospec=True)
    @mock.patch("magnum.api.utils.get_openstack_resource", autospec=True)
    def test_get_image_details_flatcar(self, mock_get, mock_osc):
        mock_image = mock.Mock()
        image_metadata = {
            "os_distro": "flatcar",
            "kube_version": "1.28.2",
        }

        def image_side_effect(arg):
            return image_metadata[arg]

        mock_image.get.side_effect = image_side_effect
        mock_image.id = "myid-flatcar"
        mock_get.return_value = mock_image

        id, version, distro = self.driver._get_image_details(
            self.context, "myimagename"
        )

        self.assertEqual("1.28.2", version)
        self.assertEqual("myid-flatcar", id)
        self.assertEqual("flatcar", distro)
        mock_image.get.assert_any_call("kube_version")
        mock_image.get.assert_any_call("os_distro")
        mock_get.assert_called_once_with(mock.ANY, "myimagename", "images")

    def test_get_chart_release_name_length(self):
        self.cluster_obj.stack_id = "foo"

        result = driver_utils.chart_release_name(self.cluster_obj)

        self.assertEqual("foo", result)

    def test_generate_release_name_skip(self):
        self.cluster_obj.stack_id = "foo"
        self.driver._generate_release_name(self.cluster_obj)
        self.assertEqual("foo", self.cluster_obj.stack_id)

    def test_generate_release_name_generates(self):
        self.cluster_obj.stack_id = None
        self.cluster_obj.name = "a" * 77

        self.driver._generate_release_name(self.cluster_obj)
        first = self.cluster_obj.stack_id

        self.assertEqual(43, len(first))
        self.assertTrue(self.cluster_obj.name[:30] in first)

        self.cluster_obj.stack_id = None
        self.driver._generate_release_name(self.cluster_obj)
        second = self.cluster_obj.stack_id

        self.assertNotEqual(first, second)
        self.assertEqual(43, len(second))
        self.assertTrue(self.cluster_obj.name[:30] in second)

    def test_get_monitoring_enabled_from_template(self):

        # Check default if label is not present
        self.assertFalse(self.driver._get_monitoring_enabled(self.cluster_obj))

        for val in ["true", "True", "TRUE"]:

            self.cluster_obj.cluster_template.labels["monitoring_enabled"] = (
                val
            )

            result = self.driver._get_monitoring_enabled(self.cluster_obj)

            self.assertTrue(result)

    def test_get_kube_dash_enabled_from_template(self):
        # Check default if label is not present
        self.assertTrue(self.driver._get_kube_dash_enabled(self.cluster_obj))

        for val in ["false", "False", "FALSE"]:

            self.cluster_obj.cluster_template.labels[
                "kube_dashboard_enabled"
            ] = val

            result = self.driver._get_kube_dash_enabled(self.cluster_obj)

            self.assertFalse(result)

    def test_get_chart_version_from_config(self):
        version = self.driver._get_chart_version(self.cluster_obj)

        self.assertEqual(CONF.capi_helm.default_helm_chart_version, version)

    def test_get_chart_version_from_template(self):
        self.cluster_obj.cluster_template.labels["capi_helm_chart_version"] = (
            "1.42.0"
        )

        version = self.driver._get_chart_version(self.cluster_obj)

        self.assertEqual("1.42.0", version)

    def _get_cluster_helm_standard_values(self):
        """Return standard helm values which can be modified for tests.

        There is little point in multiple tests writing the same dictionary
        that contains all aspects of a cluster when the side effect they are
        testing is limited to boot volumes, or keypairs.
        """
        app_cred_name = "cluster-example-a-111111111111-cloud-credentials"
        ext_net_id = self.cluster_obj.cluster_template.external_network_id

        return {
            "kubernetesVersion": "1.27.4",
            "machineImageId": "imageid1",
            "cloudCredentialsSecretName": app_cred_name,
            "clusterNetworking": {
                "externalNetworkId": ext_net_id,
                "internalNetwork": {
                    "networkFilter": None,
                    "subnetFilter": None,
                    "nodeCidr": "10.0.0.0/24",
                },
                "dnsNameservers": ["8.8.1.1"],
            },
            "etcd": {},
            "apiServer": {
                "enableLoadBalancer": True,
                "loadBalancerProvider": "amphora",
            },
            "controlPlane": {
                "machineFlavor": "flavor_small",
                "machineCount": 3,
                "healthCheck": {"enabled": True},
            },
            "addons": {
                "monitoring": {"enabled": False},
                "kubernetesDashboard": {"enabled": True},
                "ingress": {"enabled": False},
                "openstack": {
                    "csiCinder": mock.ANY,
                    "cloudConfig": {
                        "LoadBalancer": {
                            "lb-provider": "amphora",
                            "lb-method": "ROUND_ROBIN",
                            "create-monitor": True,
                        },
                    },
                },
            },
            "nodeGroups": [
                {
                    "name": "test-worker",
                    "machineFlavor": "flavor_medium",
                    "machineCount": 3,
                },
            ],
            "osDistro": "ubuntu",
            "nodeGroupDefaults": {
                "healthCheck": {"enabled": True},
            },
            "machineSSHKeyName": None,
        }

    @mock.patch.object(driver.Driver, "_get_allowed_cidrs")
    @mock.patch.object(
        driver.Driver, "_get_k8s_keystone_auth_enabled", return_value=False
    )
    @mock.patch.object(
        driver.Driver,
        "_storageclass_definitions",
        return_value=mock.ANY,
    )
    @mock.patch.object(driver.Driver, "_validate_allowed_flavor")
    @mock.patch.object(neutron, "get_network", autospec=True)
    @mock.patch.object(
        driver.Driver, "_ensure_certificate_secrets", autospec=True
    )
    @mock.patch.object(driver.Driver, "_create_appcred_secret", autospec=True)
    @mock.patch.object(kubernetes.Client, "load", autospec=True)
    @mock.patch.object(driver.Driver, "_get_image_details", autospec=True)
    @mock.patch.object(helm.Client, "install_or_upgrade", autospec=True)
    def test_create_cluster(
        self,
        mock_install,
        mock_image,
        mock_load,
        mock_appcred,
        mock_certs,
        mock_get_net,
        mock_validate_allowed_flavor,
        mock_storageclasses,
        mock_get_keystone_auth_enabled,
        mock_get_allowed_cidrs,
    ):
        mock_image.return_value = (
            "imageid1",
            "1.27.4",
            "ubuntu",
        )
        mock_client = mock.MagicMock(spec=kubernetes.Client)
        mock_load.return_value = mock_client
        mock_get_net.side_effect = (
            lambda c, net, source, target, external: f"{net}-{external}"
        )

        self.driver.create_cluster(self.context, self.cluster_obj, 10)

        expected_values = self._get_cluster_helm_standard_values()

        mock_install.assert_called_once_with(
            self.driver._helm_client,
            "cluster-example-a-111111111111",
            "openstack-cluster",
            mock.ANY,  # NOTE(dalees): Compared separately for improved diff
            repo=CONF.capi_helm.helm_chart_repo,
            version=CONF.capi_helm.default_helm_chart_version,
            namespace="magnum-fakeproject",
        )

        helm_install_values = mock_install.call_args[0][3]
        self.assertDictEqual(helm_install_values, expected_values)

        mock_client.ensure_namespace.assert_called_once_with(
            "magnum-fakeproject"
        )
        mock_appcred.assert_called_once_with(
            self.driver, self.context, self.cluster_obj
        )
        mock_certs.assert_called_once_with(
            self.driver, self.context, self.cluster_obj
        )
        self.assertEqual([], mock_get_net.call_args_list)

    @mock.patch.object(driver.Driver, "_get_allowed_cidrs")
    @mock.patch.object(
        driver.Driver, "_get_k8s_keystone_auth_enabled", return_value=False
    )
    @mock.patch.object(
        driver.Driver,
        "_storageclass_definitions",
        return_value=mock.ANY,
    )
    @mock.patch.object(driver.Driver, "_validate_allowed_flavor")
    @mock.patch.object(
        driver.Driver, "_ensure_certificate_secrets", autospec=True
    )
    @mock.patch.object(driver.Driver, "_create_appcred_secret", autospec=True)
    @mock.patch.object(kubernetes.Client, "load", autospec=True)
    @mock.patch.object(driver.Driver, "_get_image_details", autospec=True)
    @mock.patch.object(helm.Client, "install_or_upgrade", autospec=True)
    def test_create_cluster_no_dns(
        self,
        mock_install,
        mock_image,
        mock_load,
        mock_appcred,
        mock_certs,
        mock_validate_allowed_flavor,
        mock_storageclasses,
        mock_get_keystone_auth_enabled,
        mock_get_allowed_cidrs,
    ):
        mock_image.return_value = ("imageid1", "1.27.4", "ubuntu")
        mock_client = mock.MagicMock(spec=kubernetes.Client)
        mock_load.return_value = mock_client

        self.cluster_obj.cluster_template.dns_nameserver = ""

        self.driver.create_cluster(self.context, self.cluster_obj, 10)

        expected_values = self._get_cluster_helm_standard_values()
        expected_values["clusterNetworking"]["dnsNameservers"] = None

        mock_install.assert_called_once_with(
            self.driver._helm_client,
            "cluster-example-a-111111111111",
            "openstack-cluster",
            mock.ANY,
            repo=CONF.capi_helm.helm_chart_repo,
            version=CONF.capi_helm.default_helm_chart_version,
            namespace="magnum-fakeproject",
        )

        helm_install_values = mock_install.call_args[0][3]
        self.assertDictEqual(helm_install_values, expected_values)

        mock_client.ensure_namespace.assert_called_once_with(
            "magnum-fakeproject"
        )
        mock_appcred.assert_called_once_with(
            self.driver, self.context, self.cluster_obj
        )
        mock_certs.assert_called_once_with(
            self.driver, self.context, self.cluster_obj
        )

    @mock.patch.object(driver.Driver, "_get_allowed_cidrs")
    @mock.patch.object(
        driver.Driver, "_get_k8s_keystone_auth_enabled", return_value=False
    )
    @mock.patch.object(
        driver.Driver,
        "_storageclass_definitions",
        return_value=mock.ANY,
    )
    @mock.patch.object(driver.Driver, "_validate_allowed_flavor")
    @mock.patch.object(
        driver.Driver, "_ensure_certificate_secrets", autospec=True
    )
    @mock.patch.object(driver.Driver, "_create_appcred_secret", autospec=True)
    @mock.patch.object(kubernetes.Client, "load", autospec=True)
    @mock.patch.object(driver.Driver, "_get_image_details", autospec=True)
    @mock.patch.object(helm.Client, "install_or_upgrade", autospec=True)
    def test_create_cluster_boot_volume(
        self,
        mock_install,
        mock_image,
        mock_load,
        mock_appcred,
        mock_certs,
        mock_validate_allowed_flavor,
        mock_storageclasses,
        mock_get_keystone_auth_enabled,
        mock_get_allowed_cidrs,
    ):
        mock_image.return_value = ("imageid1", "1.27.4", "ubuntu")
        mock_client = mock.MagicMock(spec=kubernetes.Client)
        mock_load.return_value = mock_client

        CONF.cinder.default_boot_volume_type = "nvme"
        CONF.cinder.default_boot_volume_size = 12

        self.driver.create_cluster(self.context, self.cluster_obj, 10)

        # Get standard values and modify them to match this test
        expected_values = self._get_cluster_helm_standard_values()
        expected_values["controlPlane"]["machineRootVolume"] = {
            "volumeType": "nvme",
            "diskSize": 12,
        }
        expected_values["nodeGroupDefaults"]["machineRootVolume"] = {
            "volumeType": "nvme",
            "diskSize": 12,
        }

        mock_install.assert_called_once_with(
            self.driver._helm_client,
            "cluster-example-a-111111111111",
            "openstack-cluster",
            mock.ANY,
            repo=CONF.capi_helm.helm_chart_repo,
            version=CONF.capi_helm.default_helm_chart_version,
            namespace="magnum-fakeproject",
        )

        helm_install_values = mock_install.call_args[0][3]
        self.assertDictEqual(helm_install_values, expected_values)

        mock_client.ensure_namespace.assert_called_once_with(
            "magnum-fakeproject"
        )
        mock_appcred.assert_called_once_with(
            self.driver, self.context, self.cluster_obj
        )
        mock_certs.assert_called_once_with(
            self.driver, self.context, self.cluster_obj
        )

    @mock.patch.object(driver.Driver, "_get_allowed_cidrs")
    @mock.patch.object(
        driver.Driver, "_get_k8s_keystone_auth_enabled", return_value=False
    )
    @mock.patch.object(
        driver.Driver,
        "_storageclass_definitions",
        return_value=mock.ANY,
    )
    @mock.patch.object(driver.Driver, "_validate_allowed_flavor")
    @mock.patch.object(
        driver.Driver, "_ensure_certificate_secrets", autospec=True
    )
    @mock.patch.object(driver.Driver, "_create_appcred_secret", autospec=True)
    @mock.patch.object(kubernetes.Client, "load", autospec=True)
    @mock.patch.object(driver.Driver, "_get_image_details", autospec=True)
    @mock.patch.object(helm.Client, "install_or_upgrade", autospec=True)
    def test_create_cluster_boot_volume_extra_network(
        self,
        mock_install,
        mock_image,
        mock_load,
        mock_appcred,
        mock_certs,
        mock_validate_allowed_flavor,
        mock_storageclasses,
        mock_get_keystone_auth_enabled,
        mock_get_allowed_cidrs,
    ):
        mock_image.return_value = (
            "imageid1",
            "1.27.4",
            "ubuntu",
        )
        mock_client = mock.MagicMock(spec=kubernetes.Client)
        mock_load.return_value = mock_client

        CONF.cinder.default_boot_volume_type = "nvme"
        CONF.cinder.default_boot_volume_size = 12
        # Driver should combine boot volume with extra network.
        self.cluster_obj.cluster_template.labels["extra_network_name"] = "foo"

        self.driver.create_cluster(self.context, self.cluster_obj, 10)

        expected_values = self._get_cluster_helm_standard_values()
        expected_values["controlPlane"]["machineRootVolume"] = {
            "volumeType": "nvme",
            "diskSize": 12,
        }
        expected_values["nodeGroupDefaults"]["machineRootVolume"] = {
            "volumeType": "nvme",
            "diskSize": 12,
        }
        expected_values["nodeGroupDefaults"]["machineNetworking"] = {
            "ports": [
                {},
                {
                    "network": {
                        "name": "foo",
                    },
                    "securityGroups": [],
                },
            ],
        }

        mock_install.assert_called_once_with(
            self.driver._helm_client,
            "cluster-example-a-111111111111",
            "openstack-cluster",
            mock.ANY,
            repo=CONF.capi_helm.helm_chart_repo,
            version=CONF.capi_helm.default_helm_chart_version,
            namespace="magnum-fakeproject",
        )

        helm_install_values = mock_install.call_args[0][3]
        self.assertDictEqual(helm_install_values, expected_values)

        mock_client.ensure_namespace.assert_called_once_with(
            "magnum-fakeproject"
        )
        mock_appcred.assert_called_once_with(
            self.driver, self.context, self.cluster_obj
        )
        mock_certs.assert_called_once_with(
            self.driver, self.context, self.cluster_obj
        )

    @mock.patch.object(driver.Driver, "_get_allowed_cidrs")
    @mock.patch.object(
        driver.Driver, "_get_k8s_keystone_auth_enabled", return_value=False
    )
    @mock.patch.object(
        driver.Driver,
        "_storageclass_definitions",
        return_value=mock.ANY,
    )
    @mock.patch.object(driver.Driver, "_validate_allowed_flavor")
    @mock.patch.object(driver.Driver, "_ensure_certificate_secrets")
    @mock.patch.object(driver.Driver, "_create_appcred_secret")
    @mock.patch.object(kubernetes.Client, "load")
    @mock.patch.object(driver.Driver, "_get_image_details")
    @mock.patch.object(helm.Client, "install_or_upgrade")
    def test_create_cluster_with_keypair(
        self,
        mock_install,
        mock_image,
        mock_load,
        mock_appcred,
        mock_certs,
        mock_validate_allowed_flavor,
        mock_storageclasses,
        mock_get_keystone_auth_enabled,
        mock_get_allowed_cidrs,
    ):
        mock_image.return_value = (
            "imageid1",
            "1.27.4",
            "ubuntu",
        )
        mock_client = mock.MagicMock(spec=kubernetes.Client)
        mock_load.return_value = mock_client

        self.cluster_obj.keypair = "kp1"

        self.driver.create_cluster(self.context, self.cluster_obj, 10)

        expected_values = self._get_cluster_helm_standard_values()
        expected_values["machineSSHKeyName"] = "kp1"

        mock_install.assert_called_once_with(
            "cluster-example-a-111111111111",
            "openstack-cluster",
            mock.ANY,
            repo=CONF.capi_helm.helm_chart_repo,
            version=CONF.capi_helm.default_helm_chart_version,
            namespace="magnum-fakeproject",
        )
        helm_install_values = mock_install.call_args[0][2]
        self.assertDictEqual(helm_install_values, expected_values)

        mock_client.ensure_namespace.assert_called_once_with(
            "magnum-fakeproject"
        )
        mock_appcred.assert_called_once_with(self.context, self.cluster_obj)
        mock_certs.assert_called_once_with(self.context, self.cluster_obj)

    @mock.patch.object(driver.Driver, "_get_allowed_cidrs")
    @mock.patch.object(
        driver.Driver, "_get_k8s_keystone_auth_enabled", return_value=False
    )
    @mock.patch.object(
        driver.Driver,
        "_storageclass_definitions",
        return_value=mock.ANY,
    )
    @mock.patch.object(driver.Driver, "_validate_allowed_flavor")
    @mock.patch.object(driver.Driver, "_ensure_certificate_secrets")
    @mock.patch.object(driver.Driver, "_create_appcred_secret")
    @mock.patch.object(kubernetes.Client, "load")
    @mock.patch.object(driver.Driver, "_get_image_details")
    @mock.patch.object(helm.Client, "install_or_upgrade")
    def test_create_cluster_flatcar(
        self,
        mock_install,
        mock_image,
        mock_load,
        mock_appcred,
        mock_certs,
        mock_validate_allowed_flavor,
        mock_storageclasses,
        mock_get_keystone_auth_enabled,
        mock_get_allowed_cidrs,
    ):
        mock_image.return_value = (
            "imageid1",
            "1.27.4",
            "flatcar",
        )
        mock_client = mock.MagicMock(spec=kubernetes.Client)
        mock_load.return_value = mock_client

        self.driver.create_cluster(self.context, self.cluster_obj, 10)

        expected_values = self._get_cluster_helm_standard_values()
        expected_values["osDistro"] = "flatcar"

        mock_install.assert_called_once_with(
            "cluster-example-a-111111111111",
            "openstack-cluster",
            expected_values,
            repo=CONF.capi_helm.helm_chart_repo,
            version=CONF.capi_helm.default_helm_chart_version,
            namespace="magnum-fakeproject",
        )

        mock_client.ensure_namespace.assert_called_once_with(
            "magnum-fakeproject"
        )
        mock_appcred.assert_called_once_with(self.context, self.cluster_obj)
        mock_certs.assert_called_once_with(self.context, self.cluster_obj)

    @mock.patch.object(driver.Driver, "_get_allowed_cidrs")
    @mock.patch.object(
        driver.Driver, "_get_k8s_keystone_auth_enabled", return_value=False
    )
    @mock.patch.object(
        driver.Driver,
        "_storageclass_definitions",
        return_value=mock.ANY,
    )
    @mock.patch.object(driver.Driver, "_validate_allowed_flavor")
    @mock.patch.object(
        driver.Driver, "_ensure_certificate_secrets", autospec=True
    )
    @mock.patch.object(driver.Driver, "_create_appcred_secret", autospec=True)
    @mock.patch.object(kubernetes.Client, "load", autospec=True)
    @mock.patch.object(driver.Driver, "_get_image_details", autospec=True)
    @mock.patch.object(helm.Client, "install_or_upgrade", autospec=True)
    def test_create_cluster_no_autoheal(
        self,
        mock_install,
        mock_image,
        mock_load,
        mock_appcred,
        mock_certs,
        mock_validate_allowed_flavor,
        mock_storageclasses,
        mock_get_keystone_auth_enabled,
        mock_get_allowed_cidrs,
    ):
        mock_image.return_value = ("imageid1", "1.27.4", "ubuntu")
        mock_client = mock.MagicMock(spec=kubernetes.Client)
        mock_load.return_value = mock_client

        self.cluster_obj.cluster_template.labels["auto_healing_enabled"] = (
            "false"
        )

        self.driver.create_cluster(self.context, self.cluster_obj, 10)

        # Get standard values and modify them to match this test
        expected_values = self._get_cluster_helm_standard_values()
        expected_values["controlPlane"]["healthCheck"]["enabled"] = False
        expected_values["nodeGroupDefaults"]["healthCheck"]["enabled"] = False

        mock_install.assert_called_once_with(
            self.driver._helm_client,
            "cluster-example-a-111111111111",
            "openstack-cluster",
            mock.ANY,
            repo=CONF.capi_helm.helm_chart_repo,
            version=CONF.capi_helm.default_helm_chart_version,
            namespace="magnum-fakeproject",
        )
        helm_install_values = mock_install.call_args[0][3]
        self.assertDictEqual(helm_install_values, expected_values)

        mock_client.ensure_namespace.assert_called_once_with(
            "magnum-fakeproject"
        )
        mock_appcred.assert_called_once_with(
            self.driver, self.context, self.cluster_obj
        )
        mock_certs.assert_called_once_with(
            self.driver, self.context, self.cluster_obj
        )

    @mock.patch.object(driver.Driver, "_get_allowed_cidrs")
    @mock.patch.object(
        driver.Driver, "_get_k8s_keystone_auth_enabled", return_value=False
    )
    @mock.patch.object(
        driver.Driver,
        "_storageclass_definitions",
        return_value=mock.ANY,
    )
    @mock.patch.object(driver.Driver, "_validate_allowed_flavor")
    @mock.patch.object(
        driver.Driver, "_ensure_certificate_secrets", autospec=True
    )
    @mock.patch.object(driver.Driver, "_create_appcred_secret", autospec=True)
    @mock.patch.object(kubernetes.Client, "load", autospec=True)
    @mock.patch.object(driver.Driver, "_get_image_details", autospec=True)
    @mock.patch.object(helm.Client, "install_or_upgrade", autospec=True)
    def test_create_cluster_etcd_block_device(
        self,
        mock_install,
        mock_image,
        mock_load,
        mock_appcred,
        mock_certs,
        mock_validate_allowed_flavor,
        mock_storageclasses,
        mock_get_keystone_auth_enabled,
        mock_get_allowed_cidrs,
    ):
        mock_image.return_value = ("imageid1", "1.27.4", "ubuntu")
        mock_client = mock.MagicMock(spec=kubernetes.Client)
        mock_load.return_value = mock_client

        self.cluster_obj.cluster_template.labels.update(
            {
                "etcd_blockdevice_size": "10",
                "etcd_blockdevice_volume_type": "nvme",
            }
        )

        self.driver.create_cluster(self.context, self.cluster_obj, 10)

        # Get standard values and modify them to match this test
        expected_values = self._get_cluster_helm_standard_values()
        expected_values["etcd"] = {
            "blockDevice": {
                "size": 10,
                "type": "Volume",
                "volumeType": "nvme",
            }
        }

        mock_install.assert_called_once_with(
            self.driver._helm_client,
            "cluster-example-a-111111111111",
            "openstack-cluster",
            mock.ANY,
            repo=CONF.capi_helm.helm_chart_repo,
            version=CONF.capi_helm.default_helm_chart_version,
            namespace="magnum-fakeproject",
        )

        helm_install_values = mock_install.call_args[0][3]
        self.assertDictEqual(helm_install_values, expected_values)

        mock_client.ensure_namespace.assert_called_once_with(
            "magnum-fakeproject"
        )
        mock_appcred.assert_called_once_with(
            self.driver, self.context, self.cluster_obj
        )
        mock_certs.assert_called_once_with(
            self.driver, self.context, self.cluster_obj
        )

    @mock.patch.object(driver.Driver, "_get_allowed_cidrs")
    @mock.patch.object(
        driver.Driver, "_get_k8s_keystone_auth_enabled", return_value=False
    )
    @mock.patch.object(
        driver.Driver,
        "_storageclass_definitions",
        return_value=mock.ANY,
    )
    @mock.patch.object(driver.Driver, "_validate_allowed_flavor")
    @mock.patch.object(
        driver.Driver, "_ensure_certificate_secrets", autospec=True
    )
    @mock.patch.object(driver.Driver, "_create_appcred_secret", autospec=True)
    @mock.patch.object(kubernetes.Client, "load", autospec=True)
    @mock.patch.object(driver.Driver, "_get_image_details", autospec=True)
    @mock.patch.object(helm.Client, "install_or_upgrade", autospec=True)
    def test_create_cluster_etcd_block_device_local(
        self,
        mock_install,
        mock_image,
        mock_load,
        mock_appcred,
        mock_certs,
        mock_validate_allowed_flavor,
        mock_storageclasses,
        mock_get_keystone_auth_enabled,
        mock_get_allowed_cidrs,
    ):
        mock_image.return_value = ("imageid1", "1.27.4", "ubuntu")
        mock_client = mock.MagicMock(spec=kubernetes.Client)
        mock_load.return_value = mock_client

        self.cluster_obj.cluster_template.labels.update(
            {
                "etcd_blockdevice_size": "10",
                "etcd_blockdevice_type": "local",
            }
        )

        self.driver.create_cluster(self.context, self.cluster_obj, 10)

        # Get standard values and modify them to match this test
        expected_values = self._get_cluster_helm_standard_values()
        expected_values["etcd"] = {
            "blockDevice": {
                "size": 10,
                "type": "Local",
            }
        }

        mock_install.assert_called_once_with(
            self.driver._helm_client,
            "cluster-example-a-111111111111",
            "openstack-cluster",
            mock.ANY,
            repo=CONF.capi_helm.helm_chart_repo,
            version=CONF.capi_helm.default_helm_chart_version,
            namespace="magnum-fakeproject",
        )

        helm_install_values = mock_install.call_args[0][3]
        self.assertDictEqual(helm_install_values, expected_values)

        mock_client.ensure_namespace.assert_called_once_with(
            "magnum-fakeproject"
        )
        mock_appcred.assert_called_once_with(
            self.driver, self.context, self.cluster_obj
        )
        mock_certs.assert_called_once_with(
            self.driver, self.context, self.cluster_obj
        )

    @mock.patch.object(driver.Driver, "_get_allowed_cidrs")
    @mock.patch.object(
        driver.Driver, "_get_k8s_keystone_auth_enabled", return_value=False
    )
    @mock.patch.object(
        driver.Driver,
        "_storageclass_definitions",
        return_value=mock.ANY,
    )
    @mock.patch.object(driver.Driver, "_validate_allowed_flavor")
    @mock.patch.object(
        driver.Driver, "_ensure_certificate_secrets", autospec=True
    )
    @mock.patch.object(driver.Driver, "_create_appcred_secret", autospec=True)
    @mock.patch.object(kubernetes.Client, "load", autospec=True)
    @mock.patch.object(driver.Driver, "_get_image_details", autospec=True)
    @mock.patch.object(helm.Client, "install_or_upgrade", autospec=True)
    def test_create_cluster_etcd_block_device_legacy_labels(
        self,
        mock_install,
        mock_image,
        mock_load,
        mock_appcred,
        mock_certs,
        mock_validate_allowed_flavor,
        mock_storageclasses,
        mock_get_keystone_auth_enabled,
        mock_get_allowed_cidrs,
    ):
        mock_image.return_value = ("imageid1", "1.27.4", "ubuntu")
        mock_client = mock.MagicMock(spec=kubernetes.Client)
        mock_load.return_value = mock_client

        # Test the legacy labels for etcd volume size
        self.cluster_obj.cluster_template.labels.update(
            {
                "etcd_volume_size": "10",
                "etcd_volume_type": "nvme",
            }
        )

        self.driver.create_cluster(self.context, self.cluster_obj, 10)

        # Get standard values and modify them to match this test
        expected_values = self._get_cluster_helm_standard_values()
        expected_values["etcd"] = {
            "blockDevice": {
                "size": 10,
                "type": "Volume",
                "volumeType": "nvme",
            }
        }

        mock_install.assert_called_once_with(
            self.driver._helm_client,
            "cluster-example-a-111111111111",
            "openstack-cluster",
            mock.ANY,
            repo=CONF.capi_helm.helm_chart_repo,
            version=CONF.capi_helm.default_helm_chart_version,
            namespace="magnum-fakeproject",
        )

        helm_install_values = mock_install.call_args[0][3]
        self.assertDictEqual(helm_install_values, expected_values)

        mock_client.ensure_namespace.assert_called_once_with(
            "magnum-fakeproject"
        )
        mock_appcred.assert_called_once_with(
            self.driver, self.context, self.cluster_obj
        )
        mock_certs.assert_called_once_with(
            self.driver, self.context, self.cluster_obj
        )

    @mock.patch.object(app_creds, "get_app_cred_string_data")
    @mock.patch.object(kubernetes.Client, "load")
    def test_create_appcred_secret(self, mock_load, mock_sd):
        mock_client = mock.MagicMock(spec=kubernetes.Client)
        mock_load.return_value = mock_client
        mock_sd.return_value = {"cacert": "ca", "clouds.yaml": "appcred"}

        self.driver._create_appcred_secret(self.context, self.cluster_obj)

        uuid = self.cluster_obj.uuid
        name = "cluster-example-a-111111111111"
        mock_client.apply_secret.assert_called_once_with(
            "cluster-example-a-111111111111-cloud-credentials",
            {
                "metadata": {
                    "labels": {
                        "magnum.openstack.org/project-id": "fake_project",
                        "magnum.openstack.org/user-id": "fake_user",
                        "magnum.openstack.org/cluster-uuid": uuid,
                        "cluster.x-k8s.io/cluster-name": name,
                    }
                },
                "stringData": {"cacert": "ca", "clouds.yaml": "appcred"},
            },
            "magnum-fakeproject",
        )

    @mock.patch.object(ca_certificates, "get_certificate_string_data")
    @mock.patch.object(driver.Driver, "_k8s_resource_labels")
    @mock.patch.object(kubernetes.Client, "load")
    def test_ensure_certificate_secrets(
        self, mock_load, mock_labels, mock_string_data
    ):
        mock_client = mock.MagicMock(spec=kubernetes.Client)
        mock_load.return_value = mock_client
        mock_labels.return_value = dict(foo="bar")
        mock_string_data.return_value = {
            "ca": {"tls.crt": "cert1", "tls.key": "key1"},
            "proxy": {"tls.crt": "cert2", "tls.key": "key2"},
        }

        self.driver._ensure_certificate_secrets(self.context, self.cluster_obj)

        mock_client.apply_secret.assert_has_calls(
            [
                mock.call(
                    "cluster-example-a-111111111111-ca",
                    {
                        "metadata": {"labels": {"foo": "bar"}},
                        "type": "cluster.x-k8s.io/secret",
                        "stringData": {"tls.crt": "cert1", "tls.key": "key1"},
                    },
                    "magnum-fakeproject",
                ),
                mock.call(
                    "cluster-example-a-111111111111-proxy",
                    {
                        "metadata": {"labels": {"foo": "bar"}},
                        "type": "cluster.x-k8s.io/secret",
                        "stringData": {"tls.crt": "cert2", "tls.key": "key2"},
                    },
                    "magnum-fakeproject",
                ),
            ]
        )
        mock_string_data.assert_called_once_with(
            self.context, self.cluster_obj
        )
        mock_labels.assert_called_with(self.cluster_obj)

    @mock.patch("magnum.common.clients.OpenStackClients.cinder")
    def test_get_storage_classes(self, mock_cinder):
        CONF.capi_helm.csi_cinder_default_volume_type = "type3"
        CONF.capi_helm.csi_cinder_availability_zone = "middle_earth_east"
        mock_vol_type_1 = mock.MagicMock()
        mock_vol_type_1.name = "type1"
        mock_vol_type_2 = mock.MagicMock()
        mock_vol_type_2.name = "type2"
        mock_vol_type_3 = mock.MagicMock()
        mock_vol_type_3.name = "type3"
        mock_volume_types = mock.Mock()
        mock_volume_types.list.return_value = [
            mock_vol_type_1,
            mock_vol_type_2,
            mock_vol_type_3,
        ]
        mock_cinder_client = mock.Mock()
        mock_cinder_client.volume_types = mock_volume_types
        mock_cinder.return_value = mock_cinder_client
        storage_classes = self.driver._storageclass_definitions(
            self.context, self.cluster_obj
        )
        self.assertIsInstance(storage_classes, dict)
        self.assertIsInstance(storage_classes["defaultStorageClass"], dict)
        self.assertIsInstance(
            storage_classes["additionalStorageClasses"], list
        )
        self.assertEqual(
            "type3", storage_classes["defaultStorageClass"]["volumeType"]
        )
        self.assertEqual(
            "middle_earth_east",
            storage_classes["additionalStorageClasses"][0]["availabilityZone"],
        )

    @mock.patch("magnum.common.clients.OpenStackClients.cinder")
    def test_get_storage_class_volume_type_not_available(self, mock_cinder):
        CONF.capi_helm.csi_cinder_default_volume_type = "type4"
        CONF.capi_helm.csi_cinder_availability_zone = "middle_earth_east"
        mock_vol_type_1 = mock.MagicMock()
        mock_vol_type_1.name = "type1"
        mock_vol_type_2 = mock.MagicMock()
        mock_vol_type_2.name = "type2"
        mock_vol_type_3 = mock.MagicMock()
        mock_vol_type_3.name = "type3"
        mock_volume_types = mock.Mock()
        mock_volume_types.list.return_value = [
            mock_vol_type_1,
            mock_vol_type_2,
            mock_vol_type_3,
        ]
        mock_cinder_client = mock.Mock()
        mock_cinder_client.volume_types = mock_volume_types
        mock_cinder.return_value = mock_cinder_client
        self.assertRaisesRegex(
            exception.MagnumException,
            r"not\sa\svalid\sCinder",
            self.driver._storageclass_definitions,
            self.context,
            self.cluster_obj,
        )

    @mock.patch("magnum.common.clients.OpenStackClients.cinder")
    def test_get_storage_class_volume_type_not_defined(self, mock_cinder):
        CONF.capi_helm.csi_cinder_default_volume_type = None
        CONF.capi_helm.csi_cinder_availability_zone = "middle_earth_east"
        mock_vol_type_1 = mock.MagicMock()
        mock_vol_type_1.name = "__TYPE1__"
        mock_vol_type_2 = mock.MagicMock()
        mock_vol_type_2.name = "type2"
        mock_vol_type_3 = mock.MagicMock()
        mock_vol_type_3.name = "type3"
        mock_volume_types = mock.Mock()
        mock_volume_types.list.return_value = [
            mock_vol_type_1,
            mock_vol_type_2,
            mock_vol_type_3,
        ]
        mock_cinder_client = mock.Mock()
        mock_cinder_client.volume_types = mock_volume_types
        mock_cinder.return_value = mock_cinder_client
        storage_classes = self.driver._storageclass_definitions(
            self.context, self.cluster_obj
        )
        default_storage_class = storage_classes["defaultStorageClass"]
        volume_type = default_storage_class["name"]
        self.assertEqual("type1", volume_type)

    @mock.patch.object(helm.Client, "uninstall_release")
    def test_delete_cluster(self, mock_uninstall):
        self.driver.delete_cluster(self.context, self.cluster_obj)

        mock_uninstall.assert_called_once_with(
            "cluster-example-a-111111111111", namespace="magnum-fakeproject"
        )

    def test_update_cluster(self):
        self.assertRaises(
            NotImplementedError,
            self.driver.update_cluster,
            self.context,
            self.cluster_obj,
        )

    @mock.patch.object(driver.Driver, "_update_helm_release")
    def test_resize_cluster(self, mock_update):
        self.driver.resize_cluster(
            self.context,
            self.cluster_obj,
            None,
            None,
            None,
        )
        mock_update.assert_called_once_with(self.context, self.cluster_obj)

    @mock.patch.object(driver.Driver, "_update_helm_release")
    def test_resize_cluster_ignore_nodes_to_remove(self, mock_update):
        self.driver.resize_cluster(
            self.context,
            self.cluster_obj,
            None,
            ["node1"],
            None,
        )
        mock_update.assert_called_once_with(self.context, self.cluster_obj)

    @mock.patch.object(driver.Driver, "_validate_allowed_flavor")
    @mock.patch.object(driver.Driver, "_update_helm_release")
    def test_upgrade_cluster(
        self,
        mock_update,
        mock_validate_allowed_flavor,
    ):
        node_group = mock.MagicMock()
        mock_template = mock.MagicMock()
        mock_template.uuid = "foo"

        self.driver.upgrade_cluster(
            self.context,
            self.cluster_obj,
            mock_template,
            1,
            node_group,
        )

        # TODO(johngarbutt) improve the testing
        mock_update.assert_called_once_with(self.context, self.cluster_obj)
        self.assertEqual("UPDATE_IN_PROGRESS", self.cluster_obj.status)

    @mock.patch.object(driver.Driver, "_validate_allowed_flavor")
    @mock.patch.object(driver.Driver, "_update_helm_release")
    def test_create_nodegroup(
        self,
        mock_update,
        mock_validate_allowed_flavor,
    ):
        node_group = mock.MagicMock()

        self.driver.create_nodegroup(
            self.context, self.cluster_obj, node_group
        )

        mock_update.assert_called_once_with(self.context, self.cluster_obj)
        node_group.save.assert_called_once_with()
        self.assertEqual("CREATE_IN_PROGRESS", node_group.status)

    @mock.patch.object(driver.Driver, "_validate_allowed_flavor")
    @mock.patch.object(driver.Driver, "_update_helm_release")
    def test_update_nodegroup(
        self,
        mock_update,
        mock_validate_allowed_flavor,
    ):
        node_group = mock.MagicMock()

        self.driver.update_nodegroup(
            self.context,
            self.cluster_obj,
            node_group,
        )

        mock_update.assert_called_once_with(self.context, self.cluster_obj)
        node_group.save.assert_called_once_with()
        self.assertEqual("UPDATE_IN_PROGRESS", node_group.status)

    @mock.patch.object(driver.Driver, "_update_helm_release")
    def test_delete_nodegroup(self, mock_update):
        self.driver.delete_nodegroup(
            self.context,
            self.cluster_obj,
            self.cluster_obj.nodegroups[1],
        )

        mock_update.assert_called_once_with(
            self.context,
            self.cluster_obj,
            mock.ANY,
        )
        # because nodegroups equality is broken
        self.assertEqual(
            self.cluster_obj.nodegroups[0].as_dict(),
            mock_update.call_args.args[2][0].as_dict(),
        )

    def test_create_federation(self):
        self.assertRaises(
            NotImplementedError,
            self.driver.create_federation,
            self.context,
            None,
        )

    def test_update_federation(self):
        self.assertRaises(
            NotImplementedError,
            self.driver.update_federation,
            self.context,
            None,
        )

    def test_delete_federation(self):
        self.assertRaises(
            NotImplementedError,
            self.driver.delete_federation,
            self.context,
            None,
        )

    @mock.patch("novaclient.v2.flavors.FlavorManager", autospec=True)
    @mock.patch("novaclient.v2.client.Client", autospec=2)
    @mock.patch("novaclient.client.Client")
    @mock.patch("magnum.common.clients.OpenStackClients.nova")
    def test_validate_allowed_flavors_ram_error(
        self,
        mock_osc_nova,
        mock_nova_client,
        mock_versioned_nova_client,
        mock_flavor_manager,
    ):
        mock_flavor1 = mock.MagicMock(id=1, vcpus=1)
        mock_flavor1.name = "flavor_tiny"
        mock_flavor2 = mock.MagicMock(id=2, vcpus=1)
        mock_flavor2.name = "flavor_small"
        mock_flavor3 = mock.MagicMock(id=3, vcpus=4)
        mock_flavor3.name = "flavor_medium"
        # Assumes that the list returned by novaclient.flavors.(min_ram=xxxx)
        # is already filtered so no need to check that.
        filtered_flavors = [
            mock_flavor1,
            mock_flavor2,
            mock_flavor3,
        ]
        mock_flavor_manager.list.return_value = filtered_flavors
        mock_versioned_nova_client.flavors = mock_flavor_manager
        mock_osc_nova.return_value = mock_versioned_nova_client
        self.assertRaises(
            exception.MagnumException,
            self.driver._validate_allowed_flavor,
            self.context,
            "DS9",
        )

    @mock.patch("novaclient.v2.flavors.FlavorManager", autospec=True)
    @mock.patch("novaclient.v2.client.Client", autospec=2)
    @mock.patch("novaclient.client.Client")
    @mock.patch("magnum.common.clients.OpenStackClients.nova")
    def test_validate_allowed_flavors_vcpu_error(
        self,
        mock_osc_nova,
        mock_nova_client,
        mock_versioned_nova_client,
        mock_flavor_manager,
    ):
        mock_flavor1 = mock.MagicMock(id=1, vcpus=1)
        mock_flavor1.name = "flavor_tiny"
        mock_flavor2 = mock.MagicMock(id=2, vcpus=1)
        mock_flavor2.name = "flavor_small"
        mock_flavor3 = mock.MagicMock(id=3, vcpus=4)
        mock_flavor3.name = "flavor_medium"
        # Assumes that the list returned by novaclient.flavors.(min_ram=xxxx)
        # is already filtered so no need to check that.
        filtered_flavors = [
            mock_flavor1,
            mock_flavor2,
            mock_flavor3,
        ]
        mock_flavor_manager.list.return_value = filtered_flavors
        mock_versioned_nova_client.flavors = mock_flavor_manager
        mock_osc_nova.return_value = mock_versioned_nova_client
        self.assertRaises(
            exception.MagnumException,
            self.driver._validate_allowed_flavor,
            self.context,
            2,
        )
        self.assertRaises(
            exception.MagnumException,
            self.driver._validate_allowed_flavor,
            self.context,
            "flavor_small",
        )

    @mock.patch("novaclient.v2.flavors.FlavorManager", autospec=True)
    @mock.patch("novaclient.v2.client.Client", autospec=2)
    @mock.patch("novaclient.client.Client")
    @mock.patch("magnum.common.clients.OpenStackClients.nova")
    def test_validate_allowed_flavors_ram_ok(
        self,
        mock_osc_nova,
        mock_nova_client,
        mock_versioned_nova_client,
        mock_flavor_manager,
    ):
        mock_flavor1 = mock.MagicMock(id=1, vcpus=1)
        mock_flavor1.name = "flavor_tiny"
        mock_flavor2 = mock.MagicMock(id=2, vcpus=1)
        mock_flavor2.name = "flavor_small"
        mock_flavor3 = mock.MagicMock(id=3, vcpus=4)
        mock_flavor3.name = "flavor_medium"
        filtered_flavors = [
            mock_flavor1,
            mock_flavor2,
            mock_flavor3,
        ]
        mock_flavor_manager.list.return_value = filtered_flavors
        mock_versioned_nova_client.flavors = mock_flavor_manager
        mock_osc_nova.return_value = mock_versioned_nova_client
        try:
            self.driver._validate_allowed_flavor(self.context, 3)
        except Exception as e:
            self.fail("Raised exception %s" % e)
        try:
            self.driver._validate_allowed_flavor(self.context, "flavor_medium")
        except Exception as e:
            self.fail("Raised exception %s" % e)

    @mock.patch("novaclient.v2.flavors.FlavorManager", autospec=True)
    @mock.patch("novaclient.v2.client.Client", autospec=2)
    @mock.patch("novaclient.client.Client")
    @mock.patch("magnum.common.clients.OpenStackClients.nova")
    def test_validate_upgrade_cluster_node_group(
        self,
        mock_osc_nova,
        mock_nova_client,
        mock_versioned_nova_client,
        mock_flavor_manager,
    ):
        mock_flavor1 = mock.MagicMock(id=1, vcpus=1)
        mock_flavor1.name = "flavor_tiny"
        mock_flavor2 = mock.MagicMock(id=2, vcpus=1)
        mock_flavor2.name = "flavor_small"
        mock_flavor3 = mock.MagicMock(id=3, vcpus=4)
        mock_flavor3.name = "flavor_medium"
        filtered_flavors = [
            mock_flavor1,
            mock_flavor2,
            mock_flavor3,
        ]
        mock_flavor_manager.list.return_value = filtered_flavors
        mock_versioned_nova_client.flavors = mock_flavor_manager
        mock_osc_nova.return_value = mock_versioned_nova_client
        node_group = mock.MagicMock()
        node_group.flavor_id
        mock_template = mock.MagicMock()
        mock_template.uuid = "foo"

        for ng in self.cluster_obj.nodegroups:
            if ng.role != "master":
                ng.flavor_id = "flavor_small"
                ng.save()
        self.assertRaises(
            exception.MagnumException,
            self.driver.upgrade_cluster,
            self.context,
            self.cluster_obj,
            mock_template,
            1,
            node_group,
        )

    @mock.patch.object(
        driver.Driver, "_get_k8s_keystone_auth_enabled", return_value=False
    )
    @mock.patch.object(
        driver.Driver,
        "_storageclass_definitions",
        return_value=mock.ANY,
    )
    @mock.patch.object(driver.Driver, "_validate_allowed_flavor")
    @mock.patch.object(neutron, "get_network", autospec=True)
    @mock.patch.object(
        driver.Driver, "_ensure_certificate_secrets", autospec=True
    )
    @mock.patch.object(driver.Driver, "_create_appcred_secret", autospec=True)
    @mock.patch.object(kubernetes.Client, "load", autospec=True)
    @mock.patch.object(driver.Driver, "_get_image_details", autospec=True)
    @mock.patch.object(helm.Client, "install_or_upgrade", autospec=True)
    def test_create_cluster_api_lb_allowed_cidrs(
        self,
        mock_install,
        mock_image,
        mock_load,
        mock_appcred,
        mock_certs,
        mock_get_net,
        mock_validate_allowed_flavor,
        mock_storageclasses,
        mock_get_keystone_auth_enabled,
    ):
        cidrs = "192.168.0.0/16,10.0.0.0/8,123.123.123.123/32"
        self.cluster_obj.labels = dict(api_master_lb_allowed_cidrs=cidrs)
        mock_image.return_value = (
            "imageid1",
            "1.27.4",
            "ubuntu",
        )
        mock_client = mock.MagicMock(spec=kubernetes.Client)
        mock_load.return_value = mock_client

        self.driver.create_cluster(self.context, self.cluster_obj, 10)
        cidr_list = cidrs.split(",")
        helm_install_values = mock_install.call_args[0][3]
        self.assertEqual(
            helm_install_values["apiServer"]["allowedCidrs"], cidr_list
        )

    @mock.patch.object(driver.Driver, "_get_k8s_keystone_auth_enabled")
    @mock.patch.object(
        driver.Driver,
        "_storageclass_definitions",
        return_value=mock.ANY,
    )
    @mock.patch.object(driver.Driver, "_validate_allowed_flavor")
    @mock.patch.object(neutron, "get_network", autospec=True)
    @mock.patch.object(
        driver.Driver, "_ensure_certificate_secrets", autospec=True
    )
    @mock.patch.object(driver.Driver, "_create_appcred_secret", autospec=True)
    @mock.patch.object(kubernetes.Client, "load", autospec=True)
    @mock.patch.object(driver.Driver, "_get_image_details", autospec=True)
    @mock.patch.object(helm.Client, "install_or_upgrade", autospec=True)
    def test_create_cluster_keystone_webhook_enabled(
        self,
        mock_install,
        mock_image,
        mock_load,
        mock_appcred,
        mock_certs,
        mock_get_net,
        mock_validate_allowed_flavor,
        mock_storageclasses,
        mock_get_keystone_auth_enabled,
    ):
        mock_image.return_value = (
            "imageid1",
            "1.27.4",
            "ubuntu",
        )
        mock_client = mock.MagicMock(spec=kubernetes.Client)
        mock_load.return_value = mock_client
        mock_get_keystone_auth_enabled.return_value = True  # Enable webhook
        self.cluster_obj.labels = {}

        self.driver.create_cluster(
            self.context, self.cluster_obj, "timeout-not-used"
        )

        helm_install_values = mock_install.call_args[0][3]
        self.assertEqual(
            helm_install_values["authWebhook"], "k8s-keystone-auth"
        )
        self.assertTrue(
            helm_install_values["addons"]
            .get(
                "openstack", {}
            )  # Default to {} so that next .get isn't called on None type
            .get("k8sKeystoneAuth", {})
            .get("enabled")
        )

    @mock.patch.object(driver.Driver, "_get_k8s_keystone_auth_enabled")
    @mock.patch.object(
        driver.Driver,
        "_storageclass_definitions",
        return_value=mock.ANY,
    )
    @mock.patch.object(driver.Driver, "_validate_allowed_flavor")
    @mock.patch.object(neutron, "get_network", autospec=True)
    @mock.patch.object(
        driver.Driver, "_ensure_certificate_secrets", autospec=True
    )
    @mock.patch.object(driver.Driver, "_create_appcred_secret", autospec=True)
    @mock.patch.object(kubernetes.Client, "load", autospec=True)
    @mock.patch.object(driver.Driver, "_get_image_details", autospec=True)
    @mock.patch.object(helm.Client, "install_or_upgrade", autospec=True)
    def test_create_cluster_keystone_webhook_disabled(
        self,
        mock_install,
        mock_image,
        mock_load,
        mock_appcred,
        mock_certs,
        mock_get_net,
        mock_validate_allowed_flavor,
        mock_storageclasses,
        mock_get_keystone_auth_enabled,
    ):
        mock_image.return_value = (
            "imageid1",
            "1.27.4",
            "ubuntu",
        )
        mock_client = mock.MagicMock(spec=kubernetes.Client)
        mock_load.return_value = mock_client
        mock_get_keystone_auth_enabled.return_value = False  # Disable webhook
        self.cluster_obj.labels = {}

        self.driver.create_cluster(
            self.context, self.cluster_obj, "timeout-not-used"
        )

        helm_install_values = mock_install.call_args[0][3]
        self.assertNotEqual(
            helm_install_values.get("authWebhook"), "k8s-keystone-auth"
        )
        self.assertFalse(
            helm_install_values["addons"]
            .get("openstack", {})
            .get("k8sKeystoneAuth", {})
            .get("enabled")
        )

    @mock.patch.object(
        driver.Driver,
        "_storageclass_definitions",
        return_value=mock.ANY,
    )
    @mock.patch.object(driver.Driver, "_validate_allowed_flavor")
    @mock.patch.object(neutron, "get_network", autospec=True)
    @mock.patch.object(
        driver.Driver, "_ensure_certificate_secrets", autospec=True
    )
    @mock.patch.object(driver.Driver, "_create_appcred_secret", autospec=True)
    @mock.patch.object(kubernetes.Client, "load", autospec=True)
    @mock.patch.object(driver.Driver, "_get_image_details", autospec=True)
    @mock.patch.object(helm.Client, "install_or_upgrade", autospec=True)
    def test_create_cluster_octavia_provider_amphora(
        self,
        mock_install,
        mock_image,
        mock_load,
        mock_appcred,
        mock_certs,
        mock_get_net,
        mock_validate_allowed_flavor,
        mock_storageclasses,
    ):
        mock_image.return_value = (
            "imageid1",
            "1.27.4",
            "ubuntu",
        )
        mock_client = mock.MagicMock(spec=kubernetes.Client)
        mock_load.return_value = mock_client

        # Simulate setting provider label
        self.cluster_obj.labels = {
            "octavia_provider": "amphora",
            "octavia_lb_algorithm": "SOURCE_IP_PORT",
        }

        self.driver.create_cluster(
            self.context, self.cluster_obj, "timeout-not-used"
        )

        helm_install_values = mock_install.call_args[0][3]
        self.assertEqual(
            helm_install_values.get("apiServer", {}).get(
                "loadBalancerProvider"
            ),
            "amphora",
        )
        self.assertEqual(
            helm_install_values["addons"]
            .get("openstack", {})
            .get("cloudConfig", {})
            .get("LoadBalancer", {})
            .get("lb-provider"),
            "amphora",
        )
        self.assertEqual(
            helm_install_values["addons"]
            .get("openstack", {})
            .get("cloudConfig", {})
            .get("LoadBalancer", {})
            .get("lb-method"),
            "SOURCE_IP_PORT",
        )

    @mock.patch.object(
        driver.Driver,
        "_storageclass_definitions",
        return_value=mock.ANY,
    )
    @mock.patch.object(driver.Driver, "_validate_allowed_flavor")
    @mock.patch.object(neutron, "get_network", autospec=True)
    @mock.patch.object(
        driver.Driver, "_ensure_certificate_secrets", autospec=True
    )
    @mock.patch.object(driver.Driver, "_create_appcred_secret", autospec=True)
    @mock.patch.object(kubernetes.Client, "load", autospec=True)
    @mock.patch.object(driver.Driver, "_get_image_details", autospec=True)
    @mock.patch.object(helm.Client, "install_or_upgrade", autospec=True)
    def test_create_cluster_octavia_provider_ovn(
        self,
        mock_install,
        mock_image,
        mock_load,
        mock_appcred,
        mock_certs,
        mock_get_net,
        mock_validate_allowed_flavor,
        mock_storageclasses,
    ):
        mock_image.return_value = (
            "imageid1",
            "1.27.4",
            "ubuntu",
        )
        mock_client = mock.MagicMock(spec=kubernetes.Client)
        mock_load.return_value = mock_client

        # Simulate setting provider label
        self.cluster_obj.labels = {
            "octavia_provider": "ovn",
        }

        self.driver.create_cluster(
            self.context, self.cluster_obj, "timeout-not-used"
        )

        helm_install_values = mock_install.call_args[0][3]
        self.assertEqual(
            helm_install_values.get("apiServer", {}).get(
                "loadBalancerProvider"
            ),
            "ovn",
        )
        self.assertEqual(
            helm_install_values["addons"]
            .get("openstack", {})
            .get("cloudConfig", {})
            .get("LoadBalancer", {})
            .get("lb-provider"),
            "ovn",
        )
        # Check algorithm default if no label provided
        self.assertEqual(
            helm_install_values["addons"]
            .get("openstack", {})
            .get("cloudConfig", {})
            .get("LoadBalancer", {})
            .get("lb-method"),
            "SOURCE_IP_PORT",
        )

    @mock.patch.object(
        driver.Driver,
        "_storageclass_definitions",
        return_value=mock.ANY,
    )
    @mock.patch.object(driver.Driver, "_validate_allowed_flavor")
    @mock.patch.object(neutron, "get_network", autospec=True)
    @mock.patch.object(
        driver.Driver, "_ensure_certificate_secrets", autospec=True
    )
    @mock.patch.object(driver.Driver, "_create_appcred_secret", autospec=True)
    @mock.patch.object(kubernetes.Client, "load", autospec=True)
    @mock.patch.object(driver.Driver, "_get_image_details", autospec=True)
    @mock.patch.object(helm.Client, "install_or_upgrade", autospec=True)
    def test_create_cluster_octavia_disable_monitor(
        self,
        mock_install,
        mock_image,
        mock_load,
        mock_appcred,
        mock_certs,
        mock_get_net,
        mock_validate_allowed_flavor,
        mock_storageclasses,
    ):
        mock_image.return_value = (
            "imageid1",
            "1.27.4",
            "ubuntu",
        )
        mock_client = mock.MagicMock(spec=kubernetes.Client)
        mock_load.return_value = mock_client

        # Simulate setting provider label
        self.cluster_obj.labels = {
            "octavia_lb_healthcheck": False,
        }

        self.driver.create_cluster(
            self.context, self.cluster_obj, "timeout-not-used"
        )

        helm_install_values = mock_install.call_args[0][3]
        self.assertFalse(
            helm_install_values["addons"]
            .get("openstack", {})
            .get("cloudConfig", {})
            .get("LoadBalancer", {})
            .get("create-monitor")
        )

    def test_validate_auto_scale_max_lt_min(self):
        self.cluster_obj.labels = dict(
            auto_scaling_enabled="true", min_node_count=3, max_node_count=0
        )
        self.assertRaises(
            exception.MagnumException,
            self.driver._get_autoscale_values,
            self.cluster_obj,
            self.cluster_obj.nodegroups[0],
        )

    @mock.patch.object(
        driver.Driver, "_get_k8s_keystone_auth_enabled", return_value=False
    )
    @mock.patch.object(
        driver.Driver,
        "_storageclass_definitions",
        return_value=mock.ANY,
    )
    @mock.patch.object(driver.Driver, "_validate_allowed_flavor")
    @mock.patch.object(neutron, "get_network", autospec=True)
    @mock.patch.object(
        driver.Driver, "_ensure_certificate_secrets", autospec=True
    )
    @mock.patch.object(driver.Driver, "_create_appcred_secret", autospec=True)
    @mock.patch.object(kubernetes.Client, "load", autospec=True)
    @mock.patch.object(driver.Driver, "_get_image_details", autospec=True)
    @mock.patch.object(helm.Client, "install_or_upgrade", autospec=True)
    def test_create_cluster_auto_scale_enabled(
        self,
        mock_install,
        mock_image,
        mock_load,
        mock_appcred,
        mock_certs,
        mock_get_net,
        mock_validate_allowed_flavor,
        mock_storageclasses,
        mock_get_keystone_auth_enabled,
    ):
        auto_scale_labels = dict(
            auto_scaling_enabled="true", min_node_count=2, max_node_count=6
        )
        self.cluster_obj.labels = auto_scale_labels

        mock_image.return_value = (
            "imageid1",
            "1.27.4",
            "ubuntu",
        )
        mock_client = mock.MagicMock(spec=kubernetes.Client)
        mock_load.return_value = mock_client

        self.driver.create_cluster(self.context, self.cluster_obj, 10)
        helm_install_values = mock_install.call_args[0][3]
        self.assertEqual(
            helm_install_values["nodeGroups"][0]["autoscale"],
            auto_scale_labels["auto_scaling_enabled"],
        )
        self.assertEqual(
            helm_install_values["nodeGroups"][0]["machineCountMin"],
            auto_scale_labels["min_node_count"],
        )
        self.assertEqual(
            helm_install_values["nodeGroups"][0]["machineCountMax"],
            auto_scale_labels["max_node_count"],
        )

    @mock.patch.object(
        driver.Driver, "_get_k8s_keystone_auth_enabled", return_value=False
    )
    @mock.patch.object(
        driver.Driver,
        "_storageclass_definitions",
        return_value=mock.ANY,
    )
    @mock.patch.object(driver.Driver, "_validate_allowed_flavor")
    @mock.patch.object(neutron, "get_network", autospec=True)
    @mock.patch.object(
        driver.Driver, "_ensure_certificate_secrets", autospec=True
    )
    @mock.patch.object(driver.Driver, "_create_appcred_secret", autospec=True)
    @mock.patch.object(kubernetes.Client, "load", autospec=True)
    @mock.patch.object(driver.Driver, "_get_image_details", autospec=True)
    @mock.patch.object(helm.Client, "install_or_upgrade", autospec=True)
    def test_create_cluster_auto_scale_disabled(
        self,
        mock_install,
        mock_image,
        mock_load,
        mock_appcred,
        mock_certs,
        mock_get_net,
        mock_validate_allowed_flavor,
        mock_storageclasses,
        mock_get_keystone_auth_enabled,
    ):
        auto_scale_labels = dict(
            auto_scaling_enabled="false", min_node_count=2, max_node_count=6
        )
        self.cluster_obj.labels = auto_scale_labels

        mock_image.return_value = (
            "imageid1",
            "1.27.4",
            "ubuntu",
        )
        mock_client = mock.MagicMock(spec=kubernetes.Client)
        mock_load.return_value = mock_client

        self.driver.create_cluster(self.context, self.cluster_obj, 10)
        helm_install_values = mock_install.call_args[0][3]
        self.assertNotIn(
            "autoscale",
            helm_install_values["nodeGroups"][0],
        )
        self.assertNotIn(
            "machineCountMin",
            helm_install_values["nodeGroups"][0],
        )
        self.assertNotIn(
            "machineCountMax",
            helm_install_values["nodeGroups"][0],
        )

    @mock.patch.object(
        driver.Driver,
        "_storageclass_definitions",
        return_value=mock.ANY,
    )
    @mock.patch.object(driver.Driver, "_validate_allowed_flavor")
    @mock.patch.object(neutron, "get_network", autospec=True)
    @mock.patch.object(
        driver.Driver, "_ensure_certificate_secrets", autospec=True
    )
    @mock.patch.object(driver.Driver, "_create_appcred_secret", autospec=True)
    @mock.patch.object(kubernetes.Client, "load", autospec=True)
    @mock.patch.object(driver.Driver, "_get_image_details", autospec=True)
    @mock.patch.object(helm.Client, "install_or_upgrade", autospec=True)
    def test_nodegroup_node_count_validation(
        self,
        mock_install,
        mock_image,
        mock_load,
        mock_appcred,
        mock_certs,
        mock_get_net,
        mock_validate_allowed_flavor,
        mock_storageclasses,
    ):
        auto_scale_labels = dict(
            auto_scaling_enabled="true", min_node_count=2, max_node_count=6
        )
        self.cluster_obj.labels = auto_scale_labels

        mock_image.return_value = (
            "imageid1",
            "1.27.4",
            "ubuntu",
        )

        # Create cluster with extra node groups
        ng = obj_utils.create_test_nodegroup(
            self.context, min_node_count=3, max_node_count=2
        )
        self.cluster_obj.nodegroups.append(ng)
        self.assertRaises(
            exception.NodeGroupInvalidInput,
            self.driver.create_cluster,
            self.context,
            self.cluster_obj,
            "not-used",
        )

    @mock.patch.object(
        driver.Driver,
        "_storageclass_definitions",
        return_value=mock.ANY,
    )
    @mock.patch.object(driver.Driver, "_validate_allowed_flavor")
    @mock.patch.object(neutron, "get_network", autospec=True)
    @mock.patch.object(
        driver.Driver, "_ensure_certificate_secrets", autospec=True
    )
    @mock.patch.object(driver.Driver, "_create_appcred_secret", autospec=True)
    @mock.patch.object(kubernetes.Client, "load", autospec=True)
    @mock.patch.object(driver.Driver, "_get_image_details", autospec=True)
    @mock.patch.object(helm.Client, "install_or_upgrade", autospec=True)
    def test_create_extra_nodegroup_auto_scale_values(
        self,
        mock_install,
        mock_image,
        mock_load,
        mock_appcred,
        mock_certs,
        mock_get_net,
        mock_validate_allowed_flavor,
        mock_storageclasses,
    ):
        auto_scale_labels = dict(
            auto_scaling_enabled="true", min_node_count=2, max_node_count=6
        )
        self.cluster_obj.labels = auto_scale_labels

        mock_image.return_value = (
            "imageid1",
            "1.27.4",
            "ubuntu",
        )

        # Create cluster with extra node groups
        non_auto_scale_nodegroup = obj_utils.create_test_nodegroup(
            self.context,
            uuid=uuid4(),
            id=123,
            name="non-autoscale-group",
            node_count=1,
        )
        self.cluster_obj.nodegroups.append(non_auto_scale_nodegroup)
        auto_scale_nodegroup = obj_utils.create_test_nodegroup(
            self.context,
            uuid=uuid4(),
            id=456,
            name="autoscale-group",
            node_count=1,
            min_node_count=1,
            max_node_count=3,
        )
        self.cluster_obj.nodegroups.append(auto_scale_nodegroup)
        self.driver.create_cluster(self.context, self.cluster_obj, 10)
        for ng in self.cluster_obj.nodegroups:
            print(ng)

        # Unpack some values for asserting against
        helm_install_values = mock_install.call_args[0][3]
        helm_node_groups = helm_install_values["nodeGroups"]
        helm_values_default_nodegroup = [
            ng
            for ng in helm_node_groups
            if ng["name"] == self.cluster_obj.default_ng_worker.name
        ]
        helm_values_autoscale_nodegroup = [
            ng
            for ng in helm_node_groups
            if ng["name"] == auto_scale_nodegroup.name
        ]
        helm_values_non_autoscale_nodegroup = [
            ng
            for ng in helm_node_groups
            if ng["name"] == non_auto_scale_nodegroup.name
        ]

        # Check that node groups were passed into Helm values correctly
        self.assertEqual(len(helm_values_default_nodegroup), 1)
        self.assertEqual(len(helm_values_autoscale_nodegroup), 1)
        self.assertEqual(len(helm_values_non_autoscale_nodegroup), 1)

        # Check default node group values
        ng = helm_values_default_nodegroup[0]
        self.assertEqual(ng["autoscale"], "true")
        self.assertEqual(
            ng["machineCountMin"], auto_scale_labels["min_node_count"]
        )
        self.assertEqual(
            ng["machineCountMax"], auto_scale_labels["max_node_count"]
        )

        # Check extra autoscaling node group values
        # NOTE: CAPO doesn't support scale to zero so
        # min node count should be max(1, ng.min_node_count)
        ng = helm_values_autoscale_nodegroup[0]
        self.assertEqual(ng["autoscale"], "true")
        self.assertEqual(
            ng["machineCountMin"], max(1, auto_scale_nodegroup.min_node_count)
        )
        self.assertEqual(
            ng["machineCountMax"], auto_scale_nodegroup.max_node_count
        )

        # Check extra non-autoscaling node group values
        ng = helm_values_non_autoscale_nodegroup[0]
        self.assertEqual(ng.get("autoscale"), None)
        self.assertEqual(ng.get("machineCountMin"), None)
        self.assertEqual(ng.get("machineCountMax"), None)

    @mock.patch.object(
        driver.Driver,
        "_storageclass_definitions",
        return_value=mock.ANY,
    )
    @mock.patch.object(driver.Driver, "_validate_allowed_flavor")
    @mock.patch.object(neutron, "get_network", autospec=True)
    @mock.patch.object(
        driver.Driver, "_ensure_certificate_secrets", autospec=True
    )
    @mock.patch.object(driver.Driver, "_create_appcred_secret", autospec=True)
    @mock.patch.object(kubernetes.Client, "load", autospec=True)
    @mock.patch.object(driver.Driver, "_get_image_details", autospec=True)
    @mock.patch.object(helm.Client, "install_or_upgrade", autospec=True)
    def test_create_cluster_boot_volume_size_label_valid(
        self,
        mock_install,
        mock_image,
        mock_load,
        mock_appcred,
        mock_certs,
        mock_get_net,
        mock_validate_allowed_flavor,
        mock_storageclasses,
    ):
        disk_size_configuration_value = 15
        disk_size_label_value = 32

        CONF.cinder.default_boot_volume_size = disk_size_configuration_value
        self.cluster_obj.labels = {
            "boot_volume_size": str(disk_size_label_value)
        }

        mock_image.return_value = (
            "imageid1",
            "1.27.4",
            "ubuntu",
        )
        mock_client = mock.MagicMock(spec=kubernetes.Client)
        mock_load.return_value = mock_client

        self.driver.create_cluster(self.context, self.cluster_obj, 10)
        helm_install_values = mock_install.call_args[0][3]
        self.assertEqual(
            helm_install_values["controlPlane"]["machineRootVolume"][
                "diskSize"
            ],
            disk_size_label_value,
        )
        self.assertEqual(
            helm_install_values["nodeGroupDefaults"]["machineRootVolume"][
                "diskSize"
            ],
            disk_size_label_value,
        )

    @mock.patch.object(
        driver.Driver,
        "_storageclass_definitions",
        return_value=mock.ANY,
    )
    @mock.patch.object(driver.Driver, "_validate_allowed_flavor")
    @mock.patch.object(neutron, "get_network", autospec=True)
    @mock.patch.object(
        driver.Driver, "_ensure_certificate_secrets", autospec=True
    )
    @mock.patch.object(driver.Driver, "_create_appcred_secret", autospec=True)
    @mock.patch.object(kubernetes.Client, "load", autospec=True)
    @mock.patch.object(driver.Driver, "_get_image_details", autospec=True)
    @mock.patch.object(helm.Client, "install_or_upgrade", autospec=True)
    def test_create_cluster_boot_volume_size_label_default(
        self,
        mock_install,
        mock_image,
        mock_load,
        mock_appcred,
        mock_certs,
        mock_get_net,
        mock_validate_allowed_flavor,
        mock_storageclasses,
    ):
        disk_size_configuration_value = 15

        CONF.cinder.default_boot_volume_size = disk_size_configuration_value
        self.cluster_obj.labels = {}

        mock_image.return_value = (
            "imageid1",
            "1.27.4",
            "ubuntu",
        )
        mock_client = mock.MagicMock(spec=kubernetes.Client)
        mock_load.return_value = mock_client

        self.driver.create_cluster(self.context, self.cluster_obj, 10)
        helm_install_values = mock_install.call_args[0][3]
        self.assertEqual(
            helm_install_values["controlPlane"]["machineRootVolume"][
                "diskSize"
            ],
            disk_size_configuration_value,
        )
        self.assertEqual(
            helm_install_values["nodeGroupDefaults"]["machineRootVolume"][
                "diskSize"
            ],
            disk_size_configuration_value,
        )

    @mock.patch.object(
        driver.Driver,
        "_storageclass_definitions",
        return_value=mock.ANY,
    )
    @mock.patch.object(driver.Driver, "_validate_allowed_flavor")
    @mock.patch.object(neutron, "get_network", autospec=True)
    @mock.patch.object(
        driver.Driver, "_ensure_certificate_secrets", autospec=True
    )
    @mock.patch.object(driver.Driver, "_create_appcred_secret", autospec=True)
    @mock.patch.object(kubernetes.Client, "load", autospec=True)
    @mock.patch.object(driver.Driver, "_get_image_details", autospec=True)
    @mock.patch.object(helm.Client, "install_or_upgrade", autospec=True)
    def test_create_cluster_boot_volume_size_label_invalid(
        self,
        mock_install,
        mock_image,
        mock_load,
        mock_appcred,
        mock_certs,
        mock_get_net,
        mock_validate_allowed_flavor,
        mock_storageclasses,
    ):
        disk_size_configuration_value = 15

        CONF.cinder.default_boot_volume_size = disk_size_configuration_value
        self.cluster_obj.labels = {"boot_volume_size": "NotANumber"}

        mock_image.return_value = (
            "imageid1",
            "1.27.4",
            "ubuntu",
        )
        mock_client = mock.MagicMock(spec=kubernetes.Client)
        mock_load.return_value = mock_client

        self.driver.create_cluster(self.context, self.cluster_obj, 10)
        helm_install_values = mock_install.call_args[0][3]
        self.assertEqual(
            helm_install_values["controlPlane"]["machineRootVolume"][
                "diskSize"
            ],
            disk_size_configuration_value,
        )
        self.assertEqual(
            helm_install_values["nodeGroupDefaults"]["machineRootVolume"][
                "diskSize"
            ],
            disk_size_configuration_value,
        )

    @mock.patch.object(
        driver.Driver,
        "_storageclass_definitions",
        return_value=mock.ANY,
    )
    @mock.patch.object(driver.Driver, "_validate_allowed_flavor")
    @mock.patch.object(neutron, "get_network", autospec=True)
    @mock.patch.object(
        driver.Driver, "_ensure_certificate_secrets", autospec=True
    )
    @mock.patch.object(driver.Driver, "_create_appcred_secret", autospec=True)
    @mock.patch.object(kubernetes.Client, "load", autospec=True)
    @mock.patch.object(driver.Driver, "_get_image_details", autospec=True)
    @mock.patch.object(helm.Client, "install_or_upgrade", autospec=True)
    def test_nodegroup_node_count_min_max_equal(
        self,
        mock_install,
        mock_image,
        mock_load,
        mock_appcred,
        mock_certs,
        mock_get_net,
        mock_validate_allowed_flavor,
        mock_storageclasses,
    ):
        # When autoscaling is enabled but no min/max node counts are
        # provided for the default node group, we want autoscaling to
        # be disabled on the default node group.
        self.cluster_obj.labels = {"auto_scaling_enabled": "true"}
        mock_image.return_value = (
            "imageid1",
            "1.27.4",
            "ubuntu",
        )
        self.driver.create_cluster(self.context, self.cluster_obj, 10)
        helm_install_values = mock_install.call_args[0][3]
        helm_values_default_ng = next(
            ng
            for ng in helm_install_values["nodeGroups"]
            if ng["name"] == self.cluster_obj.default_ng_worker.name
        )
        self.assertEqual(helm_values_default_ng.get("autoscale"), None)
        self.assertEqual(helm_values_default_ng.get("machineCountMin"), None)
        self.assertEqual(helm_values_default_ng.get("machineCountMax"), None)
        self.assertEqual(helm_values_default_ng.get("machineCount"), 3)
