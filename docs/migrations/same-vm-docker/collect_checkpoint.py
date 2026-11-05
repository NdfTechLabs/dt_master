#!/usr/bin/env python3
"""Collect/fetch only the pinned encrypted checkpoint. Default is a local dry run."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile


HERE = Path(__file__).resolve().parent
RECIPIENT = "E220A389F4E9105E6465C292F4708B832A07005F"
SSH = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=30", "ssh.ndftechlabs.com"]


def public_recipient(path):
    public = path.read_text()
    if not public.startswith("-----BEGIN PGP PUBLIC KEY BLOCK-----") or "PRIVATE KEY" in public:
        raise ValueError("Only a public recipient file is accepted")
    with tempfile.TemporaryDirectory(prefix="ndf-public-key-", dir="/tmp/opencode" if Path("/tmp/opencode").is_dir() else None) as home:
        result = subprocess.run(["gpg", "--no-options", "--homedir", home, "--batch", "--with-colons", "--show-keys", str(path)],
                                capture_output=True, text=True, check=True)
    fingerprints = [line.split(":")[9] for line in result.stdout.splitlines() if line.startswith("fpr:")]
    if not fingerprints or fingerprints[0] != RECIPIENT or len([line for line in result.stdout.splitlines() if line.startswith("pub:")]) != 1:
        raise ValueError("Public recipient does not match the approved fingerprint")
    return public


def private_directory(path):
    if path.resolve().is_relative_to(HERE.parents[2]):
        raise ValueError("Production backups must remain outside the Git repository")
    if path.is_symlink():
        raise ValueError("Backup destination must not be a symlink")
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    if path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o077:
        raise ValueError("Backup destination must be owned by you and mode 700")


def validate_receipt(receipt):
    name = receipt.get("checkpoint_id", "")
    if not re.fullmatch(r"\d{8}T\d{6}Z-dalali-[a-z0-9_]{8}", name):
        raise ValueError("Invalid checkpoint identity")
    expected = f"/var/backups/ndf-migration/{name}/checkpoint.tar.gz.gpg"
    if receipt.get("artifact") != expected or receipt.get("recipient") != RECIPIENT:
        raise ValueError("Unexpected encrypted artifact/recipient")
    if receipt.get("site") != "dalali.ndftechlabs.com" or receipt.get("host") != "mail.ndftechlabs.com":
        raise ValueError("Unexpected checkpoint host/site")
    if not re.fullmatch(r"[a-f0-9]{64}", receipt.get("sha256", "")):
        raise ValueError("Invalid artifact checksum")
    if not 0 < receipt.get("bytes", 0) <= 8 * 1024**3:
        raise ValueError("Invalid artifact size")


def fetch(receipt, destination):
    validate_receipt(receipt)
    target = destination / (receipt["checkpoint_id"] + ".tar.gz.gpg")
    partial = target.with_suffix(target.suffix + ".partial")
    if target.exists() or partial.exists():
        raise FileExistsError("A local artifact/partial already exists; inspect it before retrying")
    remote = "sudo -n cat -- " + shlex.quote(receipt["artifact"])
    with open(partial, "xb") as output:
        result = subprocess.run(SSH + [remote], stdout=output, timeout=3600)
        if result.returncode:
            raise RuntimeError("Encrypted transfer failed; receipt retained for fetch-only retry")
        output.flush()
        os.fsync(output.fileno())
    digest = hashlib.sha256()
    with open(partial, "rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    if partial.stat().st_size != receipt["bytes"] or digest.hexdigest() != receipt["sha256"]:
        raise RuntimeError("Encrypted transfer checksum/size mismatch; partial retained")
    partial.rename(target)
    print(json.dumps({"encrypted_backup": str(target), "sha256": receipt["sha256"], "bytes": receipt["bytes"],
                      "transfer_verified": True, "decryption_and_restore_verification": "pending operator/rehearsal"}))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit", type=Path)
    parser.add_argument("--public-key", type=Path)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--fetch-receipt", type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    if args.fetch_receipt:
        receipt = json.loads(args.fetch_receipt.read_text())
        validate_receipt(receipt)
        if not args.execute:
            print(json.dumps({"dry_run": True, "fetch_only": receipt["artifact"], "destination": str(args.destination)}))
            return
        private_directory(args.destination)
        fetch(receipt, args.destination)
        return
    if not args.audit or not args.public_key:
        parser.error("Collection requires --audit and --public-key")
    public = public_recipient(args.public_key)
    baseline = json.loads(args.audit.read_text())
    pilot = next(site for site in baseline["sites"] if site["site"] == "dalali.ndftechlabs.com")
    request = {"site": pilot["site"], "recipient": RECIPIENT, "public_key": public,
               "checkpoint_script_sha256": hashlib.sha256((HERE / "checkpoint.py").read_bytes()).hexdigest(),
               "collector_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
               "site_config_sha256": pilot["config_sha256"], "common_config_sha256": baseline["common_config_sha256"],
               "app_commits": {app["app"]: app["commit"] for app in baseline["apps"]},
               "clean_apps": [app["app"] for app in baseline["apps"] if app["dirty"] is False],
               "table_count": pilot["database_metadata"]["table_count"]}
    request["object_type_counts"] = pilot["database_metadata"].get("object_type_counts")
    if not request["object_type_counts"]:
        raise ValueError("Refresh audit.py inventory to classify base tables and sequences before collection")
    if not args.execute:
        print(json.dumps({"dry_run": True, "host": baseline["hostname"], "site": pilot["site"],
                          "recipient": RECIPIENT, "destination": str(args.destination), "table_count": request["table_count"],
                          "app_count": len(request["app_commits"]), "acquire_deadline_seconds": 5,
                          "object_type_counts": request["object_type_counts"],
                          "lock_watchdog_seconds": 55, "session_idle_fallback_seconds": 60,
                          "encrypted_artifact_limit_bytes": 8 * 1024**3}, indent=2))
        return
    private_directory(args.destination)
    if __import__("shutil").disk_usage(args.destination).free < 20 * 1024**3:
        raise RuntimeError("Insufficient local disk safety margin")
    code = "REQUEST = " + repr(request) + "\n" + (HERE / "checkpoint.py").read_text()
    result = subprocess.run(SSH + ["sudo -n nice -n 15 ionice -c 3 /home/frappe/bench/env/bin/python -"],
                            input=code, text=True, stdout=subprocess.PIPE, timeout=2400)
    if result.returncode:
        raise RuntimeError("Checkpoint collection failed; production service/configuration changes were not requested")
    receipt = json.loads(result.stdout)
    validate_receipt(receipt)
    receipt_path = args.destination / (receipt["checkpoint_id"] + ".receipt.json")
    with open(receipt_path, "x") as target:
        json.dump(receipt, target, indent=2)
    print("Encrypted server checkpoint finalized; transferring ciphertext", flush=True)
    fetch(receipt, args.destination)


if __name__ == "__main__":
    main()
