#!/usr/bin/env python3
"""Read-only host inventory for Docker package and service lifecycle planning.

The legacy top-level fields remain stable for inspect_docker.sh consumers.
The additive docker_install_inventory object is the strict N3.2 planner input.
This probe never refreshes APT metadata or mutates packages, services or workloads.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = "1.0.0"
INSTALL_INVENTORY_SCHEMA_VERSION = "1.0.0"
DEBIAN_PACKAGES = (
	"docker-ce",
	"docker-ce-cli",
	"containerd.io",
	"docker-buildx-plugin",
	"docker-compose-plugin",
	"docker.io",
	"docker-compose",
	"podman-docker",
	"containerd",
	"runc",
)
RPM_PACKAGES = (
	"docker-ce",
	"docker-ce-cli",
	"containerd.io",
	"docker-buildx-plugin",
	"docker-compose-plugin",
	"docker",
	"docker-compose",
	"podman-docker",
	"containerd",
	"runc",
)
ENGINE_PACKAGES = ("docker-ce", "docker-ce-cli", "containerd.io", "docker-buildx-plugin")
CONFLICT_PACKAGES = ("docker.io", "docker-compose", "podman-docker", "containerd", "runc")
DOCKER_SOURCE_DOMAIN = "download.docker.com"
DOCKER_KEYRING = "/etc/apt/keyrings/docker.asc"
APT_LISTS = Path("/var/lib/apt/lists")
APT_ARCHIVES = Path("/var/cache/apt/archives")
ROOT = Path("/")


def _run(argv: list[str], timeout: int = 5) -> tuple[int, str]:
	"""Run a fixed read-only query with bounded output and a sanitized environment."""
	try:
		env = dict(os.environ)
		for key in ("DOCKER_CONTEXT", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH"):
			env.pop(key, None)
		env.update({"DOCKER_HOST": "unix:///var/run/docker.sock", "LC_ALL": "C"})
		completed = subprocess.run(
			argv,
			check=False,
			capture_output=True,
			text=True,
			timeout=timeout,
			env=env,
		)
	except (OSError, subprocess.TimeoutExpired):
		return 127, ""
	return completed.returncode, completed.stdout[:32768]


def _read_text(path: Path, limit: int = 32768) -> str:
	try:
		return path.read_text(encoding="utf-8", errors="replace")[:limit]
	except OSError:
		return ""


def _os_release() -> dict:
	values = {}
	for line in _read_text(Path("/etc/os-release"), 8192).splitlines():
		if "=" not in line or line.startswith("#"):
			continue
		key, value = line.split("=", 1)
		if key in {"ID", "ID_LIKE", "NAME", "VERSION_ID", "VERSION_CODENAME", "PRETTY_NAME"}:
			values[key] = value.strip().strip('"').strip("'")[:160]
	return {
		"id": values.get("ID") or None,
		"id_like": values.get("ID_LIKE") or None,
		"name": values.get("NAME") or platform.system(),
		"version_id": values.get("VERSION_ID") or None,
		"version_codename": values.get("VERSION_CODENAME") or None,
		"pretty_name": values.get("PRETTY_NAME") or platform.platform()[:160],
		"architecture": {"x86_64": "amd64", "aarch64": "arm64"}.get(platform.machine(), platform.machine())[
			:64
		]
		or None,
		"kernel_release": platform.release()[:128] or None,
	}


def _package_manager() -> str:
	if shutil.which("dpkg-query"):
		return "dpkg"
	if shutil.which("rpm"):
		return "rpm"
	if shutil.which("apk"):
		return "apk"
	if shutil.which("pacman"):
		return "pacman"
	return "Unknown"


def _installed_packages(manager: str) -> list[dict]:
	if manager == "dpkg":
		argv = [
			"dpkg-query",
			"-W",
			"-f=${binary:Package}\\t${Version}\\t${db:Status-Status}\\n",
			*DEBIAN_PACKAGES,
		]
	elif manager == "rpm":
		argv = ["rpm", "-q", "--qf", "%{NAME}\\t%{VERSION}-%{RELEASE}\\tinstalled\\n", *RPM_PACKAGES]
	else:
		return []
	code, output = _run(argv, timeout=8)
	if code not in (0, 1):
		return []
	allowed = set(DEBIAN_PACKAGES if manager == "dpkg" else RPM_PACKAGES)
	packages = []
	for line in output.splitlines()[:64]:
		parts = line.split("\t")
		if len(parts) != 3:
			continue
		name, version, status = (part.strip()[:160] for part in parts)
		if name in allowed and version and status == "installed":
			packages.append({"name": name, "version": version, "status": "installed"})
	return packages


def _service_state() -> dict:
	if not shutil.which("systemctl"):
		return {"manager": "Unknown", "docker": "Unknown", "containerd": "Unknown"}
	states = {}
	for service in ("docker", "containerd"):
		_, output = _run(["systemctl", "is-active", service], timeout=4)
		state = output.strip().splitlines()[0] if output.strip() else "Unknown"
		states[service] = (
			state if state in {"active", "inactive", "failed", "activating", "deactivating"} else "Unknown"
		)
	return {"manager": "systemd", **states}


def _docker_query(argv: list[str]) -> tuple[int, str]:
	"""Observe Docker using the SSH identity so fingerprints match unprivileged inspection."""
	user = os.environ.get("DT_DOCKER_WORKLOAD_USER")
	if not user:
		return _run(argv, timeout=8)
	if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", user) or not shutil.which("runuser"):
		return 127, ""
	return _run(["runuser", "-u", user, "--", *argv], timeout=8)


def _workload_summary() -> dict:
	if not shutil.which("docker"):
		return {"available": False, "running_containers": None, "total_containers": None}
	# Count only; never collect container names, IDs, labels, images, environment or logs.
	code, output = _docker_query(["docker", "ps", "--all", "--quiet"])
	if code != 0:
		return {"available": False, "running_containers": None, "total_containers": None}
	total = min(sum(bool(line.strip()) for line in output.splitlines()), 100000)
	code, output = _docker_query(["docker", "ps", "--quiet"])
	if code != 0:
		return {"available": False, "running_containers": None, "total_containers": None}
	running = min(sum(bool(line.strip()) for line in output.splitlines()), 100000)
	return {"available": True, "running_containers": running, "total_containers": total}


def _apt_package_rows() -> list[dict]:
	rows = []
	held_code, held_output = _run(["apt-mark", "showhold"], timeout=5)
	held = set(held_output.splitlines()) if held_code == 0 else set()
	for name in (*ENGINE_PACKAGES, *CONFLICT_PACKAGES):
		code, output = _run(
			[
				"dpkg-query",
				"-W",
				"-f=${Version}\\t${db:Status-Status}",
				name,
			],
			timeout=5,
		)
		installed_version, dpkg_status = None, "unknown"
		if code == 0:
			parts = output.strip().split("\t", 1)
			if len(parts) == 2:
				installed_version = parts[0][:160] or None
				dpkg_status = parts[1][:40]
		elif code == 1:
			dpkg_status = "not-installed"

		policy_code, policy = _run(["apt-cache", "policy", name], timeout=6)
		candidate, candidate_origin, installed_origin = None, None, None
		selected_version = None
		if policy_code == 0:
			for line in policy.splitlines():
				stripped = line.strip()
				if stripped.startswith("Candidate:"):
					value = stripped.partition(":")[2].strip()
					candidate = value if value not in {"(none)", ""} else None
				elif stripped[:1].isdigit() and DOCKER_SOURCE_DOMAIN in stripped:
					origin = stripped.split()[1][:512]
					if candidate and selected_version == candidate and candidate_origin is None:
						candidate_origin = origin
					if (
						installed_version
						and selected_version == installed_version
						and installed_origin is None
						and DOCKER_SOURCE_DOMAIN in origin
					):
						installed_origin = origin
				elif stripped.startswith("***") or (
					stripped and stripped[0].isdigit() and len(stripped.split()) >= 2
				):
					version_tokens = stripped.lstrip("*").split()
					selected_version = version_tokens[0] if version_tokens else None
		rows.append(
			{
				"name": name,
				"installed_version": installed_version if dpkg_status == "installed" else None,
				"installed_origin": installed_origin,
				"dpkg_status": dpkg_status,
				"candidate_version": candidate,
				"candidate_origin": candidate_origin,
				"held": name in held,
			}
		)
	return rows


def _parse_gpg_key_inventory(output: str) -> tuple[list[str], list[dict]]:
	"""Parse GnuPG colon output while distinguishing primary keys from subkeys."""
	fingerprints = []
	primary_keys = []
	current_primary = None
	current_record = None

	for line in output.splitlines():
		fields = line.split(":")
		record = fields[0]
		if record == "pub":
			current_primary = {"fingerprint": None, "identity": None, "subkey_fingerprints": []}
			primary_keys.append(current_primary)
			current_record = "pub"
		elif record == "sub":
			current_record = "sub" if current_primary is not None else None
		elif record == "fpr" and len(fields) > 9:
			fingerprint = fields[9].upper()
			if not fingerprint:
				continue
			fingerprints.append(fingerprint)
			if current_record == "pub" and current_primary is not None:
				current_primary["fingerprint"] = fingerprint
			elif current_record == "sub" and current_primary is not None:
				current_primary["subkey_fingerprints"].append(fingerprint)
			current_record = None
		elif record == "uid" and current_primary is not None and len(fields) > 9:
			if current_primary["identity"] is None:
				current_primary["identity"] = fields[9]

	return fingerprints[:8], primary_keys[:8]


def _repository_evidence() -> dict:
	source_text = ""
	for path in (
		Path("/etc/apt/sources.list.d/docker.sources"),
		Path("/etc/apt/sources.list.d/docker.list"),
		Path("/etc/apt/sources.list"),
	):
		source_text += "\n" + _read_text(path, 16384)
	source_configured = DOCKER_SOURCE_DOMAIN in source_text
	signed_by = None
	for line in source_text.splitlines():
		stripped = line.strip()
		if stripped.lower().startswith("signed-by:"):
			signed_by = stripped.partition(":")[2].strip().split()[0]
			break
		if "signed-by=" in stripped.lower():
			for token in stripped.split():
				if token.lower().startswith("signed-by="):
					signed_by = token.split("=", 1)[1].strip("[]")
					break
	fingerprints = []
	primary_keys = []
	if Path(DOCKER_KEYRING).is_file() and shutil.which("gpg"):
		code, output = _run(
			[
				"gpg",
				"--batch",
				"--no-options",
				"--show-keys",
				"--with-colons",
				DOCKER_KEYRING,
			],
			timeout=8,
		)
		if code == 0:
			fingerprints, primary_keys = _parse_gpg_key_inventory(output)
	return {
		"source_id": DOCKER_SOURCE_DOMAIN if source_configured else None,
		"keyring_path": signed_by
		if signed_by
		else (DOCKER_KEYRING if Path(DOCKER_KEYRING).is_file() else None),
		"fingerprints": fingerprints,
		"primary_keys": primary_keys,
	}


def _apt_metadata() -> dict:
	try:
		files = [
			p
			for p in APT_LISTS.iterdir()
			if p.is_file() and DOCKER_SOURCE_DOMAIN in p.name and not p.name.endswith(("lock", "partial"))
		]
		newest = max((p.stat().st_mtime for p in files), default=None)
	except OSError:
		newest = None
	if newest is None:
		return {"status": "Unavailable", "age_seconds": None}
	age = max(0, int(datetime.now(timezone.utc).timestamp() - newest))
	status = "Fresh" if age <= 86400 else "Aging" if age <= 604800 else "Stale"
	return {"status": status, "age_seconds": age}


def _package_manager_state() -> dict:
	audit_code, audit_output = _run(["dpkg", "--audit"], timeout=8)
	audit_clean = audit_code == 0 and not audit_output.strip()
	lock_paths = (
		Path("/var/lib/dpkg/lock-frontend"),
		Path("/var/lib/dpkg/lock"),
		Path("/var/lib/apt/lists/lock"),
		Path("/var/cache/apt/archives/lock"),
	)
	# Fail closed when the lock-inspection utility is unavailable; a missing
	# observer is not evidence that dpkg/APT is unlocked.
	locked = not bool(shutil.which("fuser"))
	if not locked:
		for lock_path in lock_paths:
			code, output = _run(["fuser", str(lock_path)], timeout=3)
			if code == 0 and output.strip():
				locked = True
				break
	return {"locked": locked, "dpkg_audit_clean": audit_clean}


def _storage() -> dict:
	try:
		root_free = shutil.disk_usage(ROOT).free
	except OSError:
		root_free = 0
	try:
		cache_free = shutil.disk_usage(APT_ARCHIVES if APT_ARCHIVES.exists() else ROOT).free
	except OSError:
		cache_free = 0
	return {"root_available_bytes": int(root_free), "apt_cache_available_bytes": int(cache_free)}


def _docker_engine_health(services: dict, workloads: dict) -> str:
	if not shutil.which("docker"):
		return "not_installed"
	code, output = _docker_query(["docker", "info", "--format", "{{.ServerVersion}}"])
	if code == 0 and output.strip():
		return "healthy"
	return "unhealthy" if services.get("docker") in {"active", "failed", "activating"} else "unavailable"


def _install_inventory(host: dict, manager: str, services: dict, workloads: dict) -> dict:
	if manager != "dpkg":
		return {
			"schema_version": INSTALL_INVENTORY_SCHEMA_VERSION,
			"observed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
			"host_os": {
				"id": host["id"],
				"version_id": host["version_id"],
				"codename": host["version_codename"],
				"architecture": host["architecture"],
			},
			"package_manager": manager,
			"packages": [],
			"repository": {"source_id": None, "keyring_path": None, "fingerprints": []},
			"apt_metadata": {"status": "Unavailable", "age_seconds": None},
			"package_manager_state": {"locked": False, "dpkg_audit_clean": False},
			"storage": _storage(),
			"services": {"docker": services["docker"], "containerd": services["containerd"]},
			"workloads": workloads,
			"docker_engine_health": _docker_engine_health(services, workloads),
		}
	return {
		"schema_version": INSTALL_INVENTORY_SCHEMA_VERSION,
		"observed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
		"host_os": {
			"id": host["id"],
			"version_id": host["version_id"],
			"codename": host["version_codename"],
			"architecture": host["architecture"],
		},
		"package_manager": "apt",
		"packages": _apt_package_rows(),
		"repository": _repository_evidence(),
		"apt_metadata": _apt_metadata(),
		"package_manager_state": _package_manager_state(),
		"storage": _storage(),
		"services": {"docker": services["docker"], "containerd": services["containerd"]},
		"workloads": workloads,
		"docker_engine_health": _docker_engine_health(services, workloads),
	}


def collect() -> dict:
	manager = _package_manager()
	host = _os_release()
	services = _service_state()
	workloads = _workload_summary()
	return {
		"schema_version": SCHEMA_VERSION,
		"observed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
		"host_os": host,
		"package_manager": manager,
		"docker_packages": _installed_packages(manager),
		"services": services,
		"workloads": workloads,
		"docker_install_inventory": _install_inventory(host, manager, services, workloads),
	}


if __name__ == "__main__":
	print(json.dumps(collect(), separators=(",", ":"), sort_keys=True))
