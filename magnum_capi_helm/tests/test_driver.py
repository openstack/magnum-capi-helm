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
import datetime
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
        patcher = mock.patch.object(driver.Driver, "_validate_cidr_no_overlap")
        self.mock_validate_cidr_no_overlap = patcher.start()
        self.addCleanup(patcher.stop)

    # NOTE(scott): Since the driver's _update_helm_release
    # method calls cluster.refresh() we need to call save
    # after assigning test cluster labels
    def _set_cluster_test_labels(self, labels: dict):
        """Helper function to set labels on test cluster obj"""
        self.cluster_obj.labels = labels
        self.cluster_obj.save()

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
    @mock.patch.object(driver_utils, "is_helm_locked", return_value=False)
    @mock.patch.object(kubernetes.Client, "load", autospec=True)
    def test_update_cluster_status_creating(
        self,
        mock_load,
        mock_locked,
        mock_capi,
        mock_ng,
        mock_update,
        mock_delete,
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
    @mock.patch.object(driver_utils, "is_helm_locked", return_value=False)
    @mock.patch.object(kubernetes.Client, "load", autospec=True)
    def test_update_cluster_status_creating_not_found(
        self,
        mock_load,
        mock_locked,
        mock_capi,
        mock_ng,
        mock_update,
        mock_delete,
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
    @mock.patch.object(driver_utils, "is_helm_locked", return_value=False)
    @mock.patch.object(kubernetes.Client, "load", autospec=True)
    def test_update_cluster_status_created(
        self,
        mock_load,
        mock_locked,
        mock_capi,
        mock_ng,
        mock_update,
        mock_delete,
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
    @mock.patch.object(driver_utils, "is_helm_locked", return_value=False)
    @mock.patch.object(kubernetes.Client, "load", autospec=True)
    def test_update_cluster_status_deleted(
        self,
        mock_load,
        mock_locked,
        mock_capi,
        mock_ng,
        mock_update,
        mock_delete,
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
    @mock.patch.object(driver_utils, "is_helm_locked", return_value=False)
    @mock.patch.object(kubernetes.Client, "load", autospec=True)
    def test_update_cluster_status_deleting(
        self,
        mock_load,
        mock_locked,
        mock_capi,
        mock_ng,
        mock_update,
        mock_delete,
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
    @mock.patch.object(driver_utils, "is_helm_locked", return_value=False)
    @mock.patch.object(kubernetes.Client, "load", autospec=True)
    def test_update_cluster_status_create_complete(
        self,
        mock_load,
        mock_locked,
        mock_capi,
        mock_ng,
        mock_update,
        mock_delete,
    ):
        mock_capi.return_value = {"spec": {}}
        self.cluster_obj.status = fields.ClusterStatus.CREATE_COMPLETE

        self.driver.update_cluster_status(self.context, self.cluster_obj)

        mock_ng.assert_called_once_with(self.cluster_obj)
        mock_update.assert_not_called()
        mock_delete.assert_not_called()

    @mock.patch.object(driver.Driver, "_update_status_deleting")
    @mock.patch.object(driver.Driver, "_update_status_updating")
    @mock.patch.object(driver.Driver, "_update_all_nodegroups_status")
    @mock.patch.object(driver.Driver, "_get_capi_cluster")
    @mock.patch.object(kubernetes.Client, "load", autospec=True)
    def test_update_cluster_status_skipped_while_helm_locked(
        self, mock_load, mock_capi, mock_ng, mock_update, mock_delete
    ):
        self.cluster_obj.status = fields.ClusterStatus.UPDATE_IN_PROGRESS
        with mock.patch.object(
            driver_utils, "is_helm_locked", return_value=True
        ):
            self.driver.update_cluster_status(self.context, self.cluster_obj)

        mock_capi.assert_not_called()
        mock_ng.assert_not_called()
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
        mock_client.get_k8s_control_plane.return_value = None

        self.driver._update_control_plane_nodegroup_status(
            self.cluster_obj, nodegroup
        )

        mock_client.get_k8s_control_plane.assert_called_once_with(
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
        mock_client.get_k8s_control_plane.return_value = kcp

        self.driver._update_control_plane_nodegroup_status(
            self.cluster_obj, nodegroup
        )

        mock_client.get_k8s_control_plane.assert_called_once_with(
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
        mock_client.get_k8s_control_plane.return_value = kcp

        self.driver._update_control_plane_nodegroup_status(
            self.cluster_obj, nodegroup
        )

        mock_client.get_k8s_control_plane.assert_called_once_with(
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
        mock_client.get_k8s_control_plane.return_value = kcp

        self.driver._update_control_plane_nodegroup_status(
            self.cluster_obj, nodegroup
        )

        mock_client.get_k8s_control_plane.assert_called_once_with(
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
    @mock.patch.object(driver.Driver, "_get_app_cred_id")
    def test_update_status_deleting(self, mock_get, mock_load, mock_delete):
        mock_client = mock.MagicMock(spec=kubernetes.Client)
        app_cred_id = "abc123"
        mock_get.return_value = app_cred_id
        mock_load.return_value = mock_client
        # No lease present, so the Helm lock cleanup should proceed
        # straight to deleting it.
        mock_client.get_lease.return_value = None

        self.driver._update_status_deleting(self.context, self.cluster_obj)

        self.assertEqual("DELETE_COMPLETE", self.cluster_obj.status)
        mock_delete.assert_called_once_with(self.cluster_obj, app_cred_id)
        mock_client.delete_all_secrets_by_label.assert_called_once_with(
            "magnum.openstack.org/cluster-uuid",
            self.cluster_obj.uuid,
            "magnum-fakeproject",
        )
        mock_client.delete_lease.assert_called_once_with(
            "capi-helm-cluster-example-a-111111111111", "magnum-fakeproject"
        )

    @mock.patch.object(app_creds, "delete_app_cred")
    @mock.patch.object(kubernetes.Client, "load")
    @mock.patch.object(driver.Driver, "_get_app_cred_id")
    def test_update_status_deleting_skips_lease_delete_when_locked(
        self, mock_get, mock_load, mock_delete
    ):
        # If the lease is still actively held, it may belong to a
        # genuinely in-flight Helm operation for this cluster - deleting
        # it would let a concurrent operation acquire the lock while
        # that operation is still running.
        mock_client = mock.MagicMock(spec=kubernetes.Client)
        mock_get.return_value = "abc123"
        mock_load.return_value = mock_client
        now = datetime.datetime.now(tz=datetime.timezone.utc)
        mock_client.get_lease.return_value = {
            "spec": {
                "renewTime": now.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                "leaseDurationSeconds": 360,
            }
        }

        self.driver._update_status_deleting(self.context, self.cluster_obj)

        mock_client.delete_lease.assert_not_called()

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

        result = self.driver._label(
            self.cluster_obj, "monitoring_enabled", "bar"
        )

        self.assertEqual("bar", result)

    def test_label_return_template(self):
        self.cluster_obj.cluster_template.labels = dict(monitoring_enabled=42)

        result = self.driver._label(
            self.cluster_obj, "monitoring_enabled", "bar"
        )

        self.assertEqual("42", result)

    def test_label_return_cluster(self):
        self.cluster_obj.labels = dict(monitoring_enabled=41)
        self.cluster_obj.cluster_template.labels = dict(monitoring_enabled=42)

        result = self.driver._label(
            self.cluster_obj, "monitoring_enabled", "bar"
        )

        self.assertEqual("41", result)

    def test_label_unregistered_key_raises(self):
        self.assertRaises(
            ValueError,
            self.driver._label,
            self.cluster_obj,
            "not_a_real_label",
            "default",
        )

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
        mock_image.properties = {}
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

    def test_get_kube_version_works_from_properties(self):
        # openstacksdk Image: dict.get() returns None but custom properties
        # are accessible via image.properties (unknown attrs packed there)
        mock_image = mock.Mock()
        mock_image.get.return_value = None
        mock_image.properties = {"kube_version": "v1.27.9"}

        result = self.driver._get_kube_version(mock_image)

        self.assertEqual("1.27.9", result)

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

    @mock.patch("magnum.common.clients.OpenStackClients", autospec=True)
    def test_get_image_details_ubuntu_openstacksdk(self, mock_osc):
        # Simulate real openstacksdk Image: dict.get() returns None because
        # Resource(dict) stores data in _body not the dict storage. Known
        # fields (os_distro) are attributes; unknown fields (kube_version)
        # are in image.properties.
        mock_image = mock.Mock()
        mock_image.get.return_value = None
        mock_image.os_distro = "ubuntu"
        mock_image.properties = {"kube_version": "1.27.9"}
        mock_image.id = "myid"
        mock_glance = mock.MagicMock()
        mock_glance.images = mock.Mock(spec=[])  # no 'get' → openstacksdk path
        mock_glance.find_image.return_value = mock_image
        mock_osc.return_value.glance.return_value = mock_glance

        id, version, distro = self.driver._get_image_details(
            self.context, "myimagename"
        )

        self.assertEqual("1.27.9", version)
        self.assertEqual("myid", id)
        self.assertEqual("ubuntu", distro)
        mock_glance.find_image.assert_called_once_with("myimagename")

    @mock.patch("magnum.common.clients.OpenStackClients", autospec=True)
    def test_get_image_details_flatcar_openstacksdk(self, mock_osc):
        # Simulate real openstacksdk Image: dict.get() returns None because
        # Resource(dict) stores data in _body not the dict storage. Known
        # fields (os_distro) are attributes; unknown fields (kube_version)
        # are in image.properties.
        mock_image = mock.Mock()
        mock_image.get.return_value = None
        mock_image.os_distro = "flatcar"
        mock_image.properties = {"kube_version": "1.28.2"}
        mock_image.id = "myid-flatcar"
        mock_glance = mock.MagicMock()
        mock_glance.images = mock.Mock(spec=[])  # no 'get' → openstacksdk path
        mock_glance.find_image.return_value = mock_image
        mock_osc.return_value.glance.return_value = mock_glance

        id, version, distro = self.driver._get_image_details(
            self.context, "myimagename"
        )

        self.assertEqual("1.28.2", version)
        self.assertEqual("myid-flatcar", id)
        self.assertEqual("flatcar", distro)
        mock_glance.find_image.assert_called_once_with("myimagename")

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
            "kubeNetwork": {
                "pods": {"cidrBlocks": ["10.100.0.0/16"]},
                "services": {"cidrBlocks": ["172.24.0.0/13"]},
            },
            "etcd": {},
            "apiServer": {
                "associateFloatingIP": True,
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
    @mock.patch.object(helm.HelmLock, "renew", return_value=None)
    @mock.patch.object(helm.HelmLock, "_acquire_lock", return_value=True)
    @mock.patch.object(kubernetes.Client, "load", autospec=True)
    @mock.patch.object(driver.Driver, "_get_image_details", autospec=True)
    @mock.patch.object(helm.Client, "install_or_upgrade", autospec=True)
    def test_create_cluster(
        self,
        mock_install,
        mock_image,
        mock_load,
        mock_helm_lock,
        mock_renew,
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
    @mock.patch.object(helm.HelmLock, "renew", return_value=None)
    @mock.patch.object(helm.HelmLock, "_acquire_lock", return_value=True)
    @mock.patch.object(kubernetes.Client, "load", autospec=True)
    @mock.patch.object(driver.Driver, "_get_image_details", autospec=True)
    @mock.patch.object(helm.Client, "install_or_upgrade", autospec=True)
    def test_create_cluster_no_dns(
        self,
        mock_install,
        mock_image,
        mock_load,
        mock_helm_lock,
        mock_renew,
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

        cluster_template = obj_utils.create_test_cluster_template(
            self.context, dns_nameserver="", uuid=str(uuid4())
        )
        self.cluster_obj.cluster_template_id = cluster_template.uuid
        self.cluster_obj.save()

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
    @mock.patch.object(helm.HelmLock, "renew", return_value=None)
    @mock.patch.object(helm.HelmLock, "_acquire_lock", return_value=True)
    @mock.patch.object(kubernetes.Client, "load", autospec=True)
    @mock.patch.object(driver.Driver, "_get_image_details", autospec=True)
    @mock.patch.object(helm.Client, "install_or_upgrade", autospec=True)
    def test_create_cluster_boot_volume(
        self,
        mock_install,
        mock_image,
        mock_load,
        mock_helm_lock,
        mock_renew,
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
    @mock.patch.object(helm.HelmLock, "renew", return_value=None)
    @mock.patch.object(helm.HelmLock, "_acquire_lock", return_value=True)
    @mock.patch.object(kubernetes.Client, "load", autospec=True)
    @mock.patch.object(driver.Driver, "_get_image_details", autospec=True)
    @mock.patch.object(helm.Client, "install_or_upgrade", autospec=True)
    def test_create_cluster_boot_volume_extra_networks(
        self,
        mock_install,
        mock_image,
        mock_load,
        mock_helm_lock,
        mock_renew,
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
        self._set_cluster_test_labels({"extra_network_names": "foo bar"})

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
                {
                    "network": {
                        "name": "bar",
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
    @mock.patch.object(helm.HelmLock, "renew", return_value=None)
    @mock.patch.object(helm.HelmLock, "_acquire_lock", return_value=True)
    @mock.patch.object(kubernetes.Client, "load")
    @mock.patch.object(driver.Driver, "_get_image_details")
    @mock.patch.object(helm.Client, "install_or_upgrade")
    def test_create_cluster_with_keypair(
        self,
        mock_install,
        mock_image,
        mock_load,
        mock_helm_lock,
        mock_renew,
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
        self.cluster_obj.save()

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
    @mock.patch.object(helm.HelmLock, "renew", return_value=None)
    @mock.patch.object(helm.HelmLock, "_acquire_lock", return_value=True)
    @mock.patch.object(kubernetes.Client, "load")
    @mock.patch.object(driver.Driver, "_get_image_details")
    @mock.patch.object(helm.Client, "install_or_upgrade")
    def test_create_cluster_flatcar(
        self,
        mock_install,
        mock_image,
        mock_load,
        mock_helm_lock,
        mock_renew,
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
    @mock.patch.object(helm.HelmLock, "renew", return_value=None)
    @mock.patch.object(helm.HelmLock, "_acquire_lock", return_value=True)
    @mock.patch.object(kubernetes.Client, "load", autospec=True)
    @mock.patch.object(driver.Driver, "_get_image_details", autospec=True)
    @mock.patch.object(helm.Client, "install_or_upgrade", autospec=True)
    def test_create_cluster_no_autoheal(
        self,
        mock_install,
        mock_image,
        mock_load,
        mock_helm_lock,
        mock_renew,
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

        cluster_template = obj_utils.create_test_cluster_template(
            self.context,
            uuid="e74c40e0-d825-11e2-a28f-0800200c9a67",
            labels={"auto_healing_enabled": "false"},
        )
        self.cluster_obj.cluster_template_id = cluster_template.uuid
        self.cluster_obj.save()

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
    @mock.patch.object(helm.HelmLock, "renew", return_value=None)
    @mock.patch.object(helm.HelmLock, "_acquire_lock", return_value=True)
    @mock.patch.object(kubernetes.Client, "load", autospec=True)
    @mock.patch.object(driver.Driver, "_get_image_details", autospec=True)
    @mock.patch.object(helm.Client, "install_or_upgrade", autospec=True)
    def test_create_cluster_etcd_block_device(
        self,
        mock_install,
        mock_image,
        mock_load,
        mock_helm_lock,
        mock_renew,
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

        cluster_template = obj_utils.create_test_cluster_template(
            self.context,
            uuid="e74c40e0-d825-11e2-a28f-0800200c9a67",
            labels={
                "etcd_blockdevice_size": "10",
                "etcd_blockdevice_volume_type": "nvme",
            },
        )
        self.cluster_obj.cluster_template_id = cluster_template.uuid
        self.cluster_obj.save()

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
    @mock.patch.object(helm.HelmLock, "renew", return_value=None)
    @mock.patch.object(helm.HelmLock, "_acquire_lock", return_value=True)
    @mock.patch.object(kubernetes.Client, "load", autospec=True)
    @mock.patch.object(driver.Driver, "_get_image_details", autospec=True)
    @mock.patch.object(helm.Client, "install_or_upgrade", autospec=True)
    def test_create_cluster_etcd_block_device_local(
        self,
        mock_install,
        mock_image,
        mock_load,
        mock_helm_lock,
        mock_renew,
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

        cluster_template = obj_utils.create_test_cluster_template(
            self.context,
            uuid="e74c40e0-d825-11e2-a28f-0800200c9a67",
            labels={
                "etcd_blockdevice_size": "10",
                "etcd_blockdevice_type": "local",
            },
        )
        self.cluster_obj.cluster_template_id = cluster_template.uuid
        self.cluster_obj.save()

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
    @mock.patch.object(helm.HelmLock, "renew", return_value=None)
    @mock.patch.object(helm.HelmLock, "_acquire_lock", return_value=True)
    @mock.patch.object(kubernetes.Client, "load", autospec=True)
    @mock.patch.object(driver.Driver, "_get_image_details", autospec=True)
    @mock.patch.object(helm.Client, "install_or_upgrade", autospec=True)
    def test_create_cluster_etcd_block_device_legacy_labels(
        self,
        mock_install,
        mock_image,
        mock_load,
        mock_helm_lock,
        mock_renew,
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
        cluster_template = obj_utils.create_test_cluster_template(
            self.context,
            uuid="e74c40e0-d825-11e2-a28f-0800200c9a67",
            labels={
                "etcd_volume_size": "10",
                "etcd_volume_type": "nvme",
            },
        )
        self.cluster_obj.cluster_template_id = cluster_template.uuid
        self.cluster_obj.save()

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
    @mock.patch.object(helm.HelmLock, "renew", return_value=None)
    @mock.patch.object(helm.HelmLock, "_acquire_lock", return_value=True)
    @mock.patch.object(kubernetes.Client, "load", autospec=True)
    @mock.patch.object(driver.Driver, "_get_image_details", autospec=True)
    @mock.patch.object(helm.Client, "install_or_upgrade", autospec=True)
    def test_create_cluster_network_driver(
        self,
        mock_install,
        mock_image,
        mock_load,
        mock_helm_lock,
        mock_renew,
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

        # ClusterTemplate is immutable once referenced by a cluster, so
        # create a fresh one with the setting we want and point the
        # cluster at it instead of mutating the existing one in place.
        cluster_template = obj_utils.create_test_cluster_template(
            self.context,
            uuid=str(uuid4()),
            network_driver="cilium",
        )
        self.cluster_obj.cluster_template_id = cluster_template.uuid
        self.cluster_obj.save()

        self.driver.create_cluster(self.context, self.cluster_obj, 10)

        expected_values = self._get_cluster_helm_standard_values()
        expected_values["addons"]["cni"] = {"type": "cilium"}

        helm_install_values = mock_install.call_args[0][3]
        self.assertDictEqual(helm_install_values, expected_values)

    @mock.patch.object(app_creds, "create_app_cred")
    @mock.patch.object(app_creds, "get_app_cred_string_data")
    @mock.patch.object(kubernetes.Client, "load")
    def test_create_appcred_secret(self, mock_load, mock_sd, mock_create):
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
        CONF.capi_helm_cluster_labels.csi_cinder_availability_zone = (
            "middle_earth_east"
        )
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
        CONF.capi_helm_cluster_labels.csi_cinder_availability_zone = (
            "middle_earth_east"
        )
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
        CONF.capi_helm_cluster_labels.csi_cinder_availability_zone = (
            "middle_earth_east"
        )
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

    @mock.patch("magnum.common.clients.OpenStackClients.cinder")
    def test_get_storage_classes_openstacksdk(self, mock_cinder):
        CONF.capi_helm.csi_cinder_default_volume_type = "type3"
        CONF.capi_helm_cluster_labels.csi_cinder_availability_zone = (
            "middle_earth_east"
        )
        mock_vol_type_1 = mock.MagicMock()
        mock_vol_type_1.name = "type1"
        mock_vol_type_2 = mock.MagicMock()
        mock_vol_type_2.name = "type2"
        mock_vol_type_3 = mock.MagicMock()
        mock_vol_type_3.name = "type3"
        # Simulate openstacksdk Block Storage Proxy: no volume_types attribute;
        # volume types are listed via c_client.types()
        mock_cinder_client = mock.Mock(spec=["types"])
        mock_cinder_client.types.return_value = [
            mock_vol_type_1,
            mock_vol_type_2,
            mock_vol_type_3,
        ]
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
    def test_get_storage_class_volume_type_not_available_openstacksdk(
        self, mock_cinder
    ):
        CONF.capi_helm.csi_cinder_default_volume_type = "type4"
        CONF.capi_helm_cluster_labels.csi_cinder_availability_zone = (
            "middle_earth_east"
        )
        mock_vol_type_1 = mock.MagicMock()
        mock_vol_type_1.name = "type1"
        mock_vol_type_2 = mock.MagicMock()
        mock_vol_type_2.name = "type2"
        mock_vol_type_3 = mock.MagicMock()
        mock_vol_type_3.name = "type3"
        # Simulate openstacksdk Block Storage Proxy: no volume_types attribute;
        # volume types are listed via c_client.types()
        mock_cinder_client = mock.Mock(spec=["types"])
        mock_cinder_client.types.return_value = [
            mock_vol_type_1,
            mock_vol_type_2,
            mock_vol_type_3,
        ]
        mock_cinder.return_value = mock_cinder_client
        self.assertRaisesRegex(
            exception.MagnumException,
            r"not\sa\svalid\sCinder",
            self.driver._storageclass_definitions,
            self.context,
            self.cluster_obj,
        )

    @mock.patch("magnum.common.clients.OpenStackClients.cinder")
    def test_get_storage_class_volume_type_not_defined_openstacksdk(
        self, mock_cinder
    ):
        CONF.capi_helm.csi_cinder_default_volume_type = None
        CONF.capi_helm_cluster_labels.csi_cinder_availability_zone = (
            "middle_earth_east"
        )
        mock_vol_type_1 = mock.MagicMock()
        mock_vol_type_1.name = "__TYPE1__"
        mock_vol_type_2 = mock.MagicMock()
        mock_vol_type_2.name = "type2"
        mock_vol_type_3 = mock.MagicMock()
        mock_vol_type_3.name = "type3"
        # Simulate openstacksdk Block Storage Proxy: no volume_types attribute;
        # volume types are listed via c_client.types()
        mock_cinder_client = mock.Mock(spec=["types"])
        mock_cinder_client.types.return_value = [
            mock_vol_type_1,
            mock_vol_type_2,
            mock_vol_type_3,
        ]
        mock_cinder.return_value = mock_cinder_client
        storage_classes = self.driver._storageclass_definitions(
            self.context, self.cluster_obj
        )
        default_storage_class = storage_classes["defaultStorageClass"]
        volume_type = default_storage_class["name"]
        self.assertEqual("type1", volume_type)

    @mock.patch.object(helm.HelmLock, "_acquire_lock", return_value=True)
    @mock.patch.object(kubernetes.Client, "load", autospec=True)
    @mock.patch.object(helm.Client, "uninstall_release")
    def test_delete_cluster(self, mock_uninstall, mock_load, mock_helm_lock):
        mock_client = mock.MagicMock(spec=kubernetes.Client)
        mock_load.return_value = mock_client

        self.driver.delete_cluster(self.context, self.cluster_obj)

        mock_uninstall.assert_called_once_with(
            "cluster-example-a-111111111111", namespace="magnum-fakeproject"
        )

    @mock.patch.object(helm, "HelmLock")
    @mock.patch.object(helm.Client, "uninstall_release")
    def test_delete_cluster_uses_helm_lock(
        self, mock_uninstall, mock_lock_cls
    ):
        # delete_cluster must serialize with any concurrent
        # install/upgrade on the same release, using the same lock.
        mock_lock = mock.MagicMock()
        mock_lock_cls.return_value = mock_lock

        self.driver.delete_cluster(self.context, self.cluster_obj)

        mock_lock_cls.assert_called_once_with(
            "cluster-example-a-111111111111", "magnum-fakeproject"
        )
        mock_lock.__enter__.assert_called_once()
        mock_lock.__exit__.assert_called_once()
        mock_uninstall.assert_called_once_with(
            "cluster-example-a-111111111111", namespace="magnum-fakeproject"
        )

    @mock.patch.object(driver.Driver, "_update_helm_release")
    def test_update_cluster(self, mock_update):
        self.driver.update_cluster(self.context, self.cluster_obj)
        mock_update.assert_called_once_with(self.context, self.cluster_obj)

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

    @mock.patch.object(driver.Driver, "_get_allowed_cidrs")
    @mock.patch.object(
        driver.Driver, "_storageclass_definitions", return_value=mock.ANY
    )
    @mock.patch.object(
        driver.Driver, "_get_image_details", return_value=3 * [mock.ANY]
    )
    @mock.patch.object(helm.HelmLock, "renew", return_value=None)
    @mock.patch.object(helm.HelmLock, "_acquire_lock", return_value=True)
    @mock.patch.object(kubernetes.Client, "load", autospec=True)
    @mock.patch.object(helm.Client, "install_or_upgrade")
    def test_delete_nodegroup(
        self,
        mock_helm_update,
        mock_load,
        mock_helm_lock,
        mock_renew,
        mock_image_details,
        mock_storageclasses,
        mock_get_cidrs,
    ):

        ng_to_delete = next(
            ng for ng in self.cluster_obj.nodegroups if ng.role == "worker"
        )
        self.assertTrue(ng_to_delete is not None)

        self.driver.delete_nodegroup(
            self.context,
            self.cluster_obj,
            ng_to_delete,
        )

        # Check that node group has been removed from Helm values
        helm_values = mock_helm_update.call_args[0][2]
        remaining_nodegroups = [ng["name"] for ng in helm_values["nodeGroups"]]
        self.assertNotIn(ng_to_delete.name, remaining_nodegroups)

    @mock.patch.object(driver.Driver, "_get_allowed_cidrs")
    @mock.patch.object(
        driver.Driver, "_storageclass_definitions", return_value=mock.ANY
    )
    @mock.patch.object(
        driver.Driver, "_get_image_details", return_value=3 * [mock.ANY]
    )
    @mock.patch.object(helm.HelmLock, "renew", return_value=None)
    @mock.patch.object(helm.HelmLock, "_acquire_lock", return_value=True)
    @mock.patch.object(kubernetes.Client, "load", autospec=True)
    @mock.patch.object(helm.Client, "install_or_upgrade")
    def test_delete_nodegroup_picks_up_concurrent_change(
        self,
        mock_helm_update,
        mock_load,
        mock_helm_lock,
        mock_renew,
        mock_image_details,
        mock_storageclasses,
        mock_get_cidrs,
    ):
        # Regression test: the nodegroup list used to build Helm values
        # must be (re)computed after the Helm lock is acquired, not
        # captured by the caller beforehand - otherwise a nodegroup
        # change that lands while delete_nodegroup is waiting for the
        # lock would be silently dropped from the Helm values.
        ng_to_delete = next(
            ng for ng in self.cluster_obj.nodegroups if ng.role == "worker"
        )

        concurrent_ng = obj_utils.get_test_nodegroup(
            self.context,
            uuid=str(uuid4()),
            cluster_id=self.cluster_obj.uuid,
            name="concurrent-nodegroup",
            role="worker",
        )

        def fake_refresh(*args, **kwargs):
            # Simulate this nodegroup only landing in the DB while we
            # were waiting for the lock - i.e. after delete_nodegroup
            # was called, but before the Helm values are built.
            concurrent_ng.create()

        with mock.patch.object(
            type(self.cluster_obj), "refresh", side_effect=fake_refresh
        ):
            self.driver.delete_nodegroup(
                self.context, self.cluster_obj, ng_to_delete
            )

        helm_values = mock_helm_update.call_args[0][2]
        remaining_nodegroups = [ng["name"] for ng in helm_values["nodeGroups"]]
        self.assertNotIn(ng_to_delete.name, remaining_nodegroups)
        self.assertIn(concurrent_ng.name, remaining_nodegroups)

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

    @mock.patch("magnum.common.clients.OpenStackClients.nova")
    def test_validate_allowed_flavors_ram_error_openstacksdk(
        self, mock_osc_nova
    ):
        mock_flavor1 = mock.MagicMock(id=1, vcpus=1)
        mock_flavor1.name = "flavor_tiny"
        mock_flavor2 = mock.MagicMock(id=2, vcpus=1)
        mock_flavor2.name = "flavor_small"
        mock_flavor3 = mock.MagicMock(id=3, vcpus=4)
        mock_flavor3.name = "flavor_medium"
        filtered_flavors = [mock_flavor1, mock_flavor2, mock_flavor3]
        mock_nova = mock.MagicMock()
        mock_flavors_sdk = mock.Mock(spec=[])  # no 'list' → openstacksdk path
        mock_flavors_sdk.return_value = filtered_flavors
        mock_nova.flavors = mock_flavors_sdk
        mock_osc_nova.return_value = mock_nova
        self.assertRaises(
            exception.MagnumException,
            self.driver._validate_allowed_flavor,
            self.context,
            "DS9",
        )

    @mock.patch("magnum.common.clients.OpenStackClients.nova")
    def test_validate_allowed_flavors_vcpu_error_openstacksdk(
        self, mock_osc_nova
    ):
        mock_flavor1 = mock.MagicMock(id=1, vcpus=1)
        mock_flavor1.name = "flavor_tiny"
        mock_flavor2 = mock.MagicMock(id=2, vcpus=1)
        mock_flavor2.name = "flavor_small"
        mock_flavor3 = mock.MagicMock(id=3, vcpus=4)
        mock_flavor3.name = "flavor_medium"
        filtered_flavors = [mock_flavor1, mock_flavor2, mock_flavor3]
        mock_nova = mock.MagicMock()
        mock_flavors_sdk = mock.Mock(spec=[])  # no 'list' → openstacksdk path
        mock_flavors_sdk.return_value = filtered_flavors
        mock_nova.flavors = mock_flavors_sdk
        mock_osc_nova.return_value = mock_nova
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

    @mock.patch("magnum.common.clients.OpenStackClients.nova")
    def test_validate_allowed_flavors_ram_ok_openstacksdk(self, mock_osc_nova):
        mock_flavor1 = mock.MagicMock(id=1, vcpus=1)
        mock_flavor1.name = "flavor_tiny"
        mock_flavor2 = mock.MagicMock(id=2, vcpus=1)
        mock_flavor2.name = "flavor_small"
        mock_flavor3 = mock.MagicMock(id=3, vcpus=4)
        mock_flavor3.name = "flavor_medium"
        filtered_flavors = [mock_flavor1, mock_flavor2, mock_flavor3]
        mock_nova = mock.MagicMock()
        mock_flavors_sdk = mock.Mock(spec=[])  # no 'list' → openstacksdk path
        mock_flavors_sdk.return_value = filtered_flavors
        mock_nova.flavors = mock_flavors_sdk
        mock_osc_nova.return_value = mock_nova
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
    @mock.patch.object(helm.HelmLock, "renew", return_value=None)
    @mock.patch.object(helm.HelmLock, "_acquire_lock", return_value=True)
    @mock.patch.object(kubernetes.Client, "load", autospec=True)
    @mock.patch.object(driver.Driver, "_get_image_details", autospec=True)
    @mock.patch.object(helm.Client, "install_or_upgrade", autospec=True)
    def test_create_cluster_api_lb_allowed_cidrs(
        self,
        mock_install,
        mock_image,
        mock_load,
        mock_helm_lock,
        mock_renew,
        mock_appcred,
        mock_certs,
        mock_get_net,
        mock_validate_allowed_flavor,
        mock_storageclasses,
        mock_get_keystone_auth_enabled,
    ):
        cidrs = "192.168.0.0/16,10.0.0.0/8,123.123.123.123/32"
        self._set_cluster_test_labels(dict(api_master_lb_allowed_cidrs=cidrs))
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
    @mock.patch.object(helm.HelmLock, "renew", return_value=None)
    @mock.patch.object(helm.HelmLock, "_acquire_lock", return_value=True)
    @mock.patch.object(kubernetes.Client, "load", autospec=True)
    @mock.patch.object(driver.Driver, "_get_image_details", autospec=True)
    @mock.patch.object(helm.Client, "install_or_upgrade", autospec=True)
    def test_create_cluster_keystone_webhook_enabled(
        self,
        mock_install,
        mock_image,
        mock_load,
        mock_helm_lock,
        mock_renew,
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
        self._set_cluster_test_labels({})

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
    @mock.patch.object(helm.HelmLock, "renew", return_value=None)
    @mock.patch.object(helm.HelmLock, "_acquire_lock", return_value=True)
    @mock.patch.object(kubernetes.Client, "load", autospec=True)
    @mock.patch.object(driver.Driver, "_get_image_details", autospec=True)
    @mock.patch.object(helm.Client, "install_or_upgrade", autospec=True)
    def test_create_cluster_keystone_webhook_disabled(
        self,
        mock_install,
        mock_image,
        mock_load,
        mock_helm_lock,
        mock_renew,
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
        self._set_cluster_test_labels({})

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
    @mock.patch.object(helm.HelmLock, "renew", return_value=None)
    @mock.patch.object(helm.HelmLock, "_acquire_lock", return_value=True)
    @mock.patch.object(kubernetes.Client, "load", autospec=True)
    @mock.patch.object(driver.Driver, "_get_image_details", autospec=True)
    @mock.patch.object(helm.Client, "install_or_upgrade", autospec=True)
    def test_create_cluster_octavia_provider_amphora(
        self,
        mock_install,
        mock_image,
        mock_load,
        mock_helm_lock,
        mock_renew,
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
        self._set_cluster_test_labels(
            {
                "octavia_provider": "amphora",
                "octavia_lb_algorithm": "SOURCE_IP_PORT",
            }
        )

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
    @mock.patch.object(helm.HelmLock, "renew", return_value=None)
    @mock.patch.object(helm.HelmLock, "_acquire_lock", return_value=True)
    @mock.patch.object(kubernetes.Client, "load", autospec=True)
    @mock.patch.object(driver.Driver, "_get_image_details", autospec=True)
    @mock.patch.object(helm.Client, "install_or_upgrade", autospec=True)
    def test_create_cluster_octavia_provider_ovn(
        self,
        mock_install,
        mock_image,
        mock_load,
        mock_helm_lock,
        mock_renew,
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
        self._set_cluster_test_labels(
            {
                "octavia_provider": "ovn",
            }
        )

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
    @mock.patch.object(helm.HelmLock, "renew", return_value=None)
    @mock.patch.object(helm.HelmLock, "_acquire_lock", return_value=True)
    @mock.patch.object(kubernetes.Client, "load", autospec=True)
    @mock.patch.object(driver.Driver, "_get_image_details", autospec=True)
    @mock.patch.object(helm.Client, "install_or_upgrade", autospec=True)
    def test_create_cluster_octavia_disable_monitor(
        self,
        mock_install,
        mock_image,
        mock_load,
        mock_helm_lock,
        mock_renew,
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
        self._set_cluster_test_labels(
            {
                "octavia_lb_healthcheck": False,
            }
        )

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
        self._set_cluster_test_labels(
            dict(
                auto_scaling_enabled="true", min_node_count=3, max_node_count=0
            )
        )
        self.assertRaises(
            exception.MagnumException,
            self.driver._get_autoscale_values,
            self.cluster_obj,
            self.cluster_obj.nodegroups[0],
        )

    # -------------------------------------------------------------------------
    # Scale-to-zero validation tests
    # -------------------------------------------------------------------------

    def test_validate_scale_to_zero_min_zero_allowed(self):
        """min_node_count=0 is now allowed by the driver (scale to zero)."""
        self.cluster_obj.labels = dict(
            auto_scaling_enabled="true", min_node_count=0, max_node_count=5
        )
        # Should not raise – the driver allows min=0
        min_nodes, max_nodes = self.driver._validate_allowed_node_counts(
            self.cluster_obj, self.cluster_obj.nodegroups[0]
        )
        self.assertEqual(0, min_nodes)
        self.assertEqual(5, max_nodes)

    def test_validate_scale_to_zero_min_negative_rejected(self):
        """Negative min_node_count is still rejected."""
        self.cluster_obj.labels = dict(
            auto_scaling_enabled="true", min_node_count=-1, max_node_count=5
        )
        self.assertRaises(
            exception.NodeGroupInvalidInput,
            self.driver._validate_allowed_node_counts,
            self.cluster_obj,
            self.cluster_obj.nodegroups[0],
        )

    def test_validate_scale_to_zero_max_lt_min_still_rejected(self):
        """max < min is still rejected even when min is close to zero."""
        self.cluster_obj.labels = dict(
            auto_scaling_enabled="true", min_node_count=2, max_node_count=1
        )
        self.assertRaises(
            exception.NodeGroupInvalidInput,
            self.driver._validate_allowed_node_counts,
            self.cluster_obj,
            self.cluster_obj.nodegroups[0],
        )

    def test_validate_scale_to_zero_old_chart_rejected(self):
        """min_node_count=0 with chart < 0.26.0 raises an error."""
        self.cluster_obj.labels = dict(
            auto_scaling_enabled="true", min_node_count=0, max_node_count=5
        )
        self.cluster_obj.cluster_template.labels["capi_helm_chart_version"] = (
            "0.25.9"
        )
        self.assertRaises(
            exception.NodeGroupInvalidInput,
            self.driver._validate_allowed_node_counts,
            self.cluster_obj,
            self.cluster_obj.nodegroups[0],
        )

    def test_validate_scale_to_zero_newer_major_chart_allowed(self):
        """min_node_count=0 with chart 1.0.0 (major bump) is allowed."""
        self.cluster_obj.labels = dict(
            auto_scaling_enabled="true", min_node_count=0, max_node_count=5
        )
        self.cluster_obj.cluster_template.labels["capi_helm_chart_version"] = (
            "1.0.0"
        )
        min_nodes, max_nodes = self.driver._validate_allowed_node_counts(
            self.cluster_obj, self.cluster_obj.nodegroups[0]
        )
        self.assertEqual(0, min_nodes)
        self.assertEqual(5, max_nodes)

    def test_validate_scale_to_zero_prerelease_chart_allowed(self):
        """min_node_count=0 with chart 0.26.0-alpha.1 is allowed."""
        self.cluster_obj.labels = dict(
            auto_scaling_enabled="true", min_node_count=0, max_node_count=5
        )
        self.cluster_obj.cluster_template.labels["capi_helm_chart_version"] = (
            "0.26.0-alpha.1"
        )
        min_nodes, max_nodes = self.driver._validate_allowed_node_counts(
            self.cluster_obj, self.cluster_obj.nodegroups[0]
        )
        self.assertEqual(0, min_nodes)
        self.assertEqual(5, max_nodes)

    @mock.patch.object(driver.Driver, "_update_nodegroup_status")
    @mock.patch.object(kubernetes.Client, "load")
    def test_update_worker_nodegroup_status_zero_replicas_running(
        self, mock_load, mock_update
    ):
        """MachineDeployment with 0 replicas in Running phase is READY."""
        mock_client = mock.MagicMock(spec=kubernetes.Client)
        mock_load.return_value = mock_client
        nodegroup = mock.MagicMock()
        nodegroup.name = "workers"
        # A MachineDeployment scaled to 0 reports phase=Running
        md = {
            "status": {
                "phase": "Running",
                "replicas": 0,
                "readyReplicas": 0,
            }
        }
        mock_client.get_machine_deployment.return_value = md

        self.driver._update_worker_nodegroup_status(
            self.cluster_obj, nodegroup
        )

        mock_update.assert_called_once_with(
            self.cluster_obj, mock.ANY, driver.NodeGroupState.READY
        )

    @mock.patch.object(driver.Driver, "_update_nodegroup_status")
    @mock.patch.object(kubernetes.Client, "load")
    def test_update_worker_nodegroup_status_zero_replicas_scaling_down(
        self, mock_load, mock_update
    ):
        """ScalingDown phase (transitioning to 0 replicas) is PENDING."""
        mock_client = mock.MagicMock(spec=kubernetes.Client)
        mock_load.return_value = mock_client
        nodegroup = mock.MagicMock()
        nodegroup.name = "workers"
        md = {"status": {"phase": "ScalingDown"}}
        mock_client.get_machine_deployment.return_value = md

        self.driver._update_worker_nodegroup_status(
            self.cluster_obj, nodegroup
        )

        mock_update.assert_called_once_with(
            self.cluster_obj, mock.ANY, driver.NodeGroupState.PENDING
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
    @mock.patch.object(helm.HelmLock, "renew", return_value=None)
    @mock.patch.object(helm.HelmLock, "_acquire_lock", return_value=True)
    @mock.patch.object(kubernetes.Client, "load", autospec=True)
    @mock.patch.object(driver.Driver, "_get_image_details", autospec=True)
    @mock.patch.object(helm.Client, "install_or_upgrade", autospec=True)
    def test_create_cluster_zero_node_count_worker(
        self,
        mock_install,
        mock_image,
        mock_load,
        mock_helm_lock,
        mock_renew,
        mock_appcred,
        mock_certs,
        mock_get_net,
        mock_validate_allowed_flavor,
        mock_storageclasses,
        mock_get_keystone_auth_enabled,
    ):
        """Creating a cluster with a worker nodegroup starting at 0 nodes."""
        self.cluster_obj.labels = {}
        for ng in self.cluster_obj.nodegroups:
            if ng.role != "master":
                ng.node_count = 0
                ng.save()

        mock_image.return_value = ("imageid1", "1.27.4", "ubuntu")
        mock_client = mock.MagicMock(spec=kubernetes.Client)
        mock_load.return_value = mock_client

        self.driver.create_cluster(self.context, self.cluster_obj, 10)

        helm_install_values = mock_install.call_args[0][3]
        node_groups = helm_install_values["nodeGroups"]
        self.assertEqual(1, len(node_groups))
        worker_ng = node_groups[0]
        # machineCount=0 must be passed to the Helm chart
        self.assertEqual(0, worker_ng["machineCount"])
        # No autoscaling keys when autoscaling is disabled
        self.assertNotIn("autoscale", worker_ng)
        self.assertNotIn("machineCountMin", worker_ng)
        self.assertNotIn("machineCountMax", worker_ng)

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
    @mock.patch.object(helm.HelmLock, "renew", return_value=None)
    @mock.patch.object(helm.HelmLock, "_acquire_lock", return_value=True)
    @mock.patch.object(kubernetes.Client, "load", autospec=True)
    @mock.patch.object(driver.Driver, "_get_image_details", autospec=True)
    @mock.patch.object(helm.Client, "install_or_upgrade", autospec=True)
    def test_create_cluster_autoscale_min_zero(
        self,
        mock_install,
        mock_image,
        mock_load,
        mock_helm_lock,
        mock_renew,
        mock_appcred,
        mock_certs,
        mock_get_net,
        mock_validate_allowed_flavor,
        mock_storageclasses,
        mock_get_keystone_auth_enabled,
    ):
        """Autoscaling with min_node_count=0 passes driver validation.

        Note: the Helm chart (0.10.1) does not yet support machineCountMin=0
        in autoscaling mode. Users need a chart version that removes this
        restriction to fully enable autoscaling scale-to-zero.
        """
        for ng in self.cluster_obj.nodegroups:
            if ng.role != "master":
                ng.node_count = 0
                ng.save()

        self._set_cluster_test_labels(
            dict(
                auto_scaling_enabled="true",
                min_node_count=0,
                max_node_count=5,
            )
        )
        mock_image.return_value = ("imageid1", "1.27.4", "ubuntu")
        mock_client = mock.MagicMock(spec=kubernetes.Client)
        mock_load.return_value = mock_client

        self.driver.create_cluster(self.context, self.cluster_obj, 10)

        helm_install_values = mock_install.call_args[0][3]
        node_groups = helm_install_values["nodeGroups"]
        self.assertEqual(1, len(node_groups))
        worker_ng = node_groups[0]
        # The driver must pass machineCountMin=0 to Helm
        self.assertEqual("true", worker_ng["autoscale"])
        self.assertEqual(0, worker_ng["machineCountMin"])
        self.assertEqual(5, worker_ng["machineCountMax"])

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
    @mock.patch.object(helm.HelmLock, "renew", return_value=None)
    @mock.patch.object(helm.HelmLock, "_acquire_lock", return_value=True)
    @mock.patch.object(kubernetes.Client, "load", autospec=True)
    @mock.patch.object(driver.Driver, "_get_image_details", autospec=True)
    @mock.patch.object(helm.Client, "install_or_upgrade", autospec=True)
    def test_scale_from_zero_to_n_workers(
        self,
        mock_install,
        mock_image,
        mock_load,
        mock_helm_lock,
        mock_renew,
        mock_appcred,
        mock_certs,
        mock_get_net,
        mock_validate_allowed_flavor,
        mock_storageclasses,
        mock_get_keystone_auth_enabled,
    ):
        """Resize a worker nodegroup from 0 to N nodes (scale from zero)."""
        self.cluster_obj.labels = {}
        # Simulate the conductor having already updated node_count to 3
        for ng in self.cluster_obj.nodegroups:
            if ng.role != "master":
                ng.node_count = 3
                ng.save()

        mock_image.return_value = ("imageid1", "1.27.4", "ubuntu")
        mock_client = mock.MagicMock(spec=kubernetes.Client)
        mock_load.return_value = mock_client

        self.driver.resize_cluster(
            self.context, self.cluster_obj, None, 3, None
        )

        helm_install_values = mock_install.call_args[0][3]
        node_groups = helm_install_values["nodeGroups"]
        self.assertEqual(1, len(node_groups))
        self.assertEqual(3, node_groups[0]["machineCount"])

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
    @mock.patch.object(helm.HelmLock, "renew", return_value=None)
    @mock.patch.object(helm.HelmLock, "_acquire_lock", return_value=True)
    @mock.patch.object(kubernetes.Client, "load", autospec=True)
    @mock.patch.object(driver.Driver, "_get_image_details", autospec=True)
    @mock.patch.object(helm.Client, "install_or_upgrade", autospec=True)
    def test_update_nodegroup_to_zero_node_count(
        self,
        mock_install,
        mock_image,
        mock_load,
        mock_helm_lock,
        mock_renew,
        mock_appcred,
        mock_certs,
        mock_get_net,
        mock_validate_allowed_flavor,
        mock_storageclasses,
        mock_get_keystone_auth_enabled,
    ):
        """update_nodegroup with node_count=0 passes machineCount=0 to Helm."""
        self.cluster_obj.labels = {}
        worker_ng = None
        for ng in self.cluster_obj.nodegroups:
            if ng.role != "master":
                worker_ng = ng
                ng.node_count = 0
                ng.save()

        mock_image.return_value = ("imageid1", "1.27.4", "ubuntu")
        mock_client = mock.MagicMock(spec=kubernetes.Client)
        mock_load.return_value = mock_client

        self.driver.update_nodegroup(self.context, self.cluster_obj, worker_ng)

        helm_install_values = mock_install.call_args[0][3]
        node_groups = helm_install_values["nodeGroups"]
        self.assertEqual(1, len(node_groups))
        self.assertEqual(0, node_groups[0]["machineCount"])

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
    @mock.patch.object(helm.HelmLock, "renew", return_value=None)
    @mock.patch.object(helm.HelmLock, "_acquire_lock", return_value=True)
    @mock.patch.object(kubernetes.Client, "load", autospec=True)
    @mock.patch.object(driver.Driver, "_get_image_details", autospec=True)
    @mock.patch.object(helm.Client, "install_or_upgrade", autospec=True)
    def test_create_nodegroup_zero_count_allowed(
        self,
        mock_install,
        mock_image,
        mock_load,
        mock_helm_lock,
        mock_renew,
        mock_appcred,
        mock_certs,
        mock_get_net,
        mock_validate_allowed_flavor,
        mock_storageclasses,
        mock_get_keystone_auth_enabled,
    ):
        """create_nodegroup with node_count=0 is allowed (scale from zero)."""
        self.cluster_obj.labels = {}
        new_ng = obj_utils.create_test_nodegroup(
            self.context,
            uuid=uuid4(),
            id=789,
            name="zero-start-group",
            node_count=0,
        )
        self.cluster_obj.nodegroups.append(new_ng)
        mock_image.return_value = ("imageid1", "1.27.4", "ubuntu")
        mock_client = mock.MagicMock(spec=kubernetes.Client)
        mock_load.return_value = mock_client

        self.driver.create_nodegroup(self.context, self.cluster_obj, new_ng)

        helm_install_values = mock_install.call_args[0][3]
        node_groups = helm_install_values["nodeGroups"]
        zero_ngs = [ng for ng in node_groups if ng["name"] == new_ng.name]
        self.assertEqual(1, len(zero_ngs))
        self.assertEqual(0, zero_ngs[0]["machineCount"])

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
    @mock.patch.object(helm.HelmLock, "renew", return_value=None)
    @mock.patch.object(helm.HelmLock, "_acquire_lock", return_value=True)
    @mock.patch.object(kubernetes.Client, "load", autospec=True)
    @mock.patch.object(driver.Driver, "_get_image_details", autospec=True)
    @mock.patch.object(helm.Client, "install_or_upgrade", autospec=True)
    def test_autoscale_extra_nodegroup_min_zero(
        self,
        mock_install,
        mock_image,
        mock_load,
        mock_helm_lock,
        mock_renew,
        mock_appcred,
        mock_certs,
        mock_get_net,
        mock_validate_allowed_flavor,
        mock_storageclasses,
        mock_get_keystone_auth_enabled,
    ):
        """Autoscaling extra nodegroup with min_node_count=0 is forwarded."""
        self._set_cluster_test_labels({"auto_scaling_enabled": "true"})

        zero_min_ng = obj_utils.create_test_nodegroup(
            self.context,
            uuid=uuid4(),
            id=321,
            name="zero-min-group",
            node_count=0,
            min_node_count=0,
            max_node_count=4,
        )
        self.cluster_obj.nodegroups.append(zero_min_ng)

        mock_image.return_value = ("imageid1", "1.27.4", "ubuntu")
        mock_client = mock.MagicMock(spec=kubernetes.Client)
        mock_load.return_value = mock_client

        self.driver.create_cluster(self.context, self.cluster_obj, 10)

        helm_install_values = mock_install.call_args[0][3]
        node_groups = helm_install_values["nodeGroups"]
        zero_ngs = [ng for ng in node_groups if ng["name"] == zero_min_ng.name]
        self.assertEqual(1, len(zero_ngs))
        ng = zero_ngs[0]
        self.assertEqual("true", ng["autoscale"])
        self.assertEqual(0, ng["machineCountMin"])
        self.assertEqual(4, ng["machineCountMax"])

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
    @mock.patch.object(helm.HelmLock, "renew", return_value=None)
    @mock.patch.object(helm.HelmLock, "_acquire_lock", return_value=True)
    @mock.patch.object(kubernetes.Client, "load", autospec=True)
    @mock.patch.object(driver.Driver, "_get_image_details", autospec=True)
    @mock.patch.object(helm.Client, "install_or_upgrade", autospec=True)
    def test_autoscale_extra_nodegroup_min_zero_equal_max_no_autoscale(
        self,
        mock_install,
        mock_image,
        mock_load,
        mock_helm_lock,
        mock_renew,
        mock_appcred,
        mock_certs,
        mock_get_net,
        mock_validate_allowed_flavor,
        mock_storageclasses,
        mock_get_keystone_auth_enabled,
    ):
        """Extra nodegroup with min=max=0 disables autoscaling (min==max)."""
        self.cluster_obj.labels = {"auto_scaling_enabled": "true"}

        # min == max → autoscaling disabled for this nodegroup
        zero_ng = obj_utils.create_test_nodegroup(
            self.context,
            uuid=uuid4(),
            id=654,
            name="zero-zero-group",
            node_count=0,
            min_node_count=0,
            max_node_count=0,
        )
        self.cluster_obj.nodegroups.append(zero_ng)

        mock_image.return_value = ("imageid1", "1.27.4", "ubuntu")
        mock_client = mock.MagicMock(spec=kubernetes.Client)
        mock_load.return_value = mock_client

        self.driver.create_cluster(self.context, self.cluster_obj, 10)

        helm_install_values = mock_install.call_args[0][3]
        node_groups = helm_install_values["nodeGroups"]
        zero_ngs = [ng for ng in node_groups if ng["name"] == zero_ng.name]
        self.assertEqual(1, len(zero_ngs))
        ng = zero_ngs[0]
        # min == max → autoscale args NOT emitted
        self.assertNotIn("autoscale", ng)
        self.assertNotIn("machineCountMin", ng)
        self.assertEqual(0, ng["machineCount"])

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
    @mock.patch.object(helm.HelmLock, "renew", return_value=None)
    @mock.patch.object(helm.HelmLock, "_acquire_lock", return_value=True)
    @mock.patch.object(kubernetes.Client, "load", autospec=True)
    @mock.patch.object(driver.Driver, "_get_image_details", autospec=True)
    @mock.patch.object(helm.Client, "install_or_upgrade", autospec=True)
    def test_default_nodegroup_autoscale_min_zero_max_n(
        self,
        mock_install,
        mock_image,
        mock_load,
        mock_helm_lock,
        mock_renew,
        mock_appcred,
        mock_certs,
        mock_get_net,
        mock_validate_allowed_flavor,
        mock_storageclasses,
        mock_get_keystone_auth_enabled,
    ):
        """Default nodegroup: autoscaling with min=0 via labels."""
        self._set_cluster_test_labels(
            dict(
                auto_scaling_enabled="true",
                min_node_count=0,
                max_node_count=5,
            )
        )
        for ng in self.cluster_obj.nodegroups:
            if ng.role != "master":
                ng.node_count = 0
                ng.save()

        mock_image.return_value = ("imageid1", "1.27.4", "ubuntu")
        mock_client = mock.MagicMock(spec=kubernetes.Client)
        mock_load.return_value = mock_client

        self.driver.create_cluster(self.context, self.cluster_obj, 10)

        helm_install_values = mock_install.call_args[0][3]
        node_groups = helm_install_values["nodeGroups"]
        default_ng = next(
            ng
            for ng in node_groups
            if ng["name"] == self.cluster_obj.default_ng_worker.name
        )
        self.assertEqual("true", default_ng["autoscale"])
        self.assertEqual(0, default_ng["machineCountMin"])
        self.assertEqual(5, default_ng["machineCountMax"])

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
    @mock.patch.object(helm.HelmLock, "renew", return_value=None)
    @mock.patch.object(helm.HelmLock, "_acquire_lock", return_value=True)
    @mock.patch.object(kubernetes.Client, "load", autospec=True)
    @mock.patch.object(driver.Driver, "_get_image_details", autospec=True)
    @mock.patch.object(helm.Client, "install_or_upgrade", autospec=True)
    def test_create_cluster_auto_scale_enabled(
        self,
        mock_install,
        mock_image,
        mock_load,
        mock_helm_lock,
        mock_renew,
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
        self._set_cluster_test_labels(auto_scale_labels)

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
    @mock.patch.object(helm.HelmLock, "renew", return_value=None)
    @mock.patch.object(helm.HelmLock, "_acquire_lock", return_value=True)
    @mock.patch.object(kubernetes.Client, "load", autospec=True)
    @mock.patch.object(driver.Driver, "_get_image_details", autospec=True)
    @mock.patch.object(helm.Client, "install_or_upgrade", autospec=True)
    def test_create_cluster_auto_scale_disabled(
        self,
        mock_install,
        mock_image,
        mock_load,
        mock_helm_lock,
        mock_renew,
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
        self._set_cluster_test_labels(auto_scale_labels)

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
    @mock.patch.object(helm.HelmLock, "renew", return_value=None)
    @mock.patch.object(helm.HelmLock, "_acquire_lock", return_value=True)
    @mock.patch.object(kubernetes.Client, "load", autospec=True)
    @mock.patch.object(driver.Driver, "_get_image_details", autospec=True)
    @mock.patch.object(helm.Client, "install_or_upgrade", autospec=True)
    def test_nodegroup_node_count_validation(
        self,
        mock_install,
        mock_image,
        mock_load,
        mock_helm_lock,
        mock_renew,
        mock_appcred,
        mock_certs,
        mock_get_net,
        mock_validate_allowed_flavor,
        mock_storageclasses,
    ):
        auto_scale_labels = dict(
            auto_scaling_enabled="true", min_node_count=2, max_node_count=6
        )
        self._set_cluster_test_labels(auto_scale_labels)

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
    @mock.patch.object(helm.HelmLock, "renew", return_value=None)
    @mock.patch.object(helm.HelmLock, "_acquire_lock", return_value=True)
    @mock.patch.object(kubernetes.Client, "load", autospec=True)
    @mock.patch.object(driver.Driver, "_get_image_details", autospec=True)
    @mock.patch.object(helm.Client, "install_or_upgrade", autospec=True)
    def test_create_extra_nodegroup_auto_scale_values(
        self,
        mock_install,
        mock_image,
        mock_load,
        mock_helm_lock,
        mock_renew,
        mock_appcred,
        mock_certs,
        mock_get_net,
        mock_validate_allowed_flavor,
        mock_storageclasses,
    ):
        auto_scale_labels = dict(
            auto_scaling_enabled="true", min_node_count=2, max_node_count=6
        )
        self._set_cluster_test_labels(auto_scale_labels)

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

        # Unpack some values for asserting against
        helm_install_values = mock_install.call_args[0][3]
        print(helm_install_values)
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
        ng = helm_values_autoscale_nodegroup[0]
        self.assertEqual(ng["autoscale"], "true")
        self.assertEqual(
            ng["machineCountMin"], auto_scale_nodegroup.min_node_count
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
    @mock.patch.object(helm.HelmLock, "renew", return_value=None)
    @mock.patch.object(helm.HelmLock, "_acquire_lock", return_value=True)
    @mock.patch.object(kubernetes.Client, "load", autospec=True)
    @mock.patch.object(driver.Driver, "_get_image_details", autospec=True)
    @mock.patch.object(helm.Client, "install_or_upgrade", autospec=True)
    def test_create_cluster_boot_volume_size_label_valid(
        self,
        mock_install,
        mock_image,
        mock_load,
        mock_helm_lock,
        mock_renew,
        mock_appcred,
        mock_certs,
        mock_get_net,
        mock_validate_allowed_flavor,
        mock_storageclasses,
    ):
        disk_size_configuration_value = 15
        disk_size_label_value = 32

        CONF.cinder.default_boot_volume_size = disk_size_configuration_value
        self._set_cluster_test_labels(
            {"boot_volume_size": str(disk_size_label_value)}
        )

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
    @mock.patch.object(helm.HelmLock, "renew", return_value=None)
    @mock.patch.object(helm.HelmLock, "_acquire_lock", return_value=True)
    @mock.patch.object(kubernetes.Client, "load", autospec=True)
    @mock.patch.object(driver.Driver, "_get_image_details", autospec=True)
    @mock.patch.object(helm.Client, "install_or_upgrade", autospec=True)
    def test_create_cluster_boot_volume_size_label_default(
        self,
        mock_install,
        mock_image,
        mock_load,
        mock_helm_lock,
        mock_renew,
        mock_appcred,
        mock_certs,
        mock_get_net,
        mock_validate_allowed_flavor,
        mock_storageclasses,
    ):
        disk_size_configuration_value = 15

        CONF.cinder.default_boot_volume_size = disk_size_configuration_value
        self._set_cluster_test_labels({})

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
    @mock.patch.object(helm.HelmLock, "renew", return_value=None)
    @mock.patch.object(helm.HelmLock, "_acquire_lock", return_value=True)
    @mock.patch.object(kubernetes.Client, "load", autospec=True)
    @mock.patch.object(driver.Driver, "_get_image_details", autospec=True)
    @mock.patch.object(helm.Client, "install_or_upgrade", autospec=True)
    def test_create_cluster_boot_volume_size_label_invalid(
        self,
        mock_install,
        mock_image,
        mock_load,
        mock_helm_lock,
        mock_renew,
        mock_appcred,
        mock_certs,
        mock_get_net,
        mock_validate_allowed_flavor,
        mock_storageclasses,
    ):
        disk_size_configuration_value = 15

        CONF.cinder.default_boot_volume_size = disk_size_configuration_value
        self._set_cluster_test_labels({"boot_volume_size": "NotANumber"})

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
    @mock.patch.object(helm.HelmLock, "renew", return_value=None)
    @mock.patch.object(helm.HelmLock, "_acquire_lock", return_value=True)
    @mock.patch.object(kubernetes.Client, "load", autospec=True)
    @mock.patch.object(driver.Driver, "_get_image_details", autospec=True)
    @mock.patch.object(helm.Client, "install_or_upgrade", autospec=True)
    def test_nodegroup_node_count_min_max_equal(
        self,
        mock_install,
        mock_image,
        mock_load,
        mock_helm_lock,
        mock_renew,
        mock_appcred,
        mock_certs,
        mock_get_net,
        mock_validate_allowed_flavor,
        mock_storageclasses,
    ):
        # When autoscaling is enabled but no min/max node counts are
        # provided for the default node group, we want autoscaling to
        # be disabled on the default node group.
        self._set_cluster_test_labels({"auto_scaling_enabled": "true"})
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
    @mock.patch.object(helm.HelmLock, "renew", return_value=None)
    @mock.patch.object(helm.HelmLock, "_acquire_lock", return_value=True)
    @mock.patch.object(kubernetes.Client, "load", autospec=True)
    @mock.patch.object(driver.Driver, "_get_image_details", autospec=True)
    @mock.patch.object(helm.Client, "install_or_upgrade", autospec=True)
    def test_nodegroup_master_lb_fip(
        self,
        mock_install,
        mock_image,
        mock_load,
        mock_helm_lock,
        mock_renew,
        mock_appcred,
        mock_certs,
        mock_get_net,
        mock_validate_allowed_flavor,
        mock_storageclasses,
    ):
        # Disabled master LB in labels.
        self._set_cluster_test_labels(
            {"master_lb_floating_ip_enabled": "false"}
        )
        self.cluster_obj.cluster_template.floating_ip_enabled = True
        mock_image.return_value = (
            "imageid1",
            "1.27.4",
            "ubuntu",
        )
        self.driver.create_cluster(self.context, self.cluster_obj, 10)
        helm_install_values = mock_install.call_args[0][3]
        apiserver_expected = {
            "associateFloatingIP": False,
            "enableLoadBalancer": True,
            "loadBalancerProvider": "amphora",
        }
        self.assertEqual(apiserver_expected, helm_install_values["apiServer"])

        # Enabled master LB in labels.
        self._set_cluster_test_labels(
            {"master_lb_floating_ip_enabled": "true"}
        )
        self.cluster_obj.cluster_template.floating_ip_enabled = False
        mock_image.return_value = (
            "imageid1",
            "1.27.4",
            "ubuntu",
        )
        self.driver.create_cluster(self.context, self.cluster_obj, 10)
        helm_install_values = mock_install.call_args[0][3]
        apiserver_expected = {
            "associateFloatingIP": True,
            "enableLoadBalancer": True,
            "loadBalancerProvider": "amphora",
        }
        self.assertEqual(apiserver_expected, helm_install_values["apiServer"])

        # Absent master lb label means default to True.
        # cluster_template floating_ip_enabled is used only for FIP on nodes.
        self.cluster_obj.labels = {}
        self.cluster_obj.cluster_template.floating_ip_enabled = False
        mock_image.return_value = (
            "imageid1",
            "1.27.4",
            "ubuntu",
        )
        self.driver.create_cluster(self.context, self.cluster_obj, 10)
        helm_install_values = mock_install.call_args[0][3]
        apiserver_expected = {
            "associateFloatingIP": True,
            "enableLoadBalancer": True,
            "loadBalancerProvider": "amphora",
        }
        self.assertEqual(apiserver_expected, helm_install_values["apiServer"])

        # Absent master lb label.
        # cluster_template floating_ip_enabled is used only for FIP on nodes.
        self.cluster_obj.labels = {}
        self.cluster_obj.cluster_template.floating_ip_enabled = True
        mock_image.return_value = (
            "imageid1",
            "1.27.4",
            "ubuntu",
        )
        self.driver.create_cluster(self.context, self.cluster_obj, 10)
        helm_install_values = mock_install.call_args[0][3]
        apiserver_expected = {
            "associateFloatingIP": True,
            "enableLoadBalancer": True,
            "loadBalancerProvider": "amphora",
        }
        self.assertEqual(apiserver_expected, helm_install_values["apiServer"])

    @mock.patch.object(app_creds, "delete_app_cred", autospec=True)
    @mock.patch.object(
        driver.Driver, "_ensure_certificate_secrets", autospec=True
    )
    @mock.patch.object(driver.Driver, "_create_appcred_secret", autospec=True)
    @mock.patch.object(driver.Driver, "_get_app_cred_id", autospec=True)
    def test_rotate_credential(
        self, mock_get, mock_create, mock_certs, mock_delete
    ):
        app_cred_id_old = "abcde12345"
        app_cred_id_new = "edcba54321"

        mock_app_cred = mock.MagicMock()
        mock_app_cred.id = app_cred_id_new

        mock_get.return_value = app_cred_id_old
        mock_create.return_value = mock_app_cred

        self.driver.rotate_credential(self.context, self.cluster_obj)

        mock_get.assert_called_once_with(self.driver, self.cluster_obj)
        mock_create.assert_called_once_with(
            self.driver, self.context, self.cluster_obj
        )
        mock_certs.assert_not_called()  # user_id unchanged
        mock_delete.assert_called_once_with(self.cluster_obj, app_cred_id_old)

        self.assertEqual(self.context.user_id, self.cluster_obj.user_id)
        self.assertEqual(
            fields.ClusterStatus.UPDATE_COMPLETE, self.cluster_obj.status
        )
        self.assertIsNone(self.cluster_obj.status_reason)

    @mock.patch.object(app_creds, "delete_app_cred", autospec=True)
    @mock.patch.object(
        driver.Driver, "_ensure_certificate_secrets", autospec=True
    )
    @mock.patch.object(driver.Driver, "_create_appcred_secret", autospec=True)
    @mock.patch.object(driver.Driver, "_get_app_cred_id", autospec=True)
    def test_rotate_credential_new_user(
        self, mock_get, mock_create, mock_certs, mock_delete
    ):
        self.cluster_obj.user_id = "user_a"
        self.context.user_id = "user_b"

        app_cred_id_old = "abcde12345"
        app_cred_id_new = "edcba54321"

        mock_app_cred = mock.MagicMock()
        mock_app_cred.id = app_cred_id_new

        mock_get.return_value = app_cred_id_old
        mock_create.return_value = mock_app_cred

        self.driver.rotate_credential(self.context, self.cluster_obj)

        mock_get.assert_called_once_with(self.driver, self.cluster_obj)
        mock_create.assert_called_once_with(
            self.driver, self.context, self.cluster_obj
        )
        mock_certs.assert_called_once_with(
            self.driver, self.context, self.cluster_obj
        )
        mock_delete.assert_called_once_with(self.cluster_obj, app_cred_id_old)

        self.assertEqual(
            fields.ClusterStatus.UPDATE_COMPLETE, self.cluster_obj.status
        )
        self.assertIsNone(self.cluster_obj.status_reason)

    @mock.patch.object(app_creds, "delete_app_cred", autospec=True)
    @mock.patch.object(
        driver.Driver, "_ensure_certificate_secrets", autospec=True
    )
    @mock.patch.object(driver.Driver, "_create_appcred_secret", autospec=True)
    @mock.patch.object(driver.Driver, "_get_app_cred_id", autospec=True)
    def test_rotate_credential_delete_failed(
        self, mock_get, mock_create, mock_certs, mock_delete
    ):
        app_cred_id = "abcde12345"

        mock_app_cred = mock.MagicMock()
        mock_app_cred.id = app_cred_id

        mock_get.return_value = None
        mock_create.return_value = mock_app_cred

        self.driver.rotate_credential(self.context, self.cluster_obj)

        mock_get.assert_called_once_with(self.driver, self.cluster_obj)
        mock_create.assert_called_once_with(
            self.driver, self.context, self.cluster_obj
        )
        mock_certs.assert_not_called()  # user_id unchanged
        mock_delete.assert_not_called()

        # Failure to delete the old app cred is not a error condition
        self.assertEqual(self.context.user_id, self.cluster_obj.user_id)
        self.assertEqual(
            fields.ClusterStatus.UPDATE_COMPLETE, self.cluster_obj.status
        )
        self.assertIsNone(self.cluster_obj.status_reason)

    @mock.patch.object(app_creds, "delete_app_cred", autospec=True)
    @mock.patch.object(
        driver.Driver, "_ensure_certificate_secrets", autospec=True
    )
    @mock.patch.object(driver.Driver, "_create_appcred_secret", autospec=True)
    @mock.patch.object(driver.Driver, "_get_app_cred_id", autospec=True)
    def test_rotate_credential_create_failed(
        self, mock_get, mock_create, mock_certs, mock_delete
    ):
        mock_create.side_effect = exception.AuthorizationFailure

        self.assertRaises(
            exception.MagnumException,
            self.driver.rotate_credential,
            self.context,
            self.cluster_obj,
        )

        mock_get.assert_called_once_with(self.driver, self.cluster_obj)
        mock_create.assert_called_once_with(
            self.driver, self.context, self.cluster_obj
        )
        mock_certs.assert_not_called()
        mock_delete.assert_not_called()

        self.assertEqual(
            fields.ClusterStatus.UPDATE_FAILED, self.cluster_obj.status
        )
        self.assertIsNotNone(self.cluster_obj.status_reason)

    @mock.patch.object(app_creds, "delete_app_cred", autospec=True)
    @mock.patch.object(
        driver.Driver, "_ensure_certificate_secrets", autospec=True
    )
    @mock.patch.object(driver.Driver, "_create_appcred_secret", autospec=True)
    @mock.patch.object(driver.Driver, "_get_app_cred_id", autospec=True)
    def test_rotate_credential_is_none(
        self, mock_get, mock_create, mock_certs, mock_delete
    ):
        mock_create.return_value = None

        self.assertRaises(
            exception.MagnumException,
            self.driver.rotate_credential,
            self.context,
            self.cluster_obj,
        )

        mock_get.assert_called_once_with(self.driver, self.cluster_obj)
        mock_create.assert_called_once_with(
            self.driver, self.context, self.cluster_obj
        )
        mock_certs.assert_not_called()
        mock_delete.assert_not_called()

        self.assertEqual(
            fields.ClusterStatus.UPDATE_FAILED, self.cluster_obj.status
        )
        self.assertIsNotNone(self.cluster_obj.status_reason)

    @mock.patch.object(app_creds, "delete_app_cred", autospec=True)
    @mock.patch.object(
        driver.Driver, "_ensure_certificate_secrets", autospec=True
    )
    @mock.patch.object(driver.Driver, "_create_appcred_secret", autospec=True)
    @mock.patch.object(driver.Driver, "_get_app_cred_id", autospec=True)
    def test_rotate_credential_unchanged(
        self, mock_get, mock_create, mock_certs, mock_delete
    ):
        mock_app_cred_id = "abcde12355"
        mock_app_cred = mock.MagicMock()
        mock_app_cred.id = mock_app_cred_id

        mock_get.return_value = mock_app_cred_id
        mock_create.return_value = mock_app_cred

        self.assertRaises(
            exception.MagnumException,
            self.driver.rotate_credential,
            self.context,
            self.cluster_obj,
        )

        mock_get.assert_called_once_with(self.driver, self.cluster_obj)
        mock_create.assert_called_once_with(
            self.driver, self.context, self.cluster_obj
        )
        mock_certs.assert_not_called()
        mock_delete.assert_not_called()

        self.assertEqual(
            fields.ClusterStatus.UPDATE_FAILED, self.cluster_obj.status
        )
        self.assertIsNotNone(self.cluster_obj.status_reason)


class TestClusterLabelRegistry(base.DbTestCase):
    """Enforce that every label key used in the driver is registered."""

    def test_all_label_calls_use_registered_keys(self):
        import ast
        import inspect

        from magnum_capi_helm import conf as capi_conf
        from magnum_capi_helm import driver as capi_driver

        regular_label_names = set(
            opt.name for opt in capi_conf.capi_helm_cluster_labels_opts
        )
        deprecated_label_names = []
        for opt in capi_conf.capi_helm_cluster_labels_opts:
            for dep_opt in opt.deprecated_opts:
                deprecated_label_names.append(dep_opt.name)
        registered = frozenset(
            regular_label_names.union(set(deprecated_label_names))
        )

        source = inspect.getsource(capi_driver)
        tree = ast.parse(source)

        label_methods = {
            "_label",
            "_get_label_bool",
            "_get_label_int",
            "_get_label_csv",
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (
                isinstance(func, ast.Attribute) and func.attr in label_methods
            ):
                continue
            # second positional arg (index 1) is the label key
            if len(node.args) < 2:
                continue
            key_node = node.args[1]
            # Variable keys (e.g. inside _get_label_bool forwarding to
            # _label) are validated at runtime; only check string literals.
            if not isinstance(key_node, ast.Constant):
                continue
            self.assertIn(
                key_node.value,
                registered,
                f"Label '{key_node.value}' used at line {node.lineno} "
                "is not registered in capi_helm_cluster_labels_opts. "
                "Add it to conf.py before using it in the driver.",
            )


class TestValidateCidrNoOverlap(base.DbTestCase):
    def setUp(self):
        super().setUp()
        self.driver = driver.Driver()
        self.cluster_obj = obj_utils.create_test_cluster(
            self.context,
            name="cidr-test-cluster",
            master_flavor_id="flavor_small",
            flavor_id="flavor_medium",
        )

        mock_nc = mock.MagicMock()
        del mock_nc.list_subnets
        mock_nc.subnets.return_value = []
        patcher_clients = mock.patch("magnum.common.clients.OpenStackClients")
        mock_osc = patcher_clients.start()
        mock_osc.return_value.neutron.return_value = mock_nc
        self.mock_nc = mock_nc
        self.addCleanup(patcher_clients.stop)

        patcher_fixed = mock.patch.object(
            driver.Driver,
            "_get_fixed_network_id",
            return_value=None,
        )
        self.mock_fixed_net = patcher_fixed.start()
        self.addCleanup(patcher_fixed.stop)

        patcher_ext = mock.patch(
            "magnum.common.neutron.get_external_network_id",
            return_value=None,
        )
        self.mock_ext_net = patcher_ext.start()
        self.addCleanup(patcher_ext.stop)

    def _make_subnet(self, cidr, name="test-subnet"):
        subnet = mock.MagicMock()
        subnet.cidr = cidr
        subnet.name = name
        return subnet

    def _set_labels(self, pod_cidr, service_cidr):
        self.cluster_obj.labels = {
            "pod_network_cidr": pod_cidr,
            "service_network_cidr": service_cidr,
        }

    def test_no_overlap_passes(self):
        self._set_labels("10.100.0.0/16", "10.96.0.0/16")
        self.driver._validate_cidr_no_overlap(self.context, self.cluster_obj)

    def test_no_fixed_network(self):
        self._set_labels("10.100.0.0/16", "10.96.0.0/16")
        self.driver._validate_cidr_no_overlap(self.context, self.cluster_obj)
        self.mock_nc.subnets.assert_not_called()

    def test_no_external_network(self):
        self.mock_fixed_net.return_value = "fixed-net-uuid"
        self._set_labels("10.100.0.0/16", "10.96.0.0/16")
        self.driver._validate_cidr_no_overlap(self.context, self.cluster_obj)

    def test_invalid_pod_cidr(self):
        self._set_labels("not-a-cidr", "10.96.0.0/16")
        self.assertRaises(
            exception.MagnumException,
            self.driver._validate_cidr_no_overlap,
            self.context,
            self.cluster_obj,
        )

    def test_invalid_service_cidr(self):
        self._set_labels("10.100.0.0/16", "not-a-cidr")
        self.assertRaises(
            exception.MagnumException,
            self.driver._validate_cidr_no_overlap,
            self.context,
            self.cluster_obj,
        )

    def test_pod_service_overlap(self):
        self._set_labels("10.96.0.0/16", "10.96.0.0/16")
        self.assertRaises(
            exception.MagnumException,
            self.driver._validate_cidr_no_overlap,
            self.context,
            self.cluster_obj,
        )

    def test_pod_overlaps_fixed_network_subnet(self):
        self.mock_fixed_net.return_value = "fixed-net-uuid"
        self._set_labels("192.168.0.0/16", "10.96.0.0/16")
        self.mock_nc.subnets.return_value = [
            self._make_subnet("192.168.1.0/24", "fixed-subnet")
        ]
        self.assertRaises(
            exception.MagnumException,
            self.driver._validate_cidr_no_overlap,
            self.context,
            self.cluster_obj,
        )

    def test_service_overlaps_fixed_network_subnet(self):
        self.mock_fixed_net.return_value = "fixed-net-uuid"
        self._set_labels("10.100.0.0/16", "192.168.0.0/16")
        self.mock_nc.subnets.return_value = [
            self._make_subnet("192.168.1.0/24", "fixed-subnet")
        ]
        self.assertRaises(
            exception.MagnumException,
            self.driver._validate_cidr_no_overlap,
            self.context,
            self.cluster_obj,
        )

    def test_pod_overlaps_external_network_subnet(self):
        self.mock_ext_net.return_value = "ext-net-uuid"
        self._set_labels("172.24.0.0/16", "10.96.0.0/16")
        self.mock_nc.subnets.return_value = [
            self._make_subnet("172.24.4.0/24", "ext-subnet")
        ]
        self.assertRaises(
            exception.MagnumException,
            self.driver._validate_cidr_no_overlap,
            self.context,
            self.cluster_obj,
        )

    def test_service_overlaps_external_network_subnet(self):
        self.mock_ext_net.return_value = "ext-net-uuid"
        self._set_labels("10.100.0.0/16", "172.24.0.0/16")
        self.mock_nc.subnets.return_value = [
            self._make_subnet("172.24.4.0/24", "ext-subnet")
        ]
        self.assertRaises(
            exception.MagnumException,
            self.driver._validate_cidr_no_overlap,
            self.context,
            self.cluster_obj,
        )

    def test_subnet_invalid_cidr_skipped(self):
        self.mock_fixed_net.return_value = "fixed-net-uuid"
        self._set_labels("10.100.0.0/16", "10.96.0.0/16")
        self.mock_nc.subnets.return_value = [
            self._make_subnet("not-a-cidr", "bad-subnet")
        ]
        self.driver._validate_cidr_no_overlap(self.context, self.cluster_obj)

    def test_subnet_ipv6_skipped_for_ipv4_pods(self):
        self.mock_fixed_net.return_value = "fixed-net-uuid"
        self._set_labels("10.100.0.0/16", "10.96.0.0/16")
        self.mock_nc.subnets.return_value = [
            self._make_subnet("fd00::/64", "ipv6-subnet")
        ]
        self.driver._validate_cidr_no_overlap(self.context, self.cluster_obj)
