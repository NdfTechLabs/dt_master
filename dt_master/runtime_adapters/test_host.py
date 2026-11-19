"""Unit tests for the read-only Host runtime adapter."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from dt_master.runtime_adapters.contracts import RuntimeAdapterRequest
from dt_master.runtime_adapters.host import HostRuntimeAdapter


def request(method="inspect"):
    return RuntimeAdapterRequest(
        adapter_id="host-v1",
        runtime_type="host",
        target_doctype="Infrastructure Node",
        target_name="NODE-0001",
        method=method,
        payload={},
    )


class TestHostRuntimeAdapter(unittest.TestCase):
    def setUp(self):
        self.adapter = HostRuntimeAdapter()
        self.node = SimpleNamespace(
            name="NODE-0001",
            access_verified=1,
            scripts_base_dir="/opt/dtsaas/scripts",
            project_base_dir="/opt/dtsaas",
            project_logs_dir="/var/log/dtsaas",
            ip_address="192.0.2.10",
            ssh_user="deploy",
            ssh_port=22,
            ssh_host_alias=None,
            check_permission=lambda permission: self.assertEqual(permission, "read"),
        )

    def _run_inspection(self, code=0, stdout=None, stderr=""):
        stdout = stdout or (
            "hostname=node.example.test\n"
            "os=Ubuntu 24.04\n"
            "architecture=x86_64\n"
            "cpu_cores=8\n"
            "memory_bytes=17179869184\n"
            "root_available_bytes=107374182400\n"
            "bench_available=true\n"
        )
        with (
            patch("dt_master.runtime_adapters.host.frappe.get_doc", return_value=self.node),
            patch("dt_master.runtime_adapters.host.path_environment", return_value={"BASE_DIR": "/opt/dtsaas/scripts"}),
            patch("dt_master.runtime_adapters.host.run_remote_script", return_value=(code, stdout, stderr)) as run,
        ):
            result = self.adapter.invoke("inspect", request())
        return result, run

    def test_inspection_uses_existing_ssh_script_contract_without_sudo(self):
        result, run = self._run_inspection()
        self.assertEqual(result.outcome, "succeeded")
        self.assertEqual(result.observed_state, "Available")
        self.assertEqual(result.data["cpu_cores"], 8)
        self.assertTrue(result.data["bench_available"])
        self.assertEqual(run.call_args.kwargs["use_sudo"], False)
        self.assertEqual(run.call_args.args[2:4], ("node", "inspect_host_runtime.sh"))

    def test_inspection_failure_returns_sanitized_diagnostic(self):
        result, _ = self._run_inspection(code=255, stderr="private ssh detail")
        self.assertEqual(result.outcome, "failed")
        self.assertEqual(result.diagnostics[0]["code"], "HOST_INSPECTION_FAILED")
        self.assertNotIn("private ssh detail", result.diagnostics[0]["message"])

    def test_incomplete_inspection_fails_closed(self):
        result, _ = self._run_inspection(stdout="hostname=node.example.test\n")
        self.assertEqual(result.outcome, "failed")
        self.assertEqual(result.diagnostics[0]["code"], "HOST_INSPECTION_INCOMPLETE")

    def test_invalid_capacity_measurement_fails_closed(self):
        result, _ = self._run_inspection(stdout=(
            "hostname=node.example.test\nos=Ubuntu\narchitecture=x86_64\n"
            "cpu_cores=invalid\nmemory_bytes=100\nroot_available_bytes=100\n"
        ))
        self.assertEqual(result.outcome, "failed")
        self.assertEqual(result.diagnostics[0]["code"], "HOST_INSPECTION_INVALID_MEASUREMENT")

    def test_preflight_reports_ready_after_valid_inspection(self):
        stdout = (
            "hostname=node.example.test\nos=Ubuntu\narchitecture=x86_64\n"
            "cpu_cores=8\nmemory_bytes=17179869184\nroot_available_bytes=1000000\n"
            "bench_available=true\n"
        )
        with (
            patch("dt_master.runtime_adapters.host.frappe.get_doc", return_value=self.node),
            patch("dt_master.runtime_adapters.host.path_environment", return_value={"BASE_DIR": "/opt/dtsaas/scripts"}),
            patch("dt_master.runtime_adapters.host.run_remote_script", return_value=(0, stdout, "")),
        ):
            result = self.adapter.invoke("preflight", request("preflight"))
        self.assertEqual(result.outcome, "succeeded")
        self.assertEqual(result.observed_state, "Ready")
        self.assertEqual(result.data["preflight_status"], "Ready")

    def test_preflight_blocks_when_inspection_fails(self):
        with (
            patch("dt_master.runtime_adapters.host.frappe.get_doc", return_value=self.node),
            patch("dt_master.runtime_adapters.host.path_environment", return_value={"BASE_DIR": "/opt/dtsaas/scripts"}),
            patch("dt_master.runtime_adapters.host.run_remote_script", return_value=(255, "", "")),
        ):
            result = self.adapter.invoke("preflight", request("preflight"))
        self.assertEqual(result.outcome, "failed")
        self.assertEqual(result.observed_state, "Blocked")

    def test_unverified_node_is_rejected_before_ssh(self):
        self.node.access_verified = 0
        with (
            patch("dt_master.runtime_adapters.host.frappe.get_doc", return_value=self.node),
            patch("dt_master.runtime_adapters.host.run_remote_script") as run,
        ):
            with self.assertRaises(ValueError):
                self.adapter.invoke("inspect", request())
        run.assert_not_called()

    def test_bench_target_is_rejected_until_bench_environment_exists(self):
        bench_request = RuntimeAdapterRequest(
            adapter_id="host-v1", runtime_type="host",
            target_doctype="Bench Environment", target_name="BENCH-0001",
            method="inspect", payload={},
        )
        with patch("dt_master.runtime_adapters.host.frappe.get_doc") as get_doc:
            with self.assertRaises(ValueError):
                self.adapter.invoke("inspect", bench_request)
        get_doc.assert_not_called()


if __name__ == "__main__":
    unittest.main()
