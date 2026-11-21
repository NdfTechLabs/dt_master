"""Contracts for read-only Docker privilege readiness inspection."""

import json
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from dt_master.runtime_adapters.contracts import RuntimeAdapterRequest
from dt_master.runtime_adapters.docker import DockerRuntimeAdapter

ROOT = Path(__file__).parents[1] / "scripts" / "bin" / "docker"


class TestDockerPrivilegeReadiness(unittest.TestCase):
	def payload(self):
		return {
			"schema_version": "1.0.0",
			"observed_at": "2026-11-21T00:00:00Z",
			"status": "Ready",
			"execution_identity": "deploy",
			"helper": {
				"path": "/usr/local/lib/dt-master/docker/docker_lifecycle_helper.py",
				"version": "0.1.0",
				"sha256": "a" * 64,
			},
			"sudoers": {"path": "/etc/sudoers.d/dt-master-docker", "syntax": "Valid"},
			"checks": [{"name": "helper_integrity", "status": "Passed"}],
			"diagnostics": [],
		}

	def test_accepts_valid_contract(self):
		payload = self.payload()
		self.assertEqual(DockerRuntimeAdapter._validate_privilege_readiness(payload), payload)

	def test_rejects_unrecognized_check(self):
		payload = self.payload()
		payload["checks"] = [{"name": "arbitrary_command", "status": "Passed"}]
		with self.assertRaises(ValueError):
			DockerRuntimeAdapter._validate_privilege_readiness(payload)

	def test_rejects_unknown_fields(self):
		payload = self.payload()
		payload["command"] = "id"
		with self.assertRaises(ValueError):
			DockerRuntimeAdapter._validate_privilege_readiness(payload)

	def helper_self_test(self):
		return {
			"schema_version": "1.0.0",
			"helper_version": "0.1.0",
			"operation": "self_test",
			"status": "ok",
			"effective_uid": 0,
			"effective_user": "root",
		}

	def test_rejects_duplicate_json_response_keys(self):
		with self.assertRaisesRegex(ValueError, "Duplicate"):
			DockerRuntimeAdapter._unique_json_object([("status", "ok"), ("status", "failed")])

	def test_accepts_only_root_helper_self_test_response(self):
		payload = self.helper_self_test()
		self.assertEqual(DockerRuntimeAdapter._validate_trusted_helper_self_test(payload), payload)
		payload["effective_uid"] = 1000
		with self.assertRaises(ValueError):
			DockerRuntimeAdapter._validate_trusted_helper_self_test(payload)

	def test_rejects_extra_fields_in_helper_self_test_response(self):
		payload = self.helper_self_test()
		payload["command"] = "id"
		with self.assertRaises(ValueError):
			DockerRuntimeAdapter._validate_trusted_helper_self_test(payload)

	def test_ready_probe_uses_existing_runner_for_direct_helper_self_test(self):
		adapter = DockerRuntimeAdapter()
		node = SimpleNamespace(
			name="NODE-0001",
			ssh_user="deploy",
			ssh_port=22,
			ssh_host_alias=None,
			ip_address="192.0.2.10",
		)
		readiness = self.payload()
		with (
			patch(
				"dt_master.runtime_adapters.docker.run_remote_script",
				return_value=(0, json.dumps(readiness), ""),
			),
			patch(
				"dt_master.runtime_adapters.docker.run_trusted_helper",
				return_value=(0, json.dumps(self.helper_self_test()), ""),
			) as trusted_helper,
		):
			result = adapter._privilege_readiness(node)

		self.assertEqual(result["status"], "Ready")
		trusted_helper.assert_called_once_with(
			node,
			adapter._ssh_config(node),
			{"schema_version": "1.0.0", "operation": "self_test"},
			timeout_seconds=20,
			max_output_bytes=8192,
		)

	def test_non_ready_probe_does_not_invoke_trusted_helper(self):
		adapter = DockerRuntimeAdapter()
		node = SimpleNamespace(
			name="NODE-0001",
			ssh_user="deploy",
			ssh_port=22,
			ssh_host_alias=None,
			ip_address="192.0.2.10",
		)
		readiness = self.payload()
		readiness["status"] = "Not Ready"
		with (
			patch(
				"dt_master.runtime_adapters.docker.run_remote_script",
				return_value=(0, json.dumps(readiness), ""),
			),
			patch("dt_master.runtime_adapters.docker.run_trusted_helper") as trusted_helper,
		):
			result = adapter._privilege_readiness(node)

		self.assertEqual(result["status"], "Not Ready")
		trusted_helper.assert_not_called()

	def test_preflight_blocks_when_privilege_boundary_is_not_ready(self):
		adapter = DockerRuntimeAdapter()
		request = RuntimeAdapterRequest(
			adapter_id="docker-compose-v1",
			runtime_type="docker",
			target_doctype="Infrastructure Node",
			target_name="NODE-0001",
			method="preflight",
			payload={},
		)
		inspection = adapter.result_for(
			request,
			outcome="succeeded",
			observed_state="Available",
			data={
				"readiness": "Ready",
				"readiness_evaluation": {},
				"inspection_status": "Successful",
				"privilege_readiness": {"status": "Not Ready"},
			},
		)
		with patch.object(adapter, "_inspect", return_value=inspection):
			result = adapter.invoke("preflight", request)
		self.assertEqual(result.outcome, "failed")
		self.assertEqual(result.observed_state, "Blocked")

	def test_probe_wrapper_has_valid_shell_syntax_and_no_mutation_commands(self):
		wrapper = ROOT / "check_docker_privilege_readiness.sh"
		probe = ROOT / "check_docker_privilege_readiness.py"
		result = subprocess.run(["bash", "-n", str(wrapper)], capture_output=True, text=True)
		self.assertEqual(result.returncode, 0, result.stderr)
		content = probe.read_text()
		self.assertIn("EXPECTED_HELPER_SHA256", content)
		self.assertIn('["sudo", "-n", "stat"', content)
		self.assertIn('["sudo", "-n", "cat"', content)
		self.assertIn('["sudo", "-n", "visudo"', content)
		self.assertIn("helper_self_test", content)
		for forbidden in ("apt-get install", "apt install", "chmod ", "chown "):
			self.assertNotIn(forbidden, content)


if __name__ == "__main__":
	unittest.main()
