#!/usr/bin/env python3
"""Bounded public signing-key refresh; no app data, secrets or arbitrary URLs."""

import json
import os
from pathlib import Path
import shutil
import time
import urllib.request


URL = "https://ndftech.cloudflareaccess.com/cdn-cgi/access/certs"
DESTINATION = Path("/var/lib/ndf-dalali-stage-keys/jwks.json")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        raise RuntimeError("Signing-key redirects are not permitted")


def public_snapshot(raw, now):
    if len(raw) > 65536:
        raise RuntimeError("Signing-key metadata size exceeded")
    value = json.loads(raw)
    keys = value.get("keys") if isinstance(value, dict) else None
    if not isinstance(keys, list) or not 1 <= len(keys) <= 8:
        raise RuntimeError("Unexpected signing-key metadata")
    result = []
    for key in keys:
        if not isinstance(key, dict) or key.get("kty") != "RSA" or key.get("alg") not in (None, "RS256") or key.get("use") not in (None, "sig"):
            raise RuntimeError("Unexpected public key type")
        if any(name in key for name in ("d", "p", "q", "dp", "dq", "qi", "oth")):
            raise RuntimeError("Private material in public key response")
        if not all(isinstance(key.get(name), str) and key[name] for name in ("kid", "n", "e")):
            raise RuntimeError("Missing public key components")
        if len(key["kid"]) > 256 or len(key["n"]) > 1400 or len(key["e"]) > 16:
            raise RuntimeError("Unexpected public key component size")
        result.append({name: key[name] for name in ("kid", "kty", "n", "e")})
    if len({key["kid"] for key in result}) != len(result):
        raise RuntimeError("Ambiguous public key identifier")
    return {"fetched_at": int(now), "keys": result}


def main():
    if DESTINATION.parent.is_symlink() or not DESTINATION.parent.is_dir() or DESTINATION.is_symlink():
        raise RuntimeError("Unsafe public signing-key destination")
    budget = json.loads(Path("/opt/ndf-rootless/dalali-stage-v1/budget.json").read_text())
    free = shutil.disk_usage("/").free
    if free < 60 * 1024**3 or any(max(0, budget[name] - free) + 65536 > cap for name, cap in (
        ("stage_initial_free_bytes", 64 * 1024**2), ("ui_initial_free_bytes", 1024**3),
        ("rehearsal_initial_free_bytes", 8 * 1024**3))):
        raise RuntimeError("Staging disk/free-floor budget reached")
    opener = urllib.request.build_opener(NoRedirect)
    with opener.open(URL, timeout=10) as response:
        if response.status != 200 or response.geturl() != URL:
            raise RuntimeError("Unexpected signing-key endpoint")
        snapshot = public_snapshot(response.read(65537), time.time())
    temporary = DESTINATION.with_name("jwks.new")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, "w") as output:
        json.dump(snapshot, output);output.flush();os.fsync(output.fileno())
    temporary.replace(DESTINATION)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        raise SystemExit("Public staging signing-key refresh failed; no secret diagnostics") from None
