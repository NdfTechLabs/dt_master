"""Versioned baseline preparation policy and deterministic plan contract.

NP.2 is deliberately plan-only. It evaluates existing observations and emits
server-owned verification steps; it does not install packages, alter services,
change Docker configuration, or relocate Docker storage.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any


POLICY_ID = "infrastructure-node-baseline"
POLICY_VERSION = "1.0.0"
PLAN_SCHEMA_VERSION = "1.0.0"

SUPPORTED_HOSTS = frozenset(
    {
        ("ubuntu", "22.04", "amd64"),
        ("ubuntu", "24.04", "amd64"),
        ("debian", "12", "amd64"),
    }
)

# These are verification identifiers, not shell commands or executable scripts.
# A later package must separately approve any mutating action catalogue.
VERIFICATION_ACTIONS = (
    ("verify_host_support", "host"),
    ("verify_host_capacity_evidence", "host"),
    ("verify_docker_engine", "docker"),
    ("verify_docker_compose", "docker"),
    ("verify_docker_storage", "docker"),
    ("verify_execution_identity", "host"),
)

_SECRET_PARTS = ("password", "token", "secret", "credential", "private_key")
_FORBIDDEN_KEYS = frozenset(
    {
        "command",
        "shell_command",
        "script",
        "script_path",
        "ssh_host",
        "ip_address",
        "private_key",
        "docker_host",
        "docker_context",
    }
)


class BaselinePolicyError(ValueError):
    """Invalid baseline policy input or plan contract."""


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _validate_safe_observation(value: Any, path: str = "observation") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise BaselinePolicyError(f"{path} keys must be strings")
            normalized = key.lower()
            if normalized in _FORBIDDEN_KEYS or any(part in normalized for part in _SECRET_PARTS):
                raise BaselinePolicyError(f"Sensitive or executable input is not permitted at {path}.{key}")
            _validate_safe_observation(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _validate_safe_observation(item, f"{path}[{index}]")
    elif value is not None and not isinstance(value, (str, bool, int, float)):
        raise BaselinePolicyError(f"{path} must contain JSON-compatible values")
    elif isinstance(value, float) and (value != value or abs(value) == float("inf")):
        raise BaselinePolicyError(f"{path} must not contain non-finite numbers")


def _host_support(host: dict) -> str:
    identity = (
        str(host.get("os_id") or "").lower(),
        str(host.get("os_version_id") or ""),
        str(host.get("architecture") or ""),
    )
    if any(not value or value.lower() == "unknown" for value in identity):
        return "unknown"
    return "supported" if identity in SUPPORTED_HOSTS else "unsupported"


def _host_evidence_status(host: dict) -> str:
    if host.get("inspection_status") != "Successful":
        return "incomplete"
    required = (
        "cpu_cores",
        "memory_bytes",
        "root_available_bytes",
        "package_manager",
        "service_manager",
    )
    for key in required:
        value = host.get(key)
        if value in (None, ""):
            return "incomplete"
    for key in ("cpu_cores", "memory_bytes", "root_available_bytes"):
        value = host.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return "invalid"
    return "complete"


def _docker_evidence_status(docker: dict | None) -> str:
    if not isinstance(docker, dict):
        return "missing"
    if docker.get("inspection_status") != "Successful":
        return "incomplete"
    snapshot = docker.get("snapshot")
    if not isinstance(snapshot, dict):
        return "incomplete"
    if snapshot.get("inspection_status") != "Successful":
        return "incomplete"
    return "complete"


def _action(action_id: str, scope: str, status: str, reason: str) -> dict:
    return {
        "action_id": action_id,
        "scope": scope,
        "kind": "verification",
        "status": status,
        "reason_code": reason,
    }


def build_baseline_preparation_plan(
    node_name: str,
    host_observation: dict,
    docker_observation: dict | None = None,
) -> dict:
    """Build a stable, non-mutating plan from normalized inspection evidence.

    The function is pure: identical arguments produce byte-identical canonical
    plans. Timestamps, credentials, transport details and caller instructions
    are intentionally excluded.
    """
    if not isinstance(node_name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,139}", node_name):
        raise BaselinePolicyError("Invalid Infrastructure Node identity")
    if not isinstance(host_observation, dict):
        raise BaselinePolicyError("Host observation must be an object")
    if docker_observation is not None and not isinstance(docker_observation, dict):
        raise BaselinePolicyError("Docker observation must be an object or null")

    _validate_safe_observation(host_observation, "host_observation")
    if docker_observation is not None:
        _validate_safe_observation(docker_observation, "docker_observation")

    host = dict(host_observation)
    support = _host_support(host)
    host_evidence = _host_evidence_status(host)
    docker_evidence = _docker_evidence_status(docker_observation)

    actions = [
        _action(
            "verify_host_support",
            "host",
            "verified" if support == "supported" else "blocked",
            "HOST_SUPPORTED" if support == "supported" else f"HOST_{support.upper()}",
        ),
        _action(
            "verify_host_capacity_evidence",
            "host",
            "evidence_available" if host_evidence == "complete" else "blocked",
            f"HOST_EVIDENCE_{host_evidence.upper()}",
        ),
    ]

    for action_id in ("verify_docker_engine", "verify_docker_compose", "verify_docker_storage"):
        actions.append(
            _action(
                action_id,
                "docker",
                "evidence_available" if docker_evidence == "complete" else "blocked",
                f"DOCKER_EVIDENCE_{docker_evidence.upper()}",
            )
        )

    actions.append(
        _action(
            "verify_execution_identity",
            "host",
            "requires_verification",
            "EXECUTION_IDENTITY_NOT_YET_OBSERVED",
        )
    )

    blocking = support != "supported" or host_evidence != "complete" or docker_evidence != "complete"
    plan = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "policy": {"id": POLICY_ID, "version": POLICY_VERSION},
        "target": {"doctype": "Infrastructure Node", "name": node_name},
        "mode": "plan_only",
        "mutation_authorized": False,
        "status": "blocked" if blocking else "verification_required",
        "observations": {
            "host_support": support,
            "host_evidence": host_evidence,
            "docker_evidence": docker_evidence,
        },
        "storage_policy": {
            "existing_docker_data_root": "preserve",
            "relocation_permitted": False,
        },
        "actions": actions,
    }
    plan["plan_digest"] = hashlib.sha256(_canonical(plan).encode("utf-8")).hexdigest()
    validate_baseline_preparation_plan(plan)
    return plan


def validate_baseline_preparation_plan(plan: dict) -> None:
    """Validate the versioned NP.2 plan shape and its non-mutation guarantees."""
    if not isinstance(plan, dict):
        raise BaselinePolicyError("Preparation plan must be an object")
    required = {
        "schema_version",
        "policy",
        "target",
        "mode",
        "mutation_authorized",
        "status",
        "observations",
        "storage_policy",
        "actions",
        "plan_digest",
    }
    if set(plan) != required:
        raise BaselinePolicyError("Preparation plan contains missing or unknown properties")
    if plan["schema_version"] != PLAN_SCHEMA_VERSION:
        raise BaselinePolicyError("Unsupported preparation plan schema version")
    if plan["policy"] != {"id": POLICY_ID, "version": POLICY_VERSION}:
        raise BaselinePolicyError("Unsupported baseline policy")
    target = plan["target"]
    if (
        not isinstance(target, dict)
        or set(target) != {"doctype", "name"}
        or target.get("doctype") != "Infrastructure Node"
        or not isinstance(target.get("name"), str)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,139}", target["name"])
    ):
        raise BaselinePolicyError("Invalid Infrastructure Node plan target")
    observations = plan["observations"]
    if (
        not isinstance(observations, dict)
        or set(observations) != {"host_support", "host_evidence", "docker_evidence"}
        or observations.get("host_support") not in {"supported", "unsupported", "unknown"}
        or observations.get("host_evidence") not in {"complete", "incomplete", "invalid"}
        or observations.get("docker_evidence") not in {"complete", "missing", "incomplete"}
    ):
        raise BaselinePolicyError("Invalid baseline observation summary")
    if plan["mode"] != "plan_only" or plan["mutation_authorized"] is not False:
        raise BaselinePolicyError("NP.2 plans must be non-mutating")
    if plan["status"] not in {"blocked", "verification_required"}:
        raise BaselinePolicyError("Invalid preparation plan status")
    if plan["storage_policy"] != {
        "existing_docker_data_root": "preserve",
        "relocation_permitted": False,
    }:
        raise BaselinePolicyError("Baseline plans must preserve the existing Docker data-root")
    if not isinstance(plan["actions"], list) or len(plan["actions"]) != len(VERIFICATION_ACTIONS):
        raise BaselinePolicyError("Preparation plan must contain the complete verification action set")
    expected = [
        {"action_id": action_id, "scope": scope}
        for action_id, scope in VERIFICATION_ACTIONS
    ]
    actual = [{"action_id": item.get("action_id"), "scope": item.get("scope")} for item in plan["actions"]]
    if actual != expected:
        raise BaselinePolicyError("Preparation plan actions do not match the server-owned action catalogue")
    expected_statuses = {
        "verify_host_support": {"verified", "blocked"},
        "verify_host_capacity_evidence": {"evidence_available", "blocked"},
        "verify_docker_engine": {"evidence_available", "blocked"},
        "verify_docker_compose": {"evidence_available", "blocked"},
        "verify_docker_storage": {"evidence_available", "blocked"},
        "verify_execution_identity": {"requires_verification"},
    }
    for item in plan["actions"]:
        if set(item) != {"action_id", "scope", "kind", "status", "reason_code"}:
            raise BaselinePolicyError("Preparation action contains unknown properties")
        if item.get("kind") != "verification" or item.get("status") not in expected_statuses[item["action_id"]]:
            raise BaselinePolicyError("Preparation plan contains an invalid or mutating action")
        if not isinstance(item.get("reason_code"), str) or not re.fullmatch(
            r"[A-Z][A-Z0-9_]{1,63}", item["reason_code"]
        ):
            raise BaselinePolicyError("Preparation action reason code is invalid")
    expected_plan_status = (
        "blocked"
        if plan["observations"]["host_support"] != "supported"
        or plan["observations"]["host_evidence"] != "complete"
        or plan["observations"]["docker_evidence"] != "complete"
        else "verification_required"
    )
    if plan["status"] != expected_plan_status:
        raise BaselinePolicyError("Preparation plan status does not match its evidence summary")
    digest = plan["plan_digest"]
    if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise BaselinePolicyError("Invalid preparation plan digest")
    unsigned = dict(plan)
    unsigned.pop("plan_digest")
    expected_digest = hashlib.sha256(_canonical(unsigned).encode("utf-8")).hexdigest()
    if digest != expected_digest:
        raise BaselinePolicyError("Preparation plan digest does not match its contents")
