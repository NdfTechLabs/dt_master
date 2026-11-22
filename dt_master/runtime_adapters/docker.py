"""Docker runtime adapter for Infrastructure Nodes.

The adapter reuses the durable operation bridge and trusted helper for the
initial exact-version Docker Engine installation, alongside read-only
observations and the existing constrained privilege bootstrap.
"""

import json
from copy import deepcopy
from time import monotonic

import frappe
from frappe.utils import now_datetime

from dt_master.api.script_contract import (
	TrustedHelperExecutionError,
	parse_trusted_install_engine_result,
	run_remote_script,
	run_trusted_helper,
)
from dt_master.docker_readiness import evaluate_node_docker_readiness
from dt_master.runtime_adapters.base import RuntimeAdapter
from dt_master.runtime_adapters.contracts import sanitize_diagnostic_message


class DockerRuntimeAdapter(RuntimeAdapter):
	adapter_id = "docker-compose-v1"
	runtime_type = "docker"

	def capabilities(self):
		return (
			"bootstrap_privileges",
			"prepare_repository",
			"install_engine",
			"inspect",
			"preflight",
			"updates_check",
			"verify",
			"privilege_check",
		)

	@staticmethod
	def _interpret_install_engine_result(request, helper_result):
		"""Convert validated helper evidence without declaring package install success.

		A successful privileged helper response is only an intermediate result.
		The caller must perform independent read-only postcondition inspection
		before the durable operation can be marked successful.
		"""
		from dt_master.runtime_adapters.docker_helper_contracts import (
			validate_install_engine_result,
		)

		result = validate_install_engine_result(
			helper_result,
			expected_operation_id=request.operation_id,
		)
		status = result["status"]
		if status == "failed_before_mutation":
			outcome, state = "failed", "Install Not Applied"
			diagnostics = (
				{
					"code": "DOCKER_ENGINE_INSTALL_REJECTED",
					"severity": "error",
					"message": "The trusted helper rejected the request before package mutation.",
				},
			)
		elif status == "succeeded":
			outcome, state = "partial", "Awaiting Postcondition Verification"
			diagnostics = (
				{
					"code": "DOCKER_ENGINE_POSTCONDITION_REQUIRED",
					"severity": "warning",
					"message": "The helper completed its package action; independent host inspection is still required.",
				},
			)
		else:
			outcome, state = "partial", "Recovery Required"
			diagnostics = (
				{
					"code": "DOCKER_ENGINE_INSTALL_RECONCILIATION_REQUIRED",
					"severity": "error",
					"message": "The helper reported a possible or partial mutation; fresh host reconciliation is required.",
				},
			)

		return DockerRuntimeAdapter.result_for(
			request,
			outcome=outcome,
			observed_state=state,
			capabilities=(),
			diagnostics=diagnostics,
			data={
				"helper_status": status,
				"helper_phase": result["phase"],
				"helper_version": result["helper_version"],
				"before_state_fingerprint": result["before_state_fingerprint"],
				"after_state_fingerprint": result["after_state_fingerprint"],
				"packages": result["packages"],
				"package_manager_exit_code": result["package_manager_exit_code"],
				"daemon_action": result["daemon_action"],
				"diagnostic_codes": result["diagnostics"],
				"postcondition_verified": False,
			},
		)

	@staticmethod
	def _interpret_install_engine_postcondition(request, helper_result, inventory):
		"""Require fresh independent inventory before declaring Engine installation successful."""
		from dt_master.node_preparation.docker_install_verification import (
			evaluate_docker_engine_install_postcondition,
		)
		from dt_master.runtime_adapters.docker_helper_contracts import (
			validate_install_engine_result,
		)

		helper = validate_install_engine_result(
			helper_result,
			expected_operation_id=request.operation_id,
		)
		if helper["status"] == "failed_before_mutation":
			return DockerRuntimeAdapter._interpret_install_engine_result(request, helper)

		verification = evaluate_docker_engine_install_postcondition(
			request.target_name,
			inventory,
			helper,
			expected_operation_id=request.operation_id,
		)
		if helper["status"] != "succeeded":
			base = DockerRuntimeAdapter._interpret_install_engine_result(request, helper)
			data = dict(base.data)
			data["postcondition"] = verification
			return DockerRuntimeAdapter.result_for(
				request,
				outcome="partial",
				observed_state="Recovery Required",
				capabilities=(),
				diagnostics=base.diagnostics,
				data=data,
			)

		verified = verification["status"] == "verified"
		diagnostic_rows = verification["diagnostics"]
		if verified:
			outcome = "succeeded"
			state = "Verified"
			diagnostics = ()
		else:
			outcome = "partial"
			state = "Recovery Required"
			if not diagnostic_rows:
				diagnostic_rows = [
					{
						"code": "DOCKER_INSTALL_POSTCONDITION_NOT_VERIFIED",
						"message": "Fresh host inspection did not verify the requested Docker Engine installation.",
					}
				]
			diagnostics = tuple(
				{
					"code": item["code"],
					"severity": "error",
					"message": sanitize_diagnostic_message(item["message"]),
				}
				for item in diagnostic_rows[:20]
			)

		return DockerRuntimeAdapter.result_for(
			request,
			outcome=outcome,
			observed_state=state,
			capabilities=(),
			diagnostics=diagnostics,
			data={
				"helper_status": helper["status"],
				"helper_version": helper["helper_version"],
				"postcondition_verified": verified,
				"postcondition": verification,
			},
		)

	def _verify_install_engine_postcondition(self, request, helper_result, execution_context=None):
		"""Collect a new read-only Node inventory and verify the helper's result."""
		from dt_master.runtime_adapters.docker_helper_contracts import (
			validate_install_engine_result,
		)

		helper = validate_install_engine_result(
			helper_result,
			expected_operation_id=request.operation_id,
		)
		if helper["status"] == "failed_before_mutation":
			return self._interpret_install_engine_result(request, helper)

		if execution_context is not None:
			execution_context.heartbeat()
			execution_context.assert_leases()

		inspection = self._inspect(request, execution_context=execution_context)
		if execution_context is not None:
			execution_context.assert_leases()

		snapshot = inspection.data.get("snapshot") or {}
		inventory = None
		if inspection.data.get("inspection_status") == "Successful":
			host_inventory = snapshot.get("host_inventory")
			if isinstance(host_inventory, dict):
				inventory = host_inventory.get("docker_install_inventory")

		result = self._interpret_install_engine_postcondition(request, helper, inventory)
		if execution_context is not None:
			execution_context.heartbeat()
			execution_context.assert_leases()
		return result

	@staticmethod
	def _management_snapshot(node):
		try:
			snapshot = json.loads(getattr(node, "docker_host_management_snapshot", None) or "{}")
		except (TypeError, ValueError):
			return {}
		return snapshot if isinstance(snapshot, dict) else {}

	@classmethod
	def _require_host_inspection(cls, node):
		snapshot = cls._management_snapshot(node)
		inspection = snapshot.get("host_inspection") or {}
		if (
			getattr(node, "docker_host_state", None) != "Privilege Setup Required"
			or inspection.get("inspection_status") != "Successful"
			or inspection.get("os_support_status") != "Supported"
			or not inspection.get("operation_id")
		):
			raise ValueError(
				"A successful, supported Host inspection is required before Docker privilege setup."
			)

	@classmethod
	def _require_privilege_bootstrap(cls, node):
		cls._require_host_inspection(node)
		snapshot = cls._management_snapshot(node)
		bootstrap = snapshot.get("privilege_bootstrap") or {}
		if bootstrap.get("status") != "Succeeded" or not bootstrap.get("operation_id"):
			raise ValueError(
				"The trusted Docker privilege helper must be installed before privilege verification."
			)

	@classmethod
	def _require_privilege_verified(cls, node):
		snapshot = cls._management_snapshot(node)
		verification = snapshot.get("privilege_readiness") or {}
		if (
			getattr(node, "docker_host_state", None)
			not in {
				"Docker Inspection Required",
				"Repository Preparation Required",
				"Ready for Install",
				"Engine Ready",
			}
			or verification.get("status") != "Ready"
			or not verification.get("operation_id")
		):
			raise ValueError(
				"Trusted Docker privilege verification must succeed before Docker Engine inspection."
			)

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
	def _sanitize_observation_diagnostics(snapshot, evaluation):
		"""Sanitize nested diagnostic copies as well as the top-level result list."""
		safe_snapshot = deepcopy(snapshot)
		safe_evaluation = deepcopy(evaluation)
		for item in safe_snapshot.get("diagnostics", []):
			item["message"] = sanitize_diagnostic_message(item["message"])
		for item in safe_evaluation.get("diagnostics", []):
			item["message"] = sanitize_diagnostic_message(item["message"])
		return safe_snapshot, safe_evaluation

	@staticmethod
	def _diagnostics(snapshot, evaluation):
		diagnostics = []
		for item in snapshot.get("diagnostics", []):
			diagnostics.append(
				{
					"code": item["code"],
					"severity": item["severity"].lower(),
					"message": sanitize_diagnostic_message(item["message"]),
				}
			)
		for item in evaluation.get("diagnostics", []):
			diagnostics.append(
				{
					"code": item["code"],
					"severity": "error" if item["severity"] == "Error" else "warning",
					"message": sanitize_diagnostic_message(item["message"]),
				}
			)
		return tuple(diagnostics[:100])

	@staticmethod
	def _validate_privilege_readiness(payload):
		required = {
			"schema_version",
			"observed_at",
			"status",
			"execution_identity",
			"helper",
			"sudoers",
			"checks",
			"diagnostics",
		}
		if not isinstance(payload, dict) or set(payload) != required or payload["schema_version"] != "1.0.0":
			raise ValueError("Invalid Docker privilege readiness contract")
		if payload["status"] not in {"Ready", "Not Ready", "Incomplete"}:
			raise ValueError("Invalid Docker privilege readiness status")
		if payload["execution_identity"] is not None and (
			not isinstance(payload["execution_identity"], str) or len(payload["execution_identity"]) > 64
		):
			raise ValueError("Invalid Docker privilege execution identity")
		if not isinstance(payload["helper"], dict) or set(payload["helper"]) != {"path", "version", "sha256"}:
			raise ValueError("Invalid Docker helper observation")
		if not isinstance(payload["sudoers"], dict) or set(payload["sudoers"]) != {"path", "syntax"}:
			raise ValueError("Invalid Docker sudoers observation")
		if payload["sudoers"]["syntax"] not in {"Valid", "Invalid"}:
			raise ValueError("Invalid Docker sudoers syntax status")
		if not isinstance(payload["checks"], list) or len(payload["checks"]) > 12:
			raise ValueError("Invalid Docker privilege checks")
		allowed_checks = {
			"execution_identity",
			"helper_integrity",
			"policy_integrity",
			"inventory_integrity",
			"trusted_directories",
			"sudoers_policy",
			"sudoers_syntax",
			"effective_command",
			"helper_self_test",
		}
		for item in payload["checks"]:
			if (
				not isinstance(item, dict)
				or set(item) != {"name", "status"}
				or item["name"] not in allowed_checks
				or item["status"] not in {"Passed", "Failed"}
			):
				raise ValueError("Invalid Docker privilege check row")
		if not isinstance(payload["diagnostics"], list) or len(payload["diagnostics"]) > 12:
			raise ValueError("Invalid Docker privilege diagnostics")
		for item in payload["diagnostics"]:
			if not isinstance(item, dict) or set(item) != {"code", "severity", "message"}:
				raise ValueError("Invalid Docker privilege diagnostic")
			if (
				item["severity"] not in {"Error", "Warning"}
				or not isinstance(item["message"], str)
				or len(item["message"]) > 300
			):
				raise ValueError("Invalid Docker privilege diagnostic values")
		return payload

	@staticmethod
	def _unique_json_object(pairs):
		result = {}
		for key, value in pairs:
			if key in result:
				raise ValueError("Duplicate JSON response key")
			result[key] = value
		return result

	@staticmethod
	def _validate_trusted_helper_self_test(payload):
		required = {
			"schema_version",
			"helper_version",
			"operation",
			"status",
			"effective_uid",
			"effective_user",
		}
		if not isinstance(payload, dict) or set(payload) != required:
			raise ValueError("Invalid trusted Docker helper self-test response")
		if (
			payload["schema_version"] != "1.0.0"
			or payload["helper_version"] != "0.3.0"
			or payload["operation"] != "self_test"
			or payload["status"] != "ok"
			or type(payload["effective_uid"]) is not int
			or payload["effective_uid"] != 0
			or payload["effective_user"] != "root"
		):
			raise ValueError("Trusted Docker helper self-test did not confirm the expected root identity")
		return payload

	def _privilege_readiness(self, node):
		try:
			code, stdout, _stderr = run_remote_script(
				node,
				self._ssh_config(node),
				"docker",
				"check_docker_privilege_readiness.sh",
				{"EXPECTED_SSH_USER": node.ssh_user},
				"script_execution_log",
				{"execution": "check_docker_privilege_readiness.sh", "node": node.name},
				use_sudo=False,
				timeout_seconds=20,
				max_output_bytes=8192,
			)
			if code != 0:
				raise ValueError("Privilege readiness probe did not complete")
			readiness = self._validate_privilege_readiness(json.loads(stdout.strip()))
			if readiness["status"] == "Ready":
				helper_code, helper_stdout, _helper_stderr = run_trusted_helper(
					node,
					self._ssh_config(node),
					{"schema_version": "1.0.0", "operation": "self_test"},
					timeout_seconds=20,
					max_output_bytes=8192,
				)
				if helper_code != 0:
					raise ValueError("Trusted helper transport self-test did not complete")
				try:
					helper_response = json.loads(
						helper_stdout.strip(), object_pairs_hook=self._unique_json_object
					)
					self._validate_trusted_helper_self_test(helper_response)
				except (TypeError, ValueError) as exc:
					raise TrustedHelperExecutionError(
						"invalid_response",
						"Trusted helper returned an invalid response; remote execution outcome is uncertain.",
						may_have_executed=True,
					) from exc
			return readiness
		except Exception:
			return {
				"schema_version": "1.0.0",
				"observed_at": None,
				"status": "Incomplete",
				"execution_identity": None,
				"helper": {
					"path": "/usr/local/lib/dt-master/docker/docker_lifecycle_helper.py",
					"version": None,
					"sha256": None,
				},
				"sudoers": {"path": "/etc/sudoers.d/dt-master-docker", "syntax": "Invalid"},
				"checks": [],
				"diagnostics": [
					{
						"code": "DOCKER_PRIVILEGE_PROBE_INCOMPLETE",
						"severity": "Warning",
						"message": "Docker privilege readiness could not be fully inspected.",
					}
				],
			}

	def _privilege_check(self, request, execution_context=None):
		"""Check the trusted Docker helper and sudoers boundary without changing node state."""
		started = monotonic()
		node = self._node_for_request(request)
		self._require_privilege_bootstrap(node)
		readiness = deepcopy(self._privilege_readiness(node))
		if readiness["status"] == "Ready":
			observed_at = now_datetime()
			snapshot = self._management_snapshot(node)
			snapshot["privilege_readiness"] = {
				"status": "Ready",
				"operation_id": request.operation_id,
				"observed_at": str(observed_at),
			}
			frappe.db.set_value(
				"Infrastructure Node",
				node.name,
				{
					"docker_host_state": "Docker Inspection Required",
					"docker_host_management_snapshot": json.dumps(
						snapshot, sort_keys=True, separators=(",", ":")
					),
					"docker_host_operation": request.operation_id,
					"docker_host_state_updated_on": observed_at,
				},
				update_modified=False,
			)
		for item in readiness.get("diagnostics", []):
			item["message"] = sanitize_diagnostic_message(item["message"])
		status = readiness["status"]
		outcome = "succeeded" if status == "Ready" else ("failed" if status == "Not Ready" else "partial")
		diagnostics = tuple(
			{
				"code": item["code"],
				"severity": "error" if item["severity"] == "Error" else "warning",
				"message": sanitize_diagnostic_message(item["message"]),
			}
			for item in readiness.get("diagnostics", [])
		)
		return self.result_for(
			request,
			outcome=outcome,
			observed_state=status,
			capabilities=self.capabilities(),
			diagnostics=diagnostics,
			data={"privilege_readiness": readiness},
			duration_ms=min(86400000, int((monotonic() - started) * 1000)),
		)

	def _inspect(self, request, execution_context=None):
		from dt_master.api.infrastructure_node import (
			_docker_failure_snapshot,
			_validate_docker_snapshot,
		)

		node = self._node_for_request(request)
		self._require_privilege_verified(node)
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
				"Failed",
				"Docker inspection could not be completed over the configured SSH transport.",
				elapsed,
			)

		privilege_readiness = self._privilege_readiness(node)
		evaluation = evaluate_node_docker_readiness(node, snapshot)
		snapshot, evaluation = self._sanitize_observation_diagnostics(snapshot, evaluation)
		status = snapshot["inspection_status"]
		outcome = "succeeded" if status == "Successful" else ("partial" if status == "Partial" else "failed")
		data = {
			"inspection_status": status,
			"readiness": evaluation["readiness"],
			"readiness_evaluation": evaluation,
			"snapshot": snapshot,
			"privilege_readiness": privilege_readiness,
		}
		if request.method == "inspect" and status == "Successful":
			self._persist_inspection_evidence(node, request, data)
		return self.result_for(
			request,
			outcome=outcome,
			observed_state=snapshot["engine"]["availability"],
			capabilities=self.capabilities(),
			diagnostics=self._diagnostics(snapshot, evaluation),
			data=data,
			duration_ms=snapshot["duration_ms"],
		)

	@staticmethod
	def _persist_inspection_evidence(node, request, inspection):
		"""Persist only a complete standalone observation; internal inspections are projections only."""
		if inspection.get("inspection_status") != "Successful":
			return
		observed_at = now_datetime()
		management = DockerRuntimeAdapter._management_snapshot(node)
		management["docker_inspection"] = {
			"operation_id": request.operation_id,
			"correlation_id": request.correlation_id,
			"observed_at": str(observed_at),
			"inspection_status": inspection["inspection_status"],
			"readiness": inspection["readiness"],
		}
		frappe.db.set_value(
			"Infrastructure Node",
			node.name,
			{
				"docker_inspection_status": inspection["inspection_status"],
				"docker_readiness": inspection["readiness"],
				"docker_readiness_evaluation": json.dumps(
					inspection["readiness_evaluation"], sort_keys=True, separators=(",", ":")
				),
				"docker_inspected_on": observed_at,
				"docker_inspection_snapshot": json.dumps(
					inspection["snapshot"], sort_keys=True, separators=(",", ":")
				),
				"docker_host_management_snapshot": json.dumps(
					management, sort_keys=True, separators=(",", ":")
				),
			},
			update_modified=False,
		)

	@staticmethod
	def _requires_repository_preparation(plan):
		"""Return true only when every planner blocker is addressable by repository preparation."""
		plan_reasons = set(plan.get("reason_codes") or [])
		preparation_reasons = {
			"APT_METADATA_NOT_FRESH",
			"DOCKER_KEYRING_PATH_UNTRUSTED",
			"DOCKER_REPOSITORY_UNTRUSTED",
			"DOCKER_SIGNING_KEY_UNVERIFIED",
			"WORKLOAD_STATE_UNAVAILABLE",
		}
		package_candidate_reasons = {
			reason
			for reason in plan_reasons
			if reason.startswith("PACKAGE_CANDIDATE_UNAVAILABLE_OR_UNTRUSTED:")
		}
		known_preparation_blockers = preparation_reasons | package_candidate_reasons
		return plan.get("status") == "preparation_required" or (
			bool(plan.get("prerequisites"))
			and bool(plan_reasons)
			and plan_reasons.issubset(known_preparation_blockers)
			and bool(
				set(plan.get("prerequisites") or [])
				& {
					"configure_official_docker_repository",
					"refresh_apt_metadata",
					"verify_and_pin_docker_signing_key",
				}
			)
		)

	@staticmethod
	def _persist_install_plan_evidence(node, request, inspection, plan):
		"""Persist a validated plan only when it is bound to this successful inspection."""
		from dt_master.node_preparation.docker_install import validate_docker_engine_install_plan

		if (
			inspection.get("inspection_status") != "Successful"
			or inspection.get("readiness") != "Ready"
			or (inspection.get("privilege_readiness") or {}).get("status") != "Ready"
		):
			return False
		validate_docker_engine_install_plan(plan)
		if plan.get("status") not in {"preparation_required", "ready", "blocked", "no_op"}:
			raise ValueError("Docker Engine installation plan status is invalid.")
		observed_at = now_datetime()
		management = DockerRuntimeAdapter._management_snapshot(node)
		management["docker_inspection"] = {
			"operation_id": request.operation_id,
			"correlation_id": request.correlation_id,
			"observed_at": str(observed_at),
			"inspection_status": inspection["inspection_status"],
			"readiness": inspection["readiness"],
		}
		management["docker_install_plan"] = {
			"inspection_operation_id": request.operation_id,
			"inspection_correlation_id": request.correlation_id,
			"observed_at": str(observed_at),
			"status": plan["status"],
			"plan_digest": plan["plan_digest"],
			"state_fingerprint": plan["state_fingerprint"],
			"policy_version": plan["policy"]["version"],
		}
		updates = {
			"docker_inspection_status": inspection["inspection_status"],
			"docker_readiness": inspection["readiness"],
			"docker_readiness_evaluation": json.dumps(
				inspection["readiness_evaluation"], sort_keys=True, separators=(",", ":")
			),
			"docker_inspected_on": observed_at,
			"docker_inspection_snapshot": json.dumps(
				inspection["snapshot"], sort_keys=True, separators=(",", ":")
			),
			"docker_host_management_snapshot": json.dumps(management, sort_keys=True, separators=(",", ":")),
		}
		if DockerRuntimeAdapter._requires_repository_preparation(plan):
			updates["docker_host_state"] = "Repository Preparation Required"
		elif plan["status"] == "ready":
			updates["docker_host_state"] = "Ready for Install"
		if plan["status"] in {"preparation_required", "ready"}:
			updates["docker_host_operation"] = request.operation_id
			updates["docker_host_state_updated_on"] = observed_at
		frappe.db.set_value("Infrastructure Node", node.name, updates, update_modified=False)
		return True

	@staticmethod
	def _validate_update_check(payload):
		required = {
			"schema_version",
			"observed_at",
			"package_manager",
			"metadata",
			"packages",
			"overall_status",
		}
		if not isinstance(payload, dict) or set(payload) != required or payload["schema_version"] != "1.0.0":
			raise ValueError("Invalid Docker update-check contract")
		if payload["package_manager"] not in {"apt", "Unsupported"}:
			raise ValueError("Unsupported Docker update-check package manager")
		metadata = payload["metadata"]
		if not isinstance(metadata, dict) or set(metadata) != {"status", "age_seconds", "newest_index_at"}:
			raise ValueError("Invalid APT metadata freshness contract")
		if metadata["status"] not in {"Fresh", "Aging", "Stale", "Unavailable"}:
			raise ValueError("Invalid APT metadata freshness status")
		if metadata["age_seconds"] is not None and (
			not isinstance(metadata["age_seconds"], int) or metadata["age_seconds"] < 0
		):
			raise ValueError("Invalid APT metadata age")
		if not isinstance(payload["packages"], list) or len(payload["packages"]) > 5:
			raise ValueError("Invalid Docker package update rows")
		allowed = {
			"docker-ce",
			"docker-ce-cli",
			"containerd.io",
			"docker-buildx-plugin",
			"docker-compose-plugin",
		}
		for row in payload["packages"]:
			if not isinstance(row, dict) or set(row) != {
				"name",
				"installed_version",
				"candidate_version",
				"candidate_origin",
				"status",
			}:
				raise ValueError("Invalid Docker package update row")
			if row["name"] not in allowed or row["status"] not in {
				"Unavailable",
				"No Candidate",
				"Not Installed",
				"Up To Date",
				"Update Available",
			}:
				raise ValueError("Invalid Docker package update row values")
			for key in ("installed_version", "candidate_version", "candidate_origin"):
				if row[key] is not None and (not isinstance(row[key], str) or len(row[key]) > 300):
					raise ValueError("Invalid Docker package update string")
		if payload["overall_status"] not in {
			"Unsupported",
			"Unknown",
			"Updates Available",
			"Partial",
			"Up To Date",
			"Metadata Aging",
		}:
			raise ValueError("Invalid Docker update-check status")
		return payload

	def _updates_check(self, request, execution_context=None):
		started = monotonic()
		node = self._node_for_request(request)
		try:
			code, stdout, _stderr = run_remote_script(
				node,
				self._ssh_config(node),
				"docker",
				"check_docker_updates.sh",
				{},
				"script_execution_log",
				{"execution": "check_docker_updates.sh", "node": node.name},
				use_sudo=False,
				timeout_seconds=45,
				max_output_bytes=16384,
			)
			payload = self._validate_update_check(json.loads(stdout.strip())) if code == 0 else None
		except Exception:
			payload = None
		elapsed = min(86400000, int((monotonic() - started) * 1000))
		if payload is None:
			payload = {
				"schema_version": "1.0.0",
				"observed_at": None,
				"package_manager": "Unsupported",
				"metadata": {"status": "Unavailable", "age_seconds": None, "newest_index_at": None},
				"packages": [],
				"overall_status": "Unknown",
			}
			outcome = "failed"
		elif payload["overall_status"] in {"Unknown", "Unsupported", "Partial"}:
			outcome = "partial"
		else:
			outcome = "succeeded"
		return self.result_for(
			request,
			outcome=outcome,
			observed_state=payload["overall_status"],
			capabilities=self.capabilities(),
			data={"update_check": payload},
			duration_ms=elapsed,
		)

	def _bootstrap_privileges(self, request, execution_context=None):
		"""Install the fixed helper and sudoers policy through the existing SSH runner."""
		started = monotonic()
		node = self._node_for_request(request)
		self._require_host_inspection(node)
		if not node.ssh_user:
			raise ValueError("A configured SSH user is required for Docker privilege bootstrap")

		failure_details = []
		phase = "helper_install"

		def run_installer(script_name, step):
			code, stdout, stderr = run_remote_script(
				node,
				self._ssh_config(node),
				"docker",
				script_name,
				{},
				"script_execution_log",
				{"execution": script_name, "node": node.name},
				use_sudo=False,
				timeout_seconds=90,
				max_output_bytes=16384,
			)
			step.update(
				{
					"status": "completed" if code == 0 else "failed",
					"exit_code": code,
					"stdout": sanitize_diagnostic_message(stdout)[:1200] if stdout else None,
					"stderr": sanitize_diagnostic_message(stderr)[:1200] if stderr else None,
				}
			)
			if code != 0:
				failure_details.append(
					{
						"code": "DOCKER_PRIVILEGE_INSTALLER_FAILED",
						"phase": script_name,
						"exit_code": code,
						"stdout": step["stdout"],
						"stderr": step["stderr"],
					}
				)
			return step["status"]

		helper_install = {"script": "install_docker_privilege_helper.sh", "status": "not_started"}
		sudoers_install = {"script": "install_docker_sudoers.sh", "status": "skipped"}
		readiness = None
		try:
			helper_status = run_installer(helper_install["script"], helper_install)
		except Exception as exc:
			helper_status = "failed"
			helper_install.update(
				{
					"status": "failed",
					"exception_type": type(exc).__name__,
					"reason": sanitize_diagnostic_message(str(exc))[:500],
				}
			)
			failure_details.append(
				{
					"code": "DOCKER_PRIVILEGE_HELPER_INSTALL_EXCEPTION",
					"phase": phase,
					"exception_type": type(exc).__name__,
					"reason": helper_install["reason"],
				}
			)

		if helper_status == "completed":
			phase = "sudoers_install"
			sudoers_install["status"] = "not_started"
			try:
				sudoers_status = run_installer(sudoers_install["script"], sudoers_install)
			except Exception as exc:
				sudoers_status = "failed"
				sudoers_install.update(
					{
						"status": "failed",
						"exception_type": type(exc).__name__,
						"reason": sanitize_diagnostic_message(str(exc))[:500],
					}
				)
				failure_details.append(
					{
						"code": "DOCKER_PRIVILEGE_SUDOERS_INSTALL_EXCEPTION",
						"phase": phase,
						"exception_type": type(exc).__name__,
						"reason": sudoers_install["reason"],
					}
				)
		else:
			sudoers_status = "skipped"

		if helper_status == "completed" and sudoers_status == "completed":
			observed_at = now_datetime()
			snapshot = self._management_snapshot(node)
			snapshot["privilege_bootstrap"] = {
				"status": "Succeeded",
				"operation_id": request.operation_id,
				"observed_at": str(observed_at),
			}
			frappe.db.set_value(
				"Infrastructure Node",
				node.name,
				{
					"docker_host_management_snapshot": json.dumps(
						snapshot, sort_keys=True, separators=(",", ":")
					),
					"docker_host_operation": request.operation_id,
					"docker_host_state_updated_on": observed_at,
				},
				update_modified=False,
			)

		phase = "readiness_verification"
		try:
			code, stdout, stderr = run_remote_script(
				node,
				self._ssh_config(node),
				"docker",
				"check_docker_privilege_readiness.sh",
				{"EXPECTED_SSH_USER": node.ssh_user},
				"script_execution_log",
				{"execution": "check_docker_privilege_readiness.sh", "node": node.name},
				use_sudo=False,
				timeout_seconds=20,
				max_output_bytes=8192,
			)
			if code != 0:
				failure_details.append(
					{
						"code": "DOCKER_PRIVILEGE_READINESS_PROBE_FAILED",
						"phase": phase,
						"exit_code": code,
						"stdout": sanitize_diagnostic_message(stdout)[:1200] if stdout else None,
						"stderr": sanitize_diagnostic_message(stderr)[:1200] if stderr else None,
					}
				)
				raise ValueError("Docker privilege readiness probe returned a non-zero exit code")
			readiness = self._validate_privilege_readiness(json.loads(stdout.strip()))
		except Exception as exc:
			failure_details.append(
				{
					"code": "DOCKER_PRIVILEGE_READINESS_VERIFICATION_FAILED",
					"phase": phase,
					"exception_type": type(exc).__name__,
					"reason": sanitize_diagnostic_message(str(exc))[:500],
				}
			)
			return self.result_for(
				request,
				outcome="partial",
				observed_state="Incomplete",
				capabilities=self.capabilities(),
				diagnostics=tuple(
					{
						"code": item["code"],
						"severity": "error",
						"message": item.get("reason")
						or item.get("stderr")
						or "Privileged Docker bootstrap verification failed.",
					}
					for item in failure_details[:20]
				),
				data={
					"bootstrap_status": "Incomplete",
					"helper_install": helper_install,
					"sudoers_install": sudoers_install,
					"failure": {"phase": phase, "details": failure_details[:20]},
				},
				duration_ms=min(86400000, int((monotonic() - started) * 1000)),
			)

		steps_ready = helper_status == "completed" and sudoers_status == "completed"
		ready = steps_ready and readiness["status"] == "Ready"
		diagnostics = [
			{
				"code": item["code"],
				"severity": "error" if item["severity"] == "Error" else "warning",
				"message": sanitize_diagnostic_message(item["message"]),
			}
			for item in readiness.get("diagnostics", [])
		]
		for label, step in (("helper", helper_install), ("sudoers", sudoers_install)):
			if step["status"] == "failed":
				diagnostics.append(
					{
						"code": "DOCKER_PRIVILEGE_" + label.upper() + "_INSTALL_FAILED",
						"severity": "error",
						"message": step.get("stderr")
						or step.get("reason")
						or f"The Docker {label} installer failed (exit code {step.get('exit_code', 'unknown')}).",
					}
				)
		if not steps_ready:
			failure_details.append(
				{
					"code": "DOCKER_PRIVILEGE_BOOTSTRAP_STEP_FAILED",
					"phase": "installer_execution",
				}
			)
		return self.result_for(
			request,
			outcome="succeeded"
			if ready
			else ("failed" if readiness["status"] == "Not Ready" or not steps_ready else "partial"),
			observed_state="Ready"
			if ready
			else ("Not Ready" if readiness["status"] == "Not Ready" else "Incomplete"),
			capabilities=self.capabilities(),
			diagnostics=tuple(diagnostics[:100]),
			data={
				"bootstrap_status": "Succeeded" if ready else "Failed",
				"helper_install": helper_install,
				"sudoers_install": sudoers_install,
				"privilege_readiness": readiness,
				"failure": {"phase": "installer_execution", "details": failure_details[:20]}
				if failure_details
				else None,
			},
			duration_ms=min(86400000, int((monotonic() - started) * 1000)),
		)

	def _verify(self, request, execution_context=None):
		"""Verify Docker CLI, Engine and Compose v2 from one read-only inspection."""
		inspection = self._inspect(request, execution_context=execution_context)
		snapshot = inspection.data["snapshot"]
		cli = snapshot["docker_cli"]
		engine = snapshot["engine"]
		compose = snapshot["compose"]

		failures = []
		incomplete = []

		if snapshot["inspection_status"] in {"Failed", "Timed Out"}:
			incomplete.append(
				(
					"DOCKER_INSPECTION_INCOMPLETE",
					"Docker component state could not be verified because the underlying inspection failed.",
				)
			)
		else:
			if cli["installed"] is False:
				failures.append(("DOCKER_CLI_NOT_INSTALLED", "Docker CLI is not installed."))
			elif not cli["path"] or not cli["client_version"]:
				incomplete.append(
					(
						"DOCKER_CLI_DETAILS_INCOMPLETE",
						"Docker CLI installation details could not be verified.",
					)
				)

			if engine["availability"] in {"Not Installed", "Unavailable"}:
				failures.append(("DOCKER_ENGINE_UNAVAILABLE", "Docker Engine is not available."))
			elif engine["availability"] == "Unknown":
				incomplete.append(
					("DOCKER_ENGINE_STATUS_UNKNOWN", "Docker Engine availability could not be determined.")
				)
			elif not engine["server_version"]:
				incomplete.append(
					("DOCKER_ENGINE_VERSION_UNKNOWN", "Docker Engine server version could not be verified.")
				)

			if compose["installed"] is False or compose["compatibility"] == "Unavailable":
				failures.append(
					("DOCKER_COMPOSE_NOT_INSTALLED", "Docker Compose v2 plugin is not available.")
				)
			elif compose["compatibility"] == "Incompatible":
				failures.append(
					("DOCKER_COMPOSE_INCOMPATIBLE", "Docker Compose v2 is reported as incompatible.")
				)
			elif compose["compatibility"] == "Unknown" or not compose["version"]:
				incomplete.append(
					(
						"DOCKER_COMPOSE_STATUS_UNKNOWN",
						"Docker Compose v2 installation could not be fully verified.",
					)
				)

			if snapshot["inspection_status"] == "Partial":
				incomplete.append(
					("DOCKER_INSPECTION_INCOMPLETE", "The underlying Docker inspection was partial.")
				)

		diagnostics = list(inspection.diagnostics)
		diagnostics.extend(
			{"code": code, "severity": "error", "message": message} for code, message in failures
		)
		diagnostics.extend(
			{"code": code, "severity": "warning", "message": message} for code, message in incomplete
		)

		if failures:
			outcome, state = "failed", "Not Verified"
		elif incomplete:
			outcome, state = "partial", "Incomplete"
		else:
			outcome, state = "succeeded", "Verified"

		return self.result_for(
			request,
			outcome=outcome,
			observed_state=state,
			capabilities=self.capabilities(),
			diagnostics=tuple(diagnostics[:100]),
			data={
				"verification_status": state,
				"inspection_status": snapshot["inspection_status"],
				"components": {
					"docker_cli": {
						"installed": cli["installed"],
						"path": cli["path"],
						"client_version": cli["client_version"],
					},
					"engine": {
						"availability": engine["availability"],
						"server_version": engine["server_version"],
					},
					"compose": {
						"installed": compose["installed"],
						"version": compose["version"],
						"compatibility": compose["compatibility"],
					},
				},
			},
			duration_ms=inspection.duration_ms,
		)


	@staticmethod
	def _validate_repository_preparation_result(payload):
		"""Validate the trusted helper's closed repository-preparation result."""
		required = {"schema_version", "helper_version", "operation", "status", "diagnostics"}
		if not isinstance(payload, dict) or set(payload) != required:
			raise ValueError("Invalid Docker repository preparation result fields.")
		if (
			payload["schema_version"] != "1.0.0"
			or payload["helper_version"] != "0.3.0"
			or payload["operation"] != "prepare_repository"
			or payload["status"] != "succeeded"
			or payload["diagnostics"] != []
		):
			raise ValueError("Docker repository preparation did not satisfy the trusted helper contract.")
		return payload

	def _prepare_repository(self, request, execution_context=None):
		"""Prepare the official Docker APT repository as a separate durable mutation."""
		from dt_master.node_preparation.docker_install import (
			build_docker_engine_install_plan,
			validate_docker_engine_install_plan,
		)

		started = monotonic()
		node = self._node_for_request(request)
		helper_dispatched = False
		phase = "lifecycle_eligibility"
		try:
			if getattr(node, "docker_host_state", None) not in {
				"Docker Inspection Required",
				"Repository Preparation Required",
			}:
				raise ValueError("Infrastructure Node is not eligible for Docker repository preparation.")
			self._require_privilege_verified(node)
			if execution_context is not None:
				execution_context.heartbeat()
				execution_context.assert_leases()

			phase = "preparation_preflight"
			inspection = self._inspect(request, execution_context=execution_context)
			if inspection.data.get("inspection_status") != "Successful":
				raise ValueError("A complete fresh Docker inspection is required before repository preparation.")
			if inspection.data.get("readiness") != "Ready":
				raise ValueError("Docker host readiness must be Ready before repository preparation.")
			privilege = inspection.data.get("privilege_readiness") or {}
			if privilege.get("status") != "Ready":
				raise ValueError("Trusted Docker privilege readiness must be Ready.")
			snapshot = inspection.data.get("snapshot") or {}
			inventory = (snapshot.get("host_inventory") or {}).get("docker_install_inventory")
			if not isinstance(inventory, dict):
				raise ValueError("Docker Engine installation inventory is unavailable.")
			plan = build_docker_engine_install_plan(request.target_name, inventory)
			validate_docker_engine_install_plan(plan)
			if not self._requires_repository_preparation(plan):
				raise ValueError("Fresh Docker Engine plan does not authorize repository preparation.")

			phase = "repository_preparation_dispatch"
			if execution_context is not None:
				execution_context.heartbeat()
				execution_context.assert_leases()
			helper_dispatched = True
			code, stdout, stderr = run_trusted_helper(
				node,
				self._ssh_config(node),
				{"schema_version": "1.0.0", "operation": "prepare_repository"},
				timeout_seconds=420,
				max_output_bytes=4096,
				heartbeat=(
					lambda: (
						execution_context.heartbeat(),
						execution_context.assert_leases(),
					)
					if execution_context is not None
					else None
				),
			)
			try:
				helper_result = json.loads(stdout.strip(), object_pairs_hook=self._unique_json_object)
				self._validate_repository_preparation_result(helper_result)
			except (TypeError, ValueError) as exc:
				raise ValueError("Trusted Docker repository preparation returned an invalid result.") from exc
			if code != 0:
				raise ValueError("Trusted Docker repository preparation did not complete successfully.")

			phase = "post_preparation_inspection"
			if execution_context is not None:
				execution_context.heartbeat()
				execution_context.assert_leases()
			fresh = self._inspect(request, execution_context=execution_context)
			if fresh.data.get("inspection_status") != "Successful":
				raise ValueError("Fresh Docker inspection failed after repository preparation.")
			if fresh.data.get("readiness") != "Ready":
				raise ValueError("Fresh Docker host readiness is not Ready after repository preparation.")
			fresh_privilege = fresh.data.get("privilege_readiness") or {}
			if fresh_privilege.get("status") != "Ready":
				raise ValueError("Trusted Docker privilege readiness is not Ready after repository preparation.")
			fresh_snapshot = fresh.data.get("snapshot") or {}
			fresh_inventory = (fresh_snapshot.get("host_inventory") or {}).get("docker_install_inventory")
			if not isinstance(fresh_inventory, dict):
				raise ValueError("Fresh Docker installation inventory is unavailable after preparation.")
			fresh_plan = build_docker_engine_install_plan(request.target_name, fresh_inventory)
			validate_docker_engine_install_plan(fresh_plan)
			self._persist_install_plan_evidence(node, request, fresh.data, fresh_plan)
			ready = fresh_plan.get("status") == "ready"
			return self.result_for(
				request,
				outcome="succeeded" if ready else "partial",
				observed_state="Ready for Install" if ready else "Preparation Reconciliation Required",
				capabilities=self.capabilities(),
				diagnostics=() if ready else ({
					"code": "DOCKER_REPOSITORY_PREPARATION_REQUIRES_RECONCILIATION",
					"severity": "warning",
					"message": "Repository preparation ran, but the fresh installation plan is not ready.",
				},),
				data={
					"preparation_status": "succeeded",
					"fresh_install_plan_status": fresh_plan["status"],
					"fresh_plan_digest": fresh_plan["plan_digest"],
					"fresh_state_fingerprint": fresh_plan["state_fingerprint"],
					"engine_packages_installed": False,
				},
				duration_ms=min(86400000, int((monotonic() - started) * 1000)),
			)
		except Exception as exc:
			uncertain = helper_dispatched
			return self.result_for(
				request,
				outcome="partial" if uncertain else "failed",
				observed_state="Recovery Required" if uncertain else "Preparation Not Applied",
				capabilities=(),
				diagnostics=({
					"code": "DOCKER_REPOSITORY_PREPARATION_RECONCILIATION_REQUIRED"
					if uncertain else "DOCKER_REPOSITORY_PREPARATION_BLOCKED",
					"severity": "error",
					"message": "Repository preparation may have changed the node; fresh inspection is required."
					if uncertain else "Repository preparation preconditions were not satisfied.",
				},),
				data={
					"helper_dispatched": helper_dispatched,
					"failure": {
						"phase": phase,
						"exception_type": type(exc).__name__,
						"reason": sanitize_diagnostic_message(str(exc))[:500],
						"stderr": sanitize_diagnostic_message(locals().get("stderr", ""))[:1000],
					},
					"engine_packages_installed": False,
				},
				duration_ms=min(86400000, int((monotonic() - started) * 1000)),
			)


	def _install_engine(self, request, execution_context=None):
		"""Install Docker through the trusted helper using existing Node readiness."""
		from dt_master.node_preparation.docker_install import (
			build_docker_engine_install_plan,
			validate_docker_engine_install_plan,
		)

		started = monotonic()
		node = self._node_for_request(request)
		helper_dispatched = False
		phase = "initial_inspection"
		failure_detail = None
		try:
			if execution_context is not None:
				execution_context.heartbeat()
				execution_context.assert_leases()

			inspection = self._inspect(request, execution_context=execution_context)
			if inspection.data.get("inspection_status") != "Successful":
				raise ValueError("A complete fresh Docker inspection is required.")
			privilege = inspection.data.get("privilege_readiness") or {}
			if privilege.get("status") != "Ready":
				raise ValueError("The trusted Docker privilege helper is not ready on this node.")

			# Reuse configured Node readiness for host capacity requirements.
			# Engine/Compose/version requirements describe the desired post-install
			# state, so they must not prevent installing a missing Engine.
			readiness = inspection.data.get("readiness_evaluation") or {}
			host_requirements = {
				"min_cpu_cores",
				"min_total_memory_bytes",
				"min_available_memory_bytes",
				"min_available_storage_bytes",
			}
			resource_failures = [
				item
				for item in readiness.get("diagnostics", [])
				if item.get("severity") == "Error" and item.get("requirement") in host_requirements
			]
			if resource_failures:
				raise ValueError(
					"The node does not satisfy its configured Docker host resource requirements."
				)

			snapshot = inspection.data.get("snapshot") or {}
			host_inventory = snapshot.get("host_inventory") or {}
			install_inventory = host_inventory.get("docker_install_inventory")
			if not isinstance(install_inventory, dict):
				raise ValueError("Docker Engine installation inventory is unavailable.")

			# A healthy, trusted existing Engine is a successful no-op. Do this
			# before package metadata freshness checks; installation is unnecessary.
			package_rows = {
				row.get("name"): row for row in install_inventory.get("packages", []) if isinstance(row, dict)
			}
			engine_packages = ("docker-ce", "docker-ce-cli", "containerd.io", "docker-buildx-plugin")
			engine_already_installed = install_inventory.get("docker_engine_health") == "healthy" and all(
				package_rows.get(name, {}).get("dpkg_status") == "installed"
				and "download.docker.com" in (package_rows.get(name, {}).get("installed_origin") or "")
				for name in engine_packages
			)
			if engine_already_installed:
				return self.result_for(
					request,
					outcome="succeeded",
					observed_state="Already Installed",
					capabilities=self.capabilities(),
					data={
						"installation_status": "Already Installed",
						"postcondition_verified": True,
						"no_op": True,
					},
					duration_ms=min(86400000, int((monotonic() - started) * 1000)),
				)

			# The plan is generated and validated internally. It is not an
			# operator-supplied approval token or a separate UI workflow.
			phase = "install_plan_validation"
			plan = build_docker_engine_install_plan(request.target_name, install_inventory)
			validate_docker_engine_install_plan(plan)
			# Repository preparation is a separate authorized durable operation.
			# Engine installation must never mutate APT sources or signing keys.
			if plan.get("status") != "ready":
				failure_detail = {
					"code": "DOCKER_INSTALL_PLAN_NOT_READY",
					"plan_status": plan.get("status"),
					"reason_codes": plan.get("reason_codes", []),
					"prerequisites": plan.get("prerequisites", []),
					"package_candidates": [
						{
							"name": row.get("name"),
							"candidate_version": row.get("candidate_version"),
							"action": row.get("action"),
						}
						for row in plan.get("packages", [])
						if isinstance(row, dict)
					],
				}
				reasons = ", ".join(failure_detail["reason_codes"]) or "No planner reason codes supplied"
				raise ValueError(f"Docker installation plan status is '{plan.get('status')}': {reasons}")
			package_rows = plan.get("packages") or []
			if len(package_rows) != 4 or any(
				row.get("action") != "install" or not row.get("candidate_version") for row in package_rows
			):
				raise ValueError("The trusted Docker package set is not available for installation.")

			phase = "engine_install_dispatch"
			workload_impact = plan.get("workload_impact") or {}
			helper_request = {
				"schema_version": "1.0.0",
				"operation": "install_engine",
				"operation_id": request.operation_id,
				"target": {"doctype": request.target_doctype, "name": request.target_name},
				"plan": {
					"schema_version": plan["schema_version"],
					"policy_version": plan["policy"]["version"],
					"plan_digest": plan["plan_digest"],
					"packages": [
						{"name": row["name"], "version": row["candidate_version"]} for row in package_rows
					],
				},
				"expected_state_fingerprint": plan["state_fingerprint"],
				"operator_impact_approval": bool(workload_impact.get("operator_impact_approval_required")),
			}
			if execution_context is not None:
				execution_context.heartbeat()
				execution_context.assert_leases()
			helper_dispatched = True
			code, stdout, stderr = run_trusted_helper(
				node,
				self._ssh_config(node),
				helper_request,
				timeout_seconds=960,
				max_output_bytes=16384,
				heartbeat=(
					lambda: (
						execution_context.heartbeat(),
						execution_context.assert_leases(),
					)
					if execution_context is not None
					else None
				),
			)
			failure_detail = {
				"code": "DOCKER_ENGINE_HELPER_RESPONSE",
				"exit_code": code,
				"stderr": sanitize_diagnostic_message(stderr)[:1200],
			}
			helper_result = parse_trusted_install_engine_result(
				code, stdout, stderr, expected_operation_id=request.operation_id
			)
			phase = "postcondition_verification"
			return self._verify_install_engine_postcondition(
				request, helper_result, execution_context=execution_context
			)
		except TrustedHelperExecutionError as exc:
			uncertain = exc.may_have_executed
			failure = {
				"phase": phase,
				"exception_type": type(exc).__name__,
				"reason": sanitize_diagnostic_message(str(exc))[:500],
				"detail": failure_detail,
				"helper_error_code": getattr(exc, "code", None),
				"helper_dispatched": helper_dispatched,
			}
			return self.result_for(
				request,
				outcome="partial" if uncertain else "failed",
				observed_state="Recovery Required" if uncertain else "Install Not Applied",
				capabilities=(),
				diagnostics=(
					{
						"code": "DOCKER_INSTALL_OUTCOME_UNCERTAIN"
						if uncertain
						else "DOCKER_INSTALL_TRANSPORT_FAILED",
						"severity": "error",
						"message": "Docker installation requires fresh host reconciliation."
						if uncertain
						else "Docker installation could not be dispatched.",
					},
				),
				data={
					"helper_dispatched": helper_dispatched,
					"postcondition_verified": False,
					"failure": failure,
				},
				duration_ms=min(86400000, int((monotonic() - started) * 1000)),
			)
		except Exception as exc:
			uncertain = helper_dispatched
			failure = {
				"phase": phase,
				"exception_type": type(exc).__name__,
				"reason": sanitize_diagnostic_message(str(exc))[:500],
				"detail": failure_detail,
				"helper_dispatched": helper_dispatched,
			}
			return self.result_for(
				request,
				outcome="partial" if uncertain else "failed",
				observed_state="Recovery Required" if uncertain else "Install Not Applied",
				capabilities=(),
				diagnostics=(
					{
						"code": "DOCKER_INSTALL_RECONCILIATION_REQUIRED"
						if uncertain
						else (
							failure_detail.get("code") if failure_detail else "DOCKER_INSTALL_PRECHECK_FAILED"
						),
						"severity": "error",
						"message": "The helper may have changed the node; fresh inspection is required."
						if uncertain
						else (
							f"Docker installation plan blocked: {', '.join(failure_detail.get('reason_codes', []))}."
							if failure_detail and failure_detail.get("reason_codes")
							else "Docker installation preconditions were not satisfied."
						),
					},
				),
				data={
					"helper_dispatched": helper_dispatched,
					"postcondition_verified": False,
					"failure": failure,
				},
				duration_ms=min(86400000, int((monotonic() - started) * 1000)),
			)

	def _preflight(self, request, execution_context=None):
		"""Combine runtime readiness with a read-only, deterministic Engine install plan."""
		from dt_master.node_preparation.docker_install import build_docker_engine_install_plan

		node = self._node_for_request(request)
		inspection = self._inspect(request, execution_context=execution_context)
		readiness = inspection.data["readiness"]
		privilege = inspection.data["privilege_readiness"]["status"]
		snapshot = inspection.data.get("snapshot") or {}
		host_inventory = snapshot.get("host_inventory") or {}
		install_inventory = host_inventory.get("docker_install_inventory")
		install_plan = None
		plan_diagnostic = None
		if inspection.data.get("inspection_status") in {"Failed", "Timed Out"} or not isinstance(
			install_inventory, dict
		):
			plan_diagnostic = {
				"code": "DOCKER_ENGINE_INSTALL_INVENTORY_UNAVAILABLE",
				"severity": "warning",
				"message": "Docker Engine installation inventory was unavailable; no installation plan was produced.",
			}
		else:
			try:
				install_plan = build_docker_engine_install_plan(request.target_name, install_inventory)
				from dt_master.node_preparation.docker_install import validate_docker_engine_install_plan

				validate_docker_engine_install_plan(install_plan)
			except (TypeError, ValueError):
				install_plan = None
				plan_diagnostic = {
					"code": "DOCKER_ENGINE_INSTALL_PLAN_INVALID",
					"severity": "warning",
					"message": "Docker Engine installation inventory did not satisfy the planning contract.",
				}
		if inspection.data.get("inspection_status") == "Successful" and install_plan is None:
			self._persist_inspection_evidence(node, request, inspection.data)
		if (
			inspection.outcome == "failed"
			or readiness in {"Unknown", "Not Ready"}
			or privilege == "Not Ready"
		):
			outcome, state = "failed", "Blocked"
		elif readiness == "Degraded" or inspection.outcome == "partial" or privilege == "Incomplete":
			outcome, state = "partial", "Degraded"
		else:
			outcome, state = "succeeded", "Ready"
		privilege_diagnostics = tuple(
			{
				"code": item["code"],
				"severity": "error" if item["severity"] == "Error" else "warning",
				"message": sanitize_diagnostic_message(item["message"]),
			}
			for item in inspection.data["privilege_readiness"].get("diagnostics", [])
		)
		diagnostic_rows = list(inspection.diagnostics) + list(privilege_diagnostics)
		if plan_diagnostic:
			diagnostic_rows.append(plan_diagnostic)
		elif install_plan and install_plan["status"] in {"blocked", "preparation_required"}:
			diagnostic_rows.append(
				{
					"code": "DOCKER_ENGINE_INSTALL_PLAN_" + install_plan["status"].upper(),
					"severity": "warning",
					"message": "Docker Engine installation plan requires prerequisites or operator review.",
				}
			)
		diagnostics = tuple(diagnostic_rows[:100])
		if inspection.data.get("inspection_status") == "Successful" and install_plan is not None:
			self._persist_install_plan_evidence(node, request, inspection.data, install_plan)
		return self.result_for(
			request,
			outcome=outcome,
			observed_state=state,
			capabilities=self.capabilities(),
			diagnostics=diagnostics,
			data={
				"preflight_status": state,
				"inspection_status": inspection.data["inspection_status"],
				"readiness": readiness,
				"readiness_evaluation": inspection.data["readiness_evaluation"],
				"privilege_readiness": inspection.data["privilege_readiness"],
				"docker_engine_install_plan": install_plan,
				"docker_engine_install_plan_status": install_plan["status"]
				if install_plan
				else "unavailable",
			},
			duration_ms=inspection.duration_ms,
		)


def register_docker_adapter():
	"""Register the Docker adapter from trusted application initialization."""
	from dt_master.runtime_adapters.registry import register_adapter

	return register_adapter(DockerRuntimeAdapter())
