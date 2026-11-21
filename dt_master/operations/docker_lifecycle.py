"""Typed contracts for Infrastructure Node Docker package lifecycle operations.

This module defines the request boundary only. It deliberately does not register
or execute lifecycle operations; handlers remain unavailable until the fixed
privilege boundary and package scripts have been implemented and accepted.
"""

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
MUTATING_OPERATIONS = ENGINE_MUTATIONS | COMPOSE_MUTATIONS
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
	payload = _object(payload, {"confirm", "expected_state_fingerprint"})
	if payload.get("confirm") is not True:
		raise ValueError("Docker installation requires explicit confirmation")
	return _normalized_fingerprint(payload)


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
	if operation_type in READ_ONLY_OPERATIONS:
		return _empty(payload)
	if operation_type.endswith(".install"):
		return _install(payload)
	if operation_type.endswith(".upgrade") or operation_type.endswith(".downgrade"):
		return _versioned_mutation(payload, require_version=True)
	if operation_type.endswith(".uninstall"):
		return _versioned_mutation(payload, require_version=False)
	raise ValueError("Docker lifecycle operation has no payload contract")


def docker_lifecycle_resource_lock_keys(target, operation):
	"""Serialize all Docker package/service mutations on one Infrastructure Node."""
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
