#!/usr/bin/env python3
"""Connect the existing clone as a guarded Guest website; no login/Desk or copies."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess


HERE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--reviewer-readiness-confirmed", action="store_true")
    parser.add_argument("--retry-rolled-back", action="store_true")
    parser.add_argument("--repair-owned-mapping", action="store_true")
    args = parser.parse_args()
    if not args.execute:
        print(json.dumps({"dry_run": True, "public_host": "d-stage.ndftechlabs.com", "existing_clone": "dalali-rehearsal.invalid",
            "mode": "guest-website-v1", "new_site_or_database": False, "native_changes": False,
            "clone_http_read_only": True, "login_desk_cart_private_files": "blocked"}))
        return
    if not args.reviewer_readiness_confirmed:
        parser.error("Require operator confirmation of protected readiness login before forwarding clone pages")
    names = ("stage_access.cjs", "stage_gateway.cjs", "stage_policy.cjs", "stage_reverse.cjs", "stage_live_probe.cjs")
    files = {name: (HERE / name).read_text() for name in names}
    for name in ("stage_access.test.cjs", "stage_gateway.test.cjs", "stage_reverse.test.cjs"):
        files[name] = (HERE.parents[2] / "tests" / name).read_text()
    helper = (HERE / "rehearsal_remote.py").read_text()
    remote = (HERE / "stage_publish_remote.py").read_text()
    request = {"phase": "stage-publish-guest", "files": files, "retry_rolled_back": args.retry_rolled_back,
        "repair_owned_mapping": args.repair_owned_mapping, "mapping_support": (HERE / "stage_mapping.py").read_text(),
        "readiness_confirmed": True, "old_gateway_sha256": "740c6de3be734cee67ad3193dbb7a0eb9ddd0f64e1a331c4f8e714f260c6f8c2",
        "route_policy": (HERE / "stage_route_policy.py").read_text(),
        "approval": "Operator confirmed corrected DNS and signed-in readiness, then asked to connect existing clone. Guarded Guest homepage only; clone-only read-only maintenance/public host overlays; no DB copy, native changes or business writes"}
    request["script_sha256"] = hashlib.sha256((helper + remote + json.dumps(request, sort_keys=True)).encode()).hexdigest()
    source = "REQUEST = " + repr(request) + "\nHELPER = " + repr(helper) + "\n" + remote
    result = subprocess.run(["ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=60",
        "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=2", "ssh.ndftechlabs.com", "sudo -n python3 -"],
        input=source, text=True, timeout=900)
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
