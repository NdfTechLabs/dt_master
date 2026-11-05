#!/usr/bin/env python3
"""Approved small offline corrective layer; no large rebuild/data/cleanup."""

import argparse
import base64
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
        print(json.dumps({"dry_run": True, "vm_only": True, "downloads": False, "customer_data": False,
                          "cleanup": False, "added_disk_cap_bytes": 100_000_000,
                          "overall_disk_cap_gib": 35, "new_tag": "ndf-local/exact-runtime:dalali-v2"}))
        return
    helper = (HERE / "exact_runtime_remote.py").read_text()
    implementation = (HERE / "native_yarn_remote.py").read_text()
    request = {"native_yarn": json.loads((HERE / "native-yarn.json").read_text()),
               "sources": json.loads((HERE / "source-manifest.json").read_text()),
               "retained_image": json.loads((HERE / "retained-image.json").read_text()),
               "recipe": {name: base64.b64encode((HERE / "yarn-layer" / name).read_bytes()).decode()
                          for name in ("Dockerfile", "Dockerfile.local-parent", "ndf-native-exec")},
               "script_sha256": hashlib.sha256((helper + implementation).encode()).hexdigest()}
    code = "REQUEST = " + repr(request) + "\nHELPER = " + repr(helper) + "\n" + implementation
    result = subprocess.run(["ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
                             "-o", "ConnectTimeout=60", "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=2",
                             "ssh.ndftechlabs.com", "sudo -n nice -n 15 ionice -c 3 python3 -"],
                            input=code, text=True, timeout=1800)
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
