#!/usr/bin/env python3
"""Read-only inspection of the installed Docker Helper Release and its artifacts."""

import hashlib
import json
import os
import pwd
import grp
import re
import stat
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/usr/local/lib/dt-master/docker")
MANIFEST = ROOT / "docker_helper_release.json"
COMPONENT_PATHS = {
    "lifecycle_helper": "docker_lifecycle_helper.py",
    "install_policy": "docker_install_policy.py",
    "host_inventory": "docker_install_inventory.py",
}


def canonical_digest(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def inspect():
    observed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    try:
        metadata = MANIFEST.lstat()
        if not (
            stat.S_ISREG(metadata.st_mode)
            and metadata.st_uid == 0
            and metadata.st_gid == 0
            and stat.S_IMODE(metadata.st_mode) == 0o755
        ):
            raise ValueError("untrusted manifest metadata")
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        if (
            not isinstance(manifest, dict)
            or set(manifest) != {"schema_version", "release", "compatibility", "components", "lifecycle"}
            or manifest["schema_version"] != "1.0.0"
            or not isinstance(manifest["release"], dict)
            or set(manifest["release"]) != {"id", "version", "channel", "status"}
            or manifest["release"]["status"] != "supported"
            or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,99}", manifest["release"]["id"])
            or not re.fullmatch(r"[0-9]+\\.[0-9]+\\.[0-9]+(?:-[0-9A-Za-z.-]+)?", manifest["release"]["version"])
            or manifest["release"]["channel"] not in {"stable", "beta", "edge"}
            or set(manifest["compatibility"]) != {
                "helper_protocol", "privilege_readiness_contract", "docker_install_plan",
                "docker_install_policy", "inventory_schema", "upgrade_from"
            }
            or set(manifest["lifecycle"]) != {
                "bootstrap", "upgrade", "reconcile", "activation_order", "verification"
            }
            or not isinstance(manifest["components"], list)
        ):
            raise ValueError("unrecognized manifest contract")
        component_rows = {row["id"]: row for row in manifest["components"]}
        if set(component_rows) != set(COMPONENT_PATHS) or len(manifest["components"]) != len(COMPONENT_PATHS):
            raise ValueError("unrecognized component inventory")
        rows = []
        for component_id, filename in COMPONENT_PATHS.items():
            row = component_rows[component_id]
            if (
                set(row) != {"id", "path", "version", "sha256", "required", "responsibility"}
                or row["path"] != filename
                or not isinstance(row["version"], str)
                or not re.fullmatch(r"[0-9]+\\.[0-9]+\\.[0-9]+(?:-[0-9A-Za-z.-]+)?", row["version"])
                or not isinstance(row["sha256"], str)
                or not re.fullmatch(r"[a-f0-9]{64}", row["sha256"])
                or type(row["required"]) is not bool
                or not isinstance(row["responsibility"], str)
                or not row["responsibility"].strip()
            ):
                raise ValueError("invalid component definition")
            artifact = ROOT / filename
            try:
                info = artifact.lstat()
                digest = hashlib.sha256(artifact.read_bytes()).hexdigest() if stat.S_ISREG(info.st_mode) else None
                owner = pwd.getpwuid(info.st_uid).pw_name
                group = grp.getgrgid(info.st_gid).gr_name
                mode = format(stat.S_IMODE(info.st_mode), "o")
                valid = (
                    stat.S_ISREG(info.st_mode)
                    and not artifact.is_symlink()
                    and owner == "root"
                    and group == "root"
                    and stat.S_IMODE(info.st_mode) == 0o755
                    and digest == row["sha256"]
                )
            except (OSError, KeyError):
                digest, owner, group, mode, valid = None, "unknown", "unknown", "unknown", False
            rows.append({
                "id": component_id,
                "version": row["version"],
                "expected_sha256": row["sha256"],
                "observed_sha256": digest,
                "owner": owner,
                "group": group,
                "mode": mode,
                "integrity": "verified" if valid else "mismatch",
            })
        release = manifest["release"]
        return {
            "schema_version": "1.0.0",
            "observed_at": observed_at,
            "status": "recognized",
            "manifest": {
                "id": release["id"],
                "version": release["version"],
                "channel": release["channel"],
                "schema_version": manifest["schema_version"],
                "sha256": canonical_digest(manifest),
            },
            "components": rows,
        }
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return {
            "schema_version": "1.0.0",
            "observed_at": observed_at,
            "status": "unrecognized",
            "manifest": None,
            "components": [],
        }


if __name__ == "__main__":
    print(json.dumps(inspect(), sort_keys=True, separators=(",", ":")))
