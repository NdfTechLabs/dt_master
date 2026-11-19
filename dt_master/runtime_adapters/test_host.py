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


def inspection_output(**overrides):
	values = {
		"hostname": "node.example.test",
		"os": "Ubuntu 24.04 LTS",
		"os_id": "ubuntu",
		"os_version_id": "24.04",
		"os_version_codename": "noble",
		"os_id_like": "debian",
		"raw_architecture": "x86_64",
		"architecture": "amd64",
		"package_manager": "apt",
		"service_manager": "systemd",
		"cpu_cores": "8",
		"memory_bytes": "17179869184",
		"root_available_bytes": "107374182400",
		"bench_available": "true",
	}
	values.update(overrides)
	return "".join(f"{key}={value}\n" for key, value in values.items())


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

	def _run_inspection(self, code=0, stdout=None, stderr="", method="inspect"):
		stdout = stdout if stdout is not None else inspection_output()
		with (
			patch("dt_master.runtime_adapters.host.frappe.get_doc", return_value=self.node),
			patch(
				"dt_master.runtime_adapters.host.path_environment",
				return_value={"BASE_DIR": "/opt/dtsaas/scripts"},
			),
			patch(
				"dt_master.runtime_adapters.host.run_remote_script", return_value=(code, stdout, stderr)
			) as run,
		):
			result = self.adapter.invoke(method, request(method))
		return result, run

	def test_inspection_uses_existing_ssh_script_contract_without_sudo(self):
		result, run = self._run_inspection()
		self.assertEqual(result.outcome, "succeeded")
		self.assertEqual(result.observed_state, "Available")
		self.assertEqual(result.data["cpu_cores"], 8)
		self.assertTrue(result.data["bench_available"])
		self.assertEqual(result.data["os_support_status"], "Supported")
		self.assertEqual(result.data["architecture"], "amd64")
		self.assertEqual(run.call_args.kwargs["use_sudo"], False)
		self.assertEqual(run.call_args.args[2:4], ("node", "inspect_host_runtime.sh"))

	def test_ubuntu_2204_is_supported(self):
		result, _ = self._run_inspection(
			stdout=inspection_output(
				os="Ubuntu 22.04.5 LTS",
				os_version_id="22.04",
				os_version_codename="jammy",
			)
		)
		self.assertEqual(result.data["os_support_status"], "Supported")

	def test_debian_12_is_supported(self):
		result, _ = self._run_inspection(
			stdout=inspection_output(
				os="Debian GNU/Linux 12 (bookworm)",
				os_id="debian",
				os_version_id="12",
				os_version_codename="bookworm",
			)
		)
		self.assertEqual(result.data["os_support_status"], "Supported")

	def test_other_release_or_architecture_is_detected_but_unsupported(self):
		result, _ = self._run_inspection(
			stdout=inspection_output(
				os="Ubuntu 26.04",
				os_version_id="26.04",
			)
		)
		self.assertEqual(result.outcome, "succeeded")
		self.assertEqual(result.data["os_support_status"], "Unsupported")

		result, _ = self._run_inspection(stdout=inspection_output(architecture="arm64"))
		self.assertEqual(result.data["os_support_status"], "Unsupported")

	def test_unknown_os_identity_is_not_misreported_as_supported(self):
		result, _ = self._run_inspection(stdout=inspection_output(os_id="unknown"))
		self.assertEqual(result.outcome, "succeeded")
		self.assertEqual(result.data["os_support_status"], "Unknown")

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
		result, _ = self._run_inspection(stdout=inspection_output(cpu_cores="invalid"))
		self.assertEqual(result.outcome, "failed")
		self.assertEqual(result.diagnostics[0]["code"], "HOST_INSPECTION_INVALID_MEASUREMENT")

	def test_preflight_reports_os_support_separately(self):
		result, _ = self._run_inspection(method="preflight")
		self.assertEqual(result.data["preflight_status"], "Ready")
		self.assertEqual(result.data["os_support_status"], "Supported")

	def test_preflight_blocks_unsupported_os_after_successful_inspection(self):
		result, _ = self._run_inspection(
			stdout=inspection_output(os="Ubuntu 26.04", os_version_id="26.04"),
			method="preflight",
		)
		self.assertEqual(result.outcome, "succeeded")
		self.assertEqual(result.observed_state, "Blocked")
		self.assertEqual(result.data["preflight_status"], "Blocked")
		self.assertEqual(result.data["os_support_status"], "Unsupported")
		self.assertEqual(result.diagnostics[0]["code"], "HOST_OS_UNSUPPORTED")

	def test_preflight_blocks_when_inspection_fails(self):
		with (
			patch("dt_master.runtime_adapters.host.frappe.get_doc", return_value=self.node),
			patch(
				"dt_master.runtime_adapters.host.path_environment",
				return_value={"BASE_DIR": "/opt/dtsaas/scripts"},
			),
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
			adapter_id="host-v1",
			runtime_type="host",
			target_doctype="Bench Environment",
			target_name="BENCH-0001",
			method="inspect",
			payload={},
		)
		with patch("dt_master.runtime_adapters.host.frappe.get_doc") as get_doc:
			with self.assertRaises(ValueError):
				self.adapter.invoke("inspect", bench_request)
		get_doc.assert_not_called()


if __name__ == "__main__":
	unittest.main()
