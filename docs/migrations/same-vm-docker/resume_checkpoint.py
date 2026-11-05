#!/usr/bin/env python3
"""Resume ciphertext only; never collect/dump/lock production data."""

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess


HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("ndf_checkpoint_collector", HERE / "collect_checkpoint.py")
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)


def file_hash(path):
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)


def remote_program(request):
    return "REQUEST = " + repr(request) + "\n" + '''
import hashlib,json,os,stat,subprocess,sys
if os.geteuid()!=0 or subprocess.check_output(['hostname'],text=True).strip()!=REQUEST['host']:
    raise RuntimeError('Unexpected checkpoint host/account')
fd=os.open(REQUEST['artifact'],os.O_RDONLY|os.O_NOFOLLOW)
with os.fdopen(fd,'rb') as source:
    before=os.fstat(source.fileno())
    if not stat.S_ISREG(before.st_mode) or before.st_uid!=0 or before.st_mode&0o077 or before.st_size!=REQUEST['bytes']:
        raise RuntimeError('Unexpected checkpoint file/size/permissions')
    offset=REQUEST['offset']
    if not 0<=offset<=before.st_size:
        raise RuntimeError('Invalid resume offset')
    signature={'dev':before.st_dev,'ino':before.st_ino,'mtime_ns':before.st_mtime_ns,'ctime_ns':before.st_ctime_ns}
    if REQUEST['mode']=='metadata':
        full=hashlib.sha256();prefix=hashlib.sha256();position=0
        while True:
            block=source.read(1024*1024)
            if not block:break
            full.update(block)
            prefix.update(block[:max(0,min(len(block),offset-position))])
            position+=len(block)
        after=os.fstat(source.fileno())
        if (after.st_size,after.st_mtime_ns,after.st_ctime_ns)!=(before.st_size,before.st_mtime_ns,before.st_ctime_ns):
            raise RuntimeError('Checkpoint changed while hashing')
        print(json.dumps({'sha256':full.hexdigest(),'prefix_sha256':prefix.hexdigest(),'signature':signature}))
    elif REQUEST['mode']=='suffix':
        if signature!=REQUEST['signature']:
            raise RuntimeError('Checkpoint changed after validation')
        source.seek(offset)
        remaining=before.st_size-offset
        while remaining:
            block=source.read(min(1024*1024,remaining))
            if not block:raise RuntimeError('Unexpected checkpoint EOF')
            sys.stdout.buffer.write(block)
            remaining-=len(block)
        sys.stdout.buffer.flush()
        after=os.fstat(source.fileno())
        if (after.st_size,after.st_mtime_ns,after.st_ctime_ns)!=(before.st_size,before.st_mtime_ns,before.st_ctime_ns):
            raise RuntimeError('Checkpoint changed during transfer')
    else:
        raise RuntimeError('Invalid checkpoint operation')
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    receipt = json.loads(args.receipt.read_text())
    collector.validate_receipt(receipt)
    directory = args.receipt.parent
    collector.private_directory(directory)
    target = directory / (receipt["checkpoint_id"] + ".tar.gz.gpg")
    partial = target.with_suffix(target.suffix + ".partial")
    if target.exists():
        if target.stat().st_size != receipt["bytes"] or file_hash(target) != receipt["sha256"]:
            raise RuntimeError("Existing final artifact does not match receipt")
        print(json.dumps({"already_complete": True, "sha256_verified": True, "file": str(target)}))
        return
    before = partial.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid() or before.st_mode & 0o077:
        raise RuntimeError("Unsafe local partial")
    if not 0 < before.st_size <= receipt["bytes"]:
        raise RuntimeError("Unexpected partial length")
    remaining = receipt["bytes"] - before.st_size
    if not args.execute:
        print(json.dumps({"dry_run": True, "downloaded_bytes": before.st_size, "remaining_bytes": remaining,
                          "network_scope": "read-only hashes plus ciphertext suffix only"}))
        return
    local_prefix = file_hash(partial)
    if identity(partial.lstat()) != identity(before):
        raise RuntimeError("Local partial changed during hashing")
    if remaining:
        request = {**receipt, "offset": before.st_size, "mode": "metadata"}
        command = collector.SSH + ["sudo -n nice -n 15 ionice -c 3 /home/frappe/bench/env/bin/python -"]
        response = subprocess.run(command, input=remote_program(request), text=True, capture_output=True,
                                  check=True, timeout=600)
        metadata = json.loads(response.stdout)
        if metadata["sha256"] != receipt["sha256"] or metadata["prefix_sha256"] != local_prefix:
            raise RuntimeError("Server artifact/local prefix mismatch; refusing to redownload or append")
        request.update(mode="suffix", signature=metadata["signature"])
        fd = os.open(partial, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW)
        with os.fdopen(fd, "ab") as output:
            if identity(os.fstat(output.fileno())) != identity(before):
                raise RuntimeError("Local partial changed before append")
            print(json.dumps({"prefix_verified": True, "resuming_bytes": remaining}), flush=True)
            result = subprocess.run(command, input=remote_program(request).encode(), stdout=output, timeout=3600)
            output.flush()
            os.fsync(output.fileno())
            if result.returncode:
                raise RuntimeError("Suffix transfer interrupted; partial retained, no automatic retry")
    if partial.stat().st_size != receipt["bytes"] or file_hash(partial) != receipt["sha256"]:
        raise RuntimeError("Completed artifact checksum/size mismatch; partial retained")
    # Finalize without overwriting any existing file created concurrently.
    os.link(partial, target, follow_symlinks=False)
    partial.unlink()
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    print(json.dumps({"file": str(target), "downloaded_this_run_bytes": remaining,
                      "total_bytes": receipt["bytes"], "sha256": receipt["sha256"],
                      "transfer_verified": True, "decryption_restore_verification": "pending",
                      "production_mutation": False}))


if __name__ == "__main__":
    main()
