"""Tests for the trusted Docker helper transport operation gate."""

import unittest
from unittest.mock import patch

from dt_master.api.script_contract import (
	TrustedHelperExecutionError,
	parse_trusted_install_engine_result,
	run_trusted_helper,
)
from dt_master.runtime_adapters.docker_helper_contracts import DockerHelperContractError


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


class TestTrustedDockerHelperTransport(unittest.TestCase):
	def test_valid_install_request_is_dispatched_through_fixed_transport(self):
		with (
			patch("dt_master.api.script_contract.build_ssh_argv", return_value=["ssh"]) as build_ssh,
			patch("dt_master.api.script_contract.subprocess.Popen") as popen,
		):
			process = popen.return_value
			process.poll.return_value = 0
			process.returncode = 0
			code, stdout, stderr = run_trusted_helper(
				None, {}, install_request(), timeout_seconds=2
			)

		self.assertEqual((code, stdout, stderr), (0, "", ""))
		build_ssh.assert_called_once()
		popen.assert_called_once()
		process.stdin.write.assert_called_once()
		process.stdin.close.assert_called_once()

	def test_invalid_install_request_is_rejected_before_transport(self):
		request = install_request()
		request["plan"]["packages"][0]["version"] = "latest"
		with (
			patch("dt_master.api.script_contract.build_ssh_argv") as build_ssh,
			patch("dt_master.api.script_contract.subprocess.Popen") as popen,
		):
			with self.assertRaises(DockerHelperContractError):
				run_trusted_helper(None, {}, request)

		build_ssh.assert_not_called()
		popen.assert_not_called()

	def test_install_result_is_parsed_and_correlated(self):
		import json

		result = install_result()
		parsed = parse_trusted_install_engine_result(
			0, json.dumps(result), "", expected_operation_id="OP-0001"
		)
		self.assertEqual(parsed, result)

	def test_install_result_rejects_malformed_duplicate_and_non_finite_json(self):
		invalid = (
			"{",
			'{"schema_version":"1.0.0","schema_version":"1.0.0"}',
			'{"value":NaN}',
		)
		for stdout in invalid:
			with self.subTest(stdout=stdout):
				with self.assertRaises(TrustedHelperExecutionError) as raised:
					parse_trusted_install_engine_result(0, stdout, "", expected_operation_id="OP-0001")
				self.assertEqual(raised.exception.code, "invalid_helper_result")
				self.assertTrue(raised.exception.may_have_executed)

	def test_install_result_rejects_correlation_mismatch_and_stderr(self):
		import json

		with self.assertRaises(TrustedHelperExecutionError):
			parse_trusted_install_engine_result(
				0, json.dumps(install_result()), "", expected_operation_id="OP-OTHER"
			)
		with self.assertRaises(TrustedHelperExecutionError):
			parse_trusted_install_engine_result(
				0, json.dumps(install_result()), "unexpected output", expected_operation_id="OP-0001"
			)

	def test_install_result_rejects_nonzero_process_exit_as_uncertain(self):
		import json

		with self.assertRaises(TrustedHelperExecutionError) as raised:
			parse_trusted_install_engine_result(
				1, json.dumps(install_result()), "", expected_operation_id="OP-0001"
			)
		self.assertEqual(raised.exception.code, "helper_process_failed")
		self.assertTrue(raised.exception.may_have_executed)

	def test_unknown_helper_operation_is_rejected_before_transport(self):
		with (
			patch("dt_master.api.script_contract.build_ssh_argv") as build_ssh,
			patch("dt_master.api.script_contract.subprocess.Popen") as popen,
		):
			with self.assertRaisesRegex(ValueError, "Unsupported trusted Docker helper request"):
				run_trusted_helper(
					None,
					{},
					{"schema_version": "1.0.0", "operation": "upgrade_engine"},
				)

		build_ssh.assert_not_called()
		popen.assert_not_called()


if __name__ == "__main__":
	unittest.main()
