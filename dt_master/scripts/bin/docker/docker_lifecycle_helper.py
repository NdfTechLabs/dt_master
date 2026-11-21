#!/usr/bin/python3 -I
"""Root-owned, closed Docker lifecycle helper.

The N3.4.8 implementation validates install requests and independently rebuilds
host state and the deterministic plan. Package execution remains disabled until
a separately accepted safety gate establishes maintainer-script behavior.
"""
from __future__ import annotations

import hashlib
import json
import os
import pwd
import re
import stat
import subprocess
import sys
from pathlib import Path

HELPER_VERSION = "0.2.0"
REQUEST_SCHEMA_VERSION = "1.0.0"
RESULT_SCHEMA_VERSION = "1.0.0"
MAX_REQUEST_BYTES = 16 * 1024
MAX_RESULT_BYTES = 16 * 1024
MAX_COMMAND_OUTPUT = 64 * 1024
COMMAND_TIMEOUT = 30
INSTALL_TIMEOUT = 900
INSTALL_MUTATION_ENABLED = False
HELPER_DIR = Path("/usr/local/lib/dt-master/docker")
EXPECTED_POLICY_SHA256 = "1391101db5c629a11584fb6452df07191650dd114804ec8e6f8c389660a9bf30"
EXPECTED_INVENTORY_SHA256 = "d09e83777f254f1ecdc7299561046c2167e87901f01c5c2b03f2bf0e8c153a9c"
ENGINE_PACKAGES = ("docker-ce", "docker-ce-cli", "containerd.io", "docker-buildx-plugin")
PACKAGE_VERSION = re.compile(r"^(?:[0-9]+:)?[0-9][A-Za-z0-9.+~_-]{0,158}$")
SHA256 = re.compile(r"^[a-f0-9]{64}$")
IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class HelperError(ValueError):
    def __init__(self, code: str, phase: str):
        super().__init__(code)
        self.code = code
        self.phase = phase


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _reject_constant(_value):
    raise ValueError("non_finite_json_constant")


def _run(argv: list[str], *, timeout: int = COMMAND_TIMEOUT) -> tuple[int, str]:
    """Run a fixed argv with a minimal environment and bounded captured output."""
    if not isinstance(argv, list) or not argv or any(not isinstance(x, str) for x in argv):
        raise HelperError("INVALID_FIXED_COMMAND", "host_validation")
    try:
        completed = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env={
                "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
                "LC_ALL": "C",
                "LANG": "C",
                "DEBIAN_FRONTEND": "noninteractive",
                "HOME": "/root",
                "DOCKER_HOST": "unix:///var/run/docker.sock",
            },
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HelperError("FIXED_COMMAND_UNAVAILABLE", "host_validation") from exc
    output = (completed.stdout or "") + (completed.stderr or "")
    if len(output.encode("utf-8", errors="replace")) > MAX_COMMAND_OUTPUT:
        raise HelperError("FIXED_COMMAND_OUTPUT_LIMIT", "host_validation")
    return completed.returncode, output


def _request(raw: bytes) -> dict:
    if len(raw) > MAX_REQUEST_BYTES:
        raise HelperError("REQUEST_TOO_LARGE", "request_validation")
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise HelperError("INVALID_JSON_REQUEST", "request_validation") from exc
    if not isinstance(value, dict):
        raise HelperError("REQUEST_NOT_OBJECT", "request_validation")
    if set(value) == {"schema_version", "operation"}:
        if value.get("schema_version") != REQUEST_SCHEMA_VERSION or value.get("operation") != "self_test":
            raise HelperError("UNSUPPORTED_SELF_TEST_REQUEST", "request_validation")
        return value
    required = {
        "schema_version", "operation", "operation_id", "target", "plan",
        "expected_state_fingerprint", "operator_impact_approval",
    }
    if set(value) != required:
        raise HelperError("INVALID_INSTALL_REQUEST_FIELDS", "request_validation")
    if value["schema_version"] != REQUEST_SCHEMA_VERSION or value["operation"] != "install_engine":
        raise HelperError("UNSUPPORTED_INSTALL_REQUEST", "request_validation")
    if not isinstance(value["operation_id"], str) or not IDENTIFIER.fullmatch(value["operation_id"]):
        raise HelperError("INVALID_OPERATION_ID", "request_validation")
    target = value["target"]
    if not isinstance(target, dict) or set(target) != {"doctype", "name"}:
        raise HelperError("INVALID_TARGET", "request_validation")
    if target["doctype"] != "Infrastructure Node" or not isinstance(target["name"], str) or not IDENTIFIER.fullmatch(target["name"]):
        raise HelperError("INVALID_TARGET", "request_validation")
    plan = value["plan"]
    if not isinstance(plan, dict) or set(plan) != {"schema_version", "policy_version", "plan_digest", "packages"}:
        raise HelperError("INVALID_PLAN_FIELDS", "request_validation")
    if plan["schema_version"] != "1.1.0" or plan["policy_version"] != "1.1.0":
        raise HelperError("UNSUPPORTED_PLAN_VERSION", "request_validation")
    if not isinstance(plan["plan_digest"], str) or not SHA256.fullmatch(plan["plan_digest"]):
        raise HelperError("INVALID_PLAN_DIGEST", "request_validation")
    rows = plan["packages"]
    if not isinstance(rows, list) or len(rows) != len(ENGINE_PACKAGES):
        raise HelperError("INVALID_PACKAGE_SET", "request_validation")
    for expected, row in zip(ENGINE_PACKAGES, rows, strict=True):
        if not isinstance(row, dict) or set(row) != {"name", "version"} or row["name"] != expected:
            raise HelperError("INVALID_PACKAGE_SET", "request_validation")
        if not isinstance(row["version"], str) or not PACKAGE_VERSION.fullmatch(row["version"]):
            raise HelperError("INVALID_EXACT_PACKAGE_VERSION", "request_validation")
    fingerprint = value["expected_state_fingerprint"]
    if not isinstance(fingerprint, str) or not SHA256.fullmatch(fingerprint):
        raise HelperError("INVALID_STATE_FINGERPRINT", "request_validation")
    if type(value["operator_impact_approval"]) is not bool:
        raise HelperError("INVALID_IMPACT_APPROVAL", "request_validation")
    return value


def _load_policy():
    """Load only pinned root-owned policy artifacts from the fixed helper directory."""
    if os.geteuid() != 0:
        raise HelperError("ROOT_REQUIRED", "host_validation")
    try:
        directory = HELPER_DIR.lstat()
        if (
            not stat.S_ISDIR(directory.st_mode)
            or directory.st_uid != 0
            or directory.st_gid != 0
            or stat.S_IMODE(directory.st_mode) != 0o755
        ):
            raise HelperError("HELPER_DIRECTORY_UNTRUSTED", "host_validation")
        expected = {
            "docker_install_policy.py": EXPECTED_POLICY_SHA256,
            "docker_install_inventory.py": EXPECTED_INVENTORY_SHA256,
        }
        for name, digest in expected.items():
            path = HELPER_DIR / name
            metadata = path.lstat()
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != 0
                or metadata.st_gid != 0
                or stat.S_IMODE(metadata.st_mode) != 0o755
                or hashlib.sha256(path.read_bytes()).hexdigest() != digest
            ):
                raise HelperError("POLICY_ARTIFACT_UNTRUSTED", "host_validation")
    except OSError as exc:
        raise HelperError("POLICY_ARTIFACT_UNAVAILABLE", "host_validation") from exc
    sys.path.insert(0, str(HELPER_DIR))
    import docker_install_inventory
    import docker_install_policy
    return docker_install_inventory, docker_install_policy


def _installed_versions(inventory: dict) -> dict[str, str | None]:
    return {
        row["name"]: row["installed_version"]
        for row in inventory.get("packages", [])
        if row.get("name") in ENGINE_PACKAGES
    }


def _result(
    request: dict,
    *,
    status: str,
    phase: str,
    before: str,
    after: str,
    installed: dict[str, str | None],
    exit_code: int,
    daemon_action: str,
    diagnostics: list[str],
) -> dict:
    packages = []
    for name, row in zip(ENGINE_PACKAGES, request["plan"]["packages"], strict=True):
        installed_version = installed.get(name)
        package_status = "not_attempted"
        if installed_version == row["version"]:
            package_status = "installed"
        packages.append({
            "name": name,
            "requested_version": row["version"],
            "installed_version": installed_version,
            "status": package_status,
        })
    result = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "helper_version": HELPER_VERSION,
        "operation": "install_engine",
        "operation_id": request["operation_id"],
        "status": status,
        "phase": phase,
        "before_state_fingerprint": before,
        "after_state_fingerprint": after,
        "packages": packages,
        "package_manager_exit_code": exit_code,
        "daemon_action": daemon_action,
        "diagnostics": diagnostics[:20],
    }
    encoded = (json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()
    if len(encoded) > MAX_RESULT_BYTES:
        raise HelperError("RESULT_TOO_LARGE", "result_verification")
    return result


def _simulate_exact_install(versions: list[str]) -> None:
    argv = [
        "/usr/bin/apt-get", "-s", "--no-install-recommends", "--no-upgrade",
        "--no-remove", "install",
        *[f"{name}={version}" for name, version in zip(ENGINE_PACKAGES, versions, strict=True)],
    ]
    code, output = _run(argv, timeout=90)
    if code != 0:
        raise HelperError("APT_SIMULATION_FAILED", "state_validation")
    installs = []
    removals = []
    for line in output.splitlines():
        match = re.match(r"^Inst\s+(\S+)(?:\s+\[([^]]+)\])?\s+\((\S+)", line)
        if match:
            if match.group(2):
                raise HelperError("APT_SIMULATION_UPGRADE_DETECTED", "state_validation")
            installs.append((match.group(1).split(":", 1)[0], match.group(3)))
        match = re.match(r"^Remv\s+(\S+)", line)
        if match:
            removals.append(match.group(1).split(":", 1)[0])
    if removals or any(name not in ENGINE_PACKAGES for name, _version in installs):
        raise HelperError("APT_SIMULATION_OUT_OF_POLICY", "state_validation")
    installed_names = [name for name, _version in installs]
    if (
        len(installs) != len(ENGINE_PACKAGES)
        or len(set(installed_names)) != len(installed_names)
        or set(installed_names) != set(ENGINE_PACKAGES)
    ):
        raise HelperError("APT_SIMULATION_PACKAGE_SET_MISMATCH", "state_validation")
    for name, version in installs:
        expected = versions[ENGINE_PACKAGES.index(name)]
        if version != expected:
            raise HelperError("APT_SIMULATION_VERSION_MISMATCH", "state_validation")


def _install(request: dict) -> dict:
    inventory_module, policy = _load_policy()
    inventory = inventory_module.collect()
    install_inventory = inventory.get("docker_install_inventory")
    if not isinstance(install_inventory, dict):
        raise HelperError("HOST_INVENTORY_UNAVAILABLE", "host_validation")
    try:
        plan = policy.build_docker_engine_install_plan(request["target"]["name"], install_inventory)
        policy.validate_docker_engine_install_plan(plan)
        fingerprint = policy.docker_engine_install_state_fingerprint(install_inventory)
    except (TypeError, ValueError, KeyError) as exc:
        raise HelperError("HOST_INVENTORY_INVALID", "host_validation") from exc

    if fingerprint != request["expected_state_fingerprint"]:
        raise HelperError("STATE_FINGERPRINT_MISMATCH", "state_validation")
    if plan.get("plan_digest") != request["plan"]["plan_digest"]:
        raise HelperError("PLAN_DIGEST_MISMATCH", "state_validation")
    if plan.get("status") != "ready" or plan.get("mutation_authorized") is not False:
        raise HelperError("INSTALL_PLAN_NOT_READY", "state_validation")
    if plan.get("target") != {"doctype": "Infrastructure Node", "name": request["target"]["name"]}:
        raise HelperError("PLAN_TARGET_MISMATCH", "state_validation")
    impact = plan.get("workload_impact") or {}
    approval_required = impact.get("operator_impact_approval_required")
    if type(approval_required) is not bool or request["operator_impact_approval"] is not approval_required:
        raise HelperError("WORKLOAD_IMPACT_APPROVAL_MISMATCH", "state_validation")

    plan_versions = [row["candidate_version"] for row in plan["packages"]]
    request_versions = [row["version"] for row in request["plan"]["packages"]]
    if request_versions != plan_versions or any(row.get("action") != "install" for row in plan["packages"]):
        raise HelperError("EXACT_PACKAGE_PLAN_MISMATCH", "state_validation")

    _simulate_exact_install(request_versions)

    # Recollect after simulation so the package-manager lock, candidate
    # metadata, workload count, and approved state are still current.
    try:
        fresh_inventory = inventory_module.collect()["docker_install_inventory"]
        fresh_plan = policy.build_docker_engine_install_plan(request["target"]["name"], fresh_inventory)
        policy.validate_docker_engine_install_plan(fresh_plan)
        fresh_fingerprint = policy.docker_engine_install_state_fingerprint(fresh_inventory)
    except (TypeError, ValueError, KeyError) as exc:
        raise HelperError("FRESH_INVENTORY_INVALID", "state_validation") from exc
    if (
        fresh_fingerprint != fingerprint
        or fresh_plan.get("plan_digest") != plan.get("plan_digest")
        or fresh_plan.get("status") != "ready"
    ):
        raise HelperError("STATE_CHANGED_AFTER_SIMULATION", "state_validation")
    current_versions = _installed_versions(fresh_inventory)
    if not INSTALL_MUTATION_ENABLED:
        return _result(
            request, status="failed_before_mutation", phase="state_validation",
            before=fingerprint, after=fingerprint, installed=current_versions,
            exit_code=0, daemon_action="not_requested",
            diagnostics=["INSTALL_EXECUTION_DISABLED"],
        )

    argv = [
        "/usr/bin/apt-get", "--yes", "--no-install-recommends", "--no-upgrade",
        "--no-remove", "install",
        *[f"{name}={version}" for name, version in zip(ENGINE_PACKAGES, request_versions, strict=True)],
    ]
    try:
        code, _output = _run(argv, timeout=INSTALL_TIMEOUT)
    except HelperError:
        after_inventory = inventory_module.collect()["docker_install_inventory"]
        after_fingerprint = policy.docker_engine_install_state_fingerprint(after_inventory)
        return _result(
            request, status="uncertain", phase="package_install",
            before=fingerprint, after=after_fingerprint,
            installed=_installed_versions(after_inventory), exit_code=124,
            daemon_action="package_maintainer_scripts",
            diagnostics=["PACKAGE_INSTALL_OUTCOME_UNCERTAIN"],
        )
    after_inventory = inventory_module.collect()["docker_install_inventory"]
    after_fingerprint = policy.docker_engine_install_state_fingerprint(after_inventory)
    after_versions = _installed_versions(after_inventory)
    exact = all(
        after_versions.get(name) == version
        for name, version in zip(ENGINE_PACKAGES, request_versions, strict=True)
    )
    succeeded = code == 0 and exact
    return _result(
        request, status="succeeded" if succeeded else "failed_after_mutation",
        phase="result_verification" if succeeded else "package_install",
        before=fingerprint, after=after_fingerprint, installed=after_versions,
        exit_code=code, daemon_action="package_maintainer_scripts",
        diagnostics=[] if succeeded else ["PACKAGE_INSTALL_POSTCONDITION_MISMATCH"],
    )


def _configure_workload_identity(sudo_user: str | None) -> None:
    """Make root-side workload observations as the invoking SSH identity."""
    if not isinstance(sudo_user, str) or not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", sudo_user):
        raise HelperError("SUDO_IDENTITY_UNAVAILABLE", "host_validation")
    try:
        account = pwd.getpwnam(sudo_user)
    except KeyError as exc:
        raise HelperError("SUDO_IDENTITY_UNKNOWN", "host_validation") from exc
    if account.pw_uid == 0:
        raise HelperError("SUDO_IDENTITY_INVALID", "host_validation")
    os.environ["DT_DOCKER_WORKLOAD_USER"] = sudo_user


def main() -> int:
    sudo_user = os.environ.get("SUDO_USER")
    # Ignore caller-controlled Python, locale, APT, and Docker environment.
    os.environ.clear()
    os.environ.update({
        "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
        "LC_ALL": "C",
        "LANG": "C",
        "DEBIAN_FRONTEND": "noninteractive",
        "HOME": "/root",
        "DOCKER_HOST": "unix:///var/run/docker.sock",
    })
    raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
    request = _request(raw)
    if request["operation"] == "install_engine":
        _configure_workload_identity(sudo_user)
    if request["operation"] == "self_test":
        response = {
            "schema_version": "1.0.0",
            "helper_version": HELPER_VERSION,
            "operation": "self_test",
            "status": "ok",
            "effective_uid": os.geteuid(),
            "effective_user": "root" if os.geteuid() == 0 else "non-root",
        }
    else:
        try:
            response = _install(request)
        except HelperError as exc:
            # Validation failures are always before the package-manager boundary.
            fallback = None
            installed = {}
            try:
                inventory_module, policy = _load_policy()
                current = inventory_module.collect()["docker_install_inventory"]
                fallback = policy.docker_engine_install_state_fingerprint(current)
                installed = _installed_versions(current)
            except Exception:
                # Without a fresh independent fingerprint, do not fabricate a
                # structured pre-mutation result. The caller must reconcile.
                sys.stderr.write("DOCKER_HELPER_HOST_VALIDATION_UNAVAILABLE\n")
                return 2
            response = _result(
                request, status="failed_before_mutation", phase=exc.phase,
                before=fallback, after=fallback, installed=installed,
                exit_code=0, daemon_action="not_requested", diagnostics=[exc.code],
            )
    encoded = (json.dumps(response, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()
    if len(encoded) > MAX_RESULT_BYTES:
        raise ValueError("result_too_large")
    sys.stdout.buffer.write(encoded)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        # Never echo request content, host data, command output, or tracebacks.
        sys.stderr.write("DOCKER_HELPER_REQUEST_REJECTED\n")
        raise SystemExit(2)
