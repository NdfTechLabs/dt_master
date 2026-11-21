"""Unit tests for the read-only Host runtime adapter."""

import datetime
import json
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
		"swap_total_bytes": "2147483648",
		"python3_available": "true",
		"node_available": "true",
		"npm_available": "true",
		"yarn_available": "false",
		"nginx_available": "true",
		"supervisorctl_available": "true",
		"redis_cli_available": "true",
		"mariadb_available": "true",
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
			project_base_dir=None,
			project_logs_dir=None,
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
				"dt_master.runtime_adapters.host.run_remote_script", return_value=(code, stdout, stderr)
			) as run,
		):
			result = self.adapter.invoke(method, request(method))
		return result, run

	def test_connection_check_uses_fixed_no_sudo_ssh_probe(self):
		with (
			patch("dt_master.runtime_adapters.host.frappe.get_doc", return_value=self.node),
			patch(
				"dt_master.runtime_adapters.host.run_remote_script",
				return_value=(0, "DT_MASTER_SSH_OK\\n", ""),
			) as run,
		):
			result = self.adapter.invoke("connection_check", request("connection_check"))
		self.assertEqual(result.outcome, "succeeded")
		self.assertEqual(result.observed_state, "Connected")
		self.assertEqual(result.data["connection_status"], "Connected")
		self.assertEqual(result.data["transport"], "SSH")
		self.assertEqual(run.call_args.args[2:4], ("node", "test_node_connection.sh"))
		self.assertFalse(run.call_args.kwargs["use_sudo"])
		self.assertEqual(run.call_args.kwargs["timeout_seconds"], 20)

	def test_connection_check_fails_when_remote_marker_is_missing(self):
		with (
			patch("dt_master.runtime_adapters.host.frappe.get_doc", return_value=self.node),
			patch("dt_master.runtime_adapters.host.run_remote_script", return_value=(0, "unexpected\\n", "")),
		):
			result = self.adapter.invoke("connection_check", request("connection_check"))
		self.assertEqual(result.outcome, "failed")
		self.assertEqual(result.observed_state, "Unreachable")
		self.assertEqual(result.diagnostics[0]["code"], "NODE_SSH_CONNECTION_FAILED")

	def test_connection_check_rejects_unverified_node_before_ssh(self):
		self.node.access_verified = 0
		with (
			patch("dt_master.runtime_adapters.host.frappe.get_doc", return_value=self.node),
			patch("dt_master.runtime_adapters.host.run_remote_script") as run,
		):
			with self.assertRaises(ValueError):
				self.adapter.invoke("connection_check", request("connection_check"))
		run.assert_not_called()

	def test_inspection_uses_existing_ssh_script_contract_without_sudo(self):
		result, run = self._run_inspection()
		self.assertEqual(result.outcome, "succeeded")
		self.assertEqual(result.observed_state, "Available")
		self.assertEqual(result.data["cpu_cores"], 8)
		self.assertTrue(result.data["bench_available"])
		self.assertEqual(result.data["host_stage"], "Bench CLI Available")
		self.assertEqual(result.data["swap_total_bytes"], 2147483648)
		self.assertTrue(result.data["tools"]["python3"])
		self.assertFalse(result.data["tools"]["yarn"])
		self.assertEqual(result.data["os_support_status"], "Supported")
		self.assertEqual(result.data["architecture"], "amd64")
		self.assertEqual(run.call_args.kwargs["use_sudo"], False)
		self.assertEqual(run.call_args.args[2:4], ("node", "inspect_host_runtime.sh"))

	def test_v2_discovery_persists_snapshot_and_prefills_only_blank_inventory(self):
		discovery = {
			"schema_version": "2.0.0",
			"observed_at": "2026-11-20T17:17:33Z",
			"identity": {"hostname": "node-01", "fqdn": "node-01.example.test"},
			"os": {
				"pretty_name": "Ubuntu 24.04.4 LTS", "id": "ubuntu", "version_id": "24.04",
				"version_codename": "noble", "id_like": "debian", "architecture": "x86_64",
				"package_manager": "apt", "service_manager": "systemd",
			},
			"compute": {
				"logical_cpu_cores": 6, "physical_cpu_cores": 3, "memory_total_bytes": 8 * 1024**3,
				"memory_available_bytes": 4 * 1024**3, "swap_total_bytes": 2 * 1024**3,
				"swap_free_bytes": 1 * 1024**3, "cpu_model": "Example CPU",
				"load_average": [0.1, 0.2, 0.3], "uptime_seconds": 3600, "virtualization": "kvm",
			},
			"storage": {
				"filesystems": [], "root_available_bytes": 50 * 1024**3,
				"root_size_bytes": 100 * 1024**3, "disk_total_bytes": 100 * 1024**3,
			},
			"network": {"interfaces": [], "default_routes": [], "dns_nameservers": [], "listening_sockets": []},
			"tools": {"python3": True, "nginx": True},
			"services": {"nginx": {"active": "active", "enabled": "enabled"}},
			"bench_available": False,
		}
		self.node.cpu_cores = 0
		self.node.ram_gb = 0
		self.node.storage_gb = 0
		self.node.os_type = ""
		self.node.os_version = ""
		stdout = "discovery_json=" + json.dumps(discovery, separators=(",", ":")) + "\n"
		with (
			patch("dt_master.runtime_adapters.host.frappe.get_doc", return_value=self.node),
			patch("dt_master.runtime_adapters.host.frappe.db.set_value") as set_value,
			patch(
				"dt_master.runtime_adapters.host.now_datetime",
				return_value=datetime.datetime(2026, 11, 20, 18, 0, 0),
			),
			patch("dt_master.runtime_adapters.host.run_remote_script", return_value=(0, stdout, "")),
		):
			result = self.adapter.invoke("inspect", request())
		self.assertEqual(result.data["schema_version"], "2.0.0")
		self.assertEqual(result.data["discovery"]["network"]["interfaces"], [])
		updates = set_value.call_args.args[2]
		self.assertEqual(updates["cpu_cores"], 6)
		self.assertEqual(updates["ram_gb"], 8)
		self.assertEqual(updates["storage_gb"], 100)
		self.assertEqual(updates["os_type"], "Ubuntu")
		self.assertEqual(updates["os_version"], "24.04")
		self.assertEqual(updates["host_discovery_status"], "Successful")
		self.assertIn("host_discovery_snapshot", updates)

	def test_prefill_preserves_operator_values_and_treats_zero_as_blank(self):
		node = SimpleNamespace(
			name="NODE-0001",
			cpu_cores=8,
			ram_gb=16,
			storage_gb="0",
			os_type="Debian",
			os_version="12",
		)
		discovery = {
			"identity": {"hostname": "observed-host"},
			"os": {"id": "ubuntu", "version_id": "24.04"},
			"compute": {"logical_cpu_cores": 12, "memory_total_bytes": 32 * 1024**3},
			"storage": {"disk_total_bytes": 200 * 1024**3},
		}
		with patch("dt_master.runtime_adapters.host.frappe.db.set_value") as set_value:
			HostRuntimeAdapter._persist_discovery_snapshot(node, discovery)

		updates = set_value.call_args.args[2]
		self.assertNotIn("cpu_cores", updates)
		self.assertNotIn("ram_gb", updates)
		self.assertEqual(updates["storage_gb"], 200)
		self.assertNotIn("os_type", updates)
		self.assertNotIn("os_version", updates)
		self.assertIn("host_discovery_snapshot", updates)
		self.assertEqual(set(updates) - {
			"host_discovery_status", "host_discovered_on", "host_discovery_snapshot",
			"storage_gb",
		}, set())

	def test_fresh_host_is_inspectable_without_bench_project_paths(self):
		result, run = self._run_inspection(
			stdout=inspection_output(bench_available="false", python3_available="false", node_available="false")
		)
		self.assertEqual(result.outcome, "succeeded")
		self.assertEqual(result.data["host_stage"], "Fresh Host")
		self.assertFalse(result.data["bench_available"])
		self.assertFalse(result.data["tools"]["python3"])
		run.assert_called_once()

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
