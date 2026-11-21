"""Independent Docker Engine installation postcondition evaluation.

Pure validation only: no Frappe, SSH, filesystem, package-manager or Docker I/O.
"""

from __future__ import annotations

from dt_master.node_preparation.docker_install import (
	ENGINE_PACKAGES,
	OFFICIAL_REPOSITORY,
	DockerInstallPlanError,
	build_docker_engine_install_plan,
	docker_engine_install_state_fingerprint,
)
from dt_master.runtime_adapters.docker_helper_contracts import (
	DockerHelperContractError,
	validate_install_engine_result,
)

VERIFICATION_SCHEMA_VERSION = "1.0.0"


def evaluate_docker_engine_install_postcondition(
	node_name: str,
	inventory: dict,
	helper_result: dict,
	*,
	expected_operation_id: str,
) -> dict:
	"""Evaluate fresh host inventory against validated helper evidence.

	A verified result requires exact requested package versions, trusted installed
	origins, clean dpkg state, no package-family conflict/partial install, and a
	healthy Docker Engine. Incomplete observations never become success.
	"""
	try:
		helper = validate_install_engine_result(
			helper_result,
			expected_operation_id=expected_operation_id,
		)
	except (DockerHelperContractError, TypeError, ValueError):
		return _result(
			"incomplete",
			"DOCKER_INSTALL_HELPER_EVIDENCE_INVALID",
			"Trusted helper evidence could not be validated.",
			packages=[],
			services=None,
			state_fingerprint=None,
			node_name=node_name,
		)

	if helper["status"] != "succeeded":
		return _result(
			"failed" if helper["status"] == "failed_before_mutation" else "incomplete",
			"DOCKER_INSTALL_HELPER_NOT_SUCCESSFUL",
			"Trusted helper did not report a completed package action.",
			packages=[],
			services=None,
			state_fingerprint=None,
		)

	try:
		plan = build_docker_engine_install_plan(node_name, inventory)
		state_fingerprint = docker_engine_install_state_fingerprint(inventory)
	except (DockerInstallPlanError, TypeError, ValueError):
		return _result(
			"incomplete",
			"DOCKER_INSTALL_POSTCONDITION_INVENTORY_INVALID",
			"Fresh Docker Engine inventory did not satisfy the accepted inventory contract.",
			packages=[],
			services=None,
			state_fingerprint=None,
		)

	rows = {row["name"]: row for row in inventory["packages"]}
	helper_rows = {row["name"]: row for row in helper["packages"]}
	package_results = []
	failures = []
	incomplete = []

	for name in ENGINE_PACKAGES:
		observed = rows.get(name)
		helper_row = helper_rows.get(name)
		if observed is None or helper_row is None:
			incomplete.append("DOCKER_INSTALL_PACKAGE_EVIDENCE_MISSING")
			continue

		installed_version = observed["installed_version"]
		origin = observed["installed_origin"]
		status = observed["dpkg_status"]
		requested_version = helper_row["requested_version"]
		if status != "installed" or not installed_version or not origin:
			incomplete.append("DOCKER_INSTALL_PACKAGE_NOT_VERIFIABLE")
			row_status = "unverified"
		elif (
			installed_version != requested_version
			or helper_row["installed_version"] != requested_version
			or OFFICIAL_REPOSITORY not in origin
		):
			failures.append("DOCKER_INSTALL_PACKAGE_POSTCONDITION_MISMATCH")
			row_status = "mismatch"
		else:
			row_status = "verified"
		package_results.append(
			{
				"name": name,
				"requested_version": requested_version,
				"observed_version": installed_version,
				"status": row_status,
			}
		)

	reason_codes = set(plan["reason_codes"])
	if reason_codes.intersection(
		{
			"CONFLICTING_PACKAGE_FAMILY",
			"PARTIAL_DOCKER_CE_INSTALLATION",
			"EXISTING_DOCKER_ENGINE_UNHEALTHY",
			"EXISTING_PACKAGE_ORIGIN_UNVERIFIED",
		}
	):
		failures.append("DOCKER_INSTALL_HOST_POSTCONDITION_MISMATCH")
	if inventory["package_manager"] != "apt" or not inventory["package_manager_state"]["dpkg_audit_clean"]:
		failures.append("DOCKER_INSTALL_PACKAGE_MANAGER_UNHEALTHY")
	if not plan["repository"]["signing_key_policy"]["trusted"]:
		failures.append("DOCKER_INSTALL_SIGNING_KEY_UNTRUSTED")
	if inventory["package_manager_state"]["locked"]:
		incomplete.append("DOCKER_INSTALL_PACKAGE_MANAGER_BUSY")
	for service_name, service_state in inventory["services"].items():
		if service_state in {"inactive", "failed"}:
			failures.append("DOCKER_INSTALL_REQUIRED_SERVICE_INACTIVE")
		elif service_state in {"Unknown", "activating", "deactivating"}:
			incomplete.append("DOCKER_INSTALL_REQUIRED_SERVICE_STATE_UNKNOWN")
	if inventory["docker_engine_health"] != "healthy":
		if inventory["docker_engine_health"] in {"unhealthy", "not_installed"}:
			failures.append("DOCKER_INSTALL_ENGINE_UNHEALTHY")
		else:
			incomplete.append("DOCKER_INSTALL_ENGINE_HEALTH_UNKNOWN")

	if failures:
		status = "failed"
		code = sorted(set(failures))[0]
		message = "Fresh host observations do not match the requested Docker Engine installation."
	elif incomplete:
		status = "incomplete"
		code = sorted(set(incomplete))[0]
		message = "Fresh host observations are insufficient to verify the Docker Engine installation."
	else:
		status = "verified"
		code = None
		message = None

	return {
		"schema_version": VERIFICATION_SCHEMA_VERSION,
		"status": status,
		"target": {"doctype": "Infrastructure Node", "name": node_name},
		"packages": package_results,
		"services": dict(inventory["services"]),
		"docker_engine_health": inventory["docker_engine_health"],
		"state_fingerprint": state_fingerprint,
		"diagnostics": [] if code is None else [{"code": code, "message": message}],
	}


def _result(status, code, message, *, packages, services, state_fingerprint, node_name):
	return {
		"schema_version": VERIFICATION_SCHEMA_VERSION,
		"status": status,
		"target": {"doctype": "Infrastructure Node", "name": node_name},
		"packages": packages,
		"services": services,
		"docker_engine_health": "Unknown",
		"state_fingerprint": state_fingerprint,
		"diagnostics": [{"code": code, "message": message}],
	}
