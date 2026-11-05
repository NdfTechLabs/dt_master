#!/usr/bin/env python3
"""Explicit, journaled rootless foundation application; default prints plan only."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess


HERE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    manifest = json.loads((HERE / "rootless-packages.json").read_text())
    expected = {"docker-ce", "docker-ce-cli", "docker-ce-rootless-extras", "docker-compose-plugin", "containerd.io"}
    if manifest.get("repository") != "https://download.docker.com/linux/ubuntu" or {p["name"] for p in manifest["packages"]} != expected:
        raise RuntimeError("Unexpected foundation package scope")
    if sum(p["bytes"] for p in manifest["packages"]) != manifest["total_package_bytes"]:
        raise RuntimeError("Package budget mismatch")
    for package in manifest["packages"]:
        if not re.fullmatch(r"[a-f0-9]{64}", package["sha256"]) or not package["filename"].startswith("dists/noble/pool/stable/amd64/") or ".." in package["filename"]:
            raise RuntimeError("Unpinned/unsafe package artifact")
    if not args.execute:
        print(json.dumps({"dry_run": True, "host": "mail.ndftechlabs.com", "user": "dt-containers",
                          "docker_package_bytes": manifest["total_package_bytes"], "stage_budget_bytes": 150_000_000,
                          "system_packages": ["uidmap", "slirp4netns", "libsubid4", "libslirp0"],
                          "docker_debs_installed_as_system_services": False,
                          "cpu_equivalents": 2, "memory_gib": 3, "customer_data_loaded": False}, indent=2))
        return
    source = (HERE / "rootless_foundation.py").read_text()
    request = {"manifest": manifest, "script_sha256": hashlib.sha256(source.encode()).hexdigest()}
    code = "REQUEST = " + repr(request) + "\n" + source
    result = subprocess.run(["ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
                             "-o", "ConnectTimeout=60", "ssh.ndftechlabs.com", "sudo -n nice -n 10 python3 -"],
                            input=code, text=True, timeout=1800)
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
