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
POLICY_ARTIFACT = Path("/usr/local/lib/dt-master/docker/docker_install_policy.py")
INVENTORY_ARTIFACT = Path("/usr/local/lib/dt-master/docker/docker_install_inventory.py")
POLICY = Path("/etc/sudoers.d/dt-master-docker")
EXPECTED_HELPER_SHA256 = "005f644f5384213988d1643b2af925f0b210d94ad7b9fa1be0179cda3303122f"
EXPECTED_POLICY_SHA256 = "99a98c694e26d8fa39f82d8e5b8071103db61970d23f659145d7a1de9ff0ac47"
EXPECTED_INVENTORY_SHA256 = "0cb582671b2983d8794f69563a94564d2335c6b0c52e960dd30ead8cfbe9f089"
EXPECTED_HELPER_VERSION = "0.3.1"
MAX_OUTPUT = 4096


def run(argv, *, input_text=None, timeout=5):
	try:
		return subprocess.run(
			argv,
			input=input_text,
			text=True,
			capture_output=True,
			timeout=timeout,
			check=False,
			env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"},
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
			diagnostics.append(
				{"code": "DOCKER_PRIVILEGE_" + name.upper(), "severity": "Error", "message": detail}
			)

	try:
		actual_user = pwd.getpwuid(os.geteuid()).pw_name
	except KeyError:
		actual_user = ""
	record(
		"execution_identity",
		bool(expected_user) and actual_user == expected_user,
		"Remote execution identity does not match the configured SSH user.",
	)

	helper_ok = False
	try:
		st = HELPER.lstat()
		digest = hashlib.sha256(HELPER.read_bytes()).hexdigest()
		helper_ok = (
			stat.S_ISREG(st.st_mode)
			and st.st_uid == 0
			and st.st_gid == 0
			and stat.S_IMODE(st.st_mode) == 0o755
			and digest == EXPECTED_HELPER_SHA256
		)
	except OSError:
		digest = None
	record(
		"helper_integrity",
		helper_ok,
		"Trusted helper is missing or its type, ownership, mode, or digest is incorrect.",
	)

	def artifact_integrity(path, expected_digest):
		try:
			st = path.lstat()
			digest = hashlib.sha256(path.read_bytes()).hexdigest()
			return (
				stat.S_ISREG(st.st_mode)
				and st.st_uid == 0
				and st.st_gid == 0
				and stat.S_IMODE(st.st_mode) == 0o755
				and digest == expected_digest
			)
		except OSError:
			return False

	policy_artifact_ok = artifact_integrity(POLICY_ARTIFACT, EXPECTED_POLICY_SHA256)
	inventory_artifact_ok = artifact_integrity(INVENTORY_ARTIFACT, EXPECTED_INVENTORY_SHA256)
	record(
		"policy_integrity", policy_artifact_ok, "Root-side install policy artifact is missing or untrusted."
	)
	record(
		"inventory_integrity",
		inventory_artifact_ok,
		"Root-side install inventory artifact is missing or untrusted.",
	)

	parent_paths = [
		Path("/usr/local"),
		Path("/usr/local/lib"),
		Path("/usr/local/lib/dt-master"),
		Path("/usr/local/lib/dt-master/docker"),
		Path("/etc/sudoers.d"),
	]
	parents_ok = True
	for path in parent_paths:
		try:
			st = path.lstat()
			parents_ok = parents_ok and stat.S_ISDIR(st.st_mode) and st.st_uid == 0 and st.st_gid == 0
			parents_ok = parents_ok and not (stat.S_IMODE(st.st_mode) & 0o022)
		except OSError:
			parents_ok = False
	record(
		"trusted_directories",
		parents_ok,
		"A trusted helper parent directory is missing, symlinked, incorrectly owned, or writable by group/others.",
	)

	# The managed policy is intentionally root:root 0440. Inspect it through
	# the node's pre-existing administrative sudo authority; never weaken its mode.
	metadata = run(["sudo", "-n", "stat", "-c", "%u:%g:%a", str(POLICY)])
	policy_content = run(["sudo", "-n", "cat", str(POLICY)])
	expected = f'{expected_user} ALL=(root) NOPASSWD: {HELPER} ""\n'
	policy_ok = (
		metadata is not None
		and metadata.returncode == 0
		and metadata.stdout.strip() == "0:0:440"
		and policy_content is not None
		and policy_content.returncode == 0
		and policy_content.stdout == expected
	)
	record(
		"sudoers_policy",
		policy_ok,
		"Managed sudoers file is missing or does not match the exact root-owned 0440 policy.",
	)

	visudo = run(["sudo", "-n", "visudo", "-cf", str(POLICY)])
	syntax_ok = visudo is not None and visudo.returncode == 0
	record("sudoers_syntax", syntax_ok, "visudo could not validate the managed sudoers policy.")

	allowed = False
	if policy_ok and actual_user == expected_user:
		permission = run(["sudo", "-n", "-l", str(HELPER), ""], timeout=5)
		allowed = permission is not None and permission.returncode == 0
	record(
		"effective_command",
		allowed,
		"The configured SSH identity is not authorized for the exact helper invocation.",
	)

	helper_result = None
	if helper_ok and policy_artifact_ok and inventory_artifact_ok and policy_ok and allowed:
		request = (
			json.dumps({"schema_version": "1.0.0", "operation": "self_test"}, separators=(",", ":")) + "\n"
		)
		response = run(["sudo", "-n", str(HELPER), ""], input_text=request, timeout=8)
		if response is not None and response.returncode == 0 and len(response.stdout) <= MAX_OUTPUT:
			try:
				helper_result = json.loads(response.stdout)
			except json.JSONDecodeError:
				helper_result = None
	self_test_ok = (
		isinstance(helper_result, dict)
		and set(helper_result)
		== {"schema_version", "helper_version", "operation", "status", "effective_uid", "effective_user"}
		and helper_result.get("schema_version") == "1.0.0"
		and helper_result.get("helper_version") == EXPECTED_HELPER_VERSION
		and helper_result.get("operation") == "self_test"
		and helper_result.get("status") == "ok"
		and helper_result.get("effective_uid") == 0
		and helper_result.get("effective_user") == "root"
	)
	record(
		"helper_self_test",
		self_test_ok,
		"The privileged helper self-test did not return the expected root identity response.",
	)

	failed = [item for item in checks if item["status"] == "Failed"]
	status = (
		"Ready"
		if not failed
		else (
			"Not Ready"
			if any(
				item["name"]
				in {
					"execution_identity",
					"helper_integrity",
					"policy_integrity",
					"inventory_integrity",
					"trusted_directories",
					"sudoers_policy",
					"effective_command",
				}
				for item in failed
			)
			else "Incomplete"
		)
	)
	result = {
		"schema_version": "1.0.0",
		"observed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
		"status": status,
		"execution_identity": actual_user or None,
		"helper": {
			"path": str(HELPER),
			"version": EXPECTED_HELPER_VERSION if helper_ok else None,
			"sha256": digest,
		},
		"sudoers": {"path": str(POLICY), "syntax": "Valid" if syntax_ok else "Invalid"},
		"checks": checks,
		"diagnostics": diagnostics,
	}
	print(json.dumps(result, separators=(",", ":")))
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
