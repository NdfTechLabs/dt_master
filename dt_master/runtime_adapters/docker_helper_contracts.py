"""Closed schemas for the trusted Docker lifecycle helper protocol.

Pure validation only. This module performs no I/O and does not enable helper
operations or package mutations.
"""

from __future__ import annotations

import re
from typing import Any

HELPER_REQUEST_SCHEMA_VERSION = "1.0.0"
HELPER_RESULT_SCHEMA_VERSION = "1.0.0"
HELPER_PLAN_SCHEMA_VERSION = "1.1.0"
HELPER_POLICY_VERSION = "1.1.0"
ENGINE_PACKAGES = (
	"docker-ce",
	"docker-ce-cli",
	"containerd.io",
	"docker-buildx-plugin",
)
HELPER_STATUSES = frozenset({"succeeded", "failed_before_mutation", "failed_after_mutation", "uncertain"})
HELPER_PHASES = frozenset(
	{
		"request_validation",
		"host_validation",
		"state_validation",
		"package_install",
		"result_verification",
	}
)
_PACKAGE_VERSION = re.compile(r"^(?:[0-9]+:)?[0-9][A-Za-z0-9.+~_-]{0,158}$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_DIAGNOSTIC_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


class DockerHelperContractError(ValueError):
	"""Invalid trusted Docker helper request or response."""


def _object(value: Any, required: set[str], label: str) -> dict:
	if not isinstance(value, dict) or set(value) != required:
		raise DockerHelperContractError(f"Invalid {label} fields")
	return value


def _digest(value: Any, label: str) -> None:
	if not isinstance(value, str) or not _SHA256.fullmatch(value):
		raise DockerHelperContractError(f"Invalid {label}")


def _identifier(value: Any, label: str) -> None:
	if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
		raise DockerHelperContractError(f"Invalid {label}")


def _packages(value: Any, *, result: bool) -> list[dict]:
	if not isinstance(value, list) or len(value) != len(ENGINE_PACKAGES):
		raise DockerHelperContractError("Invalid helper package set")
	normalized = []
	for expected, row in zip(ENGINE_PACKAGES, value, strict=True):
		fields = (
			{"name", "requested_version", "installed_version", "status"} if result else {"name", "version"}
		)
		row = _object(row, fields, "helper package row")
		if row["name"] != expected:
			raise DockerHelperContractError("Helper package order differs from policy")
		if result:
			requested = row["requested_version"]
			installed = row["installed_version"]
			if not isinstance(requested, str) or not _PACKAGE_VERSION.fullmatch(requested):
				raise DockerHelperContractError("Invalid requested helper package version")
			if installed is not None and (
				not isinstance(installed, str) or not _PACKAGE_VERSION.fullmatch(installed)
			):
				raise DockerHelperContractError("Invalid installed helper package version")
			if row["status"] not in {"installed", "already_installed", "not_attempted"}:
				raise DockerHelperContractError("Invalid helper package status")
			if row["status"] in {"installed", "already_installed"} and installed != requested:
				raise DockerHelperContractError("Installed helper package version does not match request")
		else:
			version = row["version"]
			if not isinstance(version, str) or not _PACKAGE_VERSION.fullmatch(version):
				raise DockerHelperContractError("Invalid exact package version")
		normalized.append(row)
	return normalized


def validate_install_engine_request(request: dict) -> dict:
	"""Validate the proposed closed install_engine helper request; never execute it."""
	request = _object(
		request,
		{
			"schema_version",
			"operation",
			"operation_id",
			"target",
			"plan",
			"expected_state_fingerprint",
			"operator_impact_approval",
		},
		"helper request",
	)
	if request["schema_version"] != HELPER_REQUEST_SCHEMA_VERSION:
		raise DockerHelperContractError("Unsupported helper request schema")
	if request["operation"] != "install_engine":
		raise DockerHelperContractError("Unsupported helper operation")
	_identifier(request["operation_id"], "operation identifier")
	target = _object(request["target"], {"doctype", "name"}, "helper target")
	if target["doctype"] != "Infrastructure Node":
		raise DockerHelperContractError("Invalid helper target doctype")
	_identifier(target["name"], "target name")
	plan = _object(
		request["plan"],
		{"schema_version", "policy_version", "plan_digest", "packages"},
		"helper plan",
	)
	if (
		plan["schema_version"] != HELPER_PLAN_SCHEMA_VERSION
		or plan["policy_version"] != HELPER_POLICY_VERSION
	):
		raise DockerHelperContractError("Unsupported helper plan schema or policy")
	_digest(plan["plan_digest"], "plan digest")
	_packages(plan["packages"], result=False)
	_digest(request["expected_state_fingerprint"], "expected state fingerprint")
	if type(request["operator_impact_approval"]) is not bool:
		raise DockerHelperContractError("Impact approval must be a boolean")
	return request


def validate_install_engine_result(result: dict, *, expected_operation_id: str) -> dict:
	"""Validate a bounded helper result; this is evidence, not operation success."""
	result = _object(
		result,
		{
			"schema_version",
			"helper_version",
			"operation",
			"operation_id",
			"status",
			"phase",
			"before_state_fingerprint",
			"after_state_fingerprint",
			"packages",
			"package_manager_exit_code",
			"daemon_action",
			"diagnostics",
		},
		"helper result",
	)
	if result["schema_version"] != HELPER_RESULT_SCHEMA_VERSION:
		raise DockerHelperContractError("Unsupported helper result schema")
	if not isinstance(result["helper_version"], str) or not re.fullmatch(
		r"[0-9]+\.[0-9]+\.[0-9]+", result["helper_version"]
	):
		raise DockerHelperContractError("Invalid helper version")
	if result["operation"] != "install_engine":
		raise DockerHelperContractError("Unexpected helper result operation")
	_identifier(result["operation_id"], "result operation identifier")
	if result["operation_id"] != expected_operation_id:
		raise DockerHelperContractError("Helper result operation correlation mismatch")
	if result["status"] not in HELPER_STATUSES or result["phase"] not in HELPER_PHASES:
		raise DockerHelperContractError("Invalid helper result status or phase")
	_digest(result["before_state_fingerprint"], "before-state fingerprint")
	_digest(result["after_state_fingerprint"], "after-state fingerprint")
	_packages(result["packages"], result=True)
	code = result["package_manager_exit_code"]
	if type(code) is not int or not 0 <= code <= 255:
		raise DockerHelperContractError("Invalid package-manager exit code")
	if result["daemon_action"] not in {"not_requested", "package_maintainer_scripts"}:
		raise DockerHelperContractError("Invalid daemon action")
	diagnostics = result["diagnostics"]
	if not isinstance(diagnostics, list) or len(diagnostics) > 20:
		raise DockerHelperContractError("Invalid helper diagnostics")
	for item in diagnostics:
		if not isinstance(item, str) or not _DIAGNOSTIC_CODE.fullmatch(item):
			raise DockerHelperContractError("Invalid helper diagnostic code")
	if result["status"] == "succeeded" and (
		code != 0
		or result["phase"] != "result_verification"
		or diagnostics
		or any(row["status"] not in {"installed", "already_installed"} for row in result["packages"])
	):
		raise DockerHelperContractError("Inconsistent successful helper result")
	if result["status"] == "failed_before_mutation" and any(
		row["status"] == "installed" for row in result["packages"]
	):
		raise DockerHelperContractError("Pre-mutation failure contains an installed package claim")
	if result["status"] == "failed_before_mutation" and result["phase"] in {
		"package_install",
		"result_verification",
	}:
		raise DockerHelperContractError("Pre-mutation failure has an invalid phase")
	if result["status"] in {"failed_after_mutation", "uncertain"} and result["phase"] not in {
		"package_install",
		"result_verification",
	}:
		raise DockerHelperContractError("Post-dispatch result has an invalid phase")
	return result
