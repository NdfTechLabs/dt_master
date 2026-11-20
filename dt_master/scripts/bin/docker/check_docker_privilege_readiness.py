#!/usr/bin/env python3
"""Read-only verification of the managed Docker helper privilege boundary."""
import hashlib
import json
import os
import pwd
import stat
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HELPER = Path("/usr/local/lib/dt-master/docker/docker_lifecycle_helper.py")
POLICY = Path("/etc/sudoers.d/dt-master-docker")
EXPECTED_HELPER_SHA256 = "afd072cd7d80a0182351ea6682779f703b95beef6c8695d8cee2a96dd59e9fa9"
MAX_OUTPUT = 4096


def run(argv, *, input_text=None, timeout=5):
    try:
        return subprocess.run(
            argv, input=input_text, text=True, capture_output=True,
            timeout=timeout, check=False, env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"},
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def main():
    expected_user = os.environ.get("EXPECTED_SSH_USER", "")
    checks = []
    diagnostics = []

    def record(name, ok, detail):
        checks.append({"name": name, "status": "Passed" if ok else "Failed"})
        if not ok:
            diagnostics.append({"code": "DOCKER_PRIVILEGE_" + name.upper(), "severity": "Error", "message": detail})

    try:
        actual_user = pwd.getpwuid(os.geteuid()).pw_name
    except KeyError:
        actual_user = ""
    record("execution_identity", bool(expected_user) and actual_user == expected_user,
           "Remote execution identity does not match the configured SSH user.")

    helper_ok = False
    try:
        st = HELPER.lstat()
        digest = hashlib.sha256(HELPER.read_bytes()).hexdigest()
        helper_ok = (
            stat.S_ISREG(st.st_mode) and st.st_uid == 0 and st.st_gid == 0
            and stat.S_IMODE(st.st_mode) == 0o755 and digest == EXPECTED_HELPER_SHA256
        )
    except OSError:
        digest = None
    record("helper_integrity", helper_ok, "Trusted helper is missing or its type, ownership, mode, or digest is incorrect.")

    parent_paths = [Path("/usr/local"), Path("/usr/local/lib"), Path("/usr/local/lib/dt-master"),
                    Path("/usr/local/lib/dt-master/docker")]
    parents_ok = True
    for path in parent_paths:
        try:
            st = path.lstat()
            parents_ok = parents_ok and stat.S_ISDIR(st.st_mode) and st.st_uid == 0 and st.st_gid == 0
            parents_ok = parents_ok and not (stat.S_IMODE(st.st_mode) & 0o022)
        except OSError:
            parents_ok = False
    record("trusted_directories", parents_ok, "A trusted helper parent directory is missing, symlinked, incorrectly owned, or writable by group/others.")

    policy_ok = False
    try:
        st = POLICY.lstat()
        content = POLICY.read_text(encoding="utf-8")
        expected = f'{expected_user} ALL=(root) NOPASSWD: {HELPER} ""\\n'
        policy_ok = (
            stat.S_ISREG(st.st_mode) and st.st_uid == 0 and st.st_gid == 0
            and stat.S_IMODE(st.st_mode) == 0o440 and content == expected
        )
    except OSError:
        content = ""
    record("sudoers_policy", policy_ok, "Managed sudoers file is missing or does not match the exact root-owned 0440 policy.")

    visudo = run(["/usr/sbin/visudo", "-cf", str(POLICY)])
    if visudo is None:
        visudo = run(["visudo", "-cf", str(POLICY)])
    syntax_ok = visudo is not None and visudo.returncode == 0
    record("sudoers_syntax", syntax_ok, "visudo could not validate the managed sudoers policy.")

    allowed = False
    if policy_ok and actual_user == expected_user:
        permission = run(["sudo", "-n", "-l", str(HELPER), ""], timeout=5)
        allowed = permission is not None and permission.returncode == 0
    record("effective_command", allowed, "The configured SSH identity is not authorized for the exact helper invocation.")

    helper_result = None
    if helper_ok and policy_ok and allowed:
        request = json.dumps({"schema_version": "1.0.0", "operation": "self_test"}, separators=(",", ":")) + "\n"
        response = run(["sudo", "-n", str(HELPER), ""], input_text=request, timeout=8)
        if response is not None and response.returncode == 0 and len(response.stdout) <= MAX_OUTPUT:
            try:
                helper_result = json.loads(response.stdout)
            except json.JSONDecodeError:
                helper_result = None
    self_test_ok = (
        isinstance(helper_result, dict)
        and set(helper_result) == {"schema_version", "helper_version", "operation", "status", "effective_uid", "effective_user"}
        and helper_result.get("schema_version") == "1.0.0"
        and helper_result.get("helper_version") == "0.1.0"
        and helper_result.get("operation") == "self_test"
        and helper_result.get("status") == "ok"
        and helper_result.get("effective_uid") == 0
        and helper_result.get("effective_user") == "root"
    )
    record("helper_self_test", self_test_ok, "The privileged helper self-test did not return the expected root identity response.")

    failed = [item for item in checks if item["status"] == "Failed"]
    status = "Ready" if not failed else ("Not Ready" if any(item["name"] in {
        "execution_identity", "helper_integrity", "trusted_directories", "sudoers_policy", "effective_command"
    } for item in failed) else "Incomplete")
    result = {
        "schema_version": "1.0.0",
        "observed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "status": status,
        "execution_identity": actual_user or None,
        "helper": {"path": str(HELPER), "version": "0.1.0" if helper_ok else None, "sha256": digest},
        "sudoers": {"path": str(POLICY), "syntax": "Valid" if syntax_ok else "Invalid"},
        "checks": checks,
        "diagnostics": diagnostics,
    }
    print(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
