"""Tests for the read-only Docker runtime adapter."""

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from dt_master.runtime_adapters.contracts import RuntimeAdapterRequest
from dt_master.runtime_adapters.docker import DockerRuntimeAdapter


def request(method="inspect"):
	return RuntimeAdapterRequest(
		adapter_id="docker-compose-v1",
		runtime_type="docker",
		target_doctype="Infrastructure Node",
		target_name="NODE-0001",
		method=method,
		payload={},
	)


def helper_install_result(status="succeeded", phase="result_verification", exit_code=0):
	return {
		"schema_version": "1.0.0",
		"helper_version": "0.2.0",
		"operation": "install_engine",
		"operation_id": "OP-0001",
		"status": status,
		"phase": phase,
		"before_state_fingerprint": "b" * 64,
		"after_state_fingerprint": "c" * 64,
		"packages": [
			{
				"name": name,
				"requested_version": "5:29.0.0-1~ubuntu.24.04~noble",
				"installed_version": "5:29.0.0-1~ubuntu.24.04~noble",
				"status": "installed",
			}
			for name in (
				"docker-ce",
				"docker-ce-cli",
				"containerd.io",
				"docker-buildx-plugin",
			)
		],
		"package_manager_exit_code": exit_code,
		"daemon_action": "package_maintainer_scripts",
		"diagnostics": [],
	}


class TestDockerRuntimeAdapter(unittest.TestCase):
	def setUp(self):
		self.adapter = DockerRuntimeAdapter()
		self.node = SimpleNamespace(
			name="NODE-0001",
			access_verified=1,
			scripts_base_dir="/opt/dtsaas/scripts",
			ip_address="192.0.2.10",
			ssh_user="deploy",
			ssh_port=22,
			ssh_host_alias=None,
			check_permission=lambda permission: self.assertEqual(permission, "read"),
			get=lambda field: None,
		)

	def test_install_engine_builds_closed_helper_request_from_fresh_plan(self):
		version = "5:29.0.0-1~ubuntu.24.04~noble"
		plan = {
			"schema_version": "1.1.0",
			"policy": {"version": "1.1.1"},
			"plan_digest": "a" * 64,
			"state_fingerprint": "b" * 64,
			"status": "ready",
			"packages": [
				{"name": name, "action": "install", "candidate_version": version}
				for name in ("docker-ce", "docker-ce-cli", "containerd.io", "docker-buildx-plugin")
			],
		}
		inspection = SimpleNamespace(
			data={
				"inspection_status": "Successful",
				"privilege_readiness": {"status": "Ready"},
				"snapshot": {"host_inventory": {"docker_install_inventory": {}}},
			}
		)
		request_obj = RuntimeAdapterRequest(
			adapter_id="docker-compose-v1",
			runtime_type="docker",
			target_doctype="Infrastructure Node",
			target_name="NODE-0001",
			method="install_engine",
			payload={
				"confirm": True,
				"plan_digest": "a" * 64,
				"expected_state_fingerprint": "b" * 64,
				"operator_impact_approval": False,
			},
			operation_id="OP-0001",
			correlation_id="CORR-0001",
		)
		verified = self.adapter.result_for(
			request_obj, outcome="succeeded", observed_state="Verified"
		)
		helper_result = helper_install_result()
		with (
			patch.object(self.adapter, "_node_for_request", return_value=self.node),
			patch.object(self.adapter, "_inspect", return_value=inspection),
			patch(
				"dt_master.node_preparation.docker_install.build_docker_engine_install_plan",
				return_value=plan,
			),
			patch(
				"dt_master.node_preparation.docker_install.validate_docker_engine_install_plan"
			),
			patch(
				"dt_master.runtime_adapters.docker.run_trusted_helper",
				return_value=(0, "{}", ""),
			) as run_helper,
			patch(
				"dt_master.runtime_adapters.docker.parse_trusted_install_engine_result",
				return_value=helper_result,
			),
			patch.object(
				self.adapter, "_verify_install_engine_postcondition", return_value=verified
			) as verify,
		):
			result = self.adapter._install_engine(request_obj)

		helper_request = run_helper.call_args.args[2]
		self.assertEqual(helper_request["operation"], "install_engine")
		self.assertEqual(helper_request["operation_id"], "OP-0001")
		self.assertEqual(helper_request["plan"]["policy_version"], "1.1.1")
		self.assertEqual(
			[package["name"] for package in helper_request["plan"]["packages"]],
			["docker-ce", "docker-ce-cli", "containerd.io", "docker-buildx-plugin"],
		)
		self.assertEqual(run_helper.call_args.kwargs["timeout_seconds"], 960)
		verify.assert_called_once_with(request_obj, helper_result, execution_context=None)
		self.assertEqual(result.observed_state, "Verified")

	def test_postcondition_flow_runs_separate_read_only_inspection(self):
		from dt_master.node_preparation.test_docker_install_verification import (
			helper_result,
			inventory,
		)

		inspection = SimpleNamespace(
			data={
				"inspection_status": "Successful",
				"snapshot": {"host_inventory": {"docker_install_inventory": inventory()}},
			}
		)
		request_obj = RuntimeAdapterRequest(
			adapter_id="docker-compose-v1",
			runtime_type="docker",
			target_doctype="Infrastructure Node",
			target_name="NODE-0001",
			method="install_engine",
			operation_id="OP-0001",
			correlation_id="CORR-0001",
		)
		with patch.object(self.adapter, "_inspect", return_value=inspection) as inspect:
			result = self.adapter._verify_install_engine_postcondition(
				request_obj, helper_result()
			)
		inspect.assert_called_once_with(request_obj, execution_context=None)
		self.assertEqual(result.outcome, "succeeded")
		self.assertTrue(result.data["postcondition_verified"])

	def test_install_postcondition_verification_is_required_for_success(self):
		from dt_master.node_preparation.test_docker_install_verification import (
			helper_result,
			inventory,
		)

		request_obj = RuntimeAdapterRequest(
			adapter_id="docker-compose-v1",
			runtime_type="docker",
			target_doctype="Infrastructure Node",
			target_name="NODE-0001",
			method="install_engine",
			operation_id="OP-0001",
			correlation_id="CORR-0001",
		)
		result = self.adapter._interpret_install_engine_postcondition(
			request_obj, helper_result(), inventory()
		)
		self.assertEqual(result.outcome, "succeeded")
		self.assertEqual(result.observed_state, "Verified")
		self.assertTrue(result.data["postcondition_verified"])

	def test_install_postcondition_mismatch_requires_recovery(self):
		from dt_master.node_preparation.test_docker_install_verification import (
			helper_result,
			inventory,
		)

		observed = inventory()
		observed["packages"][0]["installed_version"] = "5:28.0.0-1"
		request_obj = RuntimeAdapterRequest(
			adapter_id="docker-compose-v1",
			runtime_type="docker",
			target_doctype="Infrastructure Node",
			target_name="NODE-0001",
			method="install_engine",
			operation_id="OP-0001",
			correlation_id="CORR-0001",
		)
		result = self.adapter._interpret_install_engine_postcondition(
			request_obj, helper_result(), observed
		)
		self.assertEqual(result.outcome, "partial")
		self.assertEqual(result.observed_state, "Recovery Required")
		self.assertFalse(result.data["postcondition_verified"])

	def test_install_postcondition_incomplete_inspection_requires_recovery(self):
		from dt_master.node_preparation.test_docker_install_verification import (
			helper_result,
			inventory,
		)

		observed = inventory()
		observed["docker_engine_health"] = "unavailable"
		request_obj = RuntimeAdapterRequest(
			adapter_id="docker-compose-v1",
			runtime_type="docker",
			target_doctype="Infrastructure Node",
			target_name="NODE-0001",
			method="install_engine",
			operation_id="OP-0001",
			correlation_id="CORR-0001",
		)
		result = self.adapter._interpret_install_engine_postcondition(
			request_obj, helper_result(), observed
		)
		self.assertEqual(result.outcome, "partial")
		self.assertEqual(result.observed_state, "Recovery Required")

	def test_helper_success_is_intermediate_until_postcondition_verification(self):
		request_obj = RuntimeAdapterRequest(
			adapter_id="docker-compose-v1",
			runtime_type="docker",
			target_doctype="Infrastructure Node",
			target_name="NODE-0001",
			method="install_engine",
			operation_id="OP-0001",
			correlation_id="CORR-0001",
		)
		result = self.adapter._interpret_install_engine_result(request_obj, helper_install_result())
		self.assertEqual(result.outcome, "partial")
		self.assertEqual(result.observed_state, "Awaiting Postcondition Verification")
		self.assertFalse(result.data["postcondition_verified"])
		self.assertEqual(result.data["helper_status"], "succeeded")

	def test_helper_pre_mutation_failure_is_a_failed_install(self):
		request_obj = RuntimeAdapterRequest(
			adapter_id="docker-compose-v1",
			runtime_type="docker",
			target_doctype="Infrastructure Node",
			target_name="NODE-0001",
			method="install_engine",
			operation_id="OP-0001",
			correlation_id="CORR-0001",
		)
		result = self.adapter._interpret_install_engine_result(
			request_obj,
			helper_install_result("failed_before_mutation", "host_validation", 1),
		)
		self.assertEqual(result.outcome, "failed")
		self.assertEqual(result.observed_state, "Install Not Applied")

	def test_helper_uncertain_or_partial_mutation_requires_recovery(self):
		request_obj = RuntimeAdapterRequest(
			adapter_id="docker-compose-v1",
			runtime_type="docker",
			target_doctype="Infrastructure Node",
			target_name="NODE-0001",
			method="install_engine",
			operation_id="OP-0001",
			correlation_id="CORR-0001",
		)
		for status in ("failed_after_mutation", "uncertain"):
			with self.subTest(status=status):
				result = self.adapter._interpret_install_engine_result(
					request_obj,
					helper_install_result(status, "package_install", 1),
				)
				self.assertEqual(result.outcome, "partial")
				self.assertEqual(result.observed_state, "Recovery Required")

	def test_helper_result_operation_mismatch_is_rejected(self):
		request_obj = RuntimeAdapterRequest(
			adapter_id="docker-compose-v1",
			runtime_type="docker",
			target_doctype="Infrastructure Node",
			target_name="NODE-0001",
			method="install_engine",
			operation_id="OP-OTHER",
			correlation_id="CORR-0001",
		)
		with self.assertRaises(ValueError):
			self.adapter._interpret_install_engine_result(request_obj, helper_install_result())

	def _snapshot(self, status="Successful"):
		return {
			"schema_version": "1.0.0",
			"observed_at": "2026-11-19T10:00:00Z",
			"duration_ms": 25,
			"inspection_status": status,
			"host": {
				"cpu": {"logical_cores": 8},
				"memory": {"total_bytes": 17179869184, "available_bytes": 8589934592},
			},
			"docker_cli": {"installed": True, "path": "/usr/bin/docker", "client_version": "29.9.3"},
			"engine": {
				"availability": "Available",
				"server_version": "29.9.3",
				"api_version": "1.52",
				"minimum_api_version": "1.24",
			},
			"compose": {"installed": True, "version": "5.7.0", "compatibility": "Compatible"},
			"storage": {
				"driver": "overlayfs",
				"data_root": "/var/lib/docker",
				"filesystem": {
					"mount_point": "/",
					"total_bytes": 200000000000,
					"available_bytes": 100000000000,
					"used_bytes": 100000000000,
				},
			},
			"permissions": {"execution_identity": "deploy", "inspection_access": "Allowed"},
			"host_inventory": {
				"schema_version": "1.0.0",
				"observed_at": "2026-11-21T10:00:00Z",
				"host_os": {
					"id": "ubuntu",
					"id_like": "debian",
					"name": "Ubuntu",
					"version_id": "24.04",
					"version_codename": "noble",
					"pretty_name": "Ubuntu 24.04",
					"architecture": "amd64",
					"kernel_release": "6.8.0",
				},
				"package_manager": "dpkg",
				"docker_packages": [],
				"services": {"manager": "systemd", "docker": "inactive", "containerd": "inactive"},
				"workloads": {"available": True, "running_containers": 0, "total_containers": 0},
				"docker_install_inventory": {
					"schema_version": "1.0.0",
					"observed_at": "2026-11-21T10:00:00Z",
					"host_os": {
						"id": "ubuntu",
						"version_id": "24.04",
						"codename": "noble",
						"architecture": "amd64",
					},
					"package_manager": "apt",
					"packages": [
						{
							"name": name,
							"installed_version": None,
							"installed_origin": None,
							"dpkg_status": "not-installed",
							"candidate_version": "5:29.0.0-1~ubuntu.24.04~noble"
							if name in {"docker-ce", "docker-ce-cli", "containerd.io", "docker-buildx-plugin"}
							else None,
							"candidate_origin": "https://download.docker.com/linux/ubuntu noble/stable amd64 Packages"
							if name in {"docker-ce", "docker-ce-cli", "containerd.io", "docker-buildx-plugin"}
							else None,
							"held": False,
						}
						for name in (
							"docker-ce",
							"docker-ce-cli",
							"containerd.io",
							"docker-buildx-plugin",
							"docker.io",
							"docker-compose",
							"podman-docker",
							"containerd",
							"runc",
						)
					],
					"repository": {
						"source_id": "download.docker.com",
						"keyring_path": "/etc/apt/keyrings/docker.asc",
						"fingerprints": ["0123456789ABCDEF0123456789ABCDEF01234567"],
					},
					"apt_metadata": {"status": "Fresh", "age_seconds": 3600},
					"package_manager_state": {"locked": False, "dpkg_audit_clean": True},
					"storage": {
						"root_available_bytes": 20 * 1024**3,
						"apt_cache_available_bytes": 2 * 1024**3,
					},
					"services": {"docker": "inactive", "containerd": "inactive"},
					"workloads": {"available": True, "running_containers": 0, "total_containers": 0},
					"docker_engine_health": "not_installed",
				},
			},
			"diagnostics": [],
		}

	def _run(self, method="inspect", code=0, snapshot=None):
		snapshot = snapshot or self._snapshot()
		with (
			patch("dt_master.runtime_adapters.docker.frappe.get_doc", return_value=self.node),
			patch(
				"dt_master.runtime_adapters.docker.run_remote_script",
				return_value=(code, __import__("json").dumps(snapshot), ""),
			) as remote,
			patch(
				"dt_master.runtime_adapters.docker.evaluate_node_docker_readiness",
				return_value={"readiness": "Ready", "diagnostics": [], "evaluator_version": "1.1.0"},
			),
			patch.object(self.adapter, "_privilege_readiness", return_value={"status": "Ready"}),
		):
			result = self.adapter.invoke(method, request(method))
		return result, remote

	def test_privilege_check_returns_readiness_and_does_not_run_docker_inventory(self):
		readiness = {
			"schema_version": "1.0.0",
			"observed_at": "2026-11-21T06:00:00Z",
			"status": "Ready",
			"execution_identity": "deploy",
			"helper": {
				"path": "/usr/local/lib/dt-master/docker/docker_lifecycle_helper.py",
				"version": "0.2.0",
				"sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
			},
			"sudoers": {"path": "/etc/sudoers.d/dt-master-docker", "syntax": "Valid"},
			"checks": [{"name": "helper_self_test", "status": "Passed"}],
			"diagnostics": [],
		}
		with (
			patch("dt_master.runtime_adapters.docker.frappe.get_doc", return_value=self.node),
			patch.object(self.adapter, "_privilege_readiness", return_value=readiness) as check,
			patch("dt_master.runtime_adapters.docker.run_remote_script") as remote,
		):
			result = self.adapter.invoke("privilege_check", request("privilege_check"))
		check.assert_called_once_with(self.node)
		remote.assert_not_called()
		self.assertEqual(result.outcome, "succeeded")
		self.assertEqual(result.observed_state, "Ready")
		self.assertEqual(result.data["privilege_readiness"]["checks"][0]["name"], "helper_self_test")

	def test_privilege_check_reports_incomplete_without_claiming_ready(self):
		readiness = {
			"schema_version": "1.0.0",
			"observed_at": None,
			"status": "Incomplete",
			"execution_identity": None,
			"helper": {
				"path": "/usr/local/lib/dt-master/docker/docker_lifecycle_helper.py",
				"version": None,
				"sha256": None,
			},
			"sudoers": {"path": "/etc/sudoers.d/dt-master-docker", "syntax": "Invalid"},
			"checks": [],
			"diagnostics": [
				{
					"code": "DOCKER_PRIVILEGE_PROBE_INCOMPLETE",
					"severity": "Warning",
					"message": "Probe incomplete.",
				}
			],
		}
		with (
			patch("dt_master.runtime_adapters.docker.frappe.get_doc", return_value=self.node),
			patch.object(self.adapter, "_privilege_readiness", return_value=readiness),
		):
			result = self.adapter.invoke("privilege_check", request("privilege_check"))
		self.assertEqual(result.outcome, "partial")
		self.assertEqual(result.observed_state, "Incomplete")

	def test_inspection_uses_fixed_read_only_docker_script_without_sudo(self):
		result, remote = self._run()
		self.assertEqual(result.outcome, "succeeded")
		self.assertEqual(result.observed_state, "Available")
		self.assertEqual(result.data["readiness"], "Ready")
		self.assertEqual(remote.call_args_list[0].args[2:4], ("docker", "inspect_docker.sh"))
		self.assertFalse(remote.call_args_list[0].kwargs["use_sudo"])
		self.assertEqual(remote.call_args_list[0].kwargs["timeout_seconds"], 45)

	def test_inspection_is_node_scoped_and_read_permission_only(self):
		with patch("dt_master.runtime_adapters.docker.frappe.get_doc", return_value=self.node):
			self.adapter._node_for_request(request())
		self.node.access_verified = 0
		with (
			patch("dt_master.runtime_adapters.docker.frappe.get_doc", return_value=self.node),
			patch("dt_master.runtime_adapters.docker.run_remote_script") as remote,
			self.assertRaisesRegex(ValueError, "access"),
		):
			self.adapter.invoke("inspect", request())
		remote.assert_not_called()

	def test_non_node_target_is_rejected(self):
		invalid = RuntimeAdapterRequest(
			adapter_id="docker-compose-v1",
			runtime_type="docker",
			target_doctype="Bench Environment",
			target_name="BENCH-0001",
			method="inspect",
			payload={},
		)
		with patch("dt_master.runtime_adapters.docker.frappe.get_doc") as get_doc:
			with self.assertRaisesRegex(ValueError, "Infrastructure Node"):
				self.adapter.invoke("inspect", invalid)
		get_doc.assert_not_called()

	def test_failed_remote_inspection_returns_sanitized_failure(self):
		result, _ = self._run(code=255)
		self.assertEqual(result.outcome, "failed")
		self.assertEqual(result.data["inspection_status"], "Failed")
		self.assertNotIn("stderr", result.to_dict())

	def test_preflight_ready(self):
		result, _ = self._run(method="preflight")
		self.assertEqual(result.outcome, "succeeded")
		self.assertEqual(result.observed_state, "Ready")
		self.assertEqual(result.data["preflight_status"], "Ready")
		self.assertEqual(result.data["docker_engine_install_plan_status"], "preparation_required")
		self.assertFalse(result.data["docker_engine_install_plan"]["mutation_authorized"])
		self.assertEqual(result.data["docker_engine_install_plan"]["mode"], "plan_only")
		self.assertIn(
			"DOCKER_SIGNING_KEY_UNVERIFIED",
			result.data["docker_engine_install_plan"]["reason_codes"],
		)
		self.assertIn(
			"verify_and_pin_docker_signing_key",
			result.data["docker_engine_install_plan"]["prerequisites"],
		)

	def test_preflight_reports_install_plan_blocked_without_conflating_runtime_readiness(self):
		snapshot = self._snapshot()
		snapshot["host_inventory"]["docker_install_inventory"]["workloads"] = {
			"available": True,
			"running_containers": 2,
			"total_containers": 2,
		}
		with (
			patch("dt_master.runtime_adapters.docker.frappe.get_doc", return_value=self.node),
			patch(
				"dt_master.runtime_adapters.docker.run_remote_script",
				return_value=(0, __import__("json").dumps(snapshot), ""),
			),
			patch(
				"dt_master.runtime_adapters.docker.evaluate_node_docker_readiness",
				return_value={"readiness": "Ready", "diagnostics": [], "evaluator_version": "1.1.0"},
			),
			patch.object(self.adapter, "_privilege_readiness", return_value={"status": "Ready"}),
		):
			result = self.adapter.invoke("preflight", request("preflight"))
		self.assertEqual(result.outcome, "succeeded")
		self.assertEqual(result.data["preflight_status"], "Ready")
		self.assertEqual(result.data["docker_engine_install_plan_status"], "ready")
		self.assertIn(
			"ACTIVE_WORKLOADS_IMPACT_APPROVAL_REQUIRED",
			result.data["docker_engine_install_plan"]["reason_codes"],
		)

	def test_preflight_reports_missing_install_inventory_without_claiming_plan(self):
		snapshot = self._snapshot()
		del snapshot["host_inventory"]["docker_install_inventory"]
		with (
			patch("dt_master.runtime_adapters.docker.frappe.get_doc", return_value=self.node),
			patch(
				"dt_master.runtime_adapters.docker.run_remote_script",
				return_value=(0, __import__("json").dumps(snapshot), ""),
			),
			patch(
				"dt_master.runtime_adapters.docker.evaluate_node_docker_readiness",
				return_value={"readiness": "Ready", "diagnostics": [], "evaluator_version": "1.1.0"},
			),
			patch.object(self.adapter, "_privilege_readiness", return_value={"status": "Ready"}),
		):
			result = self.adapter.invoke("preflight", request("preflight"))
		self.assertEqual(result.data["docker_engine_install_plan_status"], "unavailable")
		self.assertIsNone(result.data["docker_engine_install_plan"])
		self.assertIn(
			"DOCKER_ENGINE_INSTALL_INVENTORY_UNAVAILABLE", {item["code"] for item in result.diagnostics}
		)

	def test_preflight_blocks_unknown_readiness(self):
		with (
			patch("dt_master.runtime_adapters.docker.frappe.get_doc", return_value=self.node),
			patch(
				"dt_master.runtime_adapters.docker.run_remote_script",
				return_value=(0, __import__("json").dumps(self._snapshot()), ""),
			),
			patch(
				"dt_master.runtime_adapters.docker.evaluate_node_docker_readiness",
				return_value={"readiness": "Unknown", "diagnostics": [], "evaluator_version": "1.1.0"},
			),
			patch.object(self.adapter, "_privilege_readiness", return_value={"status": "Ready"}),
		):
			result = self.adapter.invoke("preflight", request("preflight"))
		self.assertEqual(result.outcome, "failed")
		self.assertEqual(result.observed_state, "Blocked")

	def test_preflight_reports_degraded_without_claiming_ready(self):
		with (
			patch("dt_master.runtime_adapters.docker.frappe.get_doc", return_value=self.node),
			patch(
				"dt_master.runtime_adapters.docker.run_remote_script",
				return_value=(0, __import__("json").dumps(self._snapshot()), ""),
			),
			patch(
				"dt_master.runtime_adapters.docker.evaluate_node_docker_readiness",
				return_value={"readiness": "Degraded", "diagnostics": [], "evaluator_version": "1.1.0"},
			),
			patch.object(self.adapter, "_privilege_readiness", return_value={"status": "Ready"}),
		):
			result = self.adapter.invoke("preflight", request("preflight"))
		self.assertEqual(result.outcome, "partial")
		self.assertEqual(result.observed_state, "Degraded")

	def test_bootstrap_privileges_runs_existing_installers_then_readiness_probe(self):
		readiness = {
			"schema_version": "1.0.0",
			"observed_at": "2026-11-21T10:00:00Z",
			"status": "Ready",
			"execution_identity": "deploy",
			"helper": {
				"path": "/usr/local/lib/dt-master/docker/docker_lifecycle_helper.py",
				"version": "0.2.0",
				"sha256": "a" * 64,
			},
			"sudoers": {"path": "/etc/sudoers.d/dt-master-docker", "syntax": "Valid"},
			"checks": [
				{"name": n, "status": "Passed"}
				for n in (
					"execution_identity",
					"helper_integrity",
					"trusted_directories",
					"sudoers_policy",
					"sudoers_syntax",
					"effective_command",
					"helper_self_test",
				)
			],
			"diagnostics": [],
		}
		request_obj = request("bootstrap_privileges")
		with (
			patch("dt_master.runtime_adapters.docker.frappe.get_doc", return_value=self.node),
			patch(
				"dt_master.runtime_adapters.docker.run_remote_script",
				side_effect=[
					(0, "DOCKER_PRIVILEGE_HELPER_INSTALLED", ""),
					(0, "DOCKER_SUDOERS_POLICY_INSTALLED", ""),
					(0, __import__("json").dumps(readiness), ""),
				],
			) as remote,
		):
			result = self.adapter._bootstrap_privileges(request_obj)
		self.assertEqual((result.outcome, result.observed_state), ("succeeded", "Ready"))
		self.assertEqual(
			[call.args[3] for call in remote.call_args_list],
			[
				"install_docker_privilege_helper.sh",
				"install_docker_sudoers.sh",
				"check_docker_privilege_readiness.sh",
			],
		)
		self.assertTrue(all(call.kwargs["use_sudo"] is False for call in remote.call_args_list))

	def test_bootstrap_privileges_fails_closed_when_initial_authority_is_missing(self):
		readiness = {
			"schema_version": "1.0.0",
			"observed_at": None,
			"status": "Not Ready",
			"execution_identity": "deploy",
			"helper": {
				"path": "/usr/local/lib/dt-master/docker/docker_lifecycle_helper.py",
				"version": None,
				"sha256": None,
			},
			"sudoers": {"path": "/etc/sudoers.d/dt-master-docker", "syntax": "Invalid"},
			"checks": [
				{"name": n, "status": "Failed"}
				for n in (
					"helper_integrity",
					"trusted_directories",
					"sudoers_policy",
					"sudoers_syntax",
					"effective_command",
					"helper_self_test",
				)
			],
			"diagnostics": [],
		}
		request_obj = request("bootstrap_privileges")
		with (
			patch("dt_master.runtime_adapters.docker.frappe.get_doc", return_value=self.node),
			patch(
				"dt_master.runtime_adapters.docker.run_remote_script",
				side_effect=[
					(1, "", "sudo unavailable"),
					(0, __import__("json").dumps(readiness), ""),
				],
			) as remote,
		):
			result = self.adapter._bootstrap_privileges(request_obj)
		self.assertEqual((result.outcome, result.observed_state), ("failed", "Not Ready"))
		self.assertEqual(
			[call.args[3] for call in remote.call_args_list],
			[
				"install_docker_privilege_helper.sh",
				"check_docker_privilege_readiness.sh",
			],
		)

	def _verification_snapshot(self):
		return {
			"inspection_status": "Successful",
			"docker_cli": {"installed": True, "path": "/usr/bin/docker", "client_version": "29.9.3"},
			"engine": {"availability": "Available", "server_version": "29.9.3"},
			"compose": {"installed": True, "version": "5.7.0", "compatibility": "Compatible"},
		}

	def _run_verify(self, snapshot):
		request_obj = request("verify")
		inspection = self.adapter.result_for(
			request_obj,
			outcome="succeeded",
			observed_state="Available",
			data={"snapshot": snapshot},
			duration_ms=20,
		)
		with patch.object(self.adapter, "_inspect", return_value=inspection):
			return self.adapter.invoke("verify", request_obj)

	def test_verify_reports_verified_when_all_components_are_operational(self):
		result = self._run_verify(self._verification_snapshot())
		self.assertEqual(result.outcome, "succeeded")
		self.assertEqual(result.observed_state, "Verified")
		self.assertEqual(result.data["verification_status"], "Verified")
		self.assertEqual(result.data["components"]["compose"]["version"], "5.7.0")

	def test_verify_reports_not_verified_when_engine_is_unavailable(self):
		snapshot = self._verification_snapshot()
		snapshot["engine"]["availability"] = "Unavailable"
		result = self._run_verify(snapshot)
		self.assertEqual(result.outcome, "failed")
		self.assertEqual(result.observed_state, "Not Verified")
		self.assertIn("DOCKER_ENGINE_UNAVAILABLE", {item["code"] for item in result.diagnostics})

	def test_verify_reports_incomplete_when_component_state_is_unknown(self):
		snapshot = self._verification_snapshot()
		snapshot["engine"]["availability"] = "Unknown"
		result = self._run_verify(snapshot)
		self.assertEqual(result.outcome, "partial")
		self.assertEqual(result.observed_state, "Incomplete")
		self.assertIn("DOCKER_ENGINE_STATUS_UNKNOWN", {item["code"] for item in result.diagnostics})

	def test_verify_does_not_claim_components_absent_when_inspection_failed(self):
		snapshot = self._verification_snapshot()
		snapshot["inspection_status"] = "Failed"
		snapshot["docker_cli"]["installed"] = False
		snapshot["engine"]["availability"] = "Unknown"
		snapshot["compose"]["installed"] = False
		result = self._run_verify(snapshot)
		self.assertEqual(result.outcome, "partial")
		self.assertEqual(result.observed_state, "Incomplete")
		codes = {item["code"] for item in result.diagnostics}
		self.assertIn("DOCKER_INSPECTION_INCOMPLETE", codes)
		self.assertNotIn("DOCKER_CLI_NOT_INSTALLED", codes)
		self.assertNotIn("DOCKER_COMPOSE_NOT_INSTALLED", codes)

	def test_registry_resolves_builtin_docker_adapter(self):
		from dt_master.runtime_adapters.registry import clear_registry_for_tests, resolve_adapter

		clear_registry_for_tests()
		try:
			adapter = resolve_adapter("docker")
			self.assertEqual(adapter.adapter_id, "docker-compose-v1")
			self.assertEqual(
				adapter.capabilities(),
				(
					"bootstrap_privileges",
					"install_engine",
					"inspect",
					"preflight",
					"updates_check",
					"verify",
					"privilege_check",
				),
			)
		finally:
			clear_registry_for_tests()

	def test_snapshot_and_readiness_diagnostics_are_sanitized_everywhere(self):
		snapshot = self._snapshot()
		snapshot["diagnostics"] = [
			{
				"code": "REMOTE_AUTH_FAILURE",
				"severity": "Error",
				"message": "remote rejected password=remote-password-value",
			}
		]
		evaluation = {
			"readiness": "Degraded",
			"diagnostics": [
				{
					"code": "READINESS_AUTH_WARNING",
					"severity": "Warning",
					"message": "credential token=readiness-token-value was unavailable",
				}
			],
			"evaluator_version": "1.1.0",
		}
		with (
			patch("dt_master.runtime_adapters.docker.frappe.get_doc", return_value=self.node),
			patch(
				"dt_master.runtime_adapters.docker.run_remote_script",
				return_value=(0, __import__("json").dumps(snapshot), ""),
			),
			patch(
				"dt_master.runtime_adapters.docker.evaluate_node_docker_readiness", return_value=evaluation
			),
		):
			result = self.adapter.invoke("inspect", request())

		serialized = __import__("json").dumps(result.to_dict())
		self.assertNotIn("remote-password-value", serialized)
		self.assertNotIn("readiness-token-value", serialized)
		self.assertIn("[REDACTED]", serialized)
		self.assertEqual(len(result.diagnostics), 2)


class TestDockerInspectionScriptContract(unittest.TestCase):
	def test_script_pins_system_socket_and_does_not_enable_compose_mutations(self):
		script = Path(__file__).parents[1] / "scripts" / "bin" / "docker" / "inspect_docker.sh"
		content = script.read_text()
		self.assertIn("unset DOCKER_CONTEXT DOCKER_TLS_VERIFY DOCKER_CERT_PATH", content)
		self.assertIn("export DOCKER_HOST=unix:///var/run/docker.sock", content)
		for forbidden in ("docker compose up", "docker compose down", "docker rm", "docker run"):
			self.assertNotIn(forbidden, content)


if __name__ == "__main__":
	unittest.main()
