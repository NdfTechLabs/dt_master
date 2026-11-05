#!/usr/bin/env python3
"""Operator-only hidden tunnel credential entry. Never starts a service."""

import argparse
import base64
import getpass
import json
import os
from pathlib import Path
import re
import socket
import sys
import uuid


DIRECTORY = Path("/etc/ndf-dalali-stage")


class TokenInputError(RuntimeError):
    """Only deliberately sanitized error messages, never decoder exceptions."""


def token_error(code, message):
    raise TokenInputError("Staging token [" + code + "]: " + message + "; nothing stored")


def expected_tunnel(expected_id, protected_config):
    try:
        canonical = str(uuid.UUID(expected_id))
        if canonical != expected_id.lower():raise ValueError()
    except Exception:
        token_error("expected-id", "--tunnel-id must be the new tunnel's UUID, not an Access application/policy ID")
    if canonical in protected_config.lower():
        token_error("protected-tunnel", "that UUID belongs to the protected shared connector, not a new staging tunnel")
    return canonical


def validate_token(value, expected_id, protected_config):
    canonical = expected_tunnel(expected_id, protected_config)
    if not isinstance(value, str) or not 1 <= len(value) <= 16384:
        token_error("empty-or-size", "paste only the complete connector token")
    if "\x1b" in value:
        token_error("terminal-escape", "terminal paste markers were received; use your terminal's plain-text paste")
    if value != value.strip():
        token_error("clipboard-whitespace", "extra whitespace or a newline was pasted around the token")
    if "cloudflared" in value or "--token" in value or value.startswith("sudo "):
        token_error("whole-command", "paste only the token, not the cloudflared command")
    if value.startswith(("'", '"')) or value.endswith(("'", '"')):
        token_error("clipboard-quotes", "paste the token without surrounding quotes")
    if not re.fullmatch(r"[A-Za-z0-9+/]+={0,2}", value):
        token_error("encoding", "input is not a complete standard-base64 Cloudflare connector token")
    try:
        decoded = base64.b64decode(value + "=" * (-len(value) % 4), validate=True)
    except Exception:
        token_error("encoding", "connector token base64 is incomplete or invalid")
    try:
        payload = json.loads(decoded)
    except Exception:
        token_error("payload-json", "decoded input is not connector-token JSON; do not use an Access JWT or AUD")
    if not isinstance(payload, dict):token_error("payload-type", "connector token must contain an object")
    try:
        actual = str(uuid.UUID(payload.get("t", "")))
    except Exception:
        token_error("token-id", "connector token has no valid tunnel UUID")
    if actual != canonical:
        token_error("tunnel-id-mismatch", "token belongs to tunnel " + actual + ", but command specifies " + canonical)
    if not isinstance(payload.get("a"), str) or not re.fullmatch(r"[a-fA-F0-9]{32}", payload["a"]):
        token_error("account-format", "connector token account field does not match the helper's expected format")
    if not isinstance(payload.get("s"), str):token_error("secret-format", "connector token has no encoded secret")
    try:
        secret = base64.b64decode(payload["s"], validate=True)
    except Exception:
        token_error("secret-format", "connector secret encoding does not match the helper's expected format")
    # Cloudflare documents a minimum, not an exact length. The outer 16-KiB
    # token limit above still bounds decoding/allocation for longer secrets.
    if len(secret) < 32:
        token_error("secret-length", "connector secret must contain at least 32 decoded bytes")
    return canonical


def main():
    parser = argparse.ArgumentParser(description="Store only a NEW staging tunnel credential privately; no connector startup")
    parser.add_argument("--tunnel-id", required=True)
    args = parser.parse_args()
    if os.geteuid() != 0 or socket.gethostname() != "mail.ndftechlabs.com" or not sys.stdin.isatty():
        raise RuntimeError("Use the approved VM interactive terminal with sudo and a TTY")
    if DIRECTORY.is_symlink() or not DIRECTORY.is_dir() or DIRECTORY.stat().st_uid != 0 or DIRECTORY.stat().st_mode & 0o077:
        raise RuntimeError("Private staging credential directory is not ready")
    target = DIRECTORY / "tunnel-token"
    receipt = DIRECTORY / "tunnel-credential-receipt.json"
    if target.exists() or target.is_symlink() or receipt.exists() or receipt.is_symlink():
        raise RuntimeError("Existing staging credential/receipt retained; ask before replacing")
    # Only inspect the shared connector's config text to reject accidental reuse
    # of its tunnel UUID. Do not open its credential file or export config text.
    shared = Path("/etc/cloudflared/config.yml").read_text()
    expected_tunnel(args.tunnel_id, shared)
    value = getpass.getpass("Paste only the NEW staging tunnel token (hidden): ")
    identifier = validate_token(value, args.tunnel_id, shared)
    os.umask(0o077)
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as output:
        output.write(value);output.flush();os.fsync(output.fileno())
    del value
    with open(receipt, "x") as output:
        json.dump({"tunnel_id": identifier, "credential_stored": True, "services_started": False}, output)
    print(json.dumps({"tunnel_id": identifier, "credential_stored": True, "services_started": False}))


if __name__ == "__main__":
    try:
        main()
    except TokenInputError as error:
        raise SystemExit(str(error)) from None
    except Exception as error:
        # Only deliberately chosen static errors; never display token/JSON bodies.
        allowed = ("Use the approved VM", "Private staging credential", "Existing staging credential")
        message = str(error)
        raise SystemExit(message if message.startswith(allowed) else "Staging credential storage failed; nothing printed") from None
