#!/usr/bin/env python3
"""Read-only verification of the managed Docker helper privilege boundary."""

import hashlib
import json
import os
import pwd
import re
import base64
import stat
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

RELEASE_MANIFEST = Path("/usr/local/lib/dt-master/docker/docker_helper_release.json")
HELPER = Path("/usr/local/lib/dt-master/docker/docker_lifecycle_helper.py")
POLICY_ARTIFACT = Path("/usr/local/lib/dt-master/docker/docker_install_policy.py")
INVENTORY_ARTIFACT = Path("/usr/local/lib/dt-master/docker/docker_install_inventory.py")
POLICY = Path("/etc/sudoers.d/dt-master-docker")
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


def load_release_manifest():
	try:
		expected_encoded = os.environ.get("EXPECTED_RELEASE_MANIFEST_B64", "")
		if not expected_encoded:
			return None
		expected_manifest = json.loads(base64.b64decode(expected_encoded, validate=True).decode("utf-8"))
		if not isinstance(expected_manifest, dict):
			return None
		metadata = RELEASE_MANIFEST.lstat()
		if (
			not stat.S_ISREG(metadata.st_mode)
			or metadata.st_uid != 0
			or metadata.st_gid != 0
			or stat.S_IMODE(metadata.st_mode) != 0o755
		):
			return None
		manifest = json.loads(RELEASE_MANIFEST.read_text(encoding="utf-8"))
		if (
			not isinstance(manifest, dict)
			or set(manifest) != {"schema_version", "release", "compatibility", "predecessors", "components", "lifecycle"}
			or manifest != expected_manifest
			or manifest["schema_version"] != "1.1.0"
			or not isinstance(manifest["release"], dict)
			or manifest["release"].get("status") != "supported"
			or not isinstance(manifest["predecessors"], list)
			or not re.fullmatch(r"[0-9]+\\.[0-9]+\\.[0-9]+(?:-[0-9A-Za-z.-]+)?", manifest["release"].get("version", ""))
		):
			return None
		components = manifest["components"]
		if not isinstance(components, list):
			return None
		expected = {}
		for component in components:
			if (
				not isinstance(component, dict)
				or set(component) != {"id", "path", "version", "sha256", "accepted_predecessor_sha256", "required", "responsibility"}
				or component["path"] not in {
					"docker_lifecycle_helper.py", "docker_install_policy.py", "docker_install_inventory.py"
				}
				or not isinstance(component["sha256"], str)
				or not __import__("re").fullmatch(r"[a-f0-9]{64}", component["sha256"])
				or not isinstance(component["accepted_predecessor_sha256"], list)
				or any(not isinstance(value, str) or not __import__("re").fullmatch(r"[a-f0-9]{64}", value) for value in component["accepted_predecessor_sha256"])
			):
				return None
			expected[component["path"]] = component["sha256"]
		if set(expected) != {
			"docker_lifecycle_helper.py", "docker_install_policy.py", "docker_install_inventory.py"
		}:
			return None
		if next(
			(component["version"] for component in components if component.get("id") == "lifecycle_helper"),
			None,
		) != manifest["release"]["version"]:
			return None
		return manifest["release"]["version"], expected
	except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
		return None


def main():
	expected_user = os.environ.get("EXPECTED_SSH_USER", "")
	checks = []
	diagnostics = []
	release = load_release_manifest()
	release_ok = release is not None
	expected_version, expected_digests = release if release_ok else (None, {})

	def record(name, ok, detail, *, skipped=False):
		status = "Skipped" if skipped else ("Passed" if ok else "Failed")
		checks.append({"name": name, "status": status})
		if skipped:
			diagnostics.append(
				{
					"code": "DOCKER_PRIVILEGE_" + name.upper() + "_SKIPPED",
					"severity": "Warning",
					"message": detail,
				}
			)
		elif not ok:
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

	record(
		"release_manifest_integrity",
		release_ok,
		"Installed Docker Helper Release manifest is missing, incompatible, or untrusted.",
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
			and digest == expected_digests.get(HELPER.name)
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

	policy_artifact_ok = artifact_integrity(POLICY_ARTIFACT, expected_digests.get(POLICY_ARTIFACT.name, ""))
	inventory_artifact_ok = artifact_integrity(INVENTORY_ARTIFACT, expected_digests.get(INVENTORY_ARTIFACT.name, ""))
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
	self_test_attempted = helper_ok and policy_artifact_ok and inventory_artifact_ok and policy_ok and allowed
	self_test_ok = (
		isinstance(helper_result, dict)
		and set(helper_result)
		== {"schema_version", "helper_version", "operation", "status", "effective_uid", "effective_user"}
		and helper_result.get("schema_version") == "1.0.0"
		and helper_result.get("helper_version") == expected_version
		and helper_result.get("operation") == "self_test"
		and helper_result.get("status") == "ok"
		and helper_result.get("effective_uid") == 0
		and helper_result.get("effective_user") == "root"
	)
	record(
		"helper_self_test",
		self_test_ok,
		"The privileged helper self-test did not return the expected root identity response."
		if self_test_attempted
		else "The privileged helper self-test was not run because prerequisite integrity or authorization checks failed.",
		skipped=not self_test_attempted,
	)

	failed = [item for item in checks if item["status"] == "Failed"]
	skipped = [item for item in checks if item["status"] == "Skipped"]
	status = (
		"Ready"
		if not failed and not skipped
		else (
			"Not Ready"
			if any(
				item["name"]
				in {
					"execution_identity",
					"release_manifest_integrity",
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
			"version": expected_version if helper_ok else None,
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
