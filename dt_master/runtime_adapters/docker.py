"""Docker runtime adapter for Infrastructure Nodes.

The adapter reuses the durable operation bridge and trusted helper for the
initial exact-version Docker Engine installation, alongside read-only
observations and the existing constrained privilege bootstrap.
"""

import json
from copy import deepcopy
from time import monotonic

import frappe

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
		return ("bootstrap_privileges", "install_engine", "inspect", "preflight", "updates_check", "verify", "privilege_check")

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
			or payload["helper_version"] != "0.2.0"
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
		readiness = deepcopy(self._privilege_readiness(node))
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
		if not node.ssh_user:
			raise ValueError("A configured SSH user is required for Docker privilege bootstrap")

		def run_installer(script_name):
			code, _stdout, _stderr = run_remote_script(
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
			return "completed" if code == 0 else "failed"

		helper_status = "failed"
		sudoers_status = "skipped"
		try:
			helper_status = run_installer("install_docker_privilege_helper.sh")
		except Exception:
			helper_status = "failed"
		if helper_status == "completed":
			try:
				sudoers_status = run_installer("install_docker_sudoers.sh")
			except Exception:
				sudoers_status = "failed"

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
				raise ValueError("Docker privilege readiness probe failed")
			readiness = self._validate_privilege_readiness(json.loads(stdout.strip()))
		except Exception:
			return self.result_for(
				request,
				outcome="partial",
				observed_state="Incomplete",
				capabilities=self.capabilities(),
				diagnostics=(
					{
						"code": "DOCKER_PRIVILEGE_BOOTSTRAP_INCOMPLETE",
						"severity": "warning",
						"message": "The Docker privilege bootstrap did not return a valid verification result.",
					},
				),
				data={
					"bootstrap_status": "Incomplete",
					"helper_install": helper_status,
					"sudoers_install": sudoers_status,
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
		if helper_status != "completed":
			diagnostics.append(
				{
					"code": "DOCKER_PRIVILEGE_HELPER_INSTALL_FAILED",
					"severity": "error",
					"message": "The trusted Docker helper installer did not complete successfully.",
				}
			)
		if sudoers_status != "completed":
			diagnostics.append(
				{
					"code": "DOCKER_PRIVILEGE_SUDOERS_INSTALL_FAILED",
					"severity": "error",
					"message": "The scoped Docker sudoers installer did not complete successfully.",
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
				"helper_install": helper_status,
				"sudoers_install": sudoers_status,
				"privilege_readiness": readiness,
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


	def _install_engine(self, request, execution_context=None):
		"""Install the exact Docker CE package set through the existing trusted helper."""
		from dt_master.node_preparation.docker_install import (
			build_docker_engine_install_plan,
			validate_docker_engine_install_plan,
		)

		started = monotonic()
		node = self._node_for_request(request)
		helper_dispatched = False
		try:
			if execution_context is not None:
				execution_context.heartbeat()
				execution_context.assert_leases()

			inspection = self._inspect(request, execution_context=execution_context)
			if inspection.data.get("inspection_status") != "Successful":
				raise ValueError("A complete fresh Docker installation inventory is required.")
			privilege = inspection.data.get("privilege_readiness") or {}
			if privilege.get("status") != "Ready":
				raise ValueError("The existing Docker privilege helper is not ready on this node.")
			snapshot = inspection.data.get("snapshot") or {}
			host_inventory = snapshot.get("host_inventory") or {}
			install_inventory = host_inventory.get("docker_install_inventory")
			if not isinstance(install_inventory, dict):
				raise ValueError("Docker Engine installation inventory is unavailable.")

			plan = build_docker_engine_install_plan(request.target_name, install_inventory)
			validate_docker_engine_install_plan(plan)
			if (
				plan.get("plan_digest") != request.payload.get("plan_digest")
				or plan.get("state_fingerprint") != request.payload.get("expected_state_fingerprint")
			):
				raise ValueError("The Docker installation plan changed; run preflight again.")
			if plan["status"] == "no_op":
				return self.result_for(
					request,
					outcome="succeeded",
					observed_state="Already Installed",
					capabilities=self.capabilities(),
					data={
						"installation_status": "Already Installed",
						"plan_digest": plan["plan_digest"],
						"state_fingerprint": plan["state_fingerprint"],
						"postcondition_verified": True,
						"no_op": True,
					},
					duration_ms=min(86400000, int((monotonic() - started) * 1000)),
				)
			if plan["status"] != "ready":
				raise ValueError("The Docker installation plan is not ready for execution.")
			package_rows = plan.get("packages") or []
			if len(package_rows) != 4 or any(
				row.get("action") != "install" or not row.get("candidate_version")
				for row in package_rows
			):
				raise ValueError("The Docker installation plan does not contain the exact four-package install set.")

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
						{"name": row["name"], "version": row["candidate_version"]}
						for row in package_rows
					],
				},
				"expected_state_fingerprint": plan["state_fingerprint"],
				"operator_impact_approval": request.payload["operator_impact_approval"],
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
			helper_result = parse_trusted_install_engine_result(
				code, stdout, stderr, expected_operation_id=request.operation_id
			)
			return self._verify_install_engine_postcondition(
				request, helper_result, execution_context=execution_context
			)
		except TrustedHelperExecutionError as exc:
			uncertain = exc.may_have_executed
			return self.result_for(
				request,
				outcome="partial" if uncertain else "failed",
				observed_state="Recovery Required" if uncertain else "Install Not Applied",
				capabilities=(),
				diagnostics=(
					{
						"code": "DOCKER_INSTALL_OUTCOME_UNCERTAIN" if uncertain else "DOCKER_INSTALL_TRANSPORT_FAILED",
						"severity": "error",
						"message": "Docker installation requires fresh host reconciliation."
						if uncertain else "Docker installation could not be dispatched.",
					},
				),
				data={"helper_dispatched": helper_dispatched, "postcondition_verified": False},
				duration_ms=min(86400000, int((monotonic() - started) * 1000)),
			)
		except Exception:
			uncertain = helper_dispatched
			return self.result_for(
				request,
				outcome="partial" if uncertain else "failed",
				observed_state="Recovery Required" if uncertain else "Install Not Applied",
				capabilities=(),
				diagnostics=(
					{
						"code": "DOCKER_INSTALL_RECONCILIATION_REQUIRED" if uncertain else "DOCKER_INSTALL_PRECHECK_FAILED",
						"severity": "error",
						"message": "The helper may have changed the node; fresh inspection is required."
						if uncertain else "Docker installation preconditions were not satisfied.",
					},
				),
				data={"helper_dispatched": helper_dispatched, "postcondition_verified": False},
				duration_ms=min(86400000, int((monotonic() - started) * 1000)),
			)

	def _preflight(self, request, execution_context=None):
		"""Combine runtime readiness with a read-only, deterministic Engine install plan."""
		from dt_master.node_preparation.docker_install import build_docker_engine_install_plan

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
			except (TypeError, ValueError):
				plan_diagnostic = {
					"code": "DOCKER_ENGINE_INSTALL_PLAN_INVALID",
					"severity": "warning",
					"message": "Docker Engine installation inventory did not satisfy the planning contract.",
				}
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
