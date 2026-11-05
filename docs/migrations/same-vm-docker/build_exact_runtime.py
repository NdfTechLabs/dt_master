#!/usr/bin/env python3
"""Explicit approved VM-side evaluation build; no site data or production mounts."""

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
    parser.add_argument("--plugin-only", action="store_true")
    parser.add_argument("--prepare-only", action="store_true", help="Stop after context checks; never build an image")
    parser.add_argument("--review-existing-context", action="store_true", help="Verify retained v2 context against source/runtime inputs and exact reviewed hashes")
    parser.add_argument("--approve-build", action="store_true", help="Record explicit operator go-ahead and archive the preparation hold before this build")
    parser.add_argument("--validate-retained-image", action="store_true", help="Verify exact canceled-export image and run no-data validation only; never build")
    args = parser.parse_args()
    if args.review_existing_context and (not args.prepare_only or args.plugin_only):
        parser.error("--review-existing-context requires --prepare-only, without --plugin-only")
    if args.approve_build and (args.prepare_only or args.plugin_only):
        parser.error("--approve-build cannot be combined with preparation/plugin-only mode")
    if args.validate_retained_image and (args.approve_build or args.prepare_only or args.plugin_only or args.review_existing_context):
        parser.error("--validate-retained-image is a separate validation-only mode")
    metadata = json.loads((HERE / "build-tool.json").read_text())
    if not args.execute:
        print(json.dumps({"dry_run": True, "vm_only": True, "plugin_only": args.plugin_only,
                          "prepare_only": args.prepare_only,
                          "review_existing_context": args.review_existing_context,
                          "approve_build": args.approve_build,
                          "validate_retained_image": args.validate_retained_image,
                          "download_cap_bytes": 20_000_000, "incremental_disk_cap_gib": 35,
                          "host_free_floor_gib": 60, "customer_data": False}))
        return
    request = {"tool": metadata, "plugin_only": args.plugin_only,
               "prepare_only": args.prepare_only,
               "review_existing_context": args.review_existing_context,
               "approve_build": args.approve_build,
               "validate_retained_image": args.validate_retained_image,
               "sources": json.loads((HERE / "source-manifest.json").read_text()),
               "recipe": {name: base64.b64encode((HERE / "exact-runtime" / name).read_bytes()).decode()
                          for name in ("Dockerfile", "ndf-native-exec")}}
    source = (HERE / "exact_runtime_remote.py").read_text()
    if args.review_existing_context:
        request["credential_review"] = json.loads((HERE / "credential-review.json").read_text())
    if args.validate_retained_image:
        request["retained_image"] = json.loads((HERE / "retained-image.json").read_text())
    request["script_sha256"] = hashlib.sha256(source.encode()).hexdigest()
    result = subprocess.run(["ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
                             "-o", "ConnectTimeout=60", "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=2", "ssh.ndftechlabs.com",
                             "sudo -n nice -n 15 ionice -c 3 python3 -"],
                            input="REQUEST = " + repr(request) + "\n" + source, text=True, timeout=7200)
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
