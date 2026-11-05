#!/usr/bin/env python3
"""Dry-run-default readiness-only staging preparation; no public/clone activation."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess


HERE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not args.execute:
        print(json.dumps({"dry_run": True, "phase": "readiness", "hostname": "d-stage.ndftechlabs.com",
            "loopback_port": 18089, "clone_origin_enabled": False, "start_tunnel": False,
            "dns_change": False, "new_binary_downloads": False, "stage_disk_cap_mib": 64}))
        return
    files = {name: (HERE / name).read_text() for name in ("stage_access.cjs", "stage_gateway.cjs", "stage_jwks.py", "stage_store_token.py")}
    for name in ("stage_access.test.cjs", "stage_gateway.test.cjs"):
        files[name] = (HERE.parents[2] / "tests" / name).read_text()
    helper = (HERE / "rehearsal_remote.py").read_text()
    remote = (HERE / "stage_prepare_remote.py").read_text()
    request = {"phase": "stage-readiness", "files": files,
        "script_sha256": hashlib.sha256((helper + remote + json.dumps(files, sort_keys=True)).encode()).hexdigest(),
        "approval": "Operator selected Approve guarded staging: separate bounded services, manual-browser boundary, no-data guards before public clone; no native/SSH/prod route change"}
    source = "REQUEST = " + repr(request) + "\nHELPER = " + repr(helper) + "\n" + remote
    result = subprocess.run(["ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=60",
        "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=2", "ssh.ndftechlabs.com", "sudo -n nice -n 15 ionice -c 3 python3 -"],
        input=source, text=True, timeout=600)
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
