"""Read-only Host runtime adapter for Infrastructure Nodes.

Bench-scoped operations are intentionally unavailable until M4.3.6 defines
Bench Environment ownership and M4.3.4.5 connects mutations to the coordinator.
"""

import json
from time import monotonic

import frappe

from dt_master.api.script_contract import parse_output, run_remote_script
from dt_master.runtime_adapters.base import RuntimeAdapter

OS_SUPPORT_POLICY_VERSION = "1.0.0"
SUPPORTED_OS_RELEASES = {
	("ubuntu", "22.04", "amd64"),
	("ubuntu", "24.04", "amd64"),
	("debian", "12", "amd64"),
}


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
	def _os_support(values):
		identity = (
			values.get("os_id", "").lower(),
			values.get("os_version_id", ""),
			values.get("architecture", ""),
		)
		if any(value in {"", "unknown"} for value in identity):
			return "Unknown"
		return "Supported" if identity in SUPPORTED_OS_RELEASES else "Unsupported"

	def _inspect(self, request, execution_context=None):
		node = self._node_for_request(request)
		started = monotonic()
		env = {"BASE_DIR": node.scripts_base_dir}
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
			max_output_bytes=60000,
		)
		elapsed = min(86400000, int((monotonic() - started) * 1000))
		if code != 0:
			return self.result_for(
				request,
				outcome="failed",
				observed_state="Unavailable",
				capabilities=self.capabilities(),
				diagnostics=(
					{
						"code": "HOST_INSPECTION_FAILED",
						"severity": "error",
						"message": "Read-only Host inspection could not be completed over the configured SSH transport.",
					},
				),
				data={"inspection_status": "Failed"},
				duration_ms=elapsed,
			)

		parsed = parse_output(stdout)
		try:
			discovery = json.loads(parsed["discovery_json"]) if parsed.get("discovery_json") else None
		except (KeyError, TypeError, ValueError):
			discovery = None

		# Retain the v1 parser during the transition so already-deployed script
		# bundles can still be inspected while the v2 bundle is rolled out.
		if discovery:
			identity = discovery.get("identity") or {}
			os_info = discovery.get("os") or {}
			compute = discovery.get("compute") or {}
			storage = discovery.get("storage") or {}
			values = {
				"hostname": identity.get("fqdn") or identity.get("hostname"),
				"os": os_info.get("pretty_name"),
				"os_id": os_info.get("id"),
				"os_version_id": os_info.get("version_id"),
				"os_version_codename": os_info.get("version_codename"),
				"os_id_like": os_info.get("id_like"),
				"raw_architecture": os_info.get("architecture"),
				"architecture": {"x86_64": "amd64", "aarch64": "arm64"}.get(os_info.get("architecture"), os_info.get("architecture")),
				"package_manager": os_info.get("package_manager"),
				"service_manager": os_info.get("service_manager"),
				"cpu_cores": compute.get("logical_cpu_cores"),
				"memory_bytes": compute.get("memory_total_bytes"),
				"root_available_bytes": storage.get("root_available_bytes"),
				"swap_total_bytes": compute.get("swap_total_bytes"),
				"bench_available": discovery.get("bench_available"),
			}
		else:
			values = parsed

		required = (
			"hostname", "os", "os_id", "os_version_id", "os_version_codename",
			"raw_architecture", "architecture", "package_manager", "service_manager",
			"cpu_cores", "memory_bytes", "root_available_bytes",
		)
		missing = [key for key in required if values.get(key) in (None, "")]
		if missing:
			return self.result_for(
				request, outcome="failed", observed_state="Unknown",
				capabilities=self.capabilities(),
				diagnostics=({
					"code": "HOST_INSPECTION_INCOMPLETE", "severity": "error",
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
				"swap_total_bytes": int(values.get("swap_total_bytes") or 0),
			}
			if any(value < 0 for value in measurements.values()):
				raise ValueError("negative measurement")
		except (TypeError, ValueError):
			return self.result_for(
				request, outcome="failed", observed_state="Unknown",
				capabilities=self.capabilities(),
				diagnostics=({
					"code": "HOST_INSPECTION_INVALID_MEASUREMENT", "severity": "error",
					"message": "Host inspection returned invalid capacity measurements.",
				},),
				data={"inspection_status": "Invalid"},
				duration_ms=elapsed,
			)

		os_support_status = self._os_support(values)
		bench_available = discovery.get("bench_available", False) if discovery else values.get("bench_available") == "true"
		tools = discovery.get("tools", {}) if discovery else {
			key: parsed.get(f"{key}_available") == "true"
			for key in ("python3", "node", "npm", "yarn", "nginx", "supervisorctl", "redis_cli", "mariadb")
		}
		data = {
			"schema_version": discovery.get("schema_version", "1.0.0") if discovery else "1.0.0",
			"inspection_status": "Successful",
			"hostname": str(values["hostname"])[:253],
			"os": str(values["os"])[:160],
			"os_id": str(values["os_id"])[:40].lower(),
			"os_version_id": str(values["os_version_id"])[:40],
			"os_version_codename": str(values["os_version_codename"])[:80],
			"os_id_like": str(values.get("os_id_like") or "unknown")[:160],
			"raw_architecture": str(values["raw_architecture"])[:40],
			"architecture": str(values["architecture"])[:40],
			"package_manager": str(values["package_manager"])[:40],
			"service_manager": str(values["service_manager"])[:40],
			"os_support_policy_version": OS_SUPPORT_POLICY_VERSION,
			"os_support_status": os_support_status,
			**measurements,
			"bench_available": bool(bench_available),
			"host_stage": "Bench CLI Available" if bench_available else "Fresh Host",
			"tools": tools,
		}
		if discovery:
			data["discovery"] = discovery
			self._persist_discovery_snapshot(node, discovery)

	@staticmethod
	def _persist_discovery_snapshot(node, discovery):
		"""Persist the latest observed snapshot and fill only blank inventory fields."""
		updates = {
			"host_discovery_status": "Successful",
			"host_discovered_on": discovery.get("observed_at"),
			"host_discovery_snapshot": json.dumps(discovery, separators=(",", ":"), sort_keys=True),
		}
		identity = discovery.get("identity") or {}
		os_info = discovery.get("os") or {}
		compute = discovery.get("compute") or {}
		storage = discovery.get("storage") or {}
		prefill = {
			"cpu_cores": compute.get("logical_cpu_cores"),
			"ram_gb": int(compute.get("memory_total_bytes") or 0) // (1024 ** 3),
			"storage_gb": int(storage.get("root_size_bytes") or 0) // (1024 ** 3),
			"os_version": os_info.get("version_id"),
		}
		os_type = {"ubuntu": "Ubuntu", "debian": "Debian"}.get(os_info.get("id", "").lower())
		if os_type:
			prefill["os_type"] = os_type
		for field, value in prefill.items():
			if value not in (None, "", 0) and not getattr(node, field, None):
				updates[field] = value
		# Hostname is retained in the snapshot, not used to rename a managed Node.
		# Network addresses are also not copied into ip_address: interfaces may
		# contain private, public, bridge and transient addresses.
		frappe.db.set_value("Infrastructure Node", node.name, updates, update_modified=False)

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
				data={
					"preflight_status": "Blocked",
					"inspection_status": inspection.data.get("inspection_status"),
				},
				duration_ms=inspection.duration_ms,
			)

		os_support_status = inspection.data["os_support_status"]
		if os_support_status != "Supported":
			return self.result_for(
				request,
				outcome="succeeded",
				observed_state="Blocked",
				capabilities=self.capabilities(),
				diagnostics=(
					{
						"code": "HOST_OS_SUPPORT_UNKNOWN"
						if os_support_status == "Unknown"
						else "HOST_OS_UNSUPPORTED",
						"severity": "warning",
						"message": "Host OS identity is not covered by the approved baseline support matrix.",
					},
				),
				data={
					"preflight_status": "Blocked",
					"inspection_status": inspection.data["inspection_status"],
					"os_support_policy_version": inspection.data["os_support_policy_version"],
					"os_support_status": os_support_status,
				},
				duration_ms=inspection.duration_ms,
			)

		data = {
			"preflight_status": "Ready",
			"inspection_status": inspection.data["inspection_status"],
			"os_support_policy_version": inspection.data["os_support_policy_version"],
			"os_support_status": os_support_status,
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
