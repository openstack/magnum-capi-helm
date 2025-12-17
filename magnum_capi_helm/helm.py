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
import functools
import json
import pathlib
import requests
import time
import typing as t
import uuid

from magnum.common import utils
from oslo_concurrency import processutils
from oslo_log import log as logging

from magnum_capi_helm import conf
from magnum_capi_helm import kubernetes

LOG = logging.getLogger(__name__)
CONF = conf.CONF

# This code is loosely based on:
#  https://github.com/azimuth-cloud/pyhelm3
#  Ideally we can share this code in the future.


def mergeconcat(defaults, *overrides):
    """Deep-merge two or more dictionaries together.

    Lists are concatenated.
    """

    def mergeconcat2(defaults, overrides):
        if isinstance(defaults, dict) and isinstance(overrides, dict):
            merged = dict(defaults)
            for key, value in overrides.items():
                if key in defaults:
                    merged[key] = mergeconcat2(defaults[key], value)
                else:
                    merged[key] = value
            return merged
        elif isinstance(defaults, (list, tuple)) and isinstance(
            overrides, (list, tuple)
        ):
            merged = list(defaults)
            merged.extend(overrides)
            return merged
        else:
            return overrides if overrides is not None else defaults

    return functools.reduce(mergeconcat2, overrides, defaults)


class Client:
    """Client for interacting with Helm CLI."""

    def __init__(self):
        self._default_timeout = f"{CONF.capi_helm.helm_timeout}s"
        self._executable = "helm"
        self._history_max_revisions = 10
        self._kubeconfig = CONF.capi_helm.kubeconfig_file

    def _run(self, command, **kwargs) -> bytes:
        command = [self._executable] + command
        if self._kubeconfig:
            command.extend(["--kubeconfig", self._kubeconfig])
        stdout, stderr = utils.execute(*command, **kwargs)
        LOG.debug(f"Ran helm {command} got out:{stdout} err:{stderr}")
        return stdout

    def install_or_upgrade(
        self,
        release_name: str,
        chart_ref: t.Union[pathlib.Path, str],
        *values: t.Dict[str, t.Any],
        namespace: str,
        repo: t.Optional[str] = None,
        version: t.Optional[str] = None,
    ) -> t.Iterable[t.Dict[str, t.Any]]:
        """Install or upgrade specified release using chart and values."""
        assert release_name is not None
        command = [
            "upgrade",
            release_name,
            chart_ref,
            "--history-max",
            self._history_max_revisions,
            "--install",
            "--output",
            "json",
            "--timeout",
            self._default_timeout,
            # We send the values in on stdin
            "--values",
            "-",
            "--namespace",
            namespace,
        ]
        if repo:
            command += ["--repo", repo]
        if version:
            command += [
                "--version",
                version,
            ]

        process_input = json.dumps(mergeconcat({}, *values))
        return json.loads(self._run(command, process_input=process_input))

    def uninstall_release(
        self,
        release_name: str,
        namespace: str,
    ):
        """Uninstall the named release."""
        assert release_name is not None
        command = [
            "uninstall",
            release_name,
            "--timeout",
            self._default_timeout,
            "--namespace",
            namespace,
        ]
        try:
            self._run(command)
        except processutils.ProcessExecutionError as exc:
            # Swallow release not found errors, as that is our desired state
            if not exc.stderr or "release: not found" not in exc.stderr:
                raise


class HelmLockException(Exception):
    pass


def lease_name_for_release(release_name):
    return f"capi-helm-{release_name}"


class HelmLock:
    """Context manager to provide locking semantics for Helm commands.

    Uses the Kubernetes leases.coordination.k8s.io/v1 API.
    """

    def __init__(
        self,
        release_name: str,
        namespace: str,
        timeout_seconds: t.Optional[int] = None,
    ):
        # Add a safety margin so the lease cannot expire while a Helm
        # command that takes close to its own timeout is still running.
        self._lease_duration_seconds = CONF.capi_helm.helm_timeout + 60
        self._wait_timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else CONF.capi_helm.helm_lock_timeout
        )
        self._k8s_client = kubernetes.Client.load()
        # Unique per lock instance, so we can tell whether we are still
        # the current holder before renewing or releasing the lease.
        self._holder_identity = uuid.uuid4().hex
        self.namespace = namespace
        self.release_name = release_name
        self.lease_name = lease_name_for_release(release_name)
        # Set once we successfully acquire the lease, and kept up to date
        # by renew(), so renew() can reuse it instead of re-fetching the
        # lease it already has.
        self._lease = None

    def __enter__(self):
        for i in range(self._wait_timeout_seconds):
            if self._acquire_lock():
                return self
            LOG.debug(
                "Waiting for lock on Helm release "
                f"{self.namespace}/{self.release_name}"
            )
            time.sleep(1)
        raise HelmLockException(
            f"Timed out waiting {self._wait_timeout_seconds} for Helm lock"
        )

    def __exit__(self, exc_type, exc_val, exc_tb):
        lease = self._k8s_client.get_lease(self.lease_name, self.namespace)
        holder = lease.get("spec", {}).get("holderIdentity") if lease else None
        if holder != self._holder_identity:
            if lease is not None:
                # Our lease must have expired and been reacquired by
                # another holder. Deleting it here would release someone
                # else's active lock, so leave it alone - they are
                # responsible for releasing it themselves.
                LOG.warning(
                    "Not releasing Helm lock for %s/%s: currently held by "
                    "%s, not %s (the lease likely expired and was "
                    "reacquired by another process)",
                    self.namespace,
                    self.release_name,
                    holder,
                    self._holder_identity,
                )
            return
        resource_version = lease.get("metadata", {}).get("resourceVersion")
        try:
            self._k8s_client.delete_lease(
                self.lease_name,
                self.namespace,
                resource_version=resource_version,
            )
        except requests.exceptions.HTTPError as exc:
            # Best-effort cleanup only: the lease self-expires via its
            # TTL, so a failure here must not raise and mask whatever
            # exception the code this lock was protecting may have
            # raised.
            LOG.warning(
                "Failed to release Helm lock for %s/%s: %s",
                self.namespace,
                self.release_name,
                exc,
            )

    def renew(self):
        """Resets the lease's TTL clock to extend how long we hold it.

        Call this immediately before starting the operation the lock is
        actually meant to protect (e.g. the Helm CLI call) if other,
        unbounded work has already happened since __enter__ - the lease
        then only needs to comfortably outlive that one protected step,
        regardless of how long the preceding work took.

        Raises HelmLockException if we are no longer the holder (e.g.
        the lease already expired and was reacquired by someone else) -
        the caller must not proceed with the protected operation in that
        case.
        """
        # We already have the lease from when we acquired (or last
        # renewed) it, so there's no need to re-fetch it here - the
        # resourceVersion we send below is a CAS precondition, so if
        # someone else has since reacquired the lease this still fails
        # safely (with a 409, handled below) rather than stomping on it.
        holder = (
            self._lease.get("spec", {}).get("holderIdentity")
            if self._lease
            else None
        )
        if holder != self._holder_identity:
            raise HelmLockException(
                f"Lost Helm lock for {self.namespace}/{self.release_name} "
                "before it could be renewed"
            )

        now = datetime.datetime.now(tz=datetime.timezone.utc)
        lease_data = {
            "metadata": {
                "resourceVersion": self._lease.get("metadata", {}).get(
                    "resourceVersion"
                )
            },
            "spec": {
                "acquireTime": self._lease.get("spec", {}).get("acquireTime"),
                "renewTime": now.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                "holderIdentity": self._holder_identity,
                "leaseDurationSeconds": self._lease_duration_seconds,
            },
        }
        try:
            self._lease = self._k8s_client.apply_lease(
                self.lease_name, lease_data, self.namespace
            )
        except requests.exceptions.HTTPError as exc:
            if exc.response.status_code == 409:
                raise HelmLockException(
                    f"Lost Helm lock for {self.namespace}/"
                    f"{self.release_name} before it could be renewed"
                ) from exc
            raise

    def _acquire_lock(self) -> bool:
        """Attempts to acquire a lease for the given Helm release.

        Returns True when lease is successfully acquired or False otherwise.
        The caller is responsible for retrying acquisition if False.
        """

        lease = self._k8s_client.get_lease(self.lease_name, self.namespace)
        LOG.debug(
            f"Lease for Helm release {self.namespace}/{self.release_name}: "
            f"{lease}"
        )

        now = datetime.datetime.now(tz=datetime.timezone.utc)
        now_str = now.strftime("%Y-%m-%dT%H:%M:%S.%fZ")

        spec = {
            "acquireTime": now_str,
            "renewTime": now_str,
            "holderIdentity": self._holder_identity,
            "leaseDurationSeconds": self._lease_duration_seconds,
        }

        # If lease doesn't exist, we're free to create one and acquire
        # the lock. Unlike apply_lease(), create_lease() fails atomically
        # with a 409 if someone else created it first, so at most one
        # concurrent caller can win this race.
        if lease is None:
            LOG.debug(
                "Creating new lease for Helm release "
                f"{self.namespace}/{self.release_name}"
            )
            try:
                self._lease = self._k8s_client.create_lease(
                    self.lease_name, {"spec": spec}, self.namespace
                )
            except requests.exceptions.HTTPError as exc:
                if exc.response.status_code == 409:
                    return False
                raise
            return True

        renew_time = lease.get("spec", {}).get("renewTime")
        if renew_time is None:
            raise HelmLockException("renewTime should not be None")
        renew_time = datetime.datetime.fromisoformat(
            renew_time.replace("Z", "+00:00")
        )

        ttl = lease.get("spec", {}).get("leaseDurationSeconds")
        if ttl is None:
            raise HelmLockException("leaseDurationSections should not be None")
        lease_duration = datetime.timedelta(seconds=ttl)

        # If the lease has expired then previous holder must have failed
        # to delete it when finished so we can acquire it anyway
        if renew_time + lease_duration < now:
            LOG.debug(
                "Found expired lease for Helm release "
                f"{self.namespace}/{self.release_name}"
            )
            # Use the existing resourceVersion as a CAS precondition, so
            # k8s rejects this with a 409 if another conductor updated
            # the lease after we fetched it.
            lease_data = {
                "metadata": {
                    "resourceVersion": lease.get("metadata", {}).get(
                        "resourceVersion"
                    )
                },
                "spec": spec,
            }
            try:
                self._lease = self._k8s_client.apply_lease(
                    self.lease_name, lease_data, self.namespace
                )
            except requests.exceptions.HTTPError as exc:
                # If we try to apply a lease with an older version
                # (e.g. because another conductor updated the lease
                # after we fetched it) k8s will return an HTTP 409 response.
                # This is equivalent to failing to acquire the lock.
                if exc.response.status_code == 409:
                    return False
                raise exc
            return True

        return False


def is_lease_active(lease, now=None):
    """Returns True if the given lease dict has not expired.

    Unlike HelmLock._acquire_lock's own expiry check, this tolerates
    missing/malformed lease data by treating it as inactive rather than
    raising, since it is used by status/health checks that should never
    fail just because a lease could not be parsed.
    """
    if lease is None:
        return False
    if now is None:
        now = datetime.datetime.now(tz=datetime.timezone.utc)

    renew_time = lease.get("spec", {}).get("renewTime")
    ttl = lease.get("spec", {}).get("leaseDurationSeconds")
    if renew_time is None or ttl is None:
        return False

    renew_time = datetime.datetime.fromisoformat(
        renew_time.replace("Z", "+00:00")
    )
    return now < renew_time + datetime.timedelta(seconds=ttl)


def is_release_locked(k8s_client, lease_name, namespace):
    """Returns True if a HelmLock is currently held for the given release.

    Used by status/health checks to avoid trusting the current state of
    a cluster's CAPI resources while a Helm update for that cluster is
    still being applied (i.e. is waiting for, or holding, the lock) -
    that state may reflect an older, already-converged spec rather than
    whatever is currently being applied.
    """
    lease = k8s_client.get_lease(lease_name, namespace)
    return is_lease_active(lease)
