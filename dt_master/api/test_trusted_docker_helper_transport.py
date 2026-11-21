"""Tests for the trusted Docker helper transport operation gate."""

import unittest
from unittest.mock import patch

from dt_master.api.script_contract import TrustedHelperExecutionError, run_trusted_helper
from dt_master.runtime_adapters.docker_helper_contracts import DockerHelperContractError


def install_request():
	return {
		"schema_version": "1.0.0",
		"operation": "install_engine",
		"operation_id": "OP-0001",
		"target": {"doctype": "Infrastructure Node", "name": "NODE-0001"},
		"plan": {
			"schema_version": "1.1.0",
			"policy_version": "1.1.0",
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


class TestTrustedDockerHelperTransport(unittest.TestCase):
	def test_valid_install_request_is_validated_but_not_dispatched(self):
		with (
			patch("dt_master.api.script_contract.build_ssh_argv") as build_ssh,
			patch("dt_master.api.script_contract.subprocess.Popen") as popen,
		):
			with self.assertRaises(TrustedHelperExecutionError) as raised:
				run_trusted_helper(None, {}, install_request())

		self.assertEqual(raised.exception.code, "operation_disabled")
		self.assertFalse(raised.exception.may_have_executed)
		build_ssh.assert_not_called()
		popen.assert_not_called()

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
