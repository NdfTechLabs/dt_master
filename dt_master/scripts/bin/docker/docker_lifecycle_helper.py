#!/usr/bin/env python3
"""Trusted Docker lifecycle helper bootstrap.

Only a harmless self-test is implemented in N2.4.2. Privileged package
operations are intentionally unavailable until their own reviewed increment.
"""
import json
import os
import sys

HELPER_VERSION = "0.1.0"
MAX_REQUEST_BYTES = 16 * 1024


def reject_constant(value):
    raise ValueError(f"Invalid JSON constant: {value}")


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def main():
    raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
    if len(raw) > MAX_REQUEST_BYTES:
        raise ValueError("Request exceeds size limit")
    request = json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=unique_object,
        parse_constant=reject_constant,
    )
    if not isinstance(request, dict):
        raise ValueError("Request must be a JSON object")
    if set(request) != {"schema_version", "operation"}:
        raise ValueError("Unsupported request fields")
    if request["schema_version"] != "1.0.0":
        raise ValueError("Unsupported request schema version")
    if request["operation"] != "self_test":
        raise ValueError("Operation is not enabled in this helper version")
    response = {
        "schema_version": "1.0.0",
        "helper_version": HELPER_VERSION,
        "operation": "self_test",
        "status": "ok",
        "effective_uid": os.geteuid(),
        "effective_user": "root" if os.geteuid() == 0 else "non-root",
    }
    sys.stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        sys.stderr.write(f"DOCKER_HELPER_REQUEST_INVALID: {exc}\n")
        raise SystemExit(2)
