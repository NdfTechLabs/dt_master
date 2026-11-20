"""Contracts for read-only Docker privilege readiness inspection."""
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from dt_master.runtime_adapters.docker import DockerRuntimeAdapter
from dt_master.runtime_adapters.contracts import RuntimeAdapterRequest

ROOT = Path(__file__).parents[1] / "scripts" / "bin" / "docker"


class TestDockerPrivilegeReadiness(unittest.TestCase):
    def payload(self):
        return {
            "schema_version": "1.0.0", "observed_at": "2026-11-21T00:00:00Z",
            "status": "Ready", "execution_identity": "deploy",
            "helper": {"path": "/usr/local/lib/dt-master/docker/docker_lifecycle_helper.py",
                       "version": "0.1.0", "sha256": "a" * 64},
            "sudoers": {"path": "/etc/sudoers.d/dt-master-docker", "syntax": "Valid"},
            "checks": [{"name": "helper_integrity", "status": "Passed"}], "diagnostics": [],
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

    def test_preflight_blocks_when_privilege_boundary_is_not_ready(self):
        adapter = DockerRuntimeAdapter()
        request = RuntimeAdapterRequest(
            adapter_id="docker-compose-v1", runtime_type="docker",
            target_doctype="Infrastructure Node", target_name="NODE-0001",
            method="preflight", payload={},
        )
        inspection = adapter.result_for(
            request, outcome="succeeded", observed_state="Available",
            data={"readiness": "Ready", "readiness_evaluation": {},
                  "inspection_status": "Successful", "privilege_readiness": {"status": "Not Ready"}},
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
        self.assertIn("visudo", content)
        self.assertIn("helper_self_test", content)
        for forbidden in ("apt-get install", "apt install", "chmod ", "chown "):
            self.assertNotIn(forbidden, content)


if __name__ == "__main__":
    unittest.main()
