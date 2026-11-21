"""Tests for the fixed, bounded Docker trusted-helper SSH transport."""

import json
import subprocess
import unittest
from typing import ClassVar
from unittest.mock import patch

from dt_master.api.script_contract import (
	TrustedHelperExecutionError,
	run_trusted_helper,
)


class FakeProcess:
	pid = 12345
	returncode = 0

	def __init__(self, args, kwargs, *, stdout=b'{"ok":true}\n', timeout=False):
		self.args = args
		self.kwargs = kwargs
		self.stdout = stdout
		self.timeout = timeout
		self.input = None

	def communicate(self, input=None, timeout=None):
		self.input = input
		if self.timeout:
			raise subprocess.TimeoutExpired(self.args, timeout)
		self.kwargs["stdout"].write(self.stdout)
		self.kwargs["stderr"].write(b"")

	def wait(self, timeout=None):
		return self.returncode


class TestTrustedHelperTransport(unittest.TestCase):
	REQUEST: ClassVar[dict[str, str]] = {"schema_version": "1.0.0", "operation": "self_test"}

	def test_uses_fixed_helper_and_exact_sudoers_invocation(self):
		processes = []

		def popen(args, **kwargs):
			process = FakeProcess(args, kwargs)
			processes.append(process)
			return process

		with (
			patch(
				"dt_master.api.script_contract.build_ssh_argv", return_value=["ssh", "node", "REMOTE"]
			) as build,
			patch("dt_master.api.script_contract.subprocess.Popen", side_effect=popen),
		):
			code, stdout, stderr = run_trusted_helper(
				object(), {"host": "node", "user": "deploy"}, self.REQUEST
			)

		self.assertEqual(code, 0)
		self.assertEqual(json.loads(stdout), {"ok": True})
		self.assertEqual(stderr, "")
		self.assertEqual(
			build.call_args.args[1], 'sudo -n /usr/local/lib/dt-master/docker/docker_lifecycle_helper.py ""'
		)
		self.assertEqual(processes[0].input, b'{"operation":"self_test","schema_version":"1.0.0"}\n')
		self.assertIsNotNone(processes[0].kwargs["stdin"])
		self.assertIsNotNone(processes[0].kwargs["stdout"])
		self.assertIsNotNone(processes[0].kwargs["stderr"])
		self.assertTrue(processes[0].kwargs["start_new_session"])

	def test_rejects_arbitrary_operations_and_extra_request_fields(self):
		with patch("dt_master.api.script_contract.subprocess.Popen") as popen:
			with self.assertRaisesRegex(ValueError, "Unsupported"):
				run_trusted_helper(object(), {}, {"schema_version": "1.0.0", "operation": "install"})
			with self.assertRaisesRegex(ValueError, "Unsupported"):
				run_trusted_helper(
					object(),
					{},
					{"schema_version": "1.0.0", "operation": "self_test", "command": "id"},
				)
		popen.assert_not_called()

	def test_rejects_unbounded_timeout_and_output_limits(self):
		with self.assertRaisesRegex(ValueError, "timeout"):
			run_trusted_helper(object(), {}, self.REQUEST, timeout_seconds=121)
		with self.assertRaisesRegex(ValueError, "output limit"):
			run_trusted_helper(object(), {}, self.REQUEST, max_output_bytes=65537)

	def test_rejects_output_exceeding_limit(self):
		def popen(args, **kwargs):
			return FakeProcess(args, kwargs, stdout=b"x" * 8193)

		with (
			patch("dt_master.api.script_contract.build_ssh_argv", return_value=["ssh", "node", "REMOTE"]),
			patch("dt_master.api.script_contract.subprocess.Popen", side_effect=popen),
		):
			with self.assertRaises(TrustedHelperExecutionError) as raised:
				run_trusted_helper(object(), {}, self.REQUEST, max_output_bytes=8192)
		self.assertEqual(raised.exception.code, "output_limit_exceeded")
		self.assertTrue(raised.exception.may_have_executed)

	def test_timeout_is_reported_as_uncertain_execution(self):
		processes = []

		def popen(args, **kwargs):
			process = FakeProcess(args, kwargs, timeout=True)
			processes.append(process)
			return process

		with (
			patch("dt_master.api.script_contract.build_ssh_argv", return_value=["ssh", "node", "REMOTE"]),
			patch("dt_master.api.script_contract.subprocess.Popen", side_effect=popen),
			patch("dt_master.api.script_contract.os.killpg"),
		):
			with self.assertRaises(TrustedHelperExecutionError) as raised:
				run_trusted_helper(object(), {}, self.REQUEST, timeout_seconds=1)
		self.assertEqual(raised.exception.code, "execution_timeout")
		self.assertTrue(raised.exception.may_have_executed)

	def test_process_start_failure_is_not_classified_as_remote_execution(self):
		with (
			patch("dt_master.api.script_contract.build_ssh_argv", return_value=["ssh", "node", "REMOTE"]),
			patch(
				"dt_master.api.script_contract.subprocess.Popen",
				side_effect=OSError("local process unavailable"),
			),
		):
			with self.assertRaises(TrustedHelperExecutionError) as raised:
				run_trusted_helper(object(), {"host": "node", "user": "deploy"}, self.REQUEST)
		self.assertEqual(raised.exception.code, "transport_start_failed")
		self.assertFalse(raised.exception.may_have_executed)

	def test_invalid_utf8_after_process_start_is_uncertain(self):
		def popen(args, **kwargs):
			return FakeProcess(args, kwargs, stdout=b"\\xff")

		with (
			patch("dt_master.api.script_contract.build_ssh_argv", return_value=["ssh", "node", "REMOTE"]),
			patch("dt_master.api.script_contract.subprocess.Popen", side_effect=popen),
		):
			with self.assertRaises(TrustedHelperExecutionError) as raised:
				run_trusted_helper(object(), {"host": "node", "user": "deploy"}, self.REQUEST)
		self.assertEqual(raised.exception.code, "invalid_output_encoding")
		self.assertTrue(raised.exception.may_have_executed)


if __name__ == "__main__":
	unittest.main()
