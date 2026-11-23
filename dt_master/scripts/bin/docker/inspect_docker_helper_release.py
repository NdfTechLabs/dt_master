#!/usr/bin/env python3
"""Inspect installed and staged Docker Helper artifacts against a supplied release manifest."""

import ast
import base64
import grp
import hashlib
import json
import os
import pwd
import re
import stat
from datetime import datetime, timezone
from pathlib import Path

INSTALLED_ROOT = Path("/usr/local/lib/dt-master/docker")
COMPONENT_IDS = {"lifecycle_helper", "install_policy", "host_inventory"}
EXPECTED_PATHS = {
	"lifecycle_helper": "docker_lifecycle_helper.py",
	"install_policy": "docker_install_policy.py",
	"host_inventory": "docker_install_inventory.py",
}
INSTALLER_FILES = (
	"install_docker_privilege_helper.sh",
	"validate_docker_helper_release.py",
	"install_docker_sudoers.sh",
)


def canonical_digest(value):
	encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
	return hashlib.sha256(encoded).hexdigest()


def load_expected_manifest():
	encoded = os.environ.get("EXPECTED_RELEASE_MANIFEST_B64", "")
	if not encoded:
		raise ValueError("expected release manifest was not supplied")
	manifest = json.loads(base64.b64decode(encoded, validate=True).decode("utf-8"))
	if (
		not isinstance(manifest, dict)
		or set(manifest)
		!= {"schema_version", "release", "compatibility", "predecessors", "components", "lifecycle"}
		or manifest["schema_version"] != "1.1.0"
		or not isinstance(manifest["release"], dict)
		or set(manifest["release"]) != {"id", "version", "channel", "status"}
		or manifest["release"]["status"] != "supported"
		or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,99}", manifest["release"]["id"])
		or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?", manifest["release"]["version"])
		or manifest["release"]["channel"] not in {"stable", "beta", "edge"}
		or not isinstance(manifest["components"], list)
	):
		raise ValueError("invalid expected release manifest")
	compatibility = manifest["compatibility"]
	predecessor_records = manifest["predecessors"]
	if (
		not isinstance(compatibility, dict)
		or not isinstance(compatibility.get("upgrade_from"), list)
		or any(not isinstance(version, str) for version in compatibility["upgrade_from"])
		or len(compatibility["upgrade_from"]) != len(set(compatibility["upgrade_from"]))
		or not isinstance(predecessor_records, list)
	):
		raise ValueError("invalid predecessor compatibility metadata")
	seen_predecessors = set()
	for predecessor in predecessor_records:
		if (
			not isinstance(predecessor, dict)
			or set(predecessor) != {"release_id", "version", "components"}
		):
			raise ValueError("invalid predecessor release record")
		release_id = predecessor["release_id"]
		version = predecessor["version"]
		predecessor_components = predecessor["components"]
		if (
			not isinstance(release_id, str)
			or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,99}", release_id)
			or not isinstance(version, str)
			or not re.fullmatch(r"[0-9]+\\.[0-9]+\\.[0-9]+(?:-[0-9A-Za-z.-]+)?", version)
			or (release_id, version) in seen_predecessors
			or version not in compatibility["upgrade_from"]
			or not isinstance(predecessor_components, list)
		):
			raise ValueError("invalid predecessor identity or compatibility")
		seen_predecessors.add((release_id, version))
		component_rows = {}
		for row in predecessor_components:
			if (
				not isinstance(row, dict)
				or set(row) != {"id", "version", "sha256"}
				or row["id"] not in COMPONENT_IDS
				or not isinstance(row["version"], str)
				or not re.fullmatch(r"[0-9]+\\.[0-9]+\\.[0-9]+(?:-[0-9A-Za-z.-]+)?", row["version"])
				or not isinstance(row["sha256"], str)
				or not re.fullmatch(r"[a-f0-9]{64}", row["sha256"])
				or row["id"] in component_rows
			):
				raise ValueError("invalid predecessor component fingerprint")
			component_rows[row["id"]] = row
		if set(component_rows) != COMPONENT_IDS:
			raise ValueError("incomplete predecessor component inventory")
	rows = {row.get("id"): row for row in manifest["components"] if isinstance(row, dict)}
	if set(rows) != COMPONENT_IDS or len(manifest["components"]) != len(COMPONENT_IDS):
		raise ValueError("invalid expected component inventory")
	for component_id, expected_path in EXPECTED_PATHS.items():
		row = rows[component_id]
		if (
			set(row)
			!= {
				"id",
				"path",
				"version",
				"sha256",
				"accepted_predecessor_sha256",
				"required",
				"responsibility",
			}
			or row["path"] != expected_path
			or not isinstance(row["version"], str)
			or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?", row["version"])
			or not isinstance(row["sha256"], str)
			or not re.fullmatch(r"[a-f0-9]{64}", row["sha256"])
			or not isinstance(row["accepted_predecessor_sha256"], list)
			or any(
				not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value)
				for value in row["accepted_predecessor_sha256"]
			)
			or type(row["required"]) is not bool
			or not isinstance(row["responsibility"], str)
		):
			raise ValueError("invalid expected component definition")
	return manifest, rows


VERSION_SYMBOLS = {
	"lifecycle_helper": "HELPER_VERSION",
	"install_policy": "POLICY_VERSION",
	"host_inventory": "INSTALL_INVENTORY_SCHEMA_VERSION",
}


def observed_component_version(path, symbol):
	"""Read a literal version declaration without importing an inspected artifact."""
	try:
		tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.name)
		for node in tree.body:
			if isinstance(node, ast.Assign):
				targets = node.targets
			elif isinstance(node, ast.AnnAssign):
				targets = [node.target]
			else:
				continue
			if any(isinstance(target, ast.Name) and target.id == symbol for target in targets):
				value = ast.literal_eval(node.value)
				return value if isinstance(value, str) else None
	except (OSError, SyntaxError, UnicodeError, ValueError, TypeError):
		return None
	return None


def inspect_components(directory, expected_rows, require_root):
	rows = []
	for component_id in sorted(COMPONENT_IDS):
		expected = expected_rows[component_id]
		artifact = directory / expected["path"]
		version = None
		try:
			info = artifact.lstat()
			if not stat.S_ISREG(info.st_mode) or artifact.is_symlink():
				raise ValueError("not a regular file")
			digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
			version = observed_component_version(artifact, VERSION_SYMBOLS[component_id])
			owner = pwd.getpwuid(info.st_uid).pw_name
			group = grp.getgrgid(info.st_gid).gr_name
			mode = format(stat.S_IMODE(info.st_mode), "o")
			valid = digest == expected["sha256"] and version == expected["version"]
			if require_root:
				valid = valid and owner == "root" and group == "root" and stat.S_IMODE(info.st_mode) == 0o755
			integrity = "verified" if valid else "mismatch"
		except FileNotFoundError:
			digest, version, owner, group, mode, integrity = (
				None,
				None,
				"unknown",
				"unknown",
				"unknown",
				"missing",
			)
		except (OSError, KeyError, ValueError):
			digest, version, owner, group, mode, integrity = (
				None,
				None,
				"unknown",
				"unknown",
				"unknown",
				"mismatch",
			)
		rows.append(
			{
				"id": component_id,
				"version": version if version is not None else "unknown",
				"expected_sha256": expected["sha256"],
				"observed_sha256": digest,
				"owner": owner,
				"group": group,
				"mode": mode,
				"integrity": integrity,
			}
		)
	return rows


def manifest_matches(path, expected, require_root=False):
	try:
		info = path.lstat()
		if not stat.S_ISREG(info.st_mode) or path.is_symlink():
			return False
		if require_root and (info.st_uid != 0 or info.st_gid != 0 or stat.S_IMODE(info.st_mode) != 0o755):
			return False
		return json.loads(path.read_text(encoding="utf-8")) == expected
	except (OSError, TypeError, ValueError, json.JSONDecodeError):
		return False


def regular_installer_files(directory):
	for filename in INSTALLER_FILES:
		path = directory / filename
		try:
			info = path.lstat()
		except OSError:
			return False
		if not stat.S_ISREG(info.st_mode) or path.is_symlink():
			return False
	return True


def classify(rows, manifest_ok, installer_ready):
	if all(row["integrity"] == "missing" for row in rows) and not manifest_ok:
		return "missing"
	if manifest_ok and all(row["integrity"] == "verified" for row in rows) and installer_ready:
		return "matching"
	return "mismatch"


def inspect():
	observed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
	manifest, expected_rows = load_expected_manifest()
	digest = canonical_digest(manifest)
	deployed_root = Path(os.environ["SCRIPT_BASE_DIR"])
	installed_rows = inspect_components(INSTALLED_ROOT, expected_rows, require_root=True)
	deployed_rows = inspect_components(deployed_root, expected_rows, require_root=False)
	installed_manifest_ok = manifest_matches(
		INSTALLED_ROOT / "docker_helper_release.json", manifest, require_root=True
	)
	deployed_manifest_ok = manifest_matches(deployed_root / "docker_helper_release.json", manifest)
	installer_ready = regular_installer_files(deployed_root)
	return {
		"schema_version": "1.0.0",
		"observed_at": observed_at,
		"expected_manifest_sha256": digest,
		"installed": {
			"status": classify(installed_rows, installed_manifest_ok, True),
			"manifest_matches": installed_manifest_ok,
			"installer_ready": True,
			"components": installed_rows,
		},
		"deployed": {
			"status": classify(deployed_rows, deployed_manifest_ok, installer_ready),
			"manifest_matches": deployed_manifest_ok,
			"installer_ready": installer_ready,
			"components": deployed_rows,
		},
	}


if __name__ == "__main__":
	try:
		result = inspect()
	except Exception:
		result = {
			"schema_version": "1.0.0",
			"observed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
			"expected_manifest_sha256": "0" * 64,
			"installed": {
				"status": "missing",
				"manifest_matches": False,
				"installer_ready": False,
				"components": [],
			},
			"deployed": {
				"status": "missing",
				"manifest_matches": False,
				"installer_ready": False,
				"components": [],
			},
		}
	print(json.dumps(result, sort_keys=True, separators=(",", ":")))
