#!/usr/bin/env python3
"""Explicitly approved clone-only template CSP compatibility change."""

import os
import stat
import time


ORIGINAL_CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; "
                "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                "connect-src 'self'; form-action 'self'; frame-src 'none'; "
                "object-src 'none'; base-uri 'self'")
APPROVED_CSP = ORIGINAL_CSP.replace("script-src 'self' 'unsafe-inline';",
                                    "script-src 'self' 'unsafe-inline' 'unsafe-eval';")


def approved_config(original):
    header = ('add_header Content-Security-Policy "' + ORIGINAL_CSP + '" always;').encode()
    if original.count(header) != 1 or original.count(b"Content-Security-Policy") != 1:
        raise RuntimeError("Unexpected clone CSP; no broader policy change authorized")
    return original.replace(header, ('add_header Content-Security-Policy "' + APPROVED_CSP + '" always;').encode(), 1)


def apply(rehearsal, namespace, state, work, deploy):
    name = namespace["PROJECT"] + "-frontend-tmpfs"
    frontend = rehearsal.owned("container", name)
    path = deploy / "nginx-tmpfs.conf"
    destination = "/etc/ndf-rehearsal-nginx.conf"
    mounts = [m for m in frontend["Mounts"] if m["Destination"] == destination]
    if len(mounts) != 1 or mounts[0]["Source"] != str(path) or mounts[0]["RW"]:
        raise RuntimeError("Unexpected clone frontend config mount")
    if not frontend["State"]["Running"] or frontend["Config"]["Image"] != namespace["APP_IMAGE"]:
        raise RuntimeError("Unexpected clone frontend identity/state")
    if path.is_symlink() or not stat.S_ISREG(path.stat().st_mode):
        raise RuntimeError("Unsafe clone config path")
    backup = work / "nginx-before-approved-csp.conf"
    current = path.read_bytes()
    if backup.exists():
        if backup.is_symlink():raise RuntimeError("Unsafe retained config path")
        original = backup.read_bytes()
    else:
        original = current
        approved_config(original)
        with open(backup, "xb") as output:output.write(original)
        backup.chmod(0o600)
    changed = approved_config(original)
    if current not in (original, changed):
        raise RuntimeError("Clone config changed outside exact approved diff")
    inode = path.stat().st_ino
    def write(data):
        # Preserve the inode referenced by the existing read-only bind mount.
        fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
        with os.fdopen(fd, "r+b") as output:
            if os.fstat(output.fileno()).st_ino != inode:
                raise RuntimeError("Clone config inode changed")
            output.write(data);output.truncate();output.flush();os.fsync(output.fileno())
    def nginx(*args):
        return rehearsal.docker(["exec", name, "/usr/sbin/nginx", "-c", destination, *args], timeout=30)
    if current != changed:write(changed)
    try:
        nginx("-t")
    except Exception:
        write(original)
        nginx("-t")
        raise
    try:
        nginx("-s", "reload")
    except Exception:
        write(original);nginx("-t");nginx("-s", "reload")
        raise
    # Reload is asynchronous: old workers may answer until the new generation
    # starts. Permit only the exact old header during this bounded transition.
    policies = []
    for _ in range(20):
        headers = rehearsal.run(["curl", "--silent", "--show-error", "--max-time", "10",
            "--dump-header", "-", "--output", "/dev/null", "--header", "Host: " + namespace["CLONE_SITE"],
            "http://127.0.0.1:18088/api/method/ping"], timeout=15)
        policies = [line.split(":", 1)[1].strip() for line in headers.splitlines()
                    if line.lower().startswith("content-security-policy:")]
        if policies != [ORIGINAL_CSP]:break
        time.sleep(0.5)
    if policies != [APPROVED_CSP]:
        write(original);nginx("-t");nginx("-s", "reload")
        raise RuntimeError("Served clone CSP differs; original policy restored")
    state["approved_clone_csp"] = {"original_sha256": namespace["digest"](backup),
        "config_sha256": namespace["digest"](path), "frontend_id": frontend["Id"],
        "original_retained": True, "host_policy_changed": False}
    rehearsal.save()
    rehearsal.health()
    rehearsal.progress('{"approved_clone_csp_applied":true,"clone_nginx_reloaded":true,"host_policy_changed":false}')
