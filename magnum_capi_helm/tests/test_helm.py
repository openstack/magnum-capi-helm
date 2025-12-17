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

import datetime
from unittest import mock

import requests
import yaml

from magnum.common import utils
from oslo_concurrency import processutils

from magnum_capi_helm import helm
from magnum_capi_helm import kubernetes
from magnum_capi_helm.tests import base


class TestHelmClient(base.TestCase):
    def test_mergeconcat_dicts(self):
        defaults = dict(foo="bar", asdf=dict(a="b", c="d"))
        overrides = dict(asdf=dict(a="c"))

        result = helm.mergeconcat(defaults, overrides)

        expected = dict(foo="bar", asdf=dict(a="c", c="d"))
        self.assertEqual(expected, result)

    def test_mergeconcat_list(self):
        defaults = ["foo", "bar"]
        overrides = ["bar", "baz"]

        result = helm.mergeconcat(defaults, overrides)

        expected = ["foo", "bar", "bar", "baz"]
        self.assertEqual(expected, result)

    @mock.patch.object(utils, "execute")
    def test_install_or_upgrade(self, mock_execute):
        mock_execute.return_value = '[{"foo": "bar"}]', ""

        client = helm.Client()
        result = client.install_or_upgrade(
            "myfirstcluster",
            "mychart",
            dict(foo="bar", b=42),
            repo="http://myrepo",
            version="v1.42",
            namespace="mynamespace",
        )

        self.assertEqual([{"foo": "bar"}], result)
        mock_execute.assert_called_once_with(
            "helm",
            "upgrade",
            "myfirstcluster",
            "mychart",
            "--history-max",
            10,
            "--install",
            "--output",
            "json",
            "--timeout",
            "300s",
            "--values",
            "-",
            "--namespace",
            "mynamespace",
            "--repo",
            "http://myrepo",
            "--version",
            "v1.42",
            process_input='{"foo": "bar", "b": 42}',
        )

    @mock.patch.object(utils, "execute")
    def test_install_or_upgrade_oci(self, mock_execute):
        mock_execute.return_value = '[{"foo": "bar"}]', ""

        client = helm.Client()
        result = client.install_or_upgrade(
            "myfirstcluster",
            "oci://localhost:5000/helm-charts/mychart",
            dict(foo="bar", b=42),
            version="v1.42",
            namespace="mynamespace",
        )

        self.assertEqual([{"foo": "bar"}], result)
        mock_execute.assert_called_once_with(
            "helm",
            "upgrade",
            "myfirstcluster",
            "oci://localhost:5000/helm-charts/mychart",
            "--history-max",
            10,
            "--install",
            "--output",
            "json",
            "--timeout",
            "300s",
            "--values",
            "-",
            "--namespace",
            "mynamespace",
            "--version",
            "v1.42",
            process_input='{"foo": "bar", "b": 42}',
        )

    @mock.patch.object(helm.CONF, "capi_helm")
    @mock.patch.object(utils, "execute")
    def test_uninstall_release_works(self, mock_execute, mock_conf):
        mock_execute.return_value = "", ""
        mock_conf.kubeconfig_file = "/etc/magnum/kubeconfig"
        mock_conf.helm_timeout = 300

        client = helm.Client()
        result = client.uninstall_release(
            "myfirstcluster", namespace="mynamespace"
        )

        self.assertIsNone(result)
        mock_execute.assert_called_once_with(
            "helm",
            "uninstall",
            "myfirstcluster",
            "--timeout",
            "300s",
            "--namespace",
            "mynamespace",
            "--kubeconfig",
            "/etc/magnum/kubeconfig",
        )

    @mock.patch.object(utils, "execute")
    def test_uninstall_release_ignore_not_found(self, mock_execute):
        mock_execute.side_effect = processutils.ProcessExecutionError(
            stderr="release: not found"
        )

        client = helm.Client()
        result = client.uninstall_release(
            "myfirstcluster", namespace="mynamespace"
        )

        self.assertIsNone(result)

    @mock.patch.object(utils, "execute")
    def test_uninstall_release_raises(self, mock_execute):
        mock_execute.side_effect = processutils.ProcessExecutionError(
            stderr="oh dear!"
        )
        client = helm.Client()

        self.assertRaises(
            processutils.ProcessExecutionError,
            client.uninstall_release,
            "myfirstcluster",
            namespace="mynamespace",
        )


class TestHelmLock(base.TestCase):

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

    @mock.patch(
        "builtins.open",
        new_callable=mock.mock_open,
        read_data=TEST_KUBECONFIG_YAML,
    )
    @mock.patch.object(kubernetes.Lease, "delete")
    @mock.patch.object(kubernetes.Lease, "create")
    @mock.patch.object(kubernetes.Lease, "fetch")
    def test_acquire_new_lock(
        self, fetch_lease, create_lease, delete_lease, mock_open
    ):

        fetch_lease.return_value = None
        create_lease.return_value = None
        delete_lease.return_value = None

        with helm.HelmLock("foo", "bar", 1):
            return
        # Unreachable if lock acquisition works
        self.assertTrue(False)

    @mock.patch(
        "builtins.open",
        new_callable=mock.mock_open,
        read_data=TEST_KUBECONFIG_YAML,
    )
    @mock.patch.object(kubernetes.Lease, "delete")
    @mock.patch.object(kubernetes.Lease, "create")
    @mock.patch.object(kubernetes.Lease, "fetch")
    def test_enter_returns_self(
        self, fetch_lease, create_lease, delete_lease, mock_open
    ):
        fetch_lease.return_value = None
        create_lease.return_value = None
        delete_lease.return_value = None

        lock = helm.HelmLock("foo", "bar", 1)
        with lock as entered:
            self.assertIs(lock, entered)

    @mock.patch(
        "builtins.open",
        new_callable=mock.mock_open,
        read_data=TEST_KUBECONFIG_YAML,
    )
    @mock.patch.object(kubernetes.Lease, "delete")
    @mock.patch.object(kubernetes.Lease, "create")
    @mock.patch.object(kubernetes.Lease, "fetch")
    def test_lose_create_race(
        self, fetch_lease, create_lease, delete_lease, mock_open
    ):
        # Simulate a concurrent process creating the lease first: k8s
        # rejects our create with a 409 AlreadyExists. We must treat
        # this the same as failing to acquire the lock (return False),
        # not raise - the __enter__ retry loop is responsible for
        # retrying or eventually timing out.
        fetch_lease.return_value = None
        delete_lease.return_value = None

        response = mock.Mock()
        response.status_code = 409
        response.reason = "AlreadyExists"
        create_lease.side_effect = requests.exceptions.HTTPError(
            response=response
        )

        def get_lock():
            with helm.HelmLock("foo", "bar", timeout_seconds=1):
                return

        self.assertRaises(
            helm.HelmLockException,
            get_lock,
        )

    @mock.patch(
        "builtins.open",
        new_callable=mock.mock_open,
        read_data=TEST_KUBECONFIG_YAML,
    )
    @mock.patch.object(kubernetes.Lease, "delete")
    @mock.patch.object(kubernetes.Lease, "apply")
    @mock.patch.object(kubernetes.Lease, "fetch")
    def test_wait_for_lock(
        self, fetch_lease, apply_lease, delete_lease, mock_open
    ):

        now = datetime.datetime.now(tz=datetime.UTC)
        now_str = now.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        mock_lease = {
            "spec": {
                "acquireTime": now_str,
                "renewTime": now_str,
                "holderIdentity": "foo",
                "leaseDurationSeconds": 2,
            }
        }

        fetch_lease.return_value = mock_lease
        apply_lease.return_value = None
        delete_lease.return_value = None

        with helm.HelmLock("foo", "bar", 3):
            return
        # Unreachable if lock acquisition works
        self.assertTrue(False)

    @mock.patch(
        "builtins.open",
        new_callable=mock.mock_open,
        read_data=TEST_KUBECONFIG_YAML,
    )
    @mock.patch.object(kubernetes.Lease, "delete")
    @mock.patch.object(kubernetes.Lease, "apply")
    @mock.patch.object(kubernetes.Lease, "fetch")
    def test_claim_expired_lock(
        self, fetch_lease, apply_lease, delete_lease, mock_open
    ):
        now = datetime.datetime.now(tz=datetime.UTC)
        now_str = now.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        mock_lease = {
            "spec": {
                "acquireTime": now_str,
                "renewTime": now_str,
                "holderIdentity": "foo",
                "leaseDurationSeconds": 2,
            }
        }

        fetch_lease.return_value = mock_lease
        apply_lease.return_value = None
        delete_lease.return_value = None

        with helm.HelmLock("foo", "bar", timeout_seconds=3):
            pass
        apply_lease.assert_called_once()

    @mock.patch(
        "builtins.open",
        new_callable=mock.mock_open,
        read_data=TEST_KUBECONFIG_YAML,
    )
    @mock.patch.object(kubernetes.Lease, "delete")
    @mock.patch.object(kubernetes.Lease, "apply")
    @mock.patch.object(kubernetes.Lease, "fetch")
    def test_timeout_waiting_for_lock(
        self, fetch_lease, apply_lease, delete_lease, mock_open
    ):

        now = datetime.datetime.now(tz=datetime.UTC)
        now_str = now.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        mock_lease = {
            "spec": {
                "acquireTime": now_str,
                "renewTime": now_str,
                "holderIdentity": "foo",
                "leaseDurationSeconds": 3,
            }
        }

        fetch_lease.return_value = mock_lease
        apply_lease.return_value = None
        delete_lease.return_value = None

        def get_lock():
            with helm.HelmLock("foo", "bar", timeout_seconds=2):
                return

        self.assertRaises(
            helm.HelmLockException,
            get_lock,
        )

    @mock.patch(
        "builtins.open",
        new_callable=mock.mock_open,
        read_data=TEST_KUBECONFIG_YAML,
    )
    @mock.patch.object(kubernetes.Lease, "delete")
    @mock.patch.object(kubernetes.Lease, "create")
    @mock.patch.object(kubernetes.Lease, "fetch")
    def test_release_lock_deletes_when_still_owner(
        self, fetch_lease, create_lease, delete_lease, mock_open
    ):
        fetch_lease.return_value = None
        create_lease.return_value = None
        delete_lease.return_value = None

        lock = helm.HelmLock("foo", "bar", 1)
        with lock:
            # Simulate reading back our own lease when we come to release
            # it - this is the common case, where nothing else has
            # touched the lease in the meantime.
            fetch_lease.return_value = {
                "metadata": {"resourceVersion": "42"},
                "spec": {"holderIdentity": lock._holder_identity},
            }

        # The resourceVersion we last read is passed as a CAS
        # precondition, so we can't delete a lease that someone else
        # has since recreated/updated.
        delete_lease.assert_called_once_with(
            "capi-helm-foo", "bar", resource_version="42"
        )

    @mock.patch(
        "builtins.open",
        new_callable=mock.mock_open,
        read_data=TEST_KUBECONFIG_YAML,
    )
    @mock.patch.object(kubernetes.Lease, "delete")
    @mock.patch.object(kubernetes.Lease, "create")
    @mock.patch.object(kubernetes.Lease, "fetch")
    def test_release_lock_skipped_when_not_owner(
        self, fetch_lease, create_lease, delete_lease, mock_open
    ):
        fetch_lease.return_value = None
        create_lease.return_value = None
        delete_lease.return_value = None

        lock = helm.HelmLock("foo", "bar", 1)
        with lock:
            # Simulate our lease having expired and been reacquired by a
            # different holder by the time we come to release it - we
            # must not delete someone else's active lock.
            fetch_lease.return_value = {
                "spec": {"holderIdentity": "someone-else"}
            }

        delete_lease.assert_not_called()

    @mock.patch(
        "builtins.open",
        new_callable=mock.mock_open,
        read_data=TEST_KUBECONFIG_YAML,
    )
    @mock.patch.object(kubernetes.Lease, "delete")
    @mock.patch.object(kubernetes.Lease, "create")
    @mock.patch.object(kubernetes.Lease, "fetch")
    def test_exit_swallows_delete_lease_error(
        self, fetch_lease, create_lease, delete_lease, mock_open
    ):
        # __exit__ must never raise: the lease self-expires via its TTL,
        # so a failed best-effort cleanup here must not replace/mask
        # whatever exception the code the lock was protecting may have
        # raised.
        fetch_lease.return_value = None
        create_lease.return_value = None

        response = mock.Mock()
        response.status_code = 500
        delete_lease.side_effect = requests.exceptions.HTTPError(
            response=response
        )

        lock = helm.HelmLock("foo", "bar", 1)
        with lock:
            fetch_lease.return_value = {
                "spec": {"holderIdentity": lock._holder_identity}
            }
        # Reaching here (no exception propagated) is the assertion.

    @mock.patch(
        "builtins.open",
        new_callable=mock.mock_open,
        read_data=TEST_KUBECONFIG_YAML,
    )
    @mock.patch.object(kubernetes.Lease, "delete")
    @mock.patch.object(kubernetes.Lease, "apply")
    @mock.patch.object(kubernetes.Lease, "fetch")
    def test_lease_update_conflict(
        self,
        fetch_lease,
        apply_lease,
        delete_lease,
        mock_open,
    ):

        now = datetime.datetime.now(tz=datetime.UTC)
        now_str = now.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        mock_lease = {
            "metadata": {"resourceVersion": "foo"},
            "spec": {
                "acquireTime": now_str,
                "renewTime": now_str,
                "holderIdentity": "bar",
                "leaseDurationSeconds": 3,
            },
        }

        fetch_lease.return_value = mock_lease
        delete_lease.return_value = None

        # Simulate 409 response from k8s API response
        response = mock.Mock()
        response.status_code = 409
        response.reason = "Conflict"
        conflict_error = requests.exceptions.HTTPError(response=response)
        apply_lease.side_effect = conflict_error

        def get_lock():
            with helm.HelmLock("foo", "bar", timeout_seconds=1):
                return

        self.assertRaises(
            helm.HelmLockException,
            get_lock,
        )

    @mock.patch(
        "builtins.open",
        new_callable=mock.mock_open,
        read_data=TEST_KUBECONFIG_YAML,
    )
    @mock.patch.object(kubernetes.Lease, "delete")
    @mock.patch.object(kubernetes.Lease, "apply")
    @mock.patch.object(kubernetes.Lease, "create")
    @mock.patch.object(kubernetes.Lease, "fetch")
    def test_renew_still_owner(
        self,
        fetch_lease,
        create_lease,
        apply_lease,
        delete_lease,
        mock_open,
    ):
        fetch_lease.return_value = None
        delete_lease.return_value = None

        lock = helm.HelmLock("foo", "bar", 1)
        # The lease returned when we create it is cached on the lock, so
        # renew() can reuse it instead of re-fetching.
        create_lease.return_value = {
            "metadata": {"resourceVersion": "1"},
            "spec": {
                "acquireTime": "2024-01-01T00:00:00.000000Z",
                "holderIdentity": lock._holder_identity,
                "leaseDurationSeconds": lock._lease_duration_seconds,
            },
        }
        apply_lease.return_value = None

        with lock:
            # fetch_lease was already called once, by _acquire_lock() on
            # entry. renew() must not trigger another fetch - it should
            # reuse the lease cached from create_lease() above.
            fetch_count_before_renew = fetch_lease.call_count
            lock.renew()
            self.assertEqual(fetch_count_before_renew, fetch_lease.call_count)

        apply_lease.assert_called_once()
        applied_name, applied_data, applied_namespace = apply_lease.call_args[
            0
        ]
        self.assertEqual("capi-helm-foo", applied_name)
        self.assertEqual("bar", applied_namespace)
        self.assertEqual(
            lock._holder_identity, applied_data["spec"]["holderIdentity"]
        )
        self.assertEqual("1", applied_data["metadata"]["resourceVersion"])

    @mock.patch(
        "builtins.open",
        new_callable=mock.mock_open,
        read_data=TEST_KUBECONFIG_YAML,
    )
    @mock.patch.object(kubernetes.Lease, "delete")
    @mock.patch.object(kubernetes.Lease, "create")
    @mock.patch.object(kubernetes.Lease, "fetch")
    def test_renew_raises_when_never_acquired(
        self, fetch_lease, create_lease, delete_lease, mock_open
    ):
        # renew() relies on the lease cached by a prior successful
        # __enter__/_acquire_lock() - calling it without ever having
        # acquired the lock must fail fast rather than crash trying to
        # read out of a None lease.
        lock = helm.HelmLock("foo", "bar", 1)

        self.assertRaises(helm.HelmLockException, lock.renew)

    @mock.patch(
        "builtins.open",
        new_callable=mock.mock_open,
        read_data=TEST_KUBECONFIG_YAML,
    )
    @mock.patch.object(kubernetes.Lease, "delete")
    @mock.patch.object(kubernetes.Lease, "apply")
    @mock.patch.object(kubernetes.Lease, "create")
    @mock.patch.object(kubernetes.Lease, "fetch")
    def test_renew_raises_on_conflict(
        self,
        fetch_lease,
        create_lease,
        apply_lease,
        delete_lease,
        mock_open,
    ):
        fetch_lease.return_value = None
        delete_lease.return_value = None

        lock = helm.HelmLock("foo", "bar", 1)
        create_lease.return_value = {
            "metadata": {"resourceVersion": "1"},
            "spec": {
                "acquireTime": "2024-01-01T00:00:00.000000Z",
                "holderIdentity": lock._holder_identity,
                "leaseDurationSeconds": lock._lease_duration_seconds,
            },
        }

        with lock:
            # Simulate the lease having expired and been reacquired by
            # someone else while we were doing other work: our cached
            # resourceVersion is now stale, so the renewal apply() is
            # rejected with a 409 - we must not proceed with the
            # operation renew() is meant to protect.
            response = mock.Mock()
            response.status_code = 409
            apply_lease.side_effect = requests.exceptions.HTTPError(
                response=response
            )
            self.assertRaises(helm.HelmLockException, lock.renew)


class TestLeaseHelpers(base.TestCase):
    def _make_lease(self, seconds_ago, ttl):
        renew_time = datetime.datetime.now(
            tz=datetime.timezone.utc
        ) - datetime.timedelta(seconds=seconds_ago)
        return {
            "spec": {
                "renewTime": renew_time.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                "leaseDurationSeconds": ttl,
            }
        }

    def test_is_lease_active_no_lease(self):
        self.assertFalse(helm.is_lease_active(None))

    def test_is_lease_active_missing_fields(self):
        self.assertFalse(helm.is_lease_active({"spec": {}}))

    def test_is_lease_active_within_duration(self):
        lease = self._make_lease(seconds_ago=10, ttl=300)
        self.assertTrue(helm.is_lease_active(lease))

    def test_is_lease_active_expired(self):
        lease = self._make_lease(seconds_ago=400, ttl=300)
        self.assertFalse(helm.is_lease_active(lease))

    def test_is_release_locked_true(self):
        k8s_client = mock.MagicMock()
        k8s_client.get_lease.return_value = self._make_lease(
            seconds_ago=10, ttl=300
        )

        self.assertTrue(
            helm.is_release_locked(k8s_client, "capi-helm-foo", "ns1")
        )
        k8s_client.get_lease.assert_called_once_with("capi-helm-foo", "ns1")

    def test_is_release_locked_false_when_no_lease(self):
        k8s_client = mock.MagicMock()
        k8s_client.get_lease.return_value = None

        self.assertFalse(
            helm.is_release_locked(k8s_client, "capi-helm-foo", "ns1")
        )

    def test_is_release_locked_false_when_expired(self):
        k8s_client = mock.MagicMock()
        k8s_client.get_lease.return_value = self._make_lease(
            seconds_ago=400, ttl=300
        )

        self.assertFalse(
            helm.is_release_locked(k8s_client, "capi-helm-foo", "ns1")
        )
