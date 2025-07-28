#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import copy

from unittest import mock

from magnum.objects import fields as m_fields
from magnum.tests.unit.db import base
from magnum.tests.unit.objects import utils as obj_utils
from magnum_capi_helm.common import capi_monitor


class TestCAPIMonitor(base.DbTestCase):
    def setUp(self):
        super(TestCAPIMonitor, self).setUp()
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

        # Create capi monitor
        self.monitor = capi_monitor.CAPIMonitor(self.context, self.cluster_obj)

        # Patch k8s client and set Ready state for all CAPI objects.
        self.patcher = mock.patch.object(
            capi_monitor.CAPIMonitor, "_k8s_client"
        )
        self.mock_k8s = self.patcher.start()
        ready_state = {
            "status": {"conditions": [{"type": "Ready", "status": "True"}]}
        }
        self.mock_k8s.get_capi_cluster.return_value = copy.deepcopy(
            ready_state
        )
        self.mock_k8s.get_capi_openstackcluster.return_value = {
            "status": {
                "ready": True,
            }
        }
        self.mock_k8s.get_k8s_control_plane.return_value = copy.deepcopy(
            ready_state
        )
        self.mock_k8s.get_machine_deployment.return_value = copy.deepcopy(
            ready_state
        )

    def tearDown(self):
        super(TestCAPIMonitor, self).tearDown()
        self.patcher.stop()

    def test_healthy_cluster(self):
        self.monitor.poll_health_status()

        self.assertEqual(
            self.monitor.data["health_status"],
            m_fields.ClusterHealthStatus.HEALTHY,
        )
        self.assertEqual(
            self.monitor.data["health_status_reason"],
            {
                "cluster": "Ready",
                "controlplane": "Ready",
                "infrastructure": "Ready",
                "nodegroup": "Ready",
            },
        )

    def test_cluster_absent(self):
        self.mock_k8s.get_capi_cluster.return_value = None
        self.monitor.poll_health_status()

        self.assertEqual(
            self.monitor.data["health_status"],
            m_fields.ClusterHealthStatus.UNHEALTHY,
        )
        self.assertEqual(
            self.monitor.data["health_status_reason"],
            {
                "cluster": "Cluster resource not found.",
                "controlplane": "Ready",
                "infrastructure": "Ready",
                "nodegroup": "Ready",
            },
        )

    def test_cluster_unhealthy(self):
        cluster_state = {
            "status": {
                "conditions": [
                    {"type": "Ready", "status": "False"},
                    {"type": "ControlPlaneReady", "status": "False"},
                ]
            }
        }
        self.mock_k8s.get_capi_cluster.return_value = cluster_state
        self.monitor.poll_health_status()

        self.assertEqual(
            self.monitor.data["health_status"],
            m_fields.ClusterHealthStatus.UNHEALTHY,
        )
        self.assertEqual(
            self.monitor.data["health_status_reason"],
            {
                "cluster": "Waiting on ['Ready', 'ControlPlaneReady']",
                "controlplane": "Ready",
                "infrastructure": "Ready",
                "nodegroup": "Ready",
            },
        )

    def test_infra_unhealthy(self):
        infra_state = {
            "status": {
                "ready": False,
            }
        }
        self.mock_k8s.get_capi_openstackcluster.return_value = infra_state
        self.monitor.poll_health_status()

        self.assertEqual(
            self.monitor.data["health_status"],
            m_fields.ClusterHealthStatus.UNHEALTHY,
        )
        self.assertEqual(
            self.monitor.data["health_status_reason"],
            {
                "cluster": "Ready",
                "controlplane": "Ready",
                "infrastructure": "Infrastructure not ready.",
                "nodegroup": "Ready",
            },
        )

    def test_infra_has_failure_message(self):
        infra_state = {
            "status": {
                "ready": True,
                "failureMessage": "abc",
                "failureReason": "123",
            },
        }
        self.mock_k8s.get_capi_openstackcluster.return_value = infra_state
        self.monitor.poll_health_status()

        self.assertEqual(
            self.monitor.data["health_status"],
            m_fields.ClusterHealthStatus.UNHEALTHY,
        )
        self.assertEqual(
            self.monitor.data["health_status_reason"],
            {
                "cluster": "Ready",
                "controlplane": "Ready",
                "infrastructure": "123: abc",
                "nodegroup": "Ready",
            },
        )

    def test_controlplane_has_conditions(self):
        cp_state = {
            "status": {
                "conditions": [
                    {"type": "Ready", "status": "False"},
                    {"type": "Available", "status": "False"},
                    {"type": "CertificatesAvailable", "status": "False"},
                    {
                        "type": "ControlPlaneComponentsHealthy",
                        "status": "False",
                    },
                    {"type": "EtcdClusterHealthy", "status": "False"},
                    {"type": "MachinesCreated", "status": "False"},
                    {"type": "MachinesReady", "status": "False"},
                    {"type": "Resized", "status": "False"},
                ]
            }
        }
        self.mock_k8s.get_k8s_control_plane.return_value = cp_state
        self.monitor.poll_health_status()

        self.assertEqual(
            self.monitor.data["health_status"],
            m_fields.ClusterHealthStatus.UNHEALTHY,
        )
        cp_status = (
            "Waiting on ['Ready', 'Available', "
            "'CertificatesAvailable', "
            "'ControlPlaneComponentsHealthy', 'EtcdClusterHealthy', "
            "'MachinesCreated', 'MachinesReady', 'Resized']"
        )
        self.assertEqual(
            self.monitor.data["health_status_reason"],
            {
                "cluster": "Ready",
                "controlplane": cp_status,
                "infrastructure": "Ready",
                "nodegroup": "Ready",
            },
        )

    def test_nodegroup_has_conditions(self):
        ng_state = {
            "status": {
                "conditions": [
                    {"type": "Ready", "status": "True"},
                    {"type": "Available", "status": "False"},
                ]
            }
        }
        self.mock_k8s.get_machine_deployment.return_value = ng_state

        self.monitor.poll_health_status()

        self.assertEqual(
            self.monitor.data["health_status"],
            m_fields.ClusterHealthStatus.UNHEALTHY,
        )
        self.assertEqual(
            self.monitor.data["health_status_reason"],
            {
                "cluster": "Ready",
                "controlplane": "Ready",
                "infrastructure": "Ready",
                "nodegroup": "test-worker waiting on ['Available']",
            },
        )

    def test_machinedeployment_absent(self):
        self.mock_k8s.get_machine_deployment.return_value = None
        self.monitor.poll_health_status()

        self.assertEqual(
            self.monitor.data["health_status"],
            m_fields.ClusterHealthStatus.UNHEALTHY,
        )
        self.assertEqual(
            self.monitor.data["health_status_reason"],
            {
                "cluster": "Ready",
                "controlplane": "Ready",
                "infrastructure": "Ready",
                "nodegroup": "test-worker resource not found.",
            },
        )

    def test_all_missing(self):
        self.mock_k8s.get_capi_cluster.return_value = None
        self.mock_k8s.get_capi_openstackcluster.return_value = None
        self.mock_k8s.get_k8s_control_plane.return_value = None
        self.mock_k8s.get_machine_deployment.return_value = None
        self.monitor.poll_health_status()

        self.assertEqual(
            self.monitor.data["health_status"],
            m_fields.ClusterHealthStatus.UNHEALTHY,
        )
        self.assertEqual(
            self.monitor.data["health_status_reason"],
            {
                "cluster": "Cluster resource not found.",
                "controlplane": "Control plane resource not found.",
                "infrastructure": "Infrastructure resource not found.",
                "nodegroup": "test-worker resource not found.",
            },
        )
