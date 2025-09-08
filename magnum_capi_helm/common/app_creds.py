# Copyright (c) 2023 VEXXHOST, Inc.
# Copyright (c) 2023 StackHPC
#
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
#
# This code is making use of the good work done here:
# https://github.com/vexxhost/magnum-cluster-api/blob/main/magnum_cluster_api/resources.py

import secrets
import yaml

import certifi
import keystoneauth1
from oslo_log import log as logging

from magnum.common import clients
from magnum.common import context as ctx
from magnum.common import utils
import magnum.conf

CONF = magnum.conf.CONF
LOG = logging.getLogger(__name__)


class ApplicationCredentialError(Exception):
    message = "Failed to perform action on application credential."

    def __init__(self, message=None):
        self.message = message or self.message
        super(ApplicationCredentialError, self).__init__(self.message)

    def __str__(self):
        return self.message


def _get_openstack_ca_certificate():
    # This function returns the CA bundle to use when verifying TLS
    # connections to the OpenStack API in both the Cluster API provider
    # and OpenStack integrations on the cluster (e.g. OCCM, Cinder CSI)
    #
    # If no CA bundle is specified in config we use the CA bundle from
    # certifi
    # This is because the Cluster API provider contains NO trusted CAs
    # and, because it is a pod in Kubernetes, it does NOT pick up the
    # trusted CAs from the host
    ca_certificate = utils.get_openstack_ca()
    if not ca_certificate:
        with open(certifi.where(), "r") as ca_file:
            ca_certificate = ca_file.read()
    return ca_certificate


def create_app_cred(context, cluster):
    osc = clients.OpenStackClients(context)
    # TODO(johngarbutt) be sure not to allow the admin role
    # roles = [role for role in context.roles if role != "admin"]
    return osc.keystone().client.application_credentials.create(
        user=context.user_id,
        name=f"magnum-{cluster.uuid}-{secrets.token_hex(4)}",
        description=f"Magnum cluster ({cluster.name or cluster.uuid})",
        # roles=roles,
    )


def _get_app_cred_clouds_dict(context, app_cred):
    osc = clients.OpenStackClients(context)
    # ensure correct region_name for multi-region Keystone
    region_name = CONF.magnum_client.region_name or osc.cinder_region_name()
    return {
        "clouds": {
            "openstack": {
                "identity_api_version": 3,
                "region_name": region_name,
                "interface": CONF.capi_helm.app_cred_interface_type,
                # This config item indicates whether TLS should be
                # verified when connecting to the OpenStack API
                "verify": CONF.drivers.verify_ca,
                "auth": {
                    "auth_url": osc.url_for(
                        service_type="identity", interface="public", region_name=region_name
                    ),
                    "application_credential_id": app_cred.id,
                    "application_credential_secret": app_cred.secret,
                },
                "auth_type": "v3applicationcredential",
            },
        },
    }


def get_app_cred_string_data(context, app_cred):
    clouds_dict = _get_app_cred_clouds_dict(context, app_cred)
    return {
        "cacert": _get_openstack_ca_certificate(),
        "clouds.yaml": yaml.safe_dump(clouds_dict),
    }


def delete_app_cred(cluster, app_cred_id):
    # NOTE(northcottmt): Admin privileges are needed to delete app creds
    # outside the requestor scope
    context = ctx.make_admin_context()
    osc = clients.OpenStackClients(context)
    kst = osc.keystone()

    LOG.debug(
        f"Deleting application credential with ID {app_cred_id} "
        f"for cluster {cluster.uuid}"
    )

    try:
        app_cred = kst.client.application_credentials.get(app_cred_id)
    except keystoneauth1.exceptions.http.NotFound:
        raise ApplicationCredentialError(f"{app_cred_id} does not exist.")

    if not app_cred.name.startswith(f"magnum-{cluster.uuid}"):
        raise ApplicationCredentialError(
            f"{app_cred_id} is not managed by Magnum."
        )

    app_cred.delete()
