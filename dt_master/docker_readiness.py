"""Pure Docker readiness evaluation for Infrastructure Nodes.

The evaluator has no Frappe or I/O dependencies. Callers must supply a
validated requirements document and an inspection snapshot.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any


EVALUATOR_VERSION = "1.0.0"
REQUIREMENTS_VERSION = "1.0.0"
READINESS_STATES = {"Unknown", "Ready", "Degraded", "Not Ready"}
SEVERITIES = {"required", "recommended"}

# Each requirement maps to one observed value in the v1 inspection snapshot.
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
    "min_cpu_cores",
    "min_total_memory_bytes",
    "min_available_memory_bytes",
    "min_available_storage_bytes",
}
_VERSION_REQUIREMENTS = {"min_engine_version", "min_compose_version"}
_BOOLEAN_REQUIREMENTS = {"docker_engine_required", "docker_compose_required"}


class ReadinessContractError(ValueError):
    """Raised when a policy or snapshot violates the evaluator contract."""


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


def _validate_requirements(requirements: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(requirements, dict) or requirements.get("requirements_version") != REQUIREMENTS_VERSION:
        raise ReadinessContractError("Unsupported Docker readiness requirements version")
    if set(requirements) - {"requirements_version", "checks", "max_snapshot_age_seconds"}:
        raise ReadinessContractError("Unknown top-level requirements property")
    checks = requirements.get("checks")
    if not isinstance(checks, list):
        raise ReadinessContractError("Requirements checks must be a list")
    if not checks:
        raise ReadinessContractError("At least one readiness requirement must be configured")
    seen = set()
    normalized = []
    for check in checks:
        if not isinstance(check, dict) or set(check) != {"key", "severity", "expected"}:
            raise ReadinessContractError("Each check must contain exactly key, severity and expected")
        key, severity, expected = check["key"], check["severity"], check["expected"]
        if not isinstance(key, str) or key not in _REQUIREMENT_PATHS or key in seen:
            raise ReadinessContractError(f"Unknown or duplicate readiness requirement: {key}")
        if severity not in SEVERITIES:
            raise ReadinessContractError(f"Invalid severity for requirement: {key}")
        if key in _BOOLEAN_REQUIREMENTS and not isinstance(expected, bool):
            raise ReadinessContractError(f"{key} expected value must be boolean")
        if key in _NUMERIC_REQUIREMENTS and (
            isinstance(expected, bool) or not isinstance(expected, (int, float)) or expected <= 0
        ):
            raise ReadinessContractError(f"{key} expected value must be a positive number")
        if key in _VERSION_REQUIREMENTS:
            _version_tuple(expected, key)
        seen.add(key)
        normalized.append(check)
    max_age = requirements.get("max_snapshot_age_seconds")
    if max_age is not None and (
        isinstance(max_age, bool) or not isinstance(max_age, int) or max_age <= 0
    ):
        raise ReadinessContractError("max_snapshot_age_seconds must be a positive integer")
    return normalized


def validate_docker_readiness_requirements(requirements: dict[str, Any]) -> None:
    """Validate a requirements document stored directly on an Infrastructure Node."""
    _validate_requirements(requirements)


def _validate_snapshot(snapshot: dict[str, Any]) -> None:
    if not isinstance(snapshot, dict) or snapshot.get("schema_version") != "1.0.0":
        raise ReadinessContractError("Unsupported Docker inspection snapshot schema")
    if snapshot.get("inspection_status") not in {"Successful", "Partial", "Failed", "Timed Out"}:
        raise ReadinessContractError("Invalid inspection status")
    if snapshot.get("readiness") not in READINESS_STATES:
        raise ReadinessContractError("Invalid snapshot readiness value")
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
    """Evaluate configured Node requirements against one inspection snapshot.

    The snapshot's existing readiness value is deliberately ignored. Only the
    evaluator result is authoritative for persisted Docker readiness.
    """
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
    checks = _validate_requirements(requirements)

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
            "code": code,
            "severity": severity,
            "requirement": requirement,
            "observed": observed,
            "expected": expected,
            "message": message,
            "remediation": remediation,
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

    # Failed/partial snapshots do not distinguish observed false values from
    # fallback defaults, so their component values cannot prove a requirement.
    evidence_trustworthy = snapshot["inspection_status"] == "Successful" and not stale
    for check in checks:
        key, severity, expected = check["key"], check["severity"], check["expected"]
        if key in _BOOLEAN_REQUIREMENTS and expected is False:
            continue
        observed = _path_value(snapshot, _REQUIREMENT_PATHS[key])
        if not evidence_trustworthy or observed is None:
            add(
                "DOCKER_REQUIREMENT_EVIDENCE_MISSING", "Warning", key, observed, expected,
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
            if isinstance(observed, bool) or not isinstance(observed, (int, float)):
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
                "DOCKER_REQUIREMENT_EVIDENCE_INVALID", "Warning", key, observed, expected,
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
