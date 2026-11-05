#!/usr/bin/env python3
"""Approved isolated rehearsal, journaled phases; defaults to plan output only."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess


HERE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--phase", choices=("images", "isolation", "snapshot", "restore", "pdf", "application", "application-tests", "recovery", "clone-migrate", "validate"), default="images")
    parser.add_argument("--approve-clone-migration", action="store_true")
    args = parser.parse_args()
    if args.approve_clone_migration and args.phase != "clone-migrate":
        parser.error("--approve-clone-migration requires --phase clone-migrate")
    if args.execute and args.phase == "clone-migrate" and not args.approve_clone_migration:
        parser.error("Explicit --approve-clone-migration required")
    if not args.execute:
        print(json.dumps({"dry_run": True, "phase": args.phase, "project": "ndf-dalali-rehearsal-v1",
                          "download_cap_bytes": 180_000_000, "added_disk_cap_gib": 8, "host_free_floor_gib": 60,
                          "pilot_lock_max_seconds": 55, "native_service_changes": False, "cleanup": False}))
        return
    source = (HERE / "rehearsal_remote.py").read_text()
    request = {"phase": args.phase, "images": json.loads((HERE / "rehearsal-images.json").read_text()),
               "sources": json.loads((HERE / "source-manifest.json").read_text()),
               "pdf_wrapper": (HERE / "rehearsal-pdf-wrapper").read_text(),
               "http_probe": (HERE / "rehearsal_http_probe.py").read_text(),
               "clone_migration_approved": args.approve_clone_migration,
               "script_sha256": hashlib.sha256(source.encode()).hexdigest(),
               "approval": "Operator selected Approve full rehearsal: 180 MB downloads, 8 GiB additional disk, 55-second pilot lock; no cutover/cleanup"}
    if args.phase == "snapshot":
        request["checkpoint_support"] = (HERE / "checkpoint.py").read_text()
    result = subprocess.run(["ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
                             "-o", "ConnectTimeout=60", "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=2",
                             "ssh.ndftechlabs.com", "sudo -n nice -n 15 ionice -c 3 python3 -"],
                            input="REQUEST = " + repr(request) + "\n" + source, text=True, timeout=7200)
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
