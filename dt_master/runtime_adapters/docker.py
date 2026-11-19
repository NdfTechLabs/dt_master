"""Read-only Docker runtime adapter for Infrastructure Nodes.

This adapter reuses the established system-socket inspection script and the
Node readiness evaluator. Compose and container lifecycle mutations belong to
M4.3.5 and are deliberately not exposed here.
"""

import json
from time import monotonic

import frappe

from dt_master.api.script_contract import run_remote_script
from dt_master.docker_readiness import evaluate_node_docker_readiness
from dt_master.runtime_adapters.base import RuntimeAdapter


class DockerRuntimeAdapter(RuntimeAdapter):
    adapter_id = "docker-compose-v1"
    runtime_type = "docker"

    def capabilities(self):
        return ("inspect", "preflight")

    @staticmethod
    def _node_for_request(request):
        if request.target_doctype != "Infrastructure Node":
            raise ValueError("Docker inspection currently requires an Infrastructure Node target")
        node = frappe.get_doc(request.target_doctype, request.target_name)
        node.check_permission("read")
        if not node.access_verified:
            raise ValueError("Infrastructure Node SSH access has not been verified")
        if not node.scripts_base_dir or not str(node.scripts_base_dir).startswith("/"):
            raise ValueError("Infrastructure Node requires an absolute scripts base directory")
        return node

    @staticmethod
    def _ssh_config(node):
        alias = getattr(node, "ssh_host_alias", None) or None
        if not alias and (not node.ip_address or not node.ssh_user):
            raise ValueError("Infrastructure Node SSH configuration is incomplete")
        return {
            "host": (node.ip_address or "").strip(),
            "user": node.ssh_user,
            "port": node.ssh_port or 22,
            "host_alias": alias,
        }

    @staticmethod
    def _diagnostics(snapshot, evaluation):
        diagnostics = []
        for item in snapshot.get("diagnostics", []):
            diagnostics.append({
                "code": item["code"],
                "severity": item["severity"].lower(),
                "message": item["message"][:1000],
            })
        for item in evaluation.get("diagnostics", []):
            diagnostics.append({
                "code": item["code"],
                "severity": "error" if item["severity"] == "Error" else "warning",
                "message": item["message"][:1000],
            })
        return tuple(diagnostics[:100])

    def _inspect(self, request, execution_context=None):
        from dt_master.api.infrastructure_node import (
            _docker_failure_snapshot,
            _validate_docker_snapshot,
        )

        node = self._node_for_request(request)
        started = monotonic()
        code, stdout, stderr = -1, "", ""
        try:
            code, stdout, stderr = run_remote_script(
                node,
                self._ssh_config(node),
                "docker",
                "inspect_docker.sh",
                {},
                "script_execution_log",
                {"execution": "inspect_docker.sh", "node": node.name},
                use_sudo=False,
                timeout_seconds=45,
                max_output_bytes=16384,
            )
            elapsed = min(86400000, int((monotonic() - started) * 1000))
            if code == 124:
                snapshot = _docker_failure_snapshot(
                    "Timed Out", "Read-only Docker inspection exceeded its execution timeout.", elapsed
                )
            elif code != 0:
                snapshot = _docker_failure_snapshot(
                    "Failed", "Read-only Docker inspection could not be completed.", elapsed
                )
            else:
                try:
                    snapshot = _validate_docker_snapshot(json.loads(stdout.strip()))
                except (ValueError, TypeError, json.JSONDecodeError):
                    snapshot = _docker_failure_snapshot(
                        "Failed", "Docker inspection returned an invalid observation payload.", elapsed
                    )
        except Exception:
            elapsed = min(86400000, int((monotonic() - started) * 1000))
            snapshot = _docker_failure_snapshot(
                "Failed", "Docker inspection could not be completed over the configured SSH transport.", elapsed
            )

        evaluation = evaluate_node_docker_readiness(node, snapshot)
        status = snapshot["inspection_status"]
        outcome = "succeeded" if status == "Successful" else ("partial" if status == "Partial" else "failed")
        data = {
            "inspection_status": status,
            "readiness": evaluation["readiness"],
            "readiness_evaluation": evaluation,
            "snapshot": snapshot,
        }
        return self.result_for(
            request,
            outcome=outcome,
            observed_state=snapshot["engine"]["availability"],
            capabilities=self.capabilities(),
            diagnostics=self._diagnostics(snapshot, evaluation),
            data=data,
            duration_ms=snapshot["duration_ms"],
        )

    def _preflight(self, request, execution_context=None):
        inspection = self._inspect(request, execution_context=execution_context)
        readiness = inspection.data["readiness"]
        if inspection.outcome == "failed" or readiness in {"Unknown", "Not Ready"}:
            outcome, state = "failed", "Blocked"
        elif readiness == "Degraded" or inspection.outcome == "partial":
            outcome, state = "partial", "Degraded"
        else:
            outcome, state = "succeeded", "Ready"
        return self.result_for(
            request,
            outcome=outcome,
            observed_state=state,
            capabilities=self.capabilities(),
            diagnostics=inspection.diagnostics,
            data={
                "preflight_status": state,
                "inspection_status": inspection.data["inspection_status"],
                "readiness": readiness,
                "readiness_evaluation": inspection.data["readiness_evaluation"],
            },
            duration_ms=inspection.duration_ms,
        )


def register_docker_adapter():
    """Register the Docker adapter from trusted application initialization."""
    from dt_master.runtime_adapters.registry import register_adapter

    return register_adapter(DockerRuntimeAdapter())
