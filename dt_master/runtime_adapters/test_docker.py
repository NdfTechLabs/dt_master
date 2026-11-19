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
                    "mount_point": "/", "total_bytes": 200000000000,
                    "available_bytes": 100000000000, "used_bytes": 100000000000,
                },
            },
            "permissions": {"execution_identity": "deploy", "inspection_access": "Allowed"},
            "diagnostics": [],
        }

    def _run(self, method="inspect", code=0, snapshot=None):
        snapshot = snapshot or self._snapshot()
        with (
            patch("dt_master.runtime_adapters.docker.frappe.get_doc", return_value=self.node),
            patch("dt_master.runtime_adapters.docker.run_remote_script", return_value=(
                code, __import__("json").dumps(snapshot), ""
            )) as remote,
            patch("dt_master.runtime_adapters.docker.evaluate_node_docker_readiness", return_value={
                "readiness": "Ready", "diagnostics": [], "evaluator_version": "1.1.0"
            }),
        ):
            result = self.adapter.invoke(method, request(method))
        return result, remote

    def test_inspection_uses_fixed_read_only_docker_script_without_sudo(self):
        result, remote = self._run()
        self.assertEqual(result.outcome, "succeeded")
        self.assertEqual(result.observed_state, "Available")
        self.assertEqual(result.data["readiness"], "Ready")
        self.assertEqual(remote.call_args.args[2:4], ("docker", "inspect_docker.sh"))
        self.assertFalse(remote.call_args.kwargs["use_sudo"])
        self.assertEqual(remote.call_args.kwargs["timeout_seconds"], 45)

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
            adapter_id="docker-compose-v1", runtime_type="docker",
            target_doctype="Bench Environment", target_name="BENCH-0001",
            method="inspect", payload={},
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

    def test_preflight_blocks_unknown_readiness(self):
        with (
            patch("dt_master.runtime_adapters.docker.frappe.get_doc", return_value=self.node),
            patch("dt_master.runtime_adapters.docker.run_remote_script", return_value=(
                0, __import__("json").dumps(self._snapshot()), ""
            )),
            patch("dt_master.runtime_adapters.docker.evaluate_node_docker_readiness", return_value={
                "readiness": "Unknown", "diagnostics": [], "evaluator_version": "1.1.0"
            }),
        ):
            result = self.adapter.invoke("preflight", request("preflight"))
        self.assertEqual(result.outcome, "failed")
        self.assertEqual(result.observed_state, "Blocked")

    def test_preflight_reports_degraded_without_claiming_ready(self):
        with (
            patch("dt_master.runtime_adapters.docker.frappe.get_doc", return_value=self.node),
            patch("dt_master.runtime_adapters.docker.run_remote_script", return_value=(
                0, __import__("json").dumps(self._snapshot()), ""
            )),
            patch("dt_master.runtime_adapters.docker.evaluate_node_docker_readiness", return_value={
                "readiness": "Degraded", "diagnostics": [], "evaluator_version": "1.1.0"
            }),
        ):
            result = self.adapter.invoke("preflight", request("preflight"))
        self.assertEqual(result.outcome, "partial")
        self.assertEqual(result.observed_state, "Degraded")

    def test_registry_resolves_builtin_docker_adapter(self):
        from dt_master.runtime_adapters.registry import clear_registry_for_tests, resolve_adapter

        clear_registry_for_tests()
        try:
            adapter = resolve_adapter("docker")
            self.assertEqual(adapter.adapter_id, "docker-compose-v1")
            self.assertEqual(adapter.capabilities(), ("inspect", "preflight"))
        finally:
            clear_registry_for_tests()


    def test_snapshot_and_readiness_diagnostics_are_sanitized_everywhere(self):
        snapshot = self._snapshot()
        snapshot["diagnostics"] = [{
            "code": "REMOTE_AUTH_FAILURE",
            "severity": "Error",
            "message": "remote rejected password=remote-password-value",
        }]
        evaluation = {
            "readiness": "Degraded",
            "diagnostics": [{
                "code": "READINESS_AUTH_WARNING",
                "severity": "Warning",
                "message": "credential token=readiness-token-value was unavailable",
            }],
            "evaluator_version": "1.1.0",
        }
        with (
            patch("dt_master.runtime_adapters.docker.frappe.get_doc", return_value=self.node),
            patch("dt_master.runtime_adapters.docker.run_remote_script", return_value=(
                0, __import__("json").dumps(snapshot), ""
            )),
            patch("dt_master.runtime_adapters.docker.evaluate_node_docker_readiness", return_value=evaluation),
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
