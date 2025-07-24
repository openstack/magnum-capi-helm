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
import collections
from unittest import mock

import keystoneauth1
from magnum.common import clients
from magnum.common import utils
from magnum.tests.unit.db import base
from magnum.tests.unit.objects import utils as obj_utils

from magnum_capi_helm.common import app_creds


class TestAppCreds(base.DbTestCase):
    def setUp(self):
        super().setUp()
        self.cluster_obj = obj_utils.create_test_cluster(
            self.context,
            name="cluster_example_$A",
            master_flavor_id="flavor_small",
            flavor_id="flavor_medium",
        )

    @mock.patch.object(utils, "get_openstack_ca")
    def test_get_openstack_ca_certificate(self, mock_ca):
        mock_ca.return_value = "cert"

        cert = app_creds._get_openstack_ca_certificate()

        self.assertEqual("cert", cert)

    @mock.patch.object(utils, "get_openstack_ca")
    def test_get_openstack_ca_certificate_get_certify(self, mock_ca):
        mock_ca.return_value = None

        cert = app_creds._get_openstack_ca_certificate()

        self.assertIsNotNone(cert)

    @mock.patch("secrets.token_hex")
    @mock.patch.object(clients, "OpenStackClients")
    def test_create_app_cred(self, mock_client, mock_token):
        mock_client().cinder_region_name.return_value = "cinder"
        mock_client().url_for.return_value = "http://keystone"
        mock_app_cred = mock_client().keystone().client.application_credentials
        app_cred = collections.namedtuple("appcred", ["id", "secret"])
        mock_app_cred.create.return_value = app_cred("id", "pass")
        context = mock.MagicMock()
        context.user_id = "fake_user"
        context.roles = ["member", "foo", "admin"]

        mock_token_hex_nonce = "abcd1234"
        mock_token.return_value = mock_token_hex_nonce
        app_cred = app_creds.create_app_cred(context, self.cluster_obj)
        app_cred_string_data = app_creds._get_app_cred_clouds_dict(
            context, app_cred
        )

        expected = {
            "clouds": {
                "openstack": {
                    "auth": {
                        "application_credential_id": "id",
                        "application_credential_secret": "pass",
                        "auth_url": "http://keystone",
                    },
                    "auth_type": "v3applicationcredential",
                    "identity_api_version": 3,
                    "interface": "public",
                    "region_name": "cinder",
                    "verify": True,
                }
            }
        }
        self.assertEqual(expected, app_cred_string_data)
        mock_client().url_for.assert_called_once_with(
            service_type="identity", interface="public"
        )
        mock_app_cred.create.assert_called_once_with(
            user=context.user_id,
            name=f"magnum-{self.cluster_obj.uuid}-{mock_token_hex_nonce}",
            description="Magnum cluster "
            + f"({self.cluster_obj.name or self.cluster_obj.uuid})",
            # roles=["member", "foo"],
        )

    @mock.patch.object(app_creds, "_get_openstack_ca_certificate")
    @mock.patch.object(app_creds, "_get_app_cred_clouds_dict")
    def test_get_app_cred_yaml(self, mock_clouds, mock_ca):
        app_cred = collections.namedtuple("appcred", ["id", "secret"])
        mock_app_cred = app_cred("id", "secret")
        mock_clouds.return_value = {
            "clouds": {
                "openstack": {
                    "auth": {"application_credential_id": mock_app_cred.id},
                }
            }
        }

        mock_ca.return_value = "cacert"
        string_data = app_creds.get_app_cred_string_data(
            "context", mock_app_cred
        )

        mock_clouds.assert_called_once_with("context", mock_app_cred)
        mock_ca.assert_called_once_with()

        expected = {
            "cacert": "cacert",
            "clouds.yaml": """\
clouds:
  openstack:
    auth:
      application_credential_id: id
""",
        }
        self.assertEqual(expected, string_data)

    @mock.patch.object(clients, "OpenStackClients")
    def test_delete_app_cred(self, mock_client):
        mock_app_cred_client = (
            mock_client().keystone().client.application_credentials
        )
        mock_app_cred = mock.MagicMock()
        mock_app_cred.name.startswith.return_value = True
        mock_app_cred_client.get.return_value = mock_app_cred

        app_cred_id = "abcdef12345"
        app_creds.delete_app_cred(self.cluster_obj, app_cred_id)

        mock_app_cred.delete.assert_called_once()
        mock_app_cred.name.startswith.assert_called_once_with(
            f"magnum-{self.cluster_obj.uuid}"
        )
        mock_app_cred_client.get.assert_called_once_with(app_cred_id)

    @mock.patch.object(clients, "OpenStackClients")
    def test_delete_app_cred_not_found(self, mock_client):
        mock_app_cred_client = (
            mock_client().keystone().client.application_credentials
        )
        mock_app_cred_client.get.side_effect = (
            keystoneauth1.exceptions.http.NotFound
        )

        app_cred_id = "abcdef12345"

        self.assertRaises(
            app_creds.ApplicationCredentialError,
            app_creds.delete_app_cred,
            self.cluster_obj,
            app_cred_id,
        )

        mock_app_cred_client.get.assert_called_once_with(app_cred_id)

    @mock.patch.object(clients, "OpenStackClients")
    def test_delete_app_cred_invalid_name(self, mock_client):
        mock_app_cred_client = (
            mock_client().keystone().client.application_credentials
        )
        mock_app_cred = mock.MagicMock()
        mock_app_cred_name = mock.MagicMock()
        mock_app_cred_name.startswith.return_value = False
        mock_app_cred.name = mock_app_cred_name
        mock_app_cred_client.get.return_value = mock_app_cred

        app_cred_id = "abcdef12345"

        self.assertRaises(
            app_creds.ApplicationCredentialError,
            app_creds.delete_app_cred,
            self.cluster_obj,
            app_cred_id,
        )

        mock_app_cred.delete.assert_not_called()
        mock_app_cred.name.startswith.assert_called_once_with(
            f"magnum-{self.cluster_obj.uuid}"
        )
        mock_app_cred_client.get.assert_called_once_with(app_cred_id)
