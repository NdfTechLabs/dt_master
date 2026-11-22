#!/usr/bin/env python3
"""Validate a Docker Helper Release manifest and its fixed source artifacts."""

import hashlib
import json
import re
import sys
from pathlib import Path

EXPECTED_COMPONENTS = {"lifecycle_helper", "install_policy", "host_inventory"}
EXPECTED_FIELDS = {"id", "path", "version", "sha256", "required", "responsibility"}


def main():
    if len(sys.argv) not in {3, 4}:
        raise SystemExit("usage: validate_docker_helper_release.py MANIFEST SOURCE_DIR [INSTALLED_MANIFEST]")
    manifest_path, source_dir = Path(sys.argv[1]), Path(sys.argv[2])
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if set(manifest) != {"schema_version", "release", "compatibility", "components", "lifecycle"}:
            raise ValueError("invalid manifest fields")
        if manifest["schema_version"] != "1.0.0":
            raise ValueError("unsupported manifest schema")
        release = manifest["release"]
        if set(release) != {"id", "version", "channel", "status"} or release["status"] != "supported":
            raise ValueError("unsupported release metadata")
        components = manifest["components"]
        if not isinstance(components, list) or {item.get("id") for item in components} != EXPECTED_COMPONENTS:
            raise ValueError("invalid component set")
        values = {}
        for item in components:
            if set(item) != EXPECTED_FIELDS:
                raise ValueError("invalid component fields")
            if (
                not isinstance(item["path"], str)
                or Path(item["path"]).name != item["path"]
                or not re.fullmatch(r"[a-f0-9]{64}", item["sha256"])
                or type(item["required"]) is not bool
                or not isinstance(item["version"], str)
            ):
                raise ValueError("invalid component metadata")
            artifact = source_dir / item["path"]
            if not artifact.is_file() or artifact.is_symlink():
                raise ValueError("component missing or symlinked: " + item["id"])
            digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
            if digest != item["sha256"]:
                raise ValueError("component digest mismatch: " + item["id"])
            values[item["id"]] = item
        if values["lifecycle_helper"]["version"] != release["version"]:
            raise ValueError("helper/release version mismatch")
        if len(sys.argv) == 4:
            installed = json.loads(Path(sys.argv[3]).read_text(encoding="utf-8"))
            if (
                not isinstance(installed, dict)
                or installed.get("schema_version") != manifest["schema_version"]
                or installed.get("release", {}).get("version")
                not in manifest["compatibility"].get("upgrade_from", [])
            ):
                raise ValueError("installed release is not a declared upgrade predecessor")
        for name in ("helper_protocol", "privilege_readiness_contract", "docker_install_plan",
                     "docker_install_policy", "inventory_schema"):
            version_range = manifest["compatibility"][name]
            if set(version_range) != {"minimum", "maximum"}:
                raise ValueError("invalid compatibility range: " + name)
            if not all(isinstance(version_range[key], str) for key in ("minimum", "maximum")):
                raise ValueError("invalid compatibility version: " + name)
        print(values["lifecycle_helper"]["sha256"])
        print(values["install_policy"]["sha256"])
        print(values["host_inventory"]["sha256"])
        print(release["version"])
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print("Invalid Docker Helper Release: " + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
