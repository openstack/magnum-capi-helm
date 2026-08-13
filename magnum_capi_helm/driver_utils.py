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

# Collection of static functions that are shared within the driver.

import re

from magnum_capi_helm import conf
from magnum_capi_helm import helm

CONF = conf.CONF


def cluster_namespace(cluster):
    # We create clusters in a project-specific namespace
    # To generate the namespace, first sanitize the project id
    project_id = re.sub("[^a-z0-9]", "", cluster.project_id.lower())
    prefix = CONF.capi_helm.namespace_prefix
    return f"{prefix}-{project_id}"


def sanitized_name(name, suffix=None):
    if not name:
        return None
    return re.sub(
        "[^a-z0-9]+",
        "-",
        (f"{name}-{suffix}" if suffix else name).lower(),
    ).strip("-")


def chart_release_name(cluster):
    return cluster.stack_id


def get_k8s_resource_name(cluster, name):
    return sanitized_name(chart_release_name(cluster), name)


def chart_component_name(cluster, name):
    """Computes the name capi-helm-charts gives a per-component resource.

    Mirrors the "openstack-cluster.componentName" chart helper, which
    builds the name as "<release-name>-<component-name>" and then applies
    Helm's `trunc 63 | trimSuffix "-"` to respect the Kubernetes 63
    character limit on resource names. Without replicating that
    truncation here, a lookup for a resource whose untruncated name is
    over 63 characters (e.g. a nodegroup's MachineDeployment) would never
    match the object the chart actually created, and would appear not to
    exist forever.
    """
    full_name = get_k8s_resource_name(cluster, name)
    return full_name[:63].rstrip("-") if full_name else full_name


def helm_lock_lease_name(cluster):
    return helm.lease_name_for_release(chart_release_name(cluster))


def is_helm_locked(k8s_client, cluster):
    """Returns True if a HelmLock is currently held for cluster's release.

    Used by status/health checks to avoid trusting the current state of
    a cluster's CAPI resources while a Helm update for that cluster is
    still being applied (i.e. is waiting for, or holding, the lock) -
    that state may reflect an older, already-converged spec rather than
    whatever is currently being applied.
    """
    release_name = chart_release_name(cluster)
    if not release_name:
        return False
    return helm.is_release_locked(
        k8s_client,
        helm_lock_lease_name(cluster),
        cluster_namespace(cluster),
    )
