"""Read-only Host runtime adapter for Infrastructure Nodes.

Bench-scoped operations are intentionally unavailable until M4.3.6 defines
Bench Environment ownership and M4.3.4.5 connects mutations to the coordinator.
"""

from time import monotonic

import frappe

from dt_master.api.script_contract import parse_output, path_environment, run_remote_script
from dt_master.api.ssh_transport import build_ssh_argv
from dt_master.runtime_adapters.base import RuntimeAdapter


class HostRuntimeAdapter(RuntimeAdapter):
    adapter_id = "host-v1"
    runtime_type = "host"

    def capabilities(self):
        return ("inspect", "preflight")

    @staticmethod
    def _node_for_request(request):
        if request.target_doctype != "Infrastructure Node":
            raise ValueError("Host inspection currently requires an Infrastructure Node target")
        node = frappe.get_doc(request.target_doctype, request.target_name)
        node.check_permission("read")
        if not node.access_verified:
            raise ValueError("Infrastructure Node SSH access has not been verified")
        if not node.scripts_base_dir or not node.project_base_dir or not node.project_logs_dir:
            raise ValueError("Infrastructure Node script and project paths must be configured")
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

    def _inspect(self, request, execution_context=None):
        node = self._node_for_request(request)
        started = monotonic()
        env = path_environment(node)
        code, stdout, stderr = run_remote_script(
            node,
            self._ssh_config(node),
            "node",
            "inspect_host_runtime.sh",
            env,
            "host_runtime_inspection",
            {"node": node.name, "target": request.target_name},
            use_sudo=False,
            timeout_seconds=45,
            max_output_bytes=16384,
        )
        elapsed = min(86400000, int((monotonic() - started) * 1000))
        if code != 0:
            return self.result_for(
                request,
                outcome="failed",
                observed_state="Unavailable",
                capabilities=self.capabilities(),
                diagnostics=({
                    "code": "HOST_INSPECTION_FAILED",
                    "severity": "error",
                    "message": "Read-only Host inspection could not be completed over the configured SSH transport.",
                },),
                data={"inspection_status": "Failed"},
                duration_ms=elapsed,
            )

        values = parse_output(stdout)
        required = ("hostname", "os", "architecture", "cpu_cores", "memory_bytes", "root_available_bytes")
        missing = [key for key in required if key not in values]
        if missing:
            return self.result_for(
                request,
                outcome="failed",
                observed_state="Unknown",
                capabilities=self.capabilities(),
                diagnostics=({
                    "code": "HOST_INSPECTION_INCOMPLETE",
                    "severity": "error",
                    "message": "Host inspection returned an incomplete observation.",
                },),
                data={"inspection_status": "Invalid", "missing_fields": missing},
                duration_ms=elapsed,
            )

        try:
            measurements = {
                "cpu_cores": int(values["cpu_cores"]),
                "memory_bytes": int(values["memory_bytes"]),
                "root_available_bytes": int(values["root_available_bytes"]),
            }
            if any(value < 0 for value in measurements.values()):
                raise ValueError("negative measurement")
        except (TypeError, ValueError):
            return self.result_for(
                request,
                outcome="failed",
                observed_state="Unknown",
                capabilities=self.capabilities(),
                diagnostics=({
                    "code": "HOST_INSPECTION_INVALID_MEASUREMENT",
                    "severity": "error",
                    "message": "Host inspection returned invalid capacity measurements.",
                },),
                data={"inspection_status": "Invalid"},
                duration_ms=elapsed,
            )

        data = {
            "inspection_status": "Successful",
            "hostname": values["hostname"][:253],
            "os": values["os"][:80],
            "architecture": values["architecture"][:80],
            **measurements,
            "bench_available": values.get("bench_available") == "true",
        }
        return self.result_for(
            request,
            outcome="succeeded",
            observed_state="Available",
            capabilities=self.capabilities(),
            data=data,
            duration_ms=elapsed,
        )

    def _preflight(self, request, execution_context=None):
        inspection = self._inspect(request, execution_context=execution_context)
        if inspection.outcome != "succeeded":
            return self.result_for(
                request,
                outcome="failed",
                observed_state="Blocked",
                capabilities=self.capabilities(),
                diagnostics=inspection.diagnostics,
                data={"preflight_status": "Blocked", "inspection_status": inspection.data.get("inspection_status")},
                duration_ms=inspection.duration_ms,
            )

        data = {
            "preflight_status": "Ready",
            "inspection_status": inspection.data["inspection_status"],
            "cpu_cores": inspection.data["cpu_cores"],
            "memory_bytes": inspection.data["memory_bytes"],
            "root_available_bytes": inspection.data["root_available_bytes"],
            "bench_available": inspection.data["bench_available"],
        }
        return self.result_for(
            request,
            outcome="succeeded",
            observed_state="Ready",
            capabilities=self.capabilities(),
            data=data,
            duration_ms=inspection.duration_ms,
        )


def register_host_adapter():
    """Register the Host adapter from trusted application initialization."""
    from dt_master.runtime_adapters.registry import register_adapter

    return register_adapter(HostRuntimeAdapter())
