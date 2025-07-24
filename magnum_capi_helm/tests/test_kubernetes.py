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

import base64
import os
import pathlib
import tempfile
from unittest import mock
import yaml

import requests

from magnum_capi_helm import kubernetes
from magnum_capi_helm.tests import base

TEST_SERVER = "https://test:6443"
TEST_KUBECONFIG_YAML = f"""\
apiVersion: v1
clusters:
- cluster:
    certificate-authority: "cafile"
    server: {TEST_SERVER}
  name: default
contexts:
- context:
    cluster: default
    user: default
  name: default
current-context: default
kind: Config
users:
- name: default
  user:
    client-certificate: "certfile"
    client-key: "keyfile"
"""
TEST_KUBECONFIG = yaml.safe_load(TEST_KUBECONFIG_YAML)


class TestKubernetesClient(base.TestCase):
    # Basic lookup, non "-data" key
    def test_file_or_data(self):
        client = kubernetes.Client(TEST_KUBECONFIG)
        data = client.ensure_file_cert(dict(key="mydata"), "key")
        self.assertEqual("mydata", data)

    # Lookup with a "-data" key, requiring temporary file
    @mock.patch.object(tempfile, "NamedTemporaryFile")
    def test_file_or_data_create_temp(self, mock_temp):
        client = kubernetes.Client(TEST_KUBECONFIG)
        data = client.ensure_file_cert(
            {"key-data": base64.b64encode(b"mydata").decode("utf-8")}, "key"
        )
        mock_temp.assert_has_calls(
            [
                mock.call(delete=False),
                mock.call().__enter__(),
                mock.call().__enter__().write(b"mydata"),
                mock.call().__exit__(None, None, None),
            ]
        )
        self.assertEqual(mock_temp().__enter__().name, data)

    # Lookup with no key, expecting no error, and no data returned.
    def test_file_or_data_missing(self):
        client = kubernetes.Client(TEST_KUBECONFIG)
        data = client.ensure_file_cert(dict(), "key")
        self.assertIsNone(data)

    def test_client_constructor(self):
        client = kubernetes.Client(TEST_KUBECONFIG)

        self.assertEqual(TEST_SERVER, client.server)
        self.assertEqual("cafile", client.verify)
        self.assertEqual(("certfile", "keyfile"), client.cert)

    @mock.patch.object(tempfile, "NamedTemporaryFile")
    @mock.patch.object(os, "remove")
    def test_client_certificate_finalizer(self, mock_remove, mock_temp):
        kubeconfig = yaml.safe_load(TEST_KUBECONFIG_YAML)

        # Set -data in base64 to create tmp files.
        del kubeconfig["users"][0]["user"]["client-certificate"]
        kubeconfig["users"][0]["user"]["client-certificate-data"] = (
            base64.b64encode(b"client cert data").decode("utf-8")
        )

        client = kubernetes.Client(kubeconfig)

        # Ensure a temporary file was created
        mock_temp.assert_has_calls(
            [
                mock.call(delete=False),
                mock.call().__enter__(),
                mock.call().__enter__().write(b"client cert data"),
                mock.call().__exit__(None, None, None),
            ]
        )

        # Call finalizer method directly
        client.__del__()

        # Exactly one temp file should be cleaned up
        mock_remove.assert_called_once()

    def test_get_kubeconfig_path_default(self):
        self.assertEqual(
            pathlib.Path.home() / ".kube" / "config",
            kubernetes.Client._get_kubeconfig_path(),
        )

    @mock.patch.object(kubernetes.CONF, "capi_helm")
    def test_get_kubeconfig_path_config(self, mock_conf):
        mock_conf.kubeconfig_file = "foo"
        os.environ["KUBECONFIG"] = "bar"

        path = kubernetes.Client._get_kubeconfig_path()

        del os.environ["KUBECONFIG"]
        self.assertEqual("foo", path)

    def test_get_kubeconfig_path_env(self):
        os.environ["KUBECONFIG"] = "bar"

        path = kubernetes.Client._get_kubeconfig_path()

        del os.environ["KUBECONFIG"]
        self.assertEqual("bar", path)

    @mock.patch.object(kubernetes.CONF, "capi_helm")
    @mock.patch(
        "builtins.open",
        new_callable=mock.mock_open,
        read_data=TEST_KUBECONFIG_YAML,
    )
    def test_client_load(self, mock_open, mock_conf):
        mock_conf.kubeconfig_file = "mypath"

        client = kubernetes.Client.load()

        self.assertEqual(TEST_SERVER, client.server)
        mock_open.assert_called_once_with("mypath")

    @mock.patch.object(requests.Session, "request")
    def test_ensure_namespace(self, mock_request):
        client = kubernetes.Client(TEST_KUBECONFIG)

        client.ensure_namespace("namespace1")

        mock_request.assert_called_once_with(
            "PATCH",
            "https://test:6443/api/v1/namespaces/namespace1",
            data=None,
            json={
                "apiVersion": "v1",
                "kind": "Namespace",
                "metadata": {"name": "namespace1"},
            },
            headers={"Content-Type": "application/apply-patch+yaml"},
            params={"fieldManager": "magnum", "force": "true"},
        )

    @mock.patch.object(requests.Session, "request")
    def test_apply_secret(self, mock_request):
        client = kubernetes.Client(TEST_KUBECONFIG)
        test_data = dict(
            stringData=dict(foo="bar"), metadata=dict(labels=dict(baz="asdf"))
        )

        client.apply_secret("secname", test_data, "ns1")

        mock_request.assert_called_once_with(
            "PATCH",
            "https://test:6443/api/v1/namespaces/ns1/secrets/secname",
            data=None,
            json={
                "stringData": {"foo": "bar"},
                "apiVersion": "v1",
                "kind": "Secret",
                "metadata": {
                    "labels": {"baz": "asdf"},
                    "name": "secname",
                    "namespace": "ns1",
                },
            },
            headers={"Content-Type": "application/apply-patch+yaml"},
            params={"fieldManager": "magnum", "force": "true"},
        )

    @mock.patch.object(requests.Session, "request")
    def test_delete_all_secrets_by_label(self, mock_request):
        client = kubernetes.Client(TEST_KUBECONFIG)
        mock_response = mock.MagicMock()
        mock_request.return_value = mock_response

        client.delete_all_secrets_by_label("label", "cluster1", "ns1")

        mock_request.assert_called_once_with(
            "DELETE",
            "https://test:6443/api/v1/namespaces/ns1/secrets",
            params={"labelSelector": "label=cluster1"},
        )
        mock_response.raise_for_status.assert_called_once_with()

    @mock.patch.object(requests.Session, "request")
    def test_get_secret(self, mock_request):
        client = kubernetes.Client(TEST_KUBECONFIG)
        mock_response = mock.MagicMock()
        mock_response.status_code = 200
        mock_request.return_value = mock_response

        secret_name = "secret1"
        secret_namespace = "ns1"
        client.get_secret(secret_name, secret_namespace)

        mock_request.assert_called_once_with(
            "GET",
            "https://test:6443/api/v1/namespaces"
            f"/{secret_namespace}/secrets/{secret_name}",
            allow_redirects=True,
        )

    @mock.patch.object(requests.Session, "request")
    def test_get_secret_value(self, mock_request):
        client = kubernetes.Client(TEST_KUBECONFIG)
        mock_response = mock.MagicMock()
        mock_response.status_code = 200
        mock_request.return_value = mock_response

        secret_name = "secret1"
        secret_namespace = "ns1"
        secret_key = "mykey"
        secret_value = "mysecretvalue"
        mock_response.json.return_value = {
            "data": {
                secret_key: base64.b64encode(secret_value.encode()).decode(),
            }
        }

        self.assertEqual(
            client.get_secret_value(secret_name, secret_namespace, secret_key),
            secret_value,
        )
        mock_request.assert_called_once_with(
            "GET",
            "https://test:6443/api/v1/namespaces"
            f"/{secret_namespace}/secrets/{secret_name}",
            allow_redirects=True,
        )

    @mock.patch.object(requests.Session, "request")
    def test_get_capi_cluster_found(self, mock_request):
        client = kubernetes.Client(TEST_KUBECONFIG)
        mock_response = mock.MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = "mock_json"
        mock_request.return_value = mock_response

        cluster = client.get_capi_cluster("name", "ns1")

        mock_request.assert_called_once_with(
            "GET",
            (
                "https://test:6443/apis/cluster.x-k8s.io/"
                "v1beta1/namespaces/ns1/clusters/name"
            ),
            allow_redirects=True,
        )
        self.assertEqual("mock_json", cluster)

    @mock.patch.object(requests.Session, "request")
    def test_get_capi_cluster_not_found(self, mock_request):
        client = kubernetes.Client(TEST_KUBECONFIG)
        mock_response = mock.MagicMock()
        mock_response.status_code = 404
        mock_request.return_value = mock_response

        cluster = client.get_capi_cluster("name", "ns1")

        self.assertIsNone(cluster)

    @mock.patch.object(requests.Session, "request")
    def test_get_capi_cluster_error(self, mock_request):
        client = kubernetes.Client(TEST_KUBECONFIG)
        mock_response = mock.MagicMock()
        mock_response.status_code = 500
        mock_response.raise_for_status.side_effect = requests.HTTPError
        mock_request.return_value = mock_response

        self.assertRaises(
            requests.HTTPError, client.get_capi_cluster, "name", "ns1"
        )

    @mock.patch.object(requests.Session, "request")
    def test_get_kubeadm_control_plane_found(self, mock_request):
        client = kubernetes.Client(TEST_KUBECONFIG)
        mock_response = mock.MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = "mock_json"
        mock_request.return_value = mock_response

        cluster = client.get_kubeadm_control_plane("name", "ns1")

        mock_request.assert_called_once_with(
            "GET",
            (
                "https://test:6443/apis/controlplane.cluster.x-k8s.io/"
                "v1beta1/namespaces/ns1/kubeadmcontrolplanes/name"
            ),
            allow_redirects=True,
        )
        self.assertEqual("mock_json", cluster)

    @mock.patch.object(requests.Session, "request")
    def test_get_machine_deployment_found(self, mock_request):
        client = kubernetes.Client(TEST_KUBECONFIG)
        mock_response = mock.MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = "mock_json"
        mock_request.return_value = mock_response

        cluster = client.get_machine_deployment("name", "ns1")

        mock_request.assert_called_once_with(
            "GET",
            (
                "https://test:6443/apis/cluster.x-k8s.io/"
                "v1beta1/namespaces/ns1/machinedeployments/name"
            ),
            allow_redirects=True,
        )
        self.assertEqual("mock_json", cluster)

    @mock.patch.object(requests.Session, "request")
    def test_get_manifests_by_label(self, mock_request):
        items = [
            {
                "kind": "Manifests",
                "metadata": {"name": f"manifests{idx}", "namespace": "ns1"},
            }
            for idx in range(5)
        ]

        mock_response = mock.Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "metadata": {
                "continue": "",
            },
            "items": items,
        }
        mock_request.return_value = mock_response

        client = kubernetes.Client(TEST_KUBECONFIG)
        manifests = client.get_manifests_by_label({"label": "cluster1"}, "ns1")

        mock_request.assert_called_once_with(
            "GET",
            (
                "https://test:6443/apis/addons.stackhpc.com/"
                "v1alpha1/namespaces/ns1/manifests"
            ),
            params={"labelSelector": "label=cluster1"},
            allow_redirects=True,
        )
        self.assertEqual(items, manifests)

    @mock.patch.object(requests.Session, "request")
    def test_get_helm_releases_by_label(self, mock_request):
        items = [
            {
                "kind": "HelmRelease",
                "metadata": {"name": f"helmrelease{idx}", "namespace": "ns1"},
            }
            for idx in range(5)
        ]

        mock_response = mock.Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "metadata": {
                "continue": "",
            },
            "items": items,
        }
        mock_request.return_value = mock_response

        client = kubernetes.Client(TEST_KUBECONFIG)
        helm_releases = client.get_helm_releases_by_label(
            {"label": "cluster1"}, "ns1"
        )

        mock_request.assert_called_once_with(
            "GET",
            (
                "https://test:6443/apis/addons.stackhpc.com/"
                "v1alpha1/namespaces/ns1/helmreleases"
            ),
            params={"labelSelector": "label=cluster1"},
            allow_redirects=True,
        )
        self.assertEqual(items, helm_releases)

    @mock.patch.object(requests.Session, "request")
    def test_get_helm_releases_by_label_multipage(self, mock_request):
        items = [
            {
                "kind": "HelmRelease",
                "metadata": {"name": f"helmrelease{idx}", "namespace": "ns1"},
            }
            for idx in range(10)
        ]

        mock_response_page1 = mock.Mock()
        mock_response_page1.raise_for_status.return_value = None
        mock_response_page1.json.return_value = {
            "metadata": {
                "continue": "continuetoken",
            },
            "items": items[:5],
        }
        mock_response_page2 = mock.Mock()
        mock_response_page2.raise_for_status.return_value = None
        mock_response_page2.json.return_value = {
            "metadata": {
                "continue": "",
            },
            "items": items[5:],
        }
        mock_request.side_effect = [
            mock_response_page1,
            mock_response_page2,
        ]

        client = kubernetes.Client(TEST_KUBECONFIG)
        helm_releases = client.get_helm_releases_by_label(
            {"label": "cluster1"}, "ns1"
        )

        self.assertEqual(mock_request.call_count, 2)
        mock_request.assert_has_calls(
            [
                mock.call(
                    "GET",
                    (
                        "https://test:6443/apis/addons.stackhpc.com/"
                        "v1alpha1/namespaces/ns1/helmreleases"
                    ),
                    params={"labelSelector": "label=cluster1"},
                    allow_redirects=True,
                ),
                mock.call(
                    "GET",
                    (
                        "https://test:6443/apis/addons.stackhpc.com/"
                        "v1alpha1/namespaces/ns1/helmreleases"
                    ),
                    params={
                        "labelSelector": "label=cluster1",
                        "continue": "continuetoken",
                    },
                    allow_redirects=True,
                ),
            ]
        )
        self.assertEqual(items, helm_releases)

    @mock.patch.object(kubernetes.Client, "get_helm_releases_by_label")
    @mock.patch.object(kubernetes.Client, "get_manifests_by_label")
    def test_get_addons_by_label(
        self, mock_get_manifests, mock_get_helm_releases
    ):
        manifests = [
            {
                "kind": "Manifests",
                "metadata": {"name": f"manifests{idx}", "namespace": "ns1"},
            }
            for idx in range(5)
        ]
        helm_releases = [
            {
                "kind": "HelmRelease",
                "metadata": {"name": f"helmrelease{idx}", "namespace": "ns1"},
            }
            for idx in range(5)
        ]

        mock_get_manifests.return_value = manifests
        mock_get_helm_releases.return_value = helm_releases

        client = kubernetes.Client(TEST_KUBECONFIG)
        addons = client.get_addons_by_label({"label": "cluster1"}, "ns1")

        mock_get_manifests.assert_called_once_with(
            {"label": "cluster1"}, "ns1"
        )
        mock_get_helm_releases.assert_called_once_with(
            {"label": "cluster1"}, "ns1"
        )
        self.assertEqual(manifests + helm_releases, addons)

    @mock.patch.object(requests.Session, "request")
    def test_get_all_machines_by_label(self, mock_request):
        items = [
            {
                "kind": "Machine",
                "metadata": {"name": f"machine{idx}", "namespace": "ns1"},
            }
            for idx in range(5)
        ]

        mock_response = mock.Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "metadata": {
                "continue": "",
            },
            "items": items,
        }
        mock_request.return_value = mock_response

        client = kubernetes.Client(TEST_KUBECONFIG)
        machines = client.get_all_machines_by_label(
            {"capi.stackhpc.com/cluster": "cluster_name", "foo": "bar"}, "ns1"
        )

        mock_request.assert_called_once_with(
            "GET",
            (
                "https://test:6443/apis/cluster.x-k8s.io/"
                "v1beta1/namespaces/ns1/machines"
            ),
            params={
                "labelSelector": (
                    "capi.stackhpc.com/cluster=cluster_name,foo=bar"
                )
            },
            allow_redirects=True,
        )
        self.assertEqual(items, machines)
