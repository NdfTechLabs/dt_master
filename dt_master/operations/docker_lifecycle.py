"""Typed contracts for Infrastructure Node Docker lifecycle operations.

This module defines the request boundary only. Durable operation registration
lives in docker_lifecycle_registry; runtime execution lives in the Docker adapter.
"""

import json
import re

ENGINE_MUTATIONS = frozenset(
	{
		"host.node.docker.install",
		"host.node.docker.upgrade",
		"host.node.docker.downgrade",
		"host.node.docker.uninstall",
	}
)
COMPOSE_MUTATIONS = frozenset(
	{
		"host.node.docker.compose.install",
		"host.node.docker.compose.upgrade",
		"host.node.docker.compose.downgrade",
		"host.node.docker.compose.uninstall",
	}
)
PRIVILEGE_BOOTSTRAP_OPERATIONS = frozenset({"host.node.docker.privileges.bootstrap"})
REPOSITORY_PREPARATION_OPERATIONS = frozenset({"host.node.docker.repository.prepare"})
SETUP_RESTART_OPERATIONS = frozenset({"host.node.docker.setup.restart"})
MUTATING_OPERATIONS = (
	ENGINE_MUTATIONS | COMPOSE_MUTATIONS | PRIVILEGE_BOOTSTRAP_OPERATIONS | REPOSITORY_PREPARATION_OPERATIONS | SETUP_RESTART_OPERATIONS
)
READ_ONLY_OPERATIONS = frozenset(
	{
		"host.node.docker.privileges.check",
		"host.node.docker.inspect",
		"host.node.docker.preflight",
		"host.node.docker.updates.check",
		"host.node.docker.verify",
	}
)
DOCKER_LIFECYCLE_OPERATIONS = MUTATING_OPERATIONS | READ_ONLY_OPERATIONS

DOCKER_SETUP_CHECKPOINTS = {
	"connection": "Connection Required",
	"host_inspection": "Host Inspection Required",
	"privilege_helper": "Privilege Setup Required",
	"privilege_verification": "Privilege Setup Required",
	"docker_inspection": "Docker Inspection Required",
	"preflight": "Docker Inspection Required",
	"repository_preparation": "Repository Preparation Required",
	"engine_installation": "Ready for Install",
}

_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+:~_-]{0,79}$")
_FINGERPRINT = re.compile(r"^[a-f0-9]{64}$")


def _object(payload, allowed):
	if not isinstance(payload, dict):
		raise ValueError("Docker lifecycle payload must be an object")
	unknown = set(payload) - set(allowed)
	if unknown:
		raise ValueError("Unsupported Docker lifecycle payload field(s): " + ", ".join(sorted(unknown)))
	return payload


def _empty(payload):
	_object(payload, set())
	return {}


def _install(payload):
	payload = _object(payload, {"confirm"})
	if payload.get("confirm") is not True:
		raise ValueError("Docker installation requires explicit confirmation")
	return {"confirm": True}


def _versioned_mutation(payload, *, require_version):
	allowed = {"confirm", "expected_state_fingerprint"}
	if require_version:
		allowed.add("target_version")
	payload = _object(payload, allowed)
	if payload.get("confirm") is not True:
		raise ValueError("Docker package mutation requires explicit confirmation")
	normalized = _normalized_fingerprint(payload)
	if require_version:
		version = payload.get("target_version")
		if not isinstance(version, str) or not _VERSION.fullmatch(version):
			raise ValueError("A valid explicit target_version is required")
		normalized["target_version"] = version
	return normalized


def _normalized_fingerprint(payload):
	fingerprint = payload.get("expected_state_fingerprint")
	if fingerprint is not None and (
		not isinstance(fingerprint, str) or not _FINGERPRINT.fullmatch(fingerprint)
	):
		raise ValueError("expected_state_fingerprint must be a lowercase SHA-256 hex digest")
	return {
		"confirm": True,
		**({"expected_state_fingerprint": fingerprint} if fingerprint is not None else {}),
	}


def validate_docker_lifecycle_payload(operation_type, payload):
	"""Return a normalized payload for one known Docker lifecycle operation."""
	if operation_type not in DOCKER_LIFECYCLE_OPERATIONS:
		raise ValueError("Unsupported Docker lifecycle operation")
	if operation_type == "host.node.docker.setup.restart":
		payload = _object(payload, {"checkpoint"})
		checkpoint = payload.get("checkpoint")
		if checkpoint not in DOCKER_SETUP_CHECKPOINTS:
			raise ValueError("Unsupported Docker setup restart checkpoint")
		return {"checkpoint": checkpoint}
	if (
		operation_type in READ_ONLY_OPERATIONS
		or operation_type in PRIVILEGE_BOOTSTRAP_OPERATIONS
		or operation_type in REPOSITORY_PREPARATION_OPERATIONS
	):
		return _empty(payload)
	if operation_type == "host.node.docker.install":
		return _install(payload)
	if operation_type.endswith(".install"):
		return _install(payload)
	if operation_type.endswith(".upgrade") or operation_type.endswith(".downgrade"):
		return _versioned_mutation(payload, require_version=True)
	if operation_type.endswith(".uninstall"):
		return _versioned_mutation(payload, require_version=False)
	raise ValueError("Docker lifecycle operation has no payload contract")


def docker_lifecycle_resource_lock_keys(target, operation):
	"""Serialize Docker host mutations on one Infrastructure Node."""
	operation_type = getattr(operation, "operation_type", None)
	if operation_type not in MUTATING_OPERATIONS:
		raise ValueError("Only Docker lifecycle mutations acquire the Docker lifecycle lock")
	if getattr(target, "doctype", None) != "Infrastructure Node" or not getattr(target, "name", None):
		raise ValueError("Docker lifecycle lock requires an Infrastructure Node target")
	return [f"node-docker:{target.name}"]


def authorize_docker_lifecycle_node(target):
	"""Re-authorize a System Manager and require a verified Node SSH identity."""
	import frappe
	from frappe import _

	frappe.only_for("System Manager")
	if getattr(target, "doctype", None) != "Infrastructure Node":
		frappe.throw(
			_("Docker lifecycle operations require an Infrastructure Node target."), frappe.PermissionError
		)
	target.check_permission("read")
	if not getattr(target, "access_verified", False):
		frappe.throw(_("Infrastructure Node SSH access must be verified first."), frappe.PermissionError)


def authorize_docker_setup_restart(target):
	"""Authorize checkpoint recovery without requiring an already verified SSH session."""
	import frappe
	from frappe import _

	frappe.only_for("System Manager")
	if getattr(target, "doctype", None) != "Infrastructure Node":
		frappe.throw(_("Docker setup recovery requires an Infrastructure Node target."), frappe.PermissionError)
	target.check_permission("read")
	if not (getattr(target, "ssh_host_alias", None) or (getattr(target, "ip_address", None) and getattr(target, "ssh_user", None))):
		frappe.throw(_("Configure SSH access before restarting Docker setup."), frappe.ValidationError)


def docker_setup_restart_updates(node, checkpoint):
	"""Return the bounded projection changes needed to restart at a setup checkpoint."""
	if checkpoint not in DOCKER_SETUP_CHECKPOINTS:
		raise ValueError("Unsupported Docker setup restart checkpoint")
	try:
		management = json.loads(getattr(node, "docker_host_management_snapshot", None) or "{}")
	except (TypeError, ValueError):
		management = {}
	if not isinstance(management, dict):
		management = {}

	host_inspection = management.get("host_inspection") or {}
	bootstrap = management.get("privilege_bootstrap") or {}
	privilege = management.get("privilege_readiness") or {}
	plan = management.get("docker_install_plan") or {}
	if checkpoint != "connection" and not getattr(node, "access_verified", False):
		raise ValueError("Verified SSH access is required for this restart checkpoint.")
	if checkpoint in {"privilege_helper", "privilege_verification", "docker_inspection", "preflight", "repository_preparation", "engine_installation"}:
		if host_inspection.get("inspection_status") != "Successful" or host_inspection.get("os_support_status") != "Supported":
			raise ValueError("A successful supported Host inspection is required for this restart checkpoint.")
	if checkpoint in {"privilege_verification", "docker_inspection", "preflight", "repository_preparation", "engine_installation"}:
		if bootstrap.get("status") != "Succeeded":
			raise ValueError("Docker privilege helper installation must be complete before this restart checkpoint.")
	if checkpoint in {"docker_inspection", "preflight", "repository_preparation", "engine_installation"}:
		if privilege.get("status") != "Ready":
			raise ValueError("Docker privilege readiness must be Ready before this restart checkpoint.")
	if checkpoint == "preflight" and getattr(node, "docker_inspection_status", None) != "Successful":
		raise ValueError("A successful Docker runtime inspection is required before restarting preflight.")
	if checkpoint == "repository_preparation" and plan.get("status") not in {"blocked", "preparation_required"}:
		raise ValueError("A current repository-preparation plan is required for this checkpoint.")
	if checkpoint == "engine_installation" and plan.get("status") not in {"ready", "no_op"}:
		raise ValueError("A ready Docker install plan is required for the Engine installation checkpoint.")

	invalidated = {
		"connection": ("host_inspection", "privilege_bootstrap", "privilege_readiness", "docker_inspection", "docker_install_plan"),
		"host_inspection": ("host_inspection", "privilege_bootstrap", "privilege_readiness", "docker_inspection", "docker_install_plan"),
		"privilege_helper": ("privilege_bootstrap", "privilege_readiness", "docker_inspection", "docker_install_plan"),
		"privilege_verification": ("privilege_readiness", "docker_inspection", "docker_install_plan"),
		"docker_inspection": ("docker_inspection", "docker_install_plan"),
		"preflight": ("docker_install_plan",),
		"repository_preparation": (),
		"engine_installation": (),
	}[checkpoint]
	for key in invalidated:
		management.pop(key, None)
	if checkpoint == "connection":
		management.pop("connection_status", None)
		management.pop("connection_operation_id", None)
		management.pop("operation_id", None)

	from frappe.utils import now_datetime

	updates = {
		"docker_host_management_snapshot": json.dumps(management, sort_keys=True, separators=(",", ":")),
		"docker_host_state": DOCKER_SETUP_CHECKPOINTS[checkpoint],
		"docker_host_operation": None,
		"docker_host_state_updated_on": now_datetime(),
	}
	if checkpoint == "connection":
		updates["access_verified"] = 0
	if checkpoint in {"connection", "host_inspection", "privilege_helper", "privilege_verification", "docker_inspection", "preflight"}:
		updates.update({
			"docker_inspection_status": "Not Inspected",
			"docker_readiness": "Unknown",
			"docker_readiness_evaluation": None,
			"docker_inspected_on": None,
			"docker_inspection_snapshot": None,
		})
	return updates



def docker_setup_restart_lock_keys(target, operation):
	"""Fence setup recovery against both Host and Docker observations/mutations."""
	if getattr(target, "doctype", None) != "Infrastructure Node" or not getattr(target, "name", None):
		raise ValueError("Docker setup restart lock requires an Infrastructure Node target")
	if getattr(operation, "operation_type", None) != "host.node.docker.setup.restart":
		raise ValueError("Setup restart locks are reserved for the checkpoint restart operation")
	return [f"node-docker:{target.name}", f"node-host-observation:{target.name}"]
