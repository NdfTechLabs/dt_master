"""Contracts for read-only Docker privilege readiness inspection."""

import hashlib
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
	def test_helper_release_digest_and_version_are_consistent_across_components(self):
		helper = (ROOT / "docker_lifecycle_helper.py").read_bytes()
		policy = (ROOT / "docker_install_policy.py").read_bytes()
		inventory = (ROOT / "docker_install_inventory.py").read_bytes()
		installer = (ROOT / "install_docker_privilege_helper.sh").read_text()
		probe = (ROOT / "check_docker_privilege_readiness.py").read_text()
		adapter = Path(__file__).with_name("docker.py").read_text()

		digest = hashlib.sha256(helper).hexdigest()
		policy_digest = hashlib.sha256(policy).hexdigest()
		inventory_digest = hashlib.sha256(inventory).hexdigest()
		self.assertIn(f'EXPECTED_POLICY_SHA256 = "{policy_digest}"', helper.decode())
		self.assertIn(f'EXPECTED_INVENTORY_SHA256 = "{inventory_digest}"', helper.decode())
		helper_version = next(
			line.split("=", 1)[1].strip().strip('"')
			for line in helper.decode().splitlines()
			if line.startswith("HELPER_VERSION =")
		)
		installer_digest = next(
			line.split("=", 1)[1].strip().strip('"')
			for line in installer.splitlines()
			if line.startswith("readonly HELPER_SHA256=")
		)
		probe_digest = next(
			line.split("=", 1)[1].strip().strip('"')
			for line in probe.splitlines()
			if line.startswith("EXPECTED_HELPER_SHA256 =")
		)
		probe_version = next(
			line.split("=", 1)[1].strip().strip('"')
			for line in probe.splitlines()
			if line.startswith("EXPECTED_HELPER_VERSION =")
		)
		adapter_version_line = next(
			line for line in adapter.splitlines() if 'payload["helper_version"] !=' in line
		)
		adapter_version = adapter_version_line.split("!=", 1)[1].strip().strip('"')

		self.assertEqual(installer_digest, digest)
		self.assertEqual(probe_digest, digest)
		self.assertIn(
			f'POLICY_SHA256="{policy_digest}"',
			installer,
		)
		self.assertIn(
			f'INVENTORY_SHA256="{inventory_digest}"',
			installer,
		)
		self.assertIn(
			f'check_existing "$INVENTORY_NAME" "$INVENTORY_SHA256" "{hashlib.sha256(bytes.fromhex("99863a6d5d78c95ba3111d6574c3ae5c3bd653de004b24c0859904f86ac641a6")).hexdigest()}"',
			installer,
		)
		self.assertIn(
			f'EXPECTED_POLICY_SHA256 = "{policy_digest}"',
			probe,
		)
		self.assertIn(
			f'EXPECTED_INVENTORY_SHA256 = "{inventory_digest}"',
			probe,
		)
		self.assertEqual(probe_version, helper_version)
		self.assertEqual(adapter_version, helper_version)

	def payload(self):
		return {
			"schema_version": "1.0.0",
			"observed_at": "2026-11-21T00:00:00Z",
			"status": "Ready",
			"execution_identity": "deploy",
			"helper": {
				"path": "/usr/local/lib/dt-master/docker/docker_lifecycle_helper.py",
				"version": "0.3.1",
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
			"helper_version": "0.3.1",
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
		with (
			patch.object(adapter, "_node_for_request", return_value=SimpleNamespace(name="NODE-0001")),
			patch.object(adapter, "_inspect", return_value=inspection),
			patch.object(adapter, "_persist_inspection_evidence"),
		):
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
		self.assertIn(r'""\n', content)
		self.assertNotIn(r'""\\\\n', content)
		self.assertIn("helper_self_test", content)
		for forbidden in ("apt-get install", "apt install", "chmod ", "chown "):
			self.assertNotIn(forbidden, content)


if __name__ == "__main__":
	unittest.main()
