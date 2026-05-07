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
