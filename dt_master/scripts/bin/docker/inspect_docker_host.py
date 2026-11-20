#!/usr/bin/env python3
"""Read-only host inventory for Docker package and service lifecycle planning.

This probe deliberately performs no package metadata refresh, package install,
service mutation, Docker workload mutation, or privilege escalation.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = "1.0.0"
DEBIAN_PACKAGES = (
    "docker-ce",
    "docker-ce-cli",
    "containerd.io",
    "docker-buildx-plugin",
    "docker-compose-plugin",
    "docker.io",
    "docker-compose",
)
RPM_PACKAGES = (
    "docker-ce",
    "docker-ce-cli",
    "containerd.io",
    "docker-buildx-plugin",
    "docker-compose-plugin",
    "docker",
    "docker-compose",
)


def _run(argv: list[str], timeout: int = 5) -> tuple[int, str]:
    """Run a fixed, read-only system query with bounded output."""
    try:
        completed = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "LC_ALL": "C"},
        )
    except (OSError, subprocess.TimeoutExpired):
        return 127, ""
    return completed.returncode, completed.stdout[:16384]


def _os_release() -> dict:
    values = {}
    try:
        for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
            if "=" not in line or line.startswith("#"):
                continue
            key, value = line.split("=", 1)
            if key in {"ID", "ID_LIKE", "NAME", "VERSION_ID", "VERSION_CODENAME", "PRETTY_NAME"}:
                values[key] = value.strip().strip('"').strip("'")[:160]
    except OSError:
        pass
    return {
        "id": values.get("ID") or None,
        "id_like": values.get("ID_LIKE") or None,
        "name": values.get("NAME") or platform.system(),
        "version_id": values.get("VERSION_ID") or None,
        "version_codename": values.get("VERSION_CODENAME") or None,
        "pretty_name": values.get("PRETTY_NAME") or platform.platform()[:160],
        "architecture": platform.machine()[:64] or None,
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
            "dpkg-query", "-W",
            "-f=${binary:Package}\t${Version}\t${db:Status-Status}\n",
            *DEBIAN_PACKAGES,
        ]
    elif manager == "rpm":
        argv = ["rpm", "-q", "--qf", "%{NAME}\t%{VERSION}-%{RELEASE}\tinstalled\n", *RPM_PACKAGES]
    else:
        return []
    code, output = _run(argv, timeout=8)
    if code not in (0, 1):
        return []
    allowed = set(DEBIAN_PACKAGES if manager == "dpkg" else RPM_PACKAGES)
    packages = []
    for line in output.splitlines()[:32]:
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        name, version, status = (part.strip()[:160] for part in parts)
        if name not in allowed or not version or status != "installed":
            continue
        packages.append({"name": name, "version": version, "status": "installed"})
    return packages


def _service_state() -> dict:
    if not shutil.which("systemctl"):
        return {"manager": "Unknown", "docker": "Unknown", "containerd": "Unknown"}
    states = {}
    for service in ("docker", "containerd"):
        code, output = _run(["systemctl", "is-active", service], timeout=4)
        state = output.strip().splitlines()[0] if output.strip() else "Unknown"
        states[service] = state if state in {"active", "inactive", "failed", "activating", "deactivating"} else "Unknown"
    return {"manager": "systemd", **states}


def _workload_summary() -> dict:
    if not shutil.which("docker"):
        return {"available": False, "running_containers": None, "total_containers": None}
    # IDs only: never collect container names, labels, images, environment or logs.
    code, output = _run(["docker", "ps", "--all", "--quiet"], timeout=8)
    if code != 0:
        return {"available": False, "running_containers": None, "total_containers": None}
    total = min(len([line for line in output.splitlines() if line.strip()]), 100000)
    code, running_output = _run(["docker", "ps", "--quiet"], timeout=8)
    running = None
    if code == 0:
        running = min(len([line for line in running_output.splitlines() if line.strip()]), 100000)
    return {"available": True, "running_containers": running, "total_containers": total}


def collect() -> dict:
    manager = _package_manager()
    return {
        "schema_version": SCHEMA_VERSION,
        "observed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "host_os": _os_release(),
        "package_manager": manager,
        "docker_packages": _installed_packages(manager),
        "services": _service_state(),
        "workloads": _workload_summary(),
    }


if __name__ == "__main__":
    print(json.dumps(collect(), separators=(",", ":"), sort_keys=True))
