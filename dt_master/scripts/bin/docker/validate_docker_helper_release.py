#!/usr/bin/env python3
"""Validate a Docker Helper Release manifest and its fixed source artifacts."""

import hashlib
import json
import re
import sys
from pathlib import Path

EXPECTED_COMPONENTS = {"lifecycle_helper", "install_policy", "host_inventory"}
EXPECTED_FIELDS = {
    "id", "path", "version", "sha256", "accepted_predecessor_sha256",
    "required", "responsibility",
}
COMPATIBILITY_FIELDS = {
    "helper_protocol", "privilege_readiness_contract", "docker_install_plan",
    "docker_install_policy", "inventory_schema",
}


def load_manifest(path):
    manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    if set(manifest) != {"schema_version", "release", "compatibility", "components", "lifecycle"}:
        raise ValueError("invalid manifest fields")
    if manifest["schema_version"] != "1.1.0":
        raise ValueError("unsupported manifest schema")
    release = manifest["release"]
    if set(release) != {"id", "version", "channel", "status"} or release["status"] != "supported":
        raise ValueError("unsupported release metadata")
    compatibility = manifest["compatibility"]
    if not isinstance(compatibility, dict) or not COMPATIBILITY_FIELDS | {"upgrade_from"} <= set(compatibility):
        raise ValueError("incomplete compatibility metadata")
    upgrade_from = compatibility["upgrade_from"]
    if (
        not isinstance(upgrade_from, list)
        or any(not isinstance(version, str) for version in upgrade_from)
        or len(upgrade_from) != len(set(upgrade_from))
    ):
        raise ValueError("invalid upgrade_from versions")
    for name in COMPATIBILITY_FIELDS:
        version_range = compatibility[name]
        if set(version_range) != {"minimum", "maximum"} or not all(
            isinstance(version_range[key], str) for key in ("minimum", "maximum")
        ):
            raise ValueError("invalid compatibility range: " + name)

    components = manifest["components"]
    if not isinstance(components, list) or {item.get("id") for item in components} != EXPECTED_COMPONENTS:
        raise ValueError("invalid component set")
    values = {}
    all_hashes = set()
    for item in components:
        if set(item) != EXPECTED_FIELDS:
            raise ValueError("invalid component fields")
        predecessors = item["accepted_predecessor_sha256"]
        if (
            not isinstance(item["path"], str)
            or Path(item["path"]).name != item["path"]
            or not re.fullmatch(r"[a-f0-9]{64}", item["sha256"])
            or not isinstance(predecessors, list)
            or any(not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value)
                   for value in predecessors)
            or len(predecessors) != len(set(predecessors))
            or item["sha256"] in predecessors
            or type(item["required"]) is not bool
            or not isinstance(item["version"], str)
        ):
            raise ValueError("invalid component metadata: " + str(item.get("id")))
        if all_hashes.intersection(predecessors + [item["sha256"]]):
            raise ValueError("duplicate component digest")
        all_hashes.update(predecessors + [item["sha256"]])
        values[item["id"]] = item
    if values["lifecycle_helper"]["version"] != release["version"]:
        raise ValueError("helper/release version mismatch")
    return manifest, values


def load_installed_manifest(path):
    """Read predecessor metadata without requiring the current manifest schema."""
    installed = json.loads(Path(path).read_text(encoding="utf-8"))
    if (
        not isinstance(installed, dict)
        or installed.get("schema_version") not in {"1.0.0", "1.1.0"}
        or not isinstance(installed.get("release"), dict)
        or not isinstance(installed["release"].get("version"), str)
    ):
        raise ValueError("installed release manifest has unsupported metadata")
    return installed


def main():
    if len(sys.argv) not in {3, 4, 5}:
        raise SystemExit(
            "usage: validate_docker_helper_release.py MANIFEST SOURCE_DIR "
            "[INSTALLED_MANIFEST] [--accepted-hashes COMPONENT]"
        )
    manifest_path, source_dir = Path(sys.argv[1]), Path(sys.argv[2])
    try:
        manifest, values = load_manifest(manifest_path)
        for component_id, item in values.items():
            artifact = source_dir / item["path"]
            if not artifact.is_file() or artifact.is_symlink():
                raise ValueError("component missing or symlinked: " + component_id)
            digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
            if digest != item["sha256"]:
                raise ValueError("component digest mismatch: " + component_id)

        if len(sys.argv) >= 4 and sys.argv[3] != "--accepted-hashes":
            installed = load_installed_manifest(sys.argv[3])
            if installed["release"]["version"] not in manifest["compatibility"]["upgrade_from"]:
                raise ValueError("installed release is not a declared upgrade predecessor")
        if len(sys.argv) >= 4 and sys.argv[-2] == "--accepted-hashes":
            component_id = sys.argv[-1]
            if component_id not in values:
                raise ValueError("unknown component: " + component_id)
            item = values[component_id]
            print(item["sha256"])
            print("\n".join(item["accepted_predecessor_sha256"]))
            return 0

        print(values["lifecycle_helper"]["sha256"])
        print(values["install_policy"]["sha256"])
        print(values["host_inventory"]["sha256"])
        print(manifest["release"]["version"])
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print("Invalid Docker Helper Release: " + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
