"""Tests for Docker package update-check adapter routing."""
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from dt_master.runtime_adapters.contracts import RuntimeAdapterRequest
from dt_master.runtime_adapters.docker import DockerRuntimeAdapter


def update_request():
    return RuntimeAdapterRequest(
        adapter_id="docker-compose-v1",
        runtime_type="docker",
        target_doctype="Infrastructure Node",
        target_name="NODE-01",
        method="updates_check",
        payload={},
    )


class TestDockerUpdateCheckAdapter(unittest.TestCase):
    def setUp(self):
        self.node = SimpleNamespace(
            name="NODE-01", access_verified=True, scripts_base_dir="/opt/dt/scripts",
            ip_address="192.0.2.1", ssh_user="deploy", ssh_port=22, ssh_host_alias=None,
            check_permission=lambda permission: None,
        )

    def test_fixed_read_only_script_without_sudo(self):
        payload = '{"schema_version":"1.0.0","observed_at":"2026-11-20T10:00:00Z","package_manager":"apt","metadata":{"status":"Fresh","age_seconds":60,"newest_index_at":"2026-11-20T09:59:00Z"},"packages":[],"overall_status":"Up To Date"}'
        with patch("frappe.get_doc", return_value=self.node), patch(
            "dt_master.runtime_adapters.docker.run_remote_script", return_value=(0, payload, "")
        ) as run:
            result = DockerRuntimeAdapter().invoke("updates_check", update_request())
        self.assertEqual(result.outcome, "succeeded")
        self.assertEqual(result.data["update_check"]["overall_status"], "Up To Date")
        self.assertEqual(run.call_args.args[2:4], ("docker", "check_docker_updates.sh"))
        self.assertFalse(run.call_args.kwargs["use_sudo"])

    def test_invalid_payload_fails_closed(self):
        with patch("frappe.get_doc", return_value=self.node), patch(
            "dt_master.runtime_adapters.docker.run_remote_script", return_value=(0, '{"unexpected":true}', "")
        ):
            result = DockerRuntimeAdapter().invoke("updates_check", update_request())
        self.assertEqual(result.outcome, "failed")
        self.assertEqual(result.data["update_check"]["overall_status"], "Unknown")


if __name__ == "__main__":
    unittest.main()
