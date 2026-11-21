"""Tests for the closed trusted Docker helper protocol contracts."""

import unittest

from dt_master.runtime_adapters.docker_helper_contracts import (
	DockerHelperContractError,
	validate_install_engine_request,
	validate_install_engine_result,
)


def install_request():
	return {
		"schema_version": "1.0.0",
		"operation": "install_engine",
		"operation_id": "OP-0001",
		"target": {"doctype": "Infrastructure Node", "name": "NODE-0001"},
		"plan": {
			"schema_version": "1.1.0",
			"policy_version": "1.1.1",
			"plan_digest": "a" * 64,
			"packages": [
				{"name": name, "version": "5:29.0.0-1~ubuntu.24.04~noble"}
				for name in (
					"docker-ce",
					"docker-ce-cli",
					"containerd.io",
					"docker-buildx-plugin",
				)
			],
		},
		"expected_state_fingerprint": "b" * 64,
		"operator_impact_approval": False,
	}


def install_result():
	return {
		"schema_version": "1.0.0",
		"helper_version": "0.2.0",
		"operation": "install_engine",
		"operation_id": "OP-0001",
		"status": "succeeded",
		"phase": "result_verification",
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
		"package_manager_exit_code": 0,
		"daemon_action": "package_maintainer_scripts",
		"diagnostics": [],
	}


class TestDockerHelperContracts(unittest.TestCase):
	def test_valid_install_request(self):
		self.assertEqual(validate_install_engine_request(install_request())["operation"], "install_engine")

	def test_request_rejects_unknown_fields(self):
		request = install_request()
		request["command"] = "apt install docker"
		with self.assertRaises(DockerHelperContractError):
			validate_install_engine_request(request)

	def test_request_rejects_wrong_operation_and_target(self):
		for field, value in (("operation", "self_test"), ("operation", "upgrade_engine")):
			request = install_request()
			request[field] = value
			with self.assertRaises(DockerHelperContractError):
				validate_install_engine_request(request)
		request = install_request()
		request["target"]["doctype"] = "Bench Environment"
		with self.assertRaises(DockerHelperContractError):
			validate_install_engine_request(request)

	def test_request_rejects_wrong_package_order_or_extra_package(self):
		request = install_request()
		request["plan"]["packages"].reverse()
		with self.assertRaises(DockerHelperContractError):
			validate_install_engine_request(request)
		request = install_request()
		request["plan"]["packages"].append({"name": "docker-compose-plugin", "version": "2.0"})
		with self.assertRaises(DockerHelperContractError):
			validate_install_engine_request(request)

	def test_request_rejects_non_exact_versions_and_non_boolean_approval(self):
		request = install_request()
		request["plan"]["packages"][0]["version"] = "latest"
		with self.assertRaises(DockerHelperContractError):
			validate_install_engine_request(request)
		request = install_request()
		request["operator_impact_approval"] = 1
		with self.assertRaises(DockerHelperContractError):
			validate_install_engine_request(request)

	def test_request_rejects_invalid_digests(self):
		request = install_request()
		request["expected_state_fingerprint"] = "A" * 64
		with self.assertRaises(DockerHelperContractError):
			validate_install_engine_request(request)

	def test_valid_success_result(self):
		self.assertEqual(
			validate_install_engine_result(install_result(), expected_operation_id="OP-0001")["status"],
			"succeeded",
		)

	def test_pre_mutation_result_allows_not_attempted_packages(self):
		result = install_result()
		result["status"] = "failed_before_mutation"
		result["phase"] = "state_validation"
		result["daemon_action"] = "not_requested"
		result["diagnostics"] = ["INSTALL_EXECUTION_DISABLED"]
		for row in result["packages"]:
			row["status"] = "not_attempted"
			row["installed_version"] = None
		self.assertEqual(
			validate_install_engine_result(result, expected_operation_id="OP-0001")["status"],
			"failed_before_mutation",
		)

	def test_result_rejects_unknown_fields_and_correlation_mismatch(self):
		result = install_result()
		result["stdout"] = "secret output"
		with self.assertRaises(DockerHelperContractError):
			validate_install_engine_result(result, expected_operation_id="OP-0001")
		result = install_result()
		with self.assertRaises(DockerHelperContractError):
			validate_install_engine_result(result, expected_operation_id="OP-OTHER")

	def test_result_rejects_incomplete_or_reordered_package_rows(self):
		result = install_result()
		result["packages"].pop()
		with self.assertRaises(DockerHelperContractError):
			validate_install_engine_result(result, expected_operation_id="OP-0001")
		result = install_result()
		result["packages"].reverse()
		with self.assertRaises(DockerHelperContractError):
			validate_install_engine_result(result, expected_operation_id="OP-0001")

	def test_result_rejects_success_with_nonzero_exit_or_diagnostics(self):
		for change in (
			{"package_manager_exit_code": 1},
			{"diagnostics": ["PACKAGE_INSTALL_FAILED"]},
			{"phase": "package_install"},
		):
			result = install_result()
			result.update(change)
			with self.assertRaises(DockerHelperContractError):
				validate_install_engine_result(result, expected_operation_id="OP-0001")

	def test_result_rejects_invalid_status_phase_and_diagnostic(self):
		for field, value in (
			("status", "partial"),
			("phase", "run arbitrary command"),
			("diagnostics", ["raw stderr: password=hunter2"]),
		):
			result = install_result()
			result[field] = value
			with self.assertRaises(DockerHelperContractError):
				validate_install_engine_result(result, expected_operation_id="OP-0001")


if __name__ == "__main__":
	unittest.main()
