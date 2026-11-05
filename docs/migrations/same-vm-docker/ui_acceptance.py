#!/usr/bin/env python3
"""Explicit, phased VM UI acceptance; no final-sync/cutover side effects."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess


HERE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--phase", choices=("sandbox", "tools", "browser", "csp", "guard", "tests"), default="sandbox")
    args = parser.parse_args()
    if not args.execute:
        print(json.dumps({"dry_run": True, "phase": args.phase, "vm_only": True,
                          "ui_download_cap_bytes": 150_000_000, "ui_disk_cap_gib": 1,
                          "overall_rehearsal_disk_cap_gib": 8, "public_cutover": False, "source_change": False}))
        return
    helper = (HERE / "rehearsal_remote.py").read_text()
    policy = (HERE / "ui_seccomp.py").read_text()
    implementation = (HERE / "ui_acceptance_remote.py").read_text()
    request = {"phase": "ui-" + args.phase, "ui_phase": args.phase,
               "browser": json.loads((HERE / "ui-browser.json").read_text()),
               "tool_support": (HERE / "ui_tools.py").read_text(),
               "csp_support": (HERE / "ui_csp.py").read_text(),
               "proxy_source": (HERE / "ui_proxy.cjs").read_text(),
               "runner_source": (HERE / "ui_browser_runner.cjs").read_text(),
               "images": json.loads((HERE / "rehearsal-images.json").read_text()),
               "sources": json.loads((HERE / "source-manifest.json").read_text()),
               "script_sha256": hashlib.sha256((helper + policy + implementation).encode()).hexdigest(),
               "approval": "VM UI tests 150-MB/1-GiB within rehearsal cap; separately approved narrow browser-only seccomp/no-data probes and exact clone-only CSP unsafe-eval addition/nginx reload; no source/final-sync/cutover/cleanup"}
    source = "REQUEST = " + repr(request) + "\nHELPER = " + repr(helper) + "\nPOLICY = " + repr(policy) + "\n" + implementation
    result = subprocess.run(["ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=60",
                             "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=2", "ssh.ndftechlabs.com",
                             "sudo -n nice -n 15 ionice -c 3 python3 -"], input=source, text=True, timeout=3600)
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
