"""Tests for Docker runtime adapter inspection and lifecycle operations."""

import hashlib
import json
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
		operation_id="OP-0001",
		correlation_id="CORR-0001",
	)


def helper_install_result(status="succeeded", phase="result_verification", exit_code=0):
	return {
		"schema_version": "1.0.0",
		"helper_version": "0.3.2",
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
		now_patcher = patch(
			"dt_master.runtime_adapters.docker.now_datetime",
			return_value="2026-11-22 10:00:00",
		)
		now_patcher.start()
		self.addCleanup(now_patcher.stop)
		db_patcher = patch("dt_master.runtime_adapters.docker.frappe.db.set_value")
		self.db_set_value = db_patcher.start()
		self.addCleanup(db_patcher.stop)
		self.node = SimpleNamespace(
			name="NODE-0001",
			access_verified=1,
			scripts_base_dir="/opt/dtsaas/scripts",
			ip_address="192.0.2.10",
			ssh_user="deploy",
			ssh_port=22,
			ssh_host_alias=None,
			docker_host_state="Docker Inspection Required",
			docker_host_management_snapshot=json.dumps(
				{
					"schema_version": "1.0.0",
					"host_inspection": {
						"operation_id": "HOST-INSPECT-001",
						"inspection_status": "Successful",
						"os_support_status": "Supported",
					},
					"privilege_bootstrap": {
						"operation_id": "BOOTSTRAP-001",
						"status": "Succeeded",
					},
					"privilege_readiness": {
						"operation_id": "PRIVILEGE-CHECK-001",
						"status": "Ready",
					},
				}
			),
			check_permission=lambda permission: self.assertEqual(permission, "read"),
			get=lambda field: None,
		)

	def test_install_engine_exposes_planner_blocking_reason_codes(self):
		inspection = SimpleNamespace(
			data={
				"inspection_status": "Successful",
				"privilege_readiness": {"status": "Ready"},
				"readiness_evaluation": {"diagnostics": []},
				"snapshot": {"host_inventory": {"docker_install_inventory": {"packages": []}}},
			}
		)
		blocked_plan = {
			"schema_version": "1.1.0",
			"policy": {"version": "1.1.1"},
			"plan_digest": "a" * 64,
			"state_fingerprint": "b" * 64,
			"status": "blocked",
			"reason_codes": ["APT_METADATA_NOT_FRESH", "DOCKER_SIGNING_KEY_MISMATCH"],
			"prerequisites": ["refresh_apt_metadata"],
			"packages": [],
		}
		request_obj = RuntimeAdapterRequest(
			adapter_id="docker-compose-v1",
			runtime_type="docker",
			target_doctype="Infrastructure Node",
			target_name="NDF Tech Labs Contabo",
			method="install_engine",
			payload={"confirm": True},
			operation_id="OP-0001",
			correlation_id="CORR-0001",
		)
		with (
			patch.object(self.adapter, "_node_for_request", return_value=self.node),
			patch.object(self.adapter, "_inspect", return_value=inspection),
			patch(
				"dt_master.node_preparation.docker_install.build_docker_engine_install_plan",
				return_value=blocked_plan,
			),
			patch("dt_master.node_preparation.docker_install.validate_docker_engine_install_plan"),
			patch("dt_master.runtime_adapters.docker.run_trusted_helper") as run_helper,
		):
			result = self.adapter._install_engine(request_obj)
		self.assertEqual(result.outcome, "failed")
		self.assertEqual(result.data["failure"]["detail"]["plan_status"], "blocked")
		self.assertEqual(
			result.data["failure"]["detail"]["reason_codes"],
			["APT_METADATA_NOT_FRESH", "DOCKER_SIGNING_KEY_MISMATCH"],
		)
		self.assertIn("DOCKER_SIGNING_KEY_MISMATCH", result.diagnostics[0]["message"])
		run_helper.assert_not_called()

	def test_install_engine_never_prepares_repository_implicitly(self):
		inspection = SimpleNamespace(
			data={
				"inspection_status": "Successful",
				"privilege_readiness": {"status": "Ready"},
				"readiness_evaluation": {"diagnostics": []},
				"snapshot": {"host_inventory": {"docker_install_inventory": {"packages": []}}},
			}
		)
		plan = {
			"schema_version": "1.1.0",
			"policy": {"version": "1.1.1"},
			"plan_digest": "a" * 64,
			"state_fingerprint": "b" * 64,
			"status": "preparation_required",
			"reason_codes": ["APT_METADATA_NOT_FRESH"],
			"prerequisites": ["refresh_apt_metadata"],
			"packages": [],
		}
		request_obj = RuntimeAdapterRequest(
			adapter_id="docker-compose-v1",
			runtime_type="docker",
			target_doctype="Infrastructure Node",
			target_name="NODE-0001",
			method="install_engine",
			payload={"confirm": True},
			operation_id="OP-0001",
			correlation_id="CORR-0001",
		)
		with (
			patch.object(self.adapter, "_node_for_request", return_value=self.node),
			patch.object(self.adapter, "_inspect", return_value=inspection),
			patch(
				"dt_master.node_preparation.docker_install.build_docker_engine_install_plan",
				return_value=plan,
			),
			patch("dt_master.node_preparation.docker_install.validate_docker_engine_install_plan"),
			patch("dt_master.runtime_adapters.docker.run_trusted_helper") as run_helper,
		):
			result = self.adapter._install_engine(request_obj)
		self.assertEqual(result.outcome, "failed")
		self.assertEqual(result.data["failure"]["detail"]["plan_status"], "preparation_required")
		run_helper.assert_not_called()

	def test_prepare_repository_requires_eligible_lifecycle_state(self):
		self.node.docker_host_state = "Privilege Setup Required"
		request_obj = RuntimeAdapterRequest(
			adapter_id="docker-compose-v1",
			runtime_type="docker",
			target_doctype="Infrastructure Node",
			target_name="NODE-0001",
			method="prepare_repository",
			operation_id="OP-0001",
			correlation_id="CORR-0001",
		)
		with (
			patch.object(self.adapter, "_node_for_request", return_value=self.node),
			patch("dt_master.runtime_adapters.docker.run_trusted_helper") as run_helper,
		):
			result = self.adapter._prepare_repository(request_obj)
		self.assertEqual(result.outcome, "failed")
		self.assertEqual(result.observed_state, "Preparation Not Applied")
		run_helper.assert_not_called()

	def test_prepare_repository_reinspects_and_persists_fresh_install_plan(self):
		self.node.docker_host_state = "Repository Preparation Required"
		request_obj = RuntimeAdapterRequest(
			adapter_id="docker-compose-v1",
			runtime_type="docker",
			target_doctype="Infrastructure Node",
			target_name="NODE-0001",
			method="prepare_repository",
			operation_id="OP-0001",
			correlation_id="CORR-0001",
		)
		inventory = {"packages": []}
		inspection_data = {
			"inspection_status": "Successful",
			"readiness": "Not Ready",
			"readiness_evaluation": {
				"diagnostics": [
					{
						"code": "DOCKER_REQUIREMENT_NOT_MET",
						"severity": "Error",
						"requirement": "docker_engine_required",
						"expected": True,
						"observed": "Not Installed",
					}
				]
			},
			"privilege_readiness": {"status": "Ready"},
			"snapshot": {"host_inventory": {"docker_install_inventory": inventory}},
		}
		inspection = SimpleNamespace(data=inspection_data)
		preparation_plan = {"status": "preparation_required"}
		ready_plan = {
			"status": "ready",
			"plan_digest": "a" * 64,
			"state_fingerprint": "b" * 64,
		}
		helper_result = {
			"schema_version": "1.0.0",
			"helper_version": "0.3.2",
			"operation": "prepare_repository",
			"status": "succeeded",
			"diagnostics": [],
		}
		with (
			patch.object(self.adapter, "_node_for_request", return_value=self.node),
			patch.object(self.adapter, "_inspect", side_effect=[inspection, inspection]) as inspect,
			patch(
				"dt_master.node_preparation.docker_install.build_docker_engine_install_plan",
				side_effect=[preparation_plan, ready_plan],
			),
			patch("dt_master.node_preparation.docker_install.validate_docker_engine_install_plan"),
			patch(
				"dt_master.runtime_adapters.docker.run_trusted_helper",
				return_value=(0, json.dumps(helper_result), ""),
			) as run_helper,
			patch.object(self.adapter, "_persist_install_plan_evidence", return_value=True) as persist,
		):
			result = self.adapter._prepare_repository(request_obj)
		self.assertEqual(result.outcome, "succeeded")
		self.assertEqual(result.observed_state, "Ready for Install")
		self.assertEqual(result.data["fresh_install_plan_status"], "ready")
		self.assertFalse(result.data["engine_packages_installed"])
		self.assertEqual(inspect.call_count, 2)
		self.assertEqual(
			run_helper.call_args.args[2],
			{
				"schema_version": "1.0.0",
				"operation": "prepare_repository",
			},
		)
		self.assertEqual(run_helper.call_args.kwargs["timeout_seconds"], 420)
		persist.assert_called_once_with(self.node, request_obj, inspection_data, ready_plan)

	def test_prepare_repository_blocks_unmet_host_resource_requirements(self):
		self.node.docker_host_state = "Repository Preparation Required"
		request_obj = RuntimeAdapterRequest(
			adapter_id="docker-compose-v1",
			runtime_type="docker",
			target_doctype="Infrastructure Node",
			target_name="NODE-0001",
			method="prepare_repository",
			operation_id="OP-0001",
			correlation_id="CORR-0001",
		)
		inspection = SimpleNamespace(
			data={
				"inspection_status": "Successful",
				"readiness": "Not Ready",
				"readiness_evaluation": {
					"diagnostics": [
						{
							"code": "DOCKER_REQUIREMENT_NOT_MET",
							"severity": "Error",
							"requirement": "min_available_storage_bytes",
							"expected": 10_000,
							"observed": 1_000,
						}
					]
				},
				"privilege_readiness": {"status": "Ready"},
				"snapshot": {"host_inventory": {"docker_install_inventory": {"packages": []}}},
			}
		)
		with (
			patch.object(self.adapter, "_node_for_request", return_value=self.node),
			patch.object(self.adapter, "_inspect", return_value=inspection),
			patch("dt_master.runtime_adapters.docker.run_trusted_helper") as run_helper,
		):
			result = self.adapter._prepare_repository(request_obj)
		self.assertEqual(result.outcome, "failed")
		self.assertEqual(result.observed_state, "Preparation Not Applied")
		self.assertEqual(result.data["failure"]["phase"], "preparation_preflight")
		self.assertFalse(result.data["helper_dispatched"])
		run_helper.assert_not_called()

	def test_repository_preparation_planner_gate_accepts_only_known_prerequisite_blockers(self):
		preparation_required = {
			"status": "preparation_required",
			"reason_codes": ["APT_METADATA_NOT_FRESH"],
			"prerequisites": ["refresh_apt_metadata"],
		}
		blocked_but_preparable = {
			"status": "blocked",
			"reason_codes": ["DOCKER_KEYRING_PATH_UNTRUSTED"],
			"prerequisites": ["verify_and_pin_docker_signing_key"],
		}
		blocked_for_unrelated_reason = {
			"status": "blocked",
			"reason_codes": ["DOCKER_SIGNING_KEY_MISMATCH"],
			"prerequisites": ["verify_and_pin_docker_signing_key"],
		}
		self.assertTrue(self.adapter._requires_repository_preparation(preparation_required))
		self.assertTrue(self.adapter._requires_repository_preparation(blocked_but_preparable))
		self.assertFalse(self.adapter._requires_repository_preparation(blocked_for_unrelated_reason))

	def test_repository_preparation_result_requires_exact_helper_contract(self):
		valid = {
			"schema_version": "1.0.0",
			"helper_version": "0.3.2",
			"operation": "prepare_repository",
			"status": "succeeded",
			"diagnostics": [],
		}
		self.assertEqual(self.adapter._validate_repository_preparation_result(valid), valid)
		for key, value in (
			("helper_version", "0.4.0"),
			("operation", "install_engine"),
			("status", "failed"),
		):
			invalid = dict(valid)
			invalid[key] = value
			with self.subTest(key=key), self.assertRaises(ValueError):
				self.adapter._validate_repository_preparation_result(invalid)
		invalid = dict(valid, unexpected=True)
		with self.assertRaises(ValueError):
			self.adapter._validate_repository_preparation_result(invalid)

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
			target_name="NDF Tech Labs Contabo",
			method="install_engine",
			payload={"confirm": True},
			operation_id="OP-0001",
			correlation_id="CORR-0001",
		)
		verified = self.adapter.result_for(request_obj, outcome="succeeded", observed_state="Verified")
		helper_result = helper_install_result()
		with (
			patch.object(self.adapter, "_node_for_request", return_value=self.node),
			patch.object(self.adapter, "_inspect", return_value=inspection),
			patch(
				"dt_master.node_preparation.docker_install.build_docker_engine_install_plan",
				return_value=plan,
			),
			patch("dt_master.node_preparation.docker_install.validate_docker_engine_install_plan"),
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
		self.assertEqual(helper_request["target"]["name"], "NDF Tech Labs Contabo")
		self.assertEqual(helper_request["plan"]["policy_version"], "1.1.1")
		self.assertEqual(helper_request["plan"]["plan_digest"], "a" * 64)
		self.assertEqual(helper_request["expected_state_fingerprint"], "b" * 64)
		self.assertFalse(helper_request["operator_impact_approval"])
		self.assertEqual(
			[package["name"] for package in helper_request["plan"]["packages"]],
			["docker-ce", "docker-ce-cli", "containerd.io", "docker-buildx-plugin"],
		)
		self.assertEqual(run_helper.call_args.kwargs["timeout_seconds"], 960)
		verify.assert_called_once_with(request_obj, helper_result, execution_context=None)
		self.assertEqual(result.observed_state, "Verified")

	def test_install_engine_returns_already_installed_without_plan_or_package_action(self):
		names = ("docker-ce", "docker-ce-cli", "containerd.io", "docker-buildx-plugin")
		inventory = {
			"docker_engine_health": "healthy",
			"packages": [
				{
					"name": name,
					"dpkg_status": "installed",
					"installed_origin": "https://download.docker.com/linux/ubuntu",
				}
				for name in names
			],
		}
		inspection = SimpleNamespace(
			data={
				"inspection_status": "Successful",
				"privilege_readiness": {"status": "Ready"},
				"readiness_evaluation": {"diagnostics": []},
				"snapshot": {"host_inventory": {"docker_install_inventory": inventory}},
			}
		)
		request_obj = RuntimeAdapterRequest(
			adapter_id="docker-compose-v1",
			runtime_type="docker",
			target_doctype="Infrastructure Node",
			target_name="NODE-0001",
			method="install_engine",
			payload={"confirm": True},
			operation_id="OP-0001",
			correlation_id="CORR-0001",
		)
		with (
			patch.object(self.adapter, "_node_for_request", return_value=self.node),
			patch.object(self.adapter, "_inspect", return_value=inspection),
			patch("dt_master.runtime_adapters.docker.run_trusted_helper") as run_helper,
		):
			result = self.adapter._install_engine(request_obj)
		self.assertEqual(result.outcome, "succeeded")
		self.assertEqual(result.observed_state, "Already Installed")
		self.assertTrue(result.data["postcondition_verified"])
		run_helper.assert_not_called()

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
			result = self.adapter._verify_install_engine_postcondition(request_obj, helper_result())
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
		result = self.adapter._interpret_install_engine_postcondition(request_obj, helper_result(), observed)
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
		result = self.adapter._interpret_install_engine_postcondition(request_obj, helper_result(), observed)
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

	def _run(self, method="inspect", code=0, snapshot=None, readiness="Ready", readiness_diagnostics=None):
		snapshot = snapshot or self._snapshot()
		self.node.docker_host_state = "Docker Inspection Required"
		self.node.docker_host_management_snapshot = json.dumps(
			{
				"host_inspection": {
					"operation_id": "HOST-INSPECT-001",
					"inspection_status": "Successful",
					"os_support_status": "Supported",
				},
				"privilege_bootstrap": {
					"operation_id": "BOOTSTRAP-001",
					"status": "Succeeded",
				},
				"privilege_readiness": {
					"operation_id": "PRIVILEGE-CHECK-001",
					"status": "Ready",
				},
			}
		)
		with (
			patch("dt_master.runtime_adapters.docker.frappe.get_doc", return_value=self.node),
			patch(
				"dt_master.runtime_adapters.docker.run_remote_script",
				return_value=(code, json.dumps(snapshot), ""),
			) as remote,
			patch(
				"dt_master.runtime_adapters.docker.evaluate_node_docker_readiness",
				return_value={
					"readiness": readiness,
					"diagnostics": readiness_diagnostics or [],
					"evaluator_version": "1.1.0",
				},
			),
			patch.object(self.adapter, "_privilege_readiness", return_value={"status": "Ready"}),
		):
			result = self.adapter.invoke(method, request(method))
		return result, remote

	def test_release_assessment_records_timestamp_when_no_release_is_assigned(self):
		self.node.docker_helper_release = None
		with patch.object(self.adapter, "_node_for_request", return_value=self.node):
			result = self.adapter._release_assess(request("release_assess"))

		self.assertEqual(result.observed_state, "Unknown Release")
		self.assertEqual(result.data["observed_at"], "2026-11-22 10:00:00")
		self.assertEqual(
			self.db_set_value.call_args.args[2]["docker_helper_release_assessment_status"],
			"Unknown Release",
		)

	def _release_manifest_fixture(self):
		components = [
			{
				"id": component_id,
				"path": filename,
				"version": version,
				"sha256": "a" * 64,
				"required": True,
				"responsibility": "Test component",
			}
			for component_id, filename, version in (
				("lifecycle_helper", "docker_lifecycle_helper.py", "0.3.2"),
				("install_policy", "docker_install_policy.py", "1.1.1"),
				("host_inventory", "docker_install_inventory.py", "1.0.0"),
			)
		]
		manifest = {
			"schema_version": "1.0.0",
			"release": {
				"id": "dt-docker-helper-0.3.2",
				"version": "0.3.2",
				"channel": "stable",
				"status": "supported",
			},
			"compatibility": {},
			"components": components,
			"lifecycle": {},
		}
		manifest_json = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
		release = SimpleNamespace(
			release_id="dt-docker-helper-0.3.2",
			version="0.3.2",
			status="Published",
			components=[],
		)
		return manifest, manifest_json, release

	@staticmethod
	def _release_inspection_state(status, manifest_matches, installer_ready, *, installed=False):
		rows = []
		for component_id, version in (
			("lifecycle_helper", "0.3.2"),
			("install_policy", "1.1.1"),
			("host_inventory", "1.0.0"),
		):
			integrity = (
				"verified" if status == "matching" else ("missing" if status == "missing" else "mismatch")
			)
			rows.append(
				{
					"id": component_id,
					"version": version,
					"expected_sha256": "a" * 64,
					"observed_sha256": "a" * 64 if integrity == "verified" else None,
					"owner": "root" if installed and integrity == "verified" else "deploy",
					"group": "root" if installed and integrity == "verified" else "deploy",
					"mode": "755" if installed and integrity == "verified" else "644",
					"integrity": integrity,
				}
			)
		return {
			"status": status,
			"manifest_matches": manifest_matches,
			"installer_ready": installer_ready,
			"components": rows,
		}

	def _release_inspection_payload(self, digest, installed, deployed):
		return {
			"schema_version": "1.0.0",
			"observed_at": "2026-11-22T19:00:00Z",
			"expected_manifest_sha256": digest,
			"installed": installed,
			"deployed": deployed,
		}

	def test_release_assessment_exposes_sanitized_local_source_validation_reason(self):
		from dt_master.docker_helper_releases import DockerHelperReleaseError

		release = SimpleNamespace(
			release_id="dt-docker-helper-0.3.2",
			version="0.3.2",
			status="Published",
			components=[],
		)
		with (
			patch.object(self.adapter, "_node_for_request", return_value=self.node),
			patch("dt_master.runtime_adapters.docker.frappe.get_doc", return_value=release),
			patch(
				"dt_master.docker_helper_releases.serialize_release_manifest",
				side_effect=DockerHelperReleaseError("Component source file is missing: lifecycle_helper"),
			),
		):
			self.node.docker_helper_release = "dt-docker-helper-0.3.2"
			result = self.adapter._release_assess(request("release_assess"))

		self.assertEqual(result.observed_state, "Failed")
		self.assertIn("Component source file is missing: lifecycle_helper", result.diagnostics[0]["message"])

	def test_release_assessment_warns_and_stops_for_retired_assignment(self):
		release = SimpleNamespace(
			release_id="dt-docker-helper-0.3.2",
			version="0.3.2",
			status="Retired",
		)
		with (
			patch.object(self.adapter, "_node_for_request", return_value=self.node),
			patch("dt_master.runtime_adapters.docker.frappe.get_doc", return_value=release),
			patch("dt_master.runtime_adapters.docker.run_remote_script") as remote,
		):
			self.node.docker_helper_release = "dt-docker-helper-0.3.2"
			result = self.adapter._release_assess(request("release_assess"))
		self.assertEqual(result.observed_state, "Release Retired")
		self.assertEqual(result.outcome, "partial")
		self.assertEqual(result.diagnostics[0]["code"], "DOCKER_HELPER_RELEASE_RETIRED")
		self.assertIn("assign the active Published release", result.diagnostics[0]["message"])
		remote.assert_not_called()
		self.assertEqual(
			self.db_set_value.call_args.args[2]["docker_helper_release_assessment_status"],
			"Release Retired",
		)

	def test_release_assessment_marks_matching_installed_release_current(self):
		manifest, manifest_json, release = self._release_manifest_fixture()
		digest = hashlib.sha256(manifest_json.encode()).hexdigest()
		remote = self._release_inspection_payload(
			digest,
			self._release_inspection_state("matching", True, True, installed=True),
			self._release_inspection_state("missing", False, False),
		)
		with (
			patch.object(self.adapter, "_node_for_request", return_value=self.node),
			patch("dt_master.runtime_adapters.docker.frappe.get_doc", return_value=release),
			patch(
				"dt_master.docker_helper_releases.serialize_release_manifest", return_value=manifest_json
			) as serialize,
			patch(
				"dt_master.runtime_adapters.docker.run_remote_script",
				return_value=(0, json.dumps(remote), ""),
			),
			patch.object(self.adapter, "_bootstrap_privileges") as install,
		):
			self.node.docker_helper_release = "dt-docker-helper-0.3.2"
			result = self.adapter._release_assess(request("release_assess"))
		self.assertEqual(result.observed_state, "Current")
		self.assertEqual(result.outcome, "succeeded")
		serialize.assert_called_once()
		self.assertEqual(serialize.call_args.kwargs["source_root"], Path(__file__).resolve().parents[1])
		install.assert_not_called()
		self.assertEqual(result.data["expected_manifest"], manifest)
		self.assertEqual(
			self.db_set_value.call_args.args[2]["docker_helper_release_assessment_status"], "Current"
		)

	def test_release_assessment_requests_separate_bootstrap_when_deployed_scripts_match(self):
		manifest, manifest_json, release = self._release_manifest_fixture()
		digest = hashlib.sha256(manifest_json.encode()).hexdigest()
		remote = self._release_inspection_payload(
			digest,
			self._release_inspection_state("mismatch", False, True, installed=True),
			self._release_inspection_state("matching", True, True),
		)
		with (
			patch.object(self.adapter, "_node_for_request", return_value=self.node),
			patch("dt_master.runtime_adapters.docker.frappe.get_doc", return_value=release),
			patch("dt_master.docker_helper_releases.serialize_release_manifest", return_value=manifest_json),
			patch(
				"dt_master.runtime_adapters.docker.run_remote_script",
				return_value=(0, json.dumps(remote), ""),
			) as remote_call,
			patch.object(self.adapter, "_bootstrap_privileges") as install,
		):
			self.node.docker_helper_release = "dt-docker-helper-0.3.2"
			result = self.adapter._release_assess(request("release_assess"))
		self.assertEqual(result.observed_state, "Bootstrap Required")
		self.assertEqual(result.outcome, "succeeded")
		install.assert_not_called()
		remote_call.assert_called_once()
		self.assertEqual(result.data["assessment_status"], "Bootstrap Required")

	def test_release_assessment_requests_manual_deployment_when_scripts_do_not_match(self):
		manifest, manifest_json, release = self._release_manifest_fixture()
		digest = hashlib.sha256(manifest_json.encode()).hexdigest()
		remote = self._release_inspection_payload(
			digest,
			self._release_inspection_state("mismatch", False, True, installed=True),
			self._release_inspection_state("mismatch", False, False),
		)
		with (
			patch.object(self.adapter, "_node_for_request", return_value=self.node),
			patch("dt_master.runtime_adapters.docker.frappe.get_doc", return_value=release),
			patch("dt_master.docker_helper_releases.serialize_release_manifest", return_value=manifest_json),
			patch(
				"dt_master.runtime_adapters.docker.run_remote_script",
				return_value=(0, json.dumps(remote), ""),
			),
			patch.object(self.adapter, "_bootstrap_privileges") as install,
		):
			self.node.docker_helper_release = "dt-docker-helper-0.3.2"
			result = self.adapter._release_assess(request("release_assess"))
		self.assertEqual(result.observed_state, "Scripts Deployment Required")
		self.assertEqual(result.outcome, "succeeded")
		install.assert_not_called()

	def test_release_inspection_rejects_incomplete_component_inventory(self):
		payload = {
			"schema_version": "1.0.0",
			"observed_at": "2026-11-22T19:00:00Z",
			"expected_manifest_sha256": "a" * 64,
			"installed": {
				"status": "missing",
				"manifest_matches": False,
				"installer_ready": False,
				"components": [],
			},
			"deployed": {
				"status": "missing",
				"manifest_matches": False,
				"installer_ready": False,
				"components": [],
			},
		}
		with self.assertRaises(ValueError):
			self.adapter._validate_release_inspection(payload)

	def test_privilege_check_returns_readiness_and_does_not_run_docker_inventory(self):
		self.node.docker_host_state = "Privilege Setup Required"
		readiness = {
			"schema_version": "1.0.0",
			"observed_at": "2026-11-21T06:00:00Z",
			"status": "Ready",
			"execution_identity": "deploy",
			"helper": {
				"path": "/usr/local/lib/dt-master/docker/docker_lifecycle_helper.py",
				"version": "0.3.2",
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
		self.assertTrue(
			any(
				entry.args[2].get("docker_host_state") == "Docker Inspection Required"
				for entry in self.db_set_value.call_args_list
			)
		)

	def test_privilege_check_reports_incomplete_without_claiming_ready(self):
		self.node.docker_host_state = "Privilege Setup Required"
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

	def test_docker_inspection_requires_successful_privilege_verification(self):
		self.node.docker_host_state = "Privilege Setup Required"
		self.node.docker_host_management_snapshot = json.dumps(
			{
				"host_inspection": {
					"operation_id": "HOST-INSPECT-001",
					"inspection_status": "Successful",
					"os_support_status": "Supported",
				},
				"privilege_bootstrap": {
					"operation_id": "BOOTSTRAP-001",
					"status": "Succeeded",
				},
			}
		)
		with (
			patch("dt_master.runtime_adapters.docker.frappe.get_doc", return_value=self.node),
			patch("dt_master.runtime_adapters.docker.run_remote_script") as remote,
			self.assertRaisesRegex(ValueError, "privilege verification"),
		):
			self.adapter.invoke("inspect", request("inspect"))
		remote.assert_not_called()

	def test_successful_standalone_inspection_persists_correlated_evidence(self):
		result, _remote = self._run()
		self.assertEqual(result.outcome, "succeeded")
		updates = [call.args[2] for call in self.db_set_value.call_args_list]
		persisted = next(item for item in updates if "docker_inspection_snapshot" in item)
		self.assertEqual(persisted["docker_inspection_status"], "Successful")
		self.assertEqual(persisted["docker_readiness"], "Ready")
		self.assertIn("docker_readiness_evaluation", persisted)
		self.assertIn("docker_inspected_on", persisted)
		management = json.loads(persisted["docker_host_management_snapshot"])
		self.assertEqual(management["docker_inspection"]["operation_id"], "OP-0001")
		self.assertEqual(management["docker_inspection"]["correlation_id"], "CORR-0001")
		self.assertIn("host_inspection", management)
		self.assertIn("privilege_bootstrap", management)
		self.assertIn("privilege_readiness", management)

	def test_failed_and_partial_inspections_do_not_persist_or_advance_state(self):
		for code, expected in ((255, "Failed"), (124, "Timed Out"), (0, "Partial")):
			with self.subTest(expected=expected):
				self.db_set_value.reset_mock()
				snapshot = self._snapshot()
				if expected == "Partial":
					snapshot["inspection_status"] = "Partial"
				result, _remote = self._run(code=code, snapshot=snapshot)
				self.assertNotEqual(result.data["inspection_status"], "Successful")
				self.db_set_value.assert_not_called()

	def test_preflight_persists_plan_bound_to_its_inspection(self):
		result, _remote = self._run(method="preflight")
		self.assertEqual(result.data["docker_engine_install_plan_status"], "preparation_required")
		updates = [call.args[2] for call in self.db_set_value.call_args_list]
		persisted = next(item for item in updates if "docker_inspection_snapshot" in item)
		management = json.loads(persisted["docker_host_management_snapshot"])
		self.assertEqual(management["docker_install_plan"]["inspection_operation_id"], "OP-0001")
		self.assertEqual(management["docker_install_plan"]["inspection_correlation_id"], "CORR-0001")
		self.assertEqual(
			management["docker_install_plan"]["plan_digest"],
			result.data["docker_engine_install_plan"]["plan_digest"],
		)
		plan_summary = management["docker_install_plan"]
		self.assertEqual(
			plan_summary["reason_codes"],
			result.data["docker_engine_install_plan"]["reason_codes"],
		)
		self.assertEqual(
			plan_summary["prerequisites"],
			result.data["docker_engine_install_plan"]["prerequisites"],
		)
		self.assertEqual(
			plan_summary["packages"],
			result.data["docker_engine_install_plan"]["packages"],
		)
		self.assertIn("repository", plan_summary)
		self.assertIn("apt_metadata", plan_summary)
		self.assertIn("workload_impact", plan_summary)
		self.assertNotIn("mutation_authorized", plan_summary)
		self.assertNotIn("target", plan_summary)
		self.assertEqual(persisted["docker_host_state"], "Repository Preparation Required")

	def test_preflight_persists_valid_plan_when_engine_readiness_is_not_ready(self):
		result, _remote = self._run(method="preflight", readiness="Not Ready")
		self.assertEqual(result.outcome, "succeeded")
		self.assertEqual(result.observed_state, "Repository Preparation Required")
		self.assertEqual(result.data["readiness"], "Not Ready")
		updates = [call.args[2] for call in self.db_set_value.call_args_list]
		persisted = next(item for item in updates if "docker_inspection_snapshot" in item)
		management = json.loads(persisted["docker_host_management_snapshot"])
		self.assertEqual(management["docker_install_plan"]["status"], "preparation_required")
		self.assertEqual(persisted["docker_host_state"], "Repository Preparation Required")

	def test_unavailable_inventory_does_not_persist_plan_evidence_or_advance_state(self):
		snapshot = self._snapshot()
		del snapshot["host_inventory"]["docker_install_inventory"]
		self._run(method="preflight", snapshot=snapshot)
		updates = [call.args[2] for call in self.db_set_value.call_args_list]
		persisted = next(item for item in updates if "docker_inspection_snapshot" in item)
		management = json.loads(persisted["docker_host_management_snapshot"])
		self.assertNotIn("docker_install_plan", management)
		self.assertNotIn("docker_host_state", persisted)

	def test_invalid_install_inventory_does_not_persist_plan_evidence(self):
		snapshot = self._snapshot()
		del snapshot["host_inventory"]["docker_install_inventory"]["packages"]
		self._run(method="preflight", snapshot=snapshot)
		updates = [call.args[2] for call in self.db_set_value.call_args_list]
		persisted = next(item for item in updates if "docker_inspection_snapshot" in item)
		management = json.loads(persisted["docker_host_management_snapshot"])
		self.assertNotIn("docker_install_plan", management)
		self.assertNotIn("docker_host_state", persisted)

	def test_internal_install_inspection_does_not_persist_lifecycle_projection(self):
		inspection = self._snapshot()
		self.db_set_value.reset_mock()
		with (
			patch("dt_master.runtime_adapters.docker.frappe.get_doc", return_value=self.node),
			patch(
				"dt_master.runtime_adapters.docker.run_remote_script",
				return_value=(0, json.dumps(inspection), ""),
			),
			patch(
				"dt_master.runtime_adapters.docker.evaluate_node_docker_readiness",
				return_value={"readiness": "Ready", "diagnostics": [], "evaluator_version": "1.1.0"},
			),
			patch.object(self.adapter, "_privilege_readiness", return_value={"status": "Ready"}),
		):
			self.adapter._inspect(request("install_engine"))
		self.db_set_value.assert_not_called()

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
		self.assertEqual(result.observed_state, "Repository Preparation Required")
		self.assertEqual(result.data["preflight_status"], "Repository Preparation Required")
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
		inventory = snapshot["host_inventory"]["docker_install_inventory"]
		# Isolate the workload-impact approval gate: signing-key evidence must
		# already satisfy policy so it does not independently require preparation.
		inventory["repository"]["fingerprints"] = ["9DC858229FC7DD38854AE2D88D81803C0EBFCD88"]
		inventory["repository"]["primary_keys"] = [
			{
				"fingerprint": "9DC858229FC7DD38854AE2D88D81803C0EBFCD88",
				"identity": "Docker Release (CE deb) <docker@docker.com>",
				"subkey_fingerprints": [],
			}
		]
		inventory["workloads"] = {
			"available": True,
			"running_containers": 2,
			"total_containers": 2,
		}
		with (
			patch("dt_master.runtime_adapters.docker.frappe.get_doc", return_value=self.node),
			patch(
				"dt_master.runtime_adapters.docker.run_remote_script",
				return_value=(0, json.dumps(snapshot), ""),
			),
			patch(
				"dt_master.runtime_adapters.docker.evaluate_node_docker_readiness",
				return_value={"readiness": "Ready", "diagnostics": [], "evaluator_version": "1.1.0"},
			),
			patch.object(self.adapter, "_privilege_readiness", return_value={"status": "Ready"}),
		):
			result = self.adapter.invoke("preflight", request("preflight"))
		self.assertEqual(result.outcome, "succeeded")
		self.assertEqual(result.data["preflight_status"], "Ready for Install")
		self.assertEqual(result.observed_state, "Ready for Install")
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
				return_value=(0, json.dumps(snapshot), ""),
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
				return_value=(0, json.dumps(self._snapshot()), ""),
			),
			patch(
				"dt_master.runtime_adapters.docker.evaluate_node_docker_readiness",
				return_value={
					"readiness": "Unknown",
					"diagnostics": [
						{
							"code": "DOCKER_REQUIREMENT_EVIDENCE_MISSING",
							"severity": "Error",
							"requirement": "min_available_storage_bytes",
							"message": "Required host storage evidence is unavailable.",
						}
					],
					"evaluator_version": "1.1.0",
				},
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
				return_value=(0, json.dumps(self._snapshot()), ""),
			),
			patch(
				"dt_master.runtime_adapters.docker.evaluate_node_docker_readiness",
				return_value={"readiness": "Degraded", "diagnostics": [], "evaluator_version": "1.1.0"},
			),
			patch.object(self.adapter, "_privilege_readiness", return_value={"status": "Ready"}),
		):
			result = self.adapter.invoke("preflight", request("preflight"))
		self.assertEqual(result.outcome, "succeeded")
		self.assertEqual(result.observed_state, "Repository Preparation Required")

	def test_bootstrap_privileges_requires_successful_supported_host_inspection(self):
		self.node.docker_host_state = "Privilege Setup Required"
		self.node.docker_host_management_snapshot = "{}"
		with (
			patch("dt_master.runtime_adapters.docker.frappe.get_doc", return_value=self.node),
			patch("dt_master.runtime_adapters.docker.run_remote_script") as remote,
			self.assertRaisesRegex(ValueError, "Host inspection"),
		):
			self.adapter._bootstrap_privileges(request("bootstrap_privileges"))
		remote.assert_not_called()

	def test_privilege_check_requires_completed_helper_bootstrap(self):
		self.node.docker_host_state = "Privilege Setup Required"
		self.node.docker_host_management_snapshot = json.dumps(
			{
				"host_inspection": {
					"operation_id": "HOST-INSPECT-001",
					"inspection_status": "Successful",
					"os_support_status": "Supported",
				}
			}
		)
		with (
			patch("dt_master.runtime_adapters.docker.frappe.get_doc", return_value=self.node),
			patch.object(self.adapter, "_privilege_readiness") as check,
			self.assertRaisesRegex(ValueError, "must be installed"),
		):
			self.adapter.invoke("privilege_check", request("privilege_check"))
		check.assert_not_called()

	def test_bootstrap_privileges_runs_existing_installers_then_readiness_probe(self):
		self.node.docker_host_state = "Privilege Setup Required"
		readiness = {
			"schema_version": "1.0.0",
			"observed_at": "2026-11-21T10:00:00Z",
			"status": "Ready",
			"execution_identity": "deploy",
			"helper": {
				"path": "/usr/local/lib/dt-master/docker/docker_lifecycle_helper.py",
				"version": "0.3.2",
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
					(0, json.dumps(readiness), ""),
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
		self.node.docker_host_state = "Privilege Setup Required"
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
					(0, json.dumps(readiness), ""),
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

	def test_bootstrap_privileges_exposes_installer_exit_code_and_stderr(self):
		self.node.docker_host_state = "Privilege Setup Required"
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
			"sudoers": {"path": "/etc/sudoers.d/dt-master-docker", "syntax": "Valid"},
			"checks": [{"name": "helper_integrity", "status": "Failed"}],
			"diagnostics": [
				{
					"code": "DOCKER_PRIVILEGE_HELPER_INTEGRITY",
					"severity": "Error",
					"message": "Trusted helper digest mismatch.",
				}
			],
		}
		with (
			patch("dt_master.runtime_adapters.docker.frappe.get_doc", return_value=self.node),
			patch(
				"dt_master.runtime_adapters.docker.run_remote_script",
				side_effect=[
					(1, "installer started", "sha256 verification failed"),
					(0, json.dumps(readiness), ""),
				],
			) as remote,
		):
			result = self.adapter._bootstrap_privileges(request("bootstrap_privileges"))
		self.assertEqual(result.outcome, "failed")
		self.assertEqual(result.data["helper_install"]["exit_code"], 1)
		self.assertIn("sha256 verification failed", result.data["helper_install"]["stderr"])
		self.assertEqual(result.data["failure"]["phase"], "installer_execution")
		self.assertTrue(any("sha256 verification failed" in item["message"] for item in result.diagnostics))
		self.assertEqual(remote.call_count, 2)

	def test_bootstrap_privileges_exposes_readiness_probe_stderr(self):
		self.node.docker_host_state = "Privilege Setup Required"
		with (
			patch("dt_master.runtime_adapters.docker.frappe.get_doc", return_value=self.node),
			patch(
				"dt_master.runtime_adapters.docker.run_remote_script",
				side_effect=[
					(0, "helper installed", ""),
					(0, "sudoers installed", ""),
					(2, "", "readiness script: permission denied"),
				],
			),
		):
			result = self.adapter._bootstrap_privileges(request("bootstrap_privileges"))
		self.assertEqual(result.outcome, "partial")
		self.assertEqual(result.data["failure"]["phase"], "readiness_verification")
		self.assertIn("permission denied", result.data["failure"]["details"][0]["stderr"])

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
					"prepare_repository",
					"install_engine",
					"inspect",
					"preflight",
					"updates_check",
					"verify",
					"privilege_check",
					"release_assess",
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
				return_value=(0, json.dumps(snapshot), ""),
			),
			patch(
				"dt_master.runtime_adapters.docker.evaluate_node_docker_readiness", return_value=evaluation
			),
		):
			result = self.adapter.invoke("inspect", request())

		serialized = json.dumps(result.to_dict())
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
