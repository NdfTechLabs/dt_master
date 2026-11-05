#!/usr/bin/env python3
"""Explicit, dry-run-default activation of only the separate readiness connector."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess


HERE = Path(__file__).resolve().parent
TUNNEL = "9388ab42-7fa1-429c-a21b-bca54d301486"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--route-confirmed", action="store_true")
    parser.add_argument("--readiness-rollback", action="store_true")
    args = parser.parse_args()
    if not args.execute:
        print(json.dumps({"dry_run": True, "tunnel_id": TUNNEL, "service": "ndf-dalali-stage-tunnel.service",
            "origin": "http://127.0.0.1:18089", "clone_origin_enabled": False, "requires_operator_route_confirmation": True,
            "changes_existing_connector": False}))
        return
    if not args.route_confirmed:
        parser.error("Operator must confirm saved whole-hostname route and Protect with Access before connector start")
    helper = (HERE / "rehearsal_remote.py").read_text()
    remote = (HERE / "stage_connect_remote.py").read_text()
    request = {"phase": "stage-connect-readiness", "tunnel_id": TUNNEL, "route_confirmed": True,
        "readiness_rollback": args.readiness_rollback,
        "approval": "Approve guarded staging; operator confirmed saved d-stage loopback-18089 route and enabled whole-hostname Protect with Access team/AUD; readiness only, no clone or existing service changes",
        "unit_support": (HERE / "stage_prepare_remote.py").read_text(),
        "route_policy": (HERE / "stage_route_policy.py").read_text(),
        "source_hashes": {name: hashlib.sha256((HERE / name).read_bytes()).hexdigest()
            for name in ("stage_access.cjs", "stage_gateway.cjs", "stage_jwks.py", "stage_store_token.py")}}
    if args.readiness_rollback:
        request["source_hashes"]["stage_gateway.cjs"] = "740c6de3be734cee67ad3193dbb7a0eb9ddd0f64e1a331c4f8e714f260c6f8c2"
    request["script_sha256"] = hashlib.sha256((helper + remote + json.dumps(request, sort_keys=True)).encode()).hexdigest()
    source = "REQUEST = " + repr(request) + "\nHELPER = " + repr(helper) + "\n" + remote
    result = subprocess.run(["ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=60",
        "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=2", "ssh.ndftechlabs.com", "sudo -n python3 -"],
        input=source, text=True, timeout=360)
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
