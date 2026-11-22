"""Pure Docker readiness evaluation for Infrastructure Nodes.

The evaluator has no Frappe or I/O dependencies. Callers supply normalized
requirements built from the typed Infrastructure Node fields and a snapshot.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any


EVALUATOR_VERSION = "1.1.0"
REQUIREMENTS_VERSION = "1.0.0"
SEVERITIES = {"Required", "Recommended"}

_REQUIREMENT_PATHS = {
    "docker_engine_required": ("engine", "availability"),
    "docker_compose_required": ("compose", "compatibility"),
    "min_cpu_cores": ("host", "cpu", "logical_cores"),
    "min_total_memory_bytes": ("host", "memory", "total_bytes"),
    "min_available_memory_bytes": ("host", "memory", "available_bytes"),
    "min_available_storage_bytes": ("storage", "filesystem", "available_bytes"),
    "min_engine_version": ("engine", "server_version"),
    "min_compose_version": ("compose", "version"),
}
_NUMERIC_REQUIREMENTS = {
    "min_cpu_cores", "min_total_memory_bytes",
    "min_available_memory_bytes", "min_available_storage_bytes",
}
_VERSION_REQUIREMENTS = {"min_engine_version", "min_compose_version"}
_BOOLEAN_REQUIREMENTS = {"docker_engine_required", "docker_compose_required"}

# Maps normalized evaluator keys to Infrastructure Node value/severity fields.
NODE_REQUIREMENT_FIELDS = {
    "docker_engine_required": ("docker_engine_required", "docker_engine_severity", "boolean"),
    "docker_compose_required": ("docker_compose_required", "docker_compose_severity", "boolean"),
    "min_cpu_cores": ("docker_min_cpu_cores", "docker_min_cpu_cores_severity", "number"),
    "min_total_memory_bytes": ("docker_min_total_memory_gib", "docker_min_total_memory_severity", "gib"),
    "min_available_memory_bytes": ("docker_min_available_memory_gib", "docker_min_available_memory_severity", "gib"),
    "min_available_storage_bytes": ("docker_min_available_storage_gib", "docker_min_available_storage_severity", "gib"),
    "min_engine_version": ("docker_min_engine_version", "docker_min_engine_version_severity", "version"),
    "min_compose_version": ("docker_min_compose_version", "docker_min_compose_version_severity", "version"),
}


class ReadinessContractError(ValueError):
    """Raised when requirements or a snapshot violate the evaluator contract."""


def _canonical_fingerprint(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _version_tuple(value: Any, label: str) -> tuple[int, ...]:
    if not isinstance(value, str) or not re.fullmatch(r"v?\d+(?:\.\d+){1,3}", value.strip()):
        raise ReadinessContractError(f"{label} must be a numeric dotted version")
    parts = tuple(int(part) for part in value.strip().removeprefix("v").split("."))
    return parts + (0,) * (4 - len(parts))


def _path_value(snapshot: dict[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = snapshot
    for part in path:
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def build_node_readiness_requirements(node: Any) -> dict[str, Any]:
    """Normalize individual typed Node fields into the evaluator's private contract."""
    checks = []
    for key, (value_field, severity_field, kind) in NODE_REQUIREMENT_FIELDS.items():
        value = node.get(value_field)
        if kind == "boolean":
            if not value:
                if node.get(severity_field):
                    raise ReadinessContractError(f"{severity_field} requires {value_field} to be enabled")
                continue
            expected = True
        else:
            # Frappe Int fields commonly expose an unset value as zero.
            if value in (None, "", 0):
                if node.get(severity_field):
                    raise ReadinessContractError(f"{severity_field} requires {value_field} to be configured")
                continue
            if kind == "gib":
                if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
                    raise ReadinessContractError(f"{value_field} must be a positive GiB value")
                expected = round(value * 1024**3)
            else:
                expected = value
        severity = node.get(severity_field)
        if severity not in SEVERITIES:
            raise ReadinessContractError(f"{severity_field} must be Required or Recommended")
        checks.append({"key": key, "severity": severity.lower(), "expected": expected})

    max_age = node.get("docker_max_snapshot_age_seconds")
    if max_age not in (None, "", 0):
        if isinstance(max_age, bool) or not isinstance(max_age, int) or max_age <= 0:
            raise ReadinessContractError("docker_max_snapshot_age_seconds must be a positive integer")

    if not checks:
        if max_age not in (None, "", 0):
            raise ReadinessContractError(
                "At least one readiness requirement must be configured before setting maximum inspection age"
            )
        return {}
    return {
        "requirements_version": REQUIREMENTS_VERSION,
        "checks": checks,
        **({"max_snapshot_age_seconds": max_age} if max_age else {}),
    }


def validate_docker_readiness_requirements(requirements: dict[str, Any]) -> None:
    """Validate the normalized requirements document."""
    if not isinstance(requirements, dict) or requirements.get("requirements_version") != REQUIREMENTS_VERSION:
        raise ReadinessContractError("Unsupported Docker readiness requirements version")
    if set(requirements) - {"requirements_version", "checks", "max_snapshot_age_seconds"}:
        raise ReadinessContractError("Unknown requirements property")
    checks = requirements.get("checks")
    if not isinstance(checks, list) or not checks:
        raise ReadinessContractError("At least one readiness requirement must be configured")
    seen = set()
    for check in checks:
        if not isinstance(check, dict) or set(check) != {"key", "severity", "expected"}:
            raise ReadinessContractError("Each check must contain exactly key, severity and expected")
        key, severity, expected = check["key"], check["severity"], check["expected"]
        if key not in _REQUIREMENT_PATHS or key in seen:
            raise ReadinessContractError(f"Unknown or duplicate readiness requirement: {key}")
        if severity not in {"required", "recommended"}:
            raise ReadinessContractError(f"Invalid severity for requirement: {key}")
        if key in _BOOLEAN_REQUIREMENTS and expected is not True:
            raise ReadinessContractError(f"{key} must be enabled when configured")
        if key in _NUMERIC_REQUIREMENTS and (
            isinstance(expected, bool) or not isinstance(expected, int) or expected <= 0
        ):
            raise ReadinessContractError(f"{key} must be a positive integer")
        if key in _VERSION_REQUIREMENTS:
            _version_tuple(expected, key)
        seen.add(key)
    max_age = requirements.get("max_snapshot_age_seconds")
    if max_age is not None and (
        isinstance(max_age, bool) or not isinstance(max_age, int) or max_age <= 0
    ):
        raise ReadinessContractError("max_snapshot_age_seconds must be a positive integer")


def _validate_snapshot(snapshot: dict[str, Any]) -> None:
    if not isinstance(snapshot, dict) or snapshot.get("schema_version") != "1.0.0":
        raise ReadinessContractError("Unsupported Docker inspection snapshot schema")
    if snapshot.get("inspection_status") not in {"Successful", "Partial", "Failed", "Timed Out"}:
        raise ReadinessContractError("Invalid inspection status")
    observed_at = snapshot.get("observed_at")
    if not isinstance(observed_at, str):
        raise ReadinessContractError("Snapshot observed_at must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReadinessContractError("Invalid snapshot observed_at timestamp") from exc
    if parsed.tzinfo is None:
        raise ReadinessContractError("Snapshot observed_at must include a timezone")


def evaluate_docker_readiness(
    requirements: dict[str, Any] | None,
    snapshot: dict[str, Any],
    *,
    evaluated_at: datetime | None = None,
) -> dict[str, Any]:
    """Evaluate normalized Node requirements against one inspection snapshot."""
    _validate_snapshot(snapshot)
    now = evaluated_at or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ReadinessContractError("evaluated_at must be timezone-aware")
    if requirements in (None, {}):
        return {
            "readiness": "Unknown",
            "evaluator_version": EVALUATOR_VERSION,
            "requirements_version": None,
            "requirements_fingerprint": None,
            "inspection_observed_at": snapshot["observed_at"],
            "evaluated_at": now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "diagnostics": [{
                "code": "DOCKER_READINESS_REQUIREMENTS_UNCONFIGURED",
                "severity": "Warning",
                "requirement": None,
                "observed": None,
                "expected": None,
                "message": "Docker readiness requirements have not been configured on this Infrastructure Node.",
                "remediation": "Configure the approved Node-level Docker readiness requirements.",
            }],
        }
    validate_docker_readiness_requirements(requirements)
    observed_at = datetime.fromisoformat(snapshot["observed_at"].replace("Z", "+00:00"))
    max_age = requirements.get("max_snapshot_age_seconds")
    stale = max_age is not None and (now - observed_at).total_seconds() > max_age
    diagnostics: list[dict[str, Any]] = []
    failures: list[str] = []
    unknown_required = False
    unknown_evidence = False
    recommended_failure = False

    def add(code: str, severity: str, requirement: str | None, observed: Any,
            expected: Any, message: str, remediation: str) -> None:
        diagnostics.append({
            "code": code, "severity": severity, "requirement": requirement,
            "observed": observed, "expected": expected,
            "message": message, "remediation": remediation,
        })

    if stale:
        add(
            "DOCKER_INSPECTION_STALE", "Warning", "max_snapshot_age_seconds",
            int((now - observed_at).total_seconds()), max_age,
            "The Docker inspection snapshot is older than the configured maximum age.",
            "Run a fresh Docker runtime inspection.",
        )
        unknown_required = True
        unknown_evidence = True

    evidence_trustworthy = snapshot["inspection_status"] == "Successful" and not stale
    for check in requirements["checks"]:
        key, severity, expected = check["key"], check["severity"], check["expected"]
        # Compose is a post-install requirement. Until the Engine is available,
        # absence of the Compose plugin is expected and is not readiness evidence.
        if (
            key in {"docker_compose_required", "min_compose_version"}
            and snapshot["engine"]["availability"] != "Available"
        ):
            continue
        observed = _path_value(snapshot, _REQUIREMENT_PATHS[key])
        if not evidence_trustworthy or observed is None:
            add(
                "DOCKER_REQUIREMENT_EVIDENCE_MISSING",
                "Error" if severity == "required" else "Warning",
                key, observed, expected,
                f"Reliable inspection evidence is unavailable for {key}.",
                "Obtain a successful, complete Docker inspection.",
            )
            unknown_evidence = True
            if severity == "required":
                unknown_required = True
            continue

        passed = False
        if key == "docker_engine_required":
            if observed == "Unknown":
                observed = None
            else:
                passed = observed == "Available"
        elif key == "docker_compose_required":
            if observed in {"Unknown", "Unavailable"}:
                observed = None
            else:
                passed = observed == "Compatible"
        elif key in _NUMERIC_REQUIREMENTS:
            if isinstance(observed, bool) or not isinstance(observed, int | float):
                observed = None
            else:
                passed = observed >= expected
        elif key in _VERSION_REQUIREMENTS:
            try:
                passed = _version_tuple(observed, key) >= _version_tuple(expected, key)
            except ReadinessContractError:
                observed = None

        if observed is None:
            add(
                "DOCKER_REQUIREMENT_EVIDENCE_INVALID",
                "Error" if severity == "required" else "Warning",
                key, observed, expected,
                f"Inspection returned an invalid value for {key}.",
                "Correct the inspection probe and run inspection again.",
            )
            unknown_evidence = True
            if severity == "required":
                unknown_required = True
            continue

        if not passed:
            add(
                "DOCKER_REQUIREMENT_NOT_MET",
                "Error" if severity == "required" else "Warning",
                key, observed, expected,
                f"Docker readiness requirement {key} was not satisfied.",
                "Review the Node capability or adjust the approved requirement.",
            )
            if severity == "required":
                failures.append(key)
            else:
                recommended_failure = True

    if failures:
        readiness = "Not Ready"
    elif unknown_required or unknown_evidence:
        readiness = "Unknown"
    elif recommended_failure:
        readiness = "Degraded"
    else:
        readiness = "Ready"

    return {
        "readiness": readiness,
        "evaluator_version": EVALUATOR_VERSION,
        "requirements_version": REQUIREMENTS_VERSION,
        "requirements_fingerprint": _canonical_fingerprint(requirements),
        "inspection_observed_at": snapshot["observed_at"],
        "evaluated_at": now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "diagnostics": diagnostics,
    }



def evaluate_node_docker_readiness(node: Any, snapshot: dict[str, Any] | None) -> dict[str, Any]:
    """Evaluate the latest evidence against this Node's configured requirements.

    Missing evidence is an explicit Unknown result. The inspection snapshot's
    own readiness value is never used as an authority.
    """
    now = datetime.now(timezone.utc)
    try:
        requirements = build_node_readiness_requirements(node)
        if snapshot is None:
            if requirements:
                validate_docker_readiness_requirements(requirements)
            configured = bool(requirements)
            return {
                "readiness": "Unknown",
                "evaluator_version": EVALUATOR_VERSION,
                "requirements_version": REQUIREMENTS_VERSION if configured else None,
                "requirements_fingerprint": _canonical_fingerprint(requirements) if configured else None,
                "inspection_observed_at": None,
                "evaluated_at": now.isoformat().replace("+00:00", "Z"),
                "diagnostics": [{
                    "code": (
                        "DOCKER_INSPECTION_EVIDENCE_UNAVAILABLE"
                        if configured else "DOCKER_READINESS_REQUIREMENTS_UNCONFIGURED"
                    ),
                    "severity": "Warning",
                    "requirement": None,
                    "observed": None,
                    "expected": None,
                    "message": (
                        "No usable Docker inspection snapshot is available for this Infrastructure Node."
                        if configured else
                        "Docker readiness requirements have not been configured on this Infrastructure Node."
                    ),
                    "remediation": (
                        "Run a Docker runtime inspection before relying on readiness."
                        if configured else
                        "Configure the approved Node-level Docker readiness requirements."
                    ),
                }],
            }
        return evaluate_docker_readiness(requirements, snapshot, evaluated_at=now)
    except ReadinessContractError as exc:
        return {
            "readiness": "Unknown",
            "evaluator_version": EVALUATOR_VERSION,
            "requirements_version": REQUIREMENTS_VERSION,
            "requirements_fingerprint": None,
            "inspection_observed_at": snapshot.get("observed_at") if isinstance(snapshot, dict) else None,
            "evaluated_at": now.isoformat().replace("+00:00", "Z"),
            "diagnostics": [{
                "code": "DOCKER_READINESS_EVALUATION_ERROR",
                "severity": "Error",
                "requirement": None,
                "observed": None,
                "expected": None,
                "message": f"Docker readiness could not be evaluated: {exc}",
                "remediation": "Correct the Node readiness requirements or inspection evidence, then reevaluate.",
            }],
        }
