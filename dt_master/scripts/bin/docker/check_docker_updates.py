#!/usr/bin/env python3
"""Read-only Docker package update metadata probe; cached APT indexes only."""
from __future__ import annotations
import json, os, shutil, subprocess, time
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = "1.0.0"
PACKAGES = ("docker-ce", "docker-ce-cli", "containerd.io", "docker-buildx-plugin", "docker-compose-plugin")
FRESH_SECONDS = 86400
STALE_SECONDS = 604800

def _run(argv, timeout=6):
    try:
        env = dict(os.environ)
        env.update({"LC_ALL": "C", "LANG": "C"})
        result = subprocess.run(argv, check=False, capture_output=True, text=True, timeout=timeout, env=env)
    except (OSError, subprocess.TimeoutExpired):
        return 127, ""
    return result.returncode, result.stdout[:16384]

def _metadata_age(now=None):
    now = time.time() if now is None else now
    try:
        stamps = [p.stat().st_mtime for p in Path("/var/lib/apt/lists").iterdir()
                  if p.is_file() and not p.name.endswith((".lock", ".partial"))]
    except OSError:
        stamps = []
    if not stamps:
        return {"status": "Unavailable", "age_seconds": None, "newest_index_at": None}
    newest = max(stamps)
    age = max(0, int(now - newest))
    status = "Fresh" if age <= FRESH_SECONDS else ("Aging" if age <= STALE_SECONDS else "Stale")
    return {"status": status, "age_seconds": age,
            "newest_index_at": datetime.fromtimestamp(newest, timezone.utc).isoformat().replace("+00:00", "Z")}

def _policy(package):
    code, output = _run(["apt-cache", "policy", package])
    if code != 0 or not output:
        return {"name": package, "installed_version": None, "candidate_version": None,
                "candidate_origin": None, "status": "Unavailable"}
    installed = candidate = origin = None
    for raw in output.splitlines():
        line = raw.strip()
        if line.startswith("Installed:"):
            installed = line.partition(":")[2].strip()
        elif line.startswith("Candidate:"):
            candidate = line.partition(":")[2].strip()
        elif candidate and line and line[0].isdigit() and origin is None:
            parts = line.split()
            if len(parts) >= 2 and ("://" in parts[1] or parts[1].startswith("/")):
                origin = parts[1][:300]
    installed = installed if installed not in (None, "(none)") else None
    candidate = candidate if candidate not in (None, "(none)") else None
    if candidate is None:
        status = "No Candidate"
    elif installed is None:
        status = "Not Installed"
    elif candidate == installed:
        status = "Up To Date"
    else:
        status = "Update Available"
    return {"name": package, "installed_version": installed, "candidate_version": candidate,
            "candidate_origin": origin, "status": status}

def collect():
    observed = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    manager = "apt" if shutil.which("apt-cache") and shutil.which("dpkg-query") else "Unsupported"
    freshness = _metadata_age()
    if manager != "apt":
        return {"schema_version": SCHEMA_VERSION, "observed_at": observed, "package_manager": manager,
                "metadata": freshness, "packages": [], "overall_status": "Unsupported"}
    packages = [_policy(name) for name in PACKAGES]
    if freshness["status"] in {"Stale", "Unavailable"}:
        overall = "Unknown"
    elif any(p["status"] == "Update Available" for p in packages):
        overall = "Updates Available"
    elif any(p["status"] in {"Unavailable", "No Candidate", "Not Installed"} for p in packages):
        overall = "Partial"
    else:
        overall = "Up To Date"
    if freshness["status"] == "Aging" and overall == "Up To Date":
        overall = "Metadata Aging"
    return {"schema_version": SCHEMA_VERSION, "observed_at": observed, "package_manager": manager,
            "metadata": freshness, "packages": packages, "overall_status": overall}

if __name__ == "__main__":
    print(json.dumps(collect(), separators=(",", ":"), sort_keys=True))
