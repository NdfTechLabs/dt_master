"""Pure, deterministic Docker Engine installation preflight planner.

N3.2 planning is read-only. This module has no Frappe, filesystem,
subprocess, network, SSH, or package-manager dependencies.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

POLICY_ID = "docker-engine-install"
POLICY_VERSION = "1.0.0"
PLAN_SCHEMA_VERSION = "1.0.0"
SUPPORTED_HOSTS = frozenset({
    ("ubuntu", "22.04", "amd64"),
    ("ubuntu", "24.04", "amd64"),
    ("debian", "12", "amd64"),
})
ENGINE_PACKAGES = ("docker-ce", "docker-ce-cli", "containerd.io", "docker-buildx-plugin")
CONFLICT_PACKAGES = ("docker.io", "docker-compose", "podman-docker", "containerd", "runc")
ALL_PACKAGES = frozenset((*ENGINE_PACKAGES, *CONFLICT_PACKAGES))
OFFICIAL_REPOSITORY = "download.docker.com"
DOCKER_KEYRING_PATH = "/etc/apt/keyrings/docker.asc"
APT_FRESH_SECONDS = 86400
APT_AGING_SECONDS = 604800
MIN_ROOT_AVAILABLE_BYTES = 512 * 1024 * 1024
MIN_APT_CACHE_AVAILABLE_BYTES = 256 * 1024 * 1024
_FINGERPRINT = re.compile(r"^[A-F0-9]{40}$")
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+:~_-]{0,159}$")


class DockerInstallPlanError(ValueError):
    """Invalid N3.2 inventory or installation plan."""


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _safe(value: Any, path: str = "inventory") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise DockerInstallPlanError(f"{path} keys must be strings")
            key_lower = key.lower()
            if any(token in key_lower for token in ("password", "token", "secret", "credential", "private_key", "command", "script_path", "ssh_host", "ip_address", "container_id", "container_name", "image_ref")):
                raise DockerInstallPlanError(f"Sensitive or executable field is not permitted: {path}.{key}")
            _safe(item, f"{path}.{key}")
    elif isinstance(value, list):
        if len(value) > 128:
            raise DockerInstallPlanError(f"{path} exceeds the collection limit")
        for index, item in enumerate(value):
            _safe(item, f"{path}[{index}]")
    elif value is not None and not isinstance(value, (str, bool, int, float)):
        raise DockerInstallPlanError(f"{path} contains a non-JSON value")
    elif isinstance(value, float) and (value != value or abs(value) == float("inf")):
        raise DockerInstallPlanError(f"{path} contains a non-finite number")
    elif isinstance(value, str) and len(value) > 512:
        raise DockerInstallPlanError(f"{path} contains an oversized string")


def _require_keys(value: dict, keys: set[str], path: str) -> None:
    if not isinstance(value, dict) or set(value) != keys:
        raise DockerInstallPlanError(f"{path} has missing or unsupported fields")


def _int(value: Any, path: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise DockerInstallPlanError(f"{path} must be an integer >= {minimum}")
    return value


def _package_map(rows: list[dict]) -> dict[str, dict]:
    result = {}
    for row in rows:
        _require_keys(row, {"name", "installed_version", "installed_origin", "dpkg_status", "candidate_version", "candidate_origin", "held"}, "package row")
        name = row["name"]
        if name not in ALL_PACKAGES or name in result:
            raise DockerInstallPlanError("Package inventory contains an unknown or duplicate package")
        for field in ("installed_version", "candidate_version"):
            value = row[field]
            if value is not None and (not isinstance(value, str) or not _VERSION.fullmatch(value)):
                raise DockerInstallPlanError(f"Invalid {field} for {name}")
        for origin_field in ("installed_origin", "candidate_origin"):
            if row[origin_field] is not None and not isinstance(row[origin_field], str):
                raise DockerInstallPlanError(f"Invalid {origin_field} for {name}")
        if row["dpkg_status"] not in {"installed", "not-installed", "config-files", "half-installed", "unpacked", "half-configured", "unknown"}:
            raise DockerInstallPlanError(f"Invalid dpkg status for {name}")
        if type(row["held"]) is not bool:
            raise DockerInstallPlanError(f"Invalid hold state for {name}")
        result[name] = row
    return result


def build_docker_engine_install_plan(node_name: str, inventory: dict) -> dict:
    """Build a deterministic, non-mutating Engine install plan from fresh inventory."""
    if not isinstance(node_name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,139}", node_name):
        raise DockerInstallPlanError("Invalid Infrastructure Node identity")
    if not isinstance(inventory, dict):
        raise DockerInstallPlanError("Docker Engine inventory must be an object")
    _safe(inventory)
    _require_keys(inventory, {
        "schema_version", "observed_at", "host_os", "package_manager", "packages",
        "repository", "apt_metadata", "package_manager_state", "storage", "services",
        "workloads", "docker_engine_health",
    }, "inventory")
    if inventory["schema_version"] != "1.0.0":
        raise DockerInstallPlanError("Unsupported Docker Engine inventory schema")
    host = inventory["host_os"]
    _require_keys(host, {"id", "version_id", "codename", "architecture"}, "host_os")
    repository = inventory["repository"]
    if not isinstance(repository, dict) or set(repository) not in (
    {"source_id", "keyring_path", "fingerprints"},
    {"source_id", "keyring_path", "fingerprints", "primary_keys"},
):
    raise DockerInstallPlanError("repository has missing or unsupported fields")
if "primary_keys" in repository:
    if not isinstance(repository["primary_keys"], list) or len(repository["primary_keys"]) > 8:
        raise DockerInstallPlanError("Invalid primary-key inventory")
    for key in repository["primary_keys"]:
        _require_keys(key, {"fingerprint", "identity", "subkey_fingerprints"}, "repository.primary_keys[]")
        if key["fingerprint"] is not None and (
            not isinstance(key["fingerprint"], str) or not _FINGERPRINT.fullmatch(key["fingerprint"])
        ):
            raise DockerInstallPlanError("Invalid primary-key fingerprint")
        if key["identity"] is not None and (not isinstance(key["identity"], str) or len(key["identity"]) > 512):
            raise DockerInstallPlanError("Invalid primary-key identity")
        subkeys = key["subkey_fingerprints"]
        if not isinstance(subkeys, list) or len(subkeys) > 8 or any(
            not isinstance(item, str) or not _FINGERPRINT.fullmatch(item) for item in subkeys
        ):
            raise DockerInstallPlanError("Invalid subkey fingerprint inventory")
    apt = inventory["apt_metadata"]
    _require_keys(apt, {"status", "age_seconds"}, "apt_metadata")
    pm = inventory["package_manager_state"]
    _require_keys(pm, {"locked", "dpkg_audit_clean"}, "package_manager_state")
    storage = inventory["storage"]
    _require_keys(storage, {"root_available_bytes", "apt_cache_available_bytes"}, "storage")
    services = inventory["services"]
    _require_keys(services, {"docker", "containerd"}, "services")
    workloads = inventory["workloads"]
    _require_keys(workloads, {"available", "running_containers", "total_containers"}, "workloads")
    if not isinstance(inventory["packages"], list):
        raise DockerInstallPlanError("packages must be a list")
    packages = _package_map(inventory["packages"])

    reasons: list[str] = []
    prerequisites: list[str] = []
    package_plan = []
    os_tuple = (host["id"], host["version_id"], host["architecture"])
    if any(not isinstance(item, str) or not item for item in (*os_tuple, host["codename"])):
        reasons.append("HOST_IDENTITY_UNKNOWN")
    elif os_tuple not in SUPPORTED_HOSTS:
        reasons.append("HOST_UNSUPPORTED")
    if inventory["package_manager"] != "apt":
        reasons.append("PACKAGE_MANAGER_UNSUPPORTED")
    if type(pm["locked"]) is not bool or type(pm["dpkg_audit_clean"]) is not bool:
        raise DockerInstallPlanError("Invalid package manager state")
    if pm["locked"]:
        reasons.append("PACKAGE_MANAGER_BUSY")
    if not pm["dpkg_audit_clean"]:
        reasons.append("DPKG_STATE_BROKEN")
    if apt["status"] not in {"Fresh", "Aging", "Stale", "Unavailable"}:
        raise DockerInstallPlanError("Invalid APT metadata status")
    if apt["age_seconds"] is not None:
        _int(apt["age_seconds"], "apt_metadata.age_seconds")
    if apt["status"] != "Fresh":
        prerequisites.append("refresh_apt_metadata")
        reasons.append("APT_METADATA_NOT_FRESH")
    if repository["source_id"] != OFFICIAL_REPOSITORY:
        prerequisites.append("configure_official_docker_repository")
        reasons.append("DOCKER_REPOSITORY_UNTRUSTED")
    if repository["keyring_path"] != DOCKER_KEYRING_PATH:
        reasons.append("DOCKER_KEYRING_PATH_UNTRUSTED")
    fingerprints = repository["fingerprints"]
    if not isinstance(fingerprints, list) or not fingerprints:
        prerequisites.append("verify_and_pin_docker_signing_key")
        reasons.append("DOCKER_SIGNING_KEY_UNVERIFIED")
    else:
        if any(not isinstance(item, str) or not _FINGERPRINT.fullmatch(item) for item in fingerprints):
            raise DockerInstallPlanError("Invalid observed signing-key fingerprint")
        # The expected fingerprint is intentionally not guessed in N3.2.
        # A later reviewed policy constant supplies the comparison before N3.3.
        prerequisites.append("verify_and_pin_docker_signing_key")
        reasons.append("DOCKER_SIGNING_KEY_POLICY_PENDING")
    root_bytes = _int(storage["root_available_bytes"], "storage.root_available_bytes")
    cache_bytes = _int(storage["apt_cache_available_bytes"], "storage.apt_cache_available_bytes")
    if root_bytes < MIN_ROOT_AVAILABLE_BYTES:
        reasons.append("ROOT_STORAGE_INSUFFICIENT")
    if cache_bytes < MIN_APT_CACHE_AVAILABLE_BYTES:
        reasons.append("APT_CACHE_STORAGE_INSUFFICIENT")
    for name, state in services.items():
        if state not in {"active", "inactive", "failed", "activating", "deactivating", "Unknown"}:
            raise DockerInstallPlanError(f"Invalid {name} service state")
    _require_keys(workloads, {"available", "running_containers", "total_containers"}, "workloads")
    if type(workloads["available"]) is not bool:
        raise DockerInstallPlanError("Invalid workload availability")
    if workloads["available"]:
        running = _int(workloads["running_containers"], "workloads.running_containers")
        total = _int(workloads["total_containers"], "workloads.total_containers")
        if running > total:
            raise DockerInstallPlanError("Running workload count exceeds total count")
        if running:
            reasons.append("ACTIVE_WORKLOADS_IMPACT_APPROVAL_REQUIRED")
    elif workloads["running_containers"] is not None or workloads["total_containers"] is not None:
        raise DockerInstallPlanError("Unavailable workload evidence must not contain counts")

    installed_engine = [packages.get(name) for name in ENGINE_PACKAGES]
    conflict_rows = [packages[name] for name in CONFLICT_PACKAGES if name in packages and packages[name]["dpkg_status"] == "installed"]
    partial_engine = any(row and row["dpkg_status"] not in {"installed", "not-installed"} for row in installed_engine)
    installed_count = sum(bool(row and row["dpkg_status"] == "installed") for row in installed_engine)
    if conflict_rows:
        reasons.append("CONFLICTING_PACKAGE_FAMILY")
    if partial_engine or (installed_count not in {0, len(ENGINE_PACKAGES)}):
        reasons.append("PARTIAL_DOCKER_CE_INSTALLATION")
    if any(row and row["held"] for row in installed_engine):
        reasons.append("ENGINE_PACKAGE_HELD")
    if installed_count == len(ENGINE_PACKAGES):
        if inventory["docker_engine_health"] != "healthy":
            reasons.append("EXISTING_DOCKER_ENGINE_UNHEALTHY")
        elif any(
            not packages[name]["installed_origin"]
            or OFFICIAL_REPOSITORY not in packages[name]["installed_origin"]
            for name in ENGINE_PACKAGES
        ):
            reasons.append("EXISTING_PACKAGE_ORIGIN_UNVERIFIED")
        else:
            status = "no_op"
    else:
        status = "ready"

    if inventory["docker_engine_health"] not in {"healthy", "not_installed", "unavailable", "unhealthy"}:
        raise DockerInstallPlanError("Invalid Docker Engine health state")
    for name in ENGINE_PACKAGES:
        row = packages.get(name)
        if row is None:
            reasons.append(f"PACKAGE_INVENTORY_MISSING:{name}")
            continue
        if row["dpkg_status"] == "installed":
            package_plan.append({"name": name, "action": "keep", "installed_version": row["installed_version"], "candidate_version": row["candidate_version"]})
            continue
        if row["candidate_version"] is None or row["candidate_origin"] is None or OFFICIAL_REPOSITORY not in row["candidate_origin"]:
            reasons.append(f"PACKAGE_CANDIDATE_UNAVAILABLE_OR_UNTRUSTED:{name}")
            continue
        package_plan.append({"name": name, "action": "install", "installed_version": None, "candidate_version": row["candidate_version"]})

    if reasons:
        if any(reason.startswith("PACKAGE_INVENTORY_MISSING") for reason in reasons):
            status = "blocked"
        elif status != "no_op":
            status = "preparation_required" if prerequisites and set(reasons).issubset({
                "APT_METADATA_NOT_FRESH", "DOCKER_REPOSITORY_UNTRUSTED", "DOCKER_SIGNING_KEY_UNVERIFIED",
                "DOCKER_SIGNING_KEY_POLICY_PENDING",
            }) else "blocked"
        elif status == "no_op" and any(reason not in {"DOCKER_SIGNING_KEY_POLICY_PENDING"} for reason in reasons):
            status = "blocked"
    elif status != "no_op":
        status = "ready"

    plan = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "policy": {"id": POLICY_ID, "version": POLICY_VERSION},
        "target": {"doctype": "Infrastructure Node", "name": node_name},
        "status": status,
        "mode": "plan_only",
        "mutation_authorized": False,
        "package_set": list(ENGINE_PACKAGES),
        "packages": package_plan,
        "prerequisites": sorted(set(prerequisites)),
        "reason_codes": sorted(set(reasons)),
        "repository": {"source_id": repository["source_id"], "keyring_path": repository["keyring_path"], "fingerprint_evidence": "observed" if fingerprints else "missing"},
        "apt_metadata": {"status": apt["status"], "age_seconds": apt["age_seconds"]},
        "package_manager": {"locked": pm["locked"], "dpkg_audit_clean": pm["dpkg_audit_clean"]},
        "storage": {"root_available_bytes": root_bytes, "apt_cache_available_bytes": cache_bytes, "data_root_policy": "preserve"},
        "services": dict(services),
        "workload_impact": {"available": workloads["available"], "running_containers": workloads["running_containers"], "total_containers": workloads["total_containers"], "operator_impact_approval_required": bool(workloads["available"] and workloads["running_containers"])},
        "daemon_impact": "Starting Docker may interrupt existing containers; no daemon action is performed by this plan.",
        "expires_after_seconds": 900,
    }
    digest_payload = dict(plan)
    plan["plan_digest"] = hashlib.sha256(_canonical(digest_payload).encode("utf-8")).hexdigest()
    validate_docker_engine_install_plan(plan)
    return plan


def validate_docker_engine_install_plan(plan: dict) -> None:
    """Strictly validate plan structure, mutation boundary and canonical digest."""
    if not isinstance(plan, dict):
        raise DockerInstallPlanError("Plan must be an object")
    required = {
        "schema_version", "policy", "target", "status", "mode", "mutation_authorized",
        "package_set", "packages", "prerequisites", "reason_codes", "repository",
        "apt_metadata", "package_manager", "storage", "services", "workload_impact",
        "daemon_impact", "expires_after_seconds", "plan_digest",
    }
    _require_keys(plan, required, "plan")
    if plan["schema_version"] != PLAN_SCHEMA_VERSION or plan["policy"] != {"id": POLICY_ID, "version": POLICY_VERSION}:
        raise DockerInstallPlanError("Unsupported Docker Engine plan policy/schema")
    if plan["status"] not in {"blocked", "preparation_required", "ready", "no_op"}:
        raise DockerInstallPlanError("Invalid plan status")
    if plan["mode"] != "plan_only" or plan["mutation_authorized"] is not False:
        raise DockerInstallPlanError("N3.2 plan must remain non-mutating")
    if plan["package_set"] != list(ENGINE_PACKAGES):
        raise DockerInstallPlanError("Plan package set differs from accepted policy")
    if not isinstance(plan["plan_digest"], str) or not re.fullmatch(r"[a-f0-9]{64}", plan["plan_digest"]):
        raise DockerInstallPlanError("Invalid plan digest")
    unsigned = dict(plan)
    digest = unsigned.pop("plan_digest")
    if hashlib.sha256(_canonical(unsigned).encode("utf-8")).hexdigest() != digest:
        raise DockerInstallPlanError("Plan digest mismatch")
