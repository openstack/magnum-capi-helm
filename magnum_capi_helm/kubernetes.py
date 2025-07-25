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

import base64
import copy
import os
import pathlib
import re
import tempfile
import yaml

from oslo_log import log as logging
import requests

from magnum_capi_helm import conf


LOG = logging.getLogger(__name__)
CONF = conf.CONF


class Client(requests.Session):
    """Object for producing Kubernetes clients."""

    KUBECONFIG_ENV_NAME = "KUBECONFIG"

    def __init__(self, kubeconfig):
        super().__init__()
        self._tempfiles = []
        cluster, user = self._get_cluster_and_user(kubeconfig)

        self.server = cluster["server"].rstrip("/")
        ca_file = self.ensure_file_cert(cluster, "certificate-authority")
        if ca_file:
            self.verify = ca_file

        # convert certs into files as required by requests
        # https://requests.readthedocs.io/en/latest/api/#requests.Session.cert

        client_cert = self.ensure_file_cert(user, "client-certificate")
        client_key = self.ensure_file_cert(user, "client-key")

        if client_cert and client_key:
            self.cert = (client_cert, client_key)
        elif user.get("token"):
            self.headers.update({"Authorization": f"Bearer {user['token']}"})
        else:
            raise Exception(
                "No supported authentication method found in kubeconfig"
            )

    def ensure_file_cert(self, obj, file_key):
        """Returns the path of cert.

        Returns a string containing the path to a file with the requesteddata.
        First check if there is a file path already,
        if the data is there, put it in a file, add path to the _tempfiles
        list for cleanup and return the created file.
        """
        if file_key in obj:
            return obj[file_key]

        data_key = file_key + "-data"
        if data_key in obj:
            # NOTE(dalees): The created file may contain private key material
            # but is owned by the current user and mode 0600.
            # See also: tempfile.mkstemp
            # In Python 3.12, cleanup may be improved with
            # delete=True,delete_on_close=False
            with tempfile.NamedTemporaryFile(delete=False) as fd:
                fd.write(base64.standard_b64decode(obj[data_key]))
                self._tempfiles.append(fd.name)
            return fd.name
        return None

    def __del__(self):
        # Remove any temporary certificate files this class owns.
        for file_path in self._tempfiles:
            try:
                os.remove(file_path)
            except (OSError, FileNotFoundError):
                pass
        self._tempfiles = []

    def _get_cluster_and_user(self, kubeconfig):
        # get the context
        current_context = kubeconfig["current-context"]
        context = [
            c["context"]
            for c in kubeconfig["contexts"]
            if c["name"] == current_context
        ][0]
        # extract cluster and user from context
        cluster = [
            c["cluster"]
            for c in kubeconfig["clusters"]
            if c["name"] == context["cluster"]
        ][0]
        user = [
            u["user"]
            for u in kubeconfig["users"]
            if u["name"] == context["user"]
        ][0]
        return cluster, user

    @classmethod
    def _get_kubeconfig_path(cls):
        # use config if specified
        if CONF.capi_helm.kubeconfig_file:
            return CONF.capi_helm.kubeconfig_file
        if cls.KUBECONFIG_ENV_NAME in os.environ:
            return os.environ[cls.KUBECONFIG_ENV_NAME]
        # the default kubeconfig location
        return pathlib.Path.home() / ".kube" / "config"

    @classmethod
    def _load_kubeconfig(cls, path):
        with open(path) as fd:
            return yaml.safe_load(fd)

    @classmethod
    def load(cls):
        path = cls._get_kubeconfig_path()
        kubeconfig = cls._load_kubeconfig(path)
        return Client(kubeconfig)

    def request(self, method, url, *args, **kwargs):
        # Make sure to add the server to any relative URLs
        if re.match(r"^http(s)://", url) is None:
            url = "{}{}".format(self.server, url)
        response = super().request(method, url, *args, **kwargs)
        LOG.debug(
            'Kubernetes API request: "%s %s" %s',
            method,
            url,
            response.status_code,
        )
        return response

    def ensure_namespace(self, namespace):
        Namespace(self).apply(namespace)

    def apply_secret(self, secret_name, data, namespace):
        Secret(self).apply(secret_name, data, namespace)

    def delete_all_secrets_by_label(self, label, value, namespace):
        Secret(self).delete_all_by_label(label, value, namespace)

    def get_capi_cluster(self, name, namespace):
        return Cluster(self).fetch(name, namespace)

    def get_capi_openstackcluster(self, name, namespace):
        return OpenstackCluster(self).fetch(name, namespace)

    def get_kubeadm_control_plane(self, name, namespace):
        return KubeadmControlPlane(self).fetch(name, namespace)

    def get_machine_deployment(self, name, namespace):
        return MachineDeployment(self).fetch(name, namespace)

    def get_manifests_by_label(self, labels, namespace):
        return list(Manifests(self).fetch_all_by_label(labels, namespace))

    def get_helm_releases_by_label(self, labels, namespace):
        return list(HelmRelease(self).fetch_all_by_label(labels, namespace))

    def get_addons_by_label(self, labels, namespace):
        addons = list(self.get_manifests_by_label(labels, namespace))
        addons.extend(self.get_helm_releases_by_label(labels, namespace))
        return addons

    def get_all_machines_by_label(self, labels, namespace):
        return list(Machine(self).fetch_all_by_label(labels, namespace))


class Resource:
    def __init__(self, client):
        self.client = client
        assert hasattr(self, "api_version")
        self.kind = getattr(self, "kind", type(self).__name__)
        self.plural_name = getattr(
            self, "plural_name", self.kind.lower() + "s"
        )
        self.namespaced = getattr(self, "namespaced", True)

    def prepare_path(self, name=None, namespace=None):
        # Begin with either /api or /apis depending whether the api version
        # is the core API
        prefix = "/apis" if "/" in self.api_version else "/api"
        # Include the namespace unless the resource is namespaced
        path_namespace = f"/namespaces/{namespace}" if namespace else ""
        # Include the resource name if given
        path_name = f"/{name}" if name else ""
        return (
            f"{prefix}/{self.api_version}{path_namespace}/"
            f"{self.plural_name}{path_name}"
        )

    def fetch(self, name, namespace=None):
        """Fetches specified object from the target Kubernetes cluster.

        If the object is not found, None is returned.
        """
        assert self.namespaced == bool(namespace)
        assert name is not None
        response = self.client.get(self.prepare_path(name, namespace))
        if 200 <= response.status_code < 300:
            return response.json()
        elif response.status_code == 404:
            return None
        else:
            response.raise_for_status()

    def fetch_all_by_label(self, labels, namespace=None):
        """Fetches objects matching the labels from the target cluster."""
        assert self.namespaced == bool(namespace)
        label_selector = ",".join(f"{k}={v}" for k, v in labels.items())
        continue_token = ""
        while True:
            params = {"labelSelector": label_selector}
            if continue_token:
                params["continue"] = continue_token
            response = self.client.get(
                self.prepare_path(namespace=namespace), params=params
            )
            response.raise_for_status()
            response_data = response.json()
            yield from response_data["items"]
            continue_token = response_data["metadata"]["continue"]
            if not continue_token:
                break

    def apply(self, name, data=None, namespace=None):
        """Applies the given object to the target Kubernetes cluster."""
        assert self.namespaced == bool(namespace)
        body_data = copy.deepcopy(data) if data else {}
        body_data["apiVersion"] = self.api_version
        body_data["kind"] = self.kind
        body_data.setdefault("metadata", {})["name"] = name
        if namespace:
            body_data["metadata"]["namespace"] = namespace
        response = self.client.patch(
            self.prepare_path(name, namespace),
            json=body_data,
            headers={"Content-Type": "application/apply-patch+yaml"},
            params={"fieldManager": "magnum", "force": "true"},
        )
        response.raise_for_status()
        return response.json()

    def delete_all_by_label(self, label, value, namespace=None):
        """Deletes all objects with the specified label from cluster."""
        assert self.namespaced == bool(namespace)
        response = self.client.delete(
            self.prepare_path(namespace=namespace),
            params={"labelSelector": f"{label}={value}"},
        )
        response.raise_for_status()


class Namespace(Resource):
    api_version = "v1"
    namespaced = False


class Secret(Resource):
    api_version = "v1"


class Cluster(Resource):
    api_version = "cluster.x-k8s.io/v1beta1"


class OpenstackCluster(Resource):
    api_version = "infrastructure.cluster.x-k8s.io/v1alpha6"


class MachineDeployment(Resource):
    api_version = "cluster.x-k8s.io/v1beta1"


class KubeadmControlPlane(Resource):
    api_version = "controlplane.cluster.x-k8s.io/v1beta1"


class Machine(Resource):
    api_version = "cluster.x-k8s.io/v1beta1"


class Manifests(Resource):
    api_version = "addons.stackhpc.com/v1alpha1"
    plural_name = "manifests"


class HelmRelease(Resource):
    api_version = "addons.stackhpc.com/v1alpha1"
