"""Docker Helper Release catalog fingerprinting.

The catalog is authoritative. Component hashes are calculated from the actual
application source files; the aggregate digest covers a canonical, sorted
representation of every deployable component definition.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath

_COMPONENT_ID = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_OWNER = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
_FILE_MODE = re.compile(r"^[0-7]{4}$")
_SOURCE_PREFIX = "scripts/bin/docker/"
_INSTALL_PREFIX = "/usr/local/lib/dt-master/docker/"

RELEASE_MANIFEST_SCHEMA_VERSION = "1.0.0"
COMPATIBILITY_FIELDS = (
	"helper_protocol",
	"privilege_readiness_contract",
	"docker_install_plan",
	"docker_install_policy",
	"inventory_schema",
)
LIFECYCLE_FIELDS = ("bootstrap", "upgrade", "reconcile", "activation_order", "verification")
MANIFEST_COMPONENT_IDS = {"lifecycle_helper", "install_policy", "host_inventory"}

COMPONENT_FINGERPRINT_FIELDS = (
	"component_id",
	"version",
	"source_path",
	"install_path",
	"required",
	"file_owner",
	"file_group",
	"file_mode",
	"sha256",
	"responsibility",
)


class DockerHelperReleaseError(ValueError):
	"""Invalid Docker Helper Release definition or source artifact."""


def _value(component, field):
	return component.get(field) if hasattr(component, "get") else getattr(component, field, None)


def validate_component_definition(component, *, require_digest=True):
	"""Validate a component row without accessing the filesystem."""
	component_id = _value(component, "component_id")
	version = _value(component, "version")
	source_path = _value(component, "source_path")
	install_path = _value(component, "install_path")
	required = _value(component, "required")
	owner = _value(component, "file_owner")
	group = _value(component, "file_group")
	mode = _value(component, "file_mode")
	digest = _value(component, "sha256")
	responsibility = _value(component, "responsibility")

	if not isinstance(component_id, str) or not _COMPONENT_ID.fullmatch(component_id):
		raise DockerHelperReleaseError("Invalid component identifier")
	if not isinstance(version, str) or not _VERSION.fullmatch(version):
		raise DockerHelperReleaseError(f"Invalid component version: {component_id}")
	if not isinstance(source_path, str):
		raise DockerHelperReleaseError(f"Invalid source path: {component_id}")
	source = PurePosixPath(source_path)
	if (
		source.is_absolute()
		or str(source) != source_path
		or not source_path.startswith(_SOURCE_PREFIX)
		or any(part in {"", ".", ".."} for part in source.parts)
	):
		raise DockerHelperReleaseError(f"Unsafe component source path: {component_id}")
	if not isinstance(install_path, str):
		raise DockerHelperReleaseError(f"Invalid install path: {component_id}")
	target = PurePosixPath(install_path)
	if (
		not target.is_absolute()
		or str(target) != install_path
		or not install_path.startswith(_INSTALL_PREFIX)
		or any(part in {"", ".", ".."} for part in target.parts)
	):
		raise DockerHelperReleaseError(f"Unsafe component install path: {component_id}")
	if type(required) not in {bool, int} or required not in (0, 1):
		raise DockerHelperReleaseError(f"Invalid required flag: {component_id}")
	if not isinstance(owner, str) or not _OWNER.fullmatch(owner):
		raise DockerHelperReleaseError(f"Invalid file owner: {component_id}")
	if not isinstance(group, str) or not _OWNER.fullmatch(group):
		raise DockerHelperReleaseError(f"Invalid file group: {component_id}")
	if not isinstance(mode, str) or not _FILE_MODE.fullmatch(mode):
		raise DockerHelperReleaseError(f"Invalid file mode: {component_id}")
	if require_digest and (not isinstance(digest, str) or not _SHA256.fullmatch(digest)):
		raise DockerHelperReleaseError(f"Invalid component SHA-256: {component_id}")
	if not isinstance(responsibility, str) or not responsibility.strip() or len(responsibility) > 500:
		raise DockerHelperReleaseError(f"Invalid component responsibility: {component_id}")


def component_source_digest(source_root: str | Path, source_path: str) -> str:
	"""Hash a regular, non-symlink source file confined to the application root."""
	validate_source_path = PurePosixPath(source_path)
	if (
		validate_source_path.is_absolute()
		or str(validate_source_path) != source_path
		or not source_path.startswith(_SOURCE_PREFIX)
		or any(part in {"", ".", ".."} for part in validate_source_path.parts)
	):
		raise DockerHelperReleaseError("Unsafe component source path")

	root = Path(source_root).resolve(strict=True)
	candidate = root.joinpath(*validate_source_path.parts)
	try:
		candidate.lstat()
	except OSError as exc:
		raise DockerHelperReleaseError("Component source file is missing") from exc
	if candidate.is_symlink() or not candidate.is_file():
		raise DockerHelperReleaseError("Component source must be a regular, non-symlink file")
	resolved = candidate.resolve(strict=True)
	if not resolved.is_relative_to(root):
		raise DockerHelperReleaseError("Component source escapes the application root")
	return hashlib.sha256(resolved.read_bytes()).hexdigest()


def canonical_component_records(components):
	"""Return validated component definitions in stable component-id order."""
	records = []
	seen = set()
	for component in components:
		record = {field: _value(component, field) for field in COMPONENT_FINGERPRINT_FIELDS}
		validate_component_definition(record)
		record["required"] = bool(record["required"])
		if record["component_id"] in seen:
			raise DockerHelperReleaseError("Duplicate Docker Helper Release component")
		seen.add(record["component_id"])
		records.append(record)
	if not records:
		raise DockerHelperReleaseError("A Docker Helper Release must contain components")
	return sorted(records, key=lambda item: item["component_id"])


def aggregate_release_digest(components) -> str:
	"""Calculate the release digest from canonical component definitions."""
	records = canonical_component_records(components)
	payload = json.dumps(records, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
	return hashlib.sha256(payload).hexdigest()


def refresh_component_digests(components, source_root: str | Path) -> str:
	"""Recalculate each component SHA-256 from its actual local source file."""
	for component in components:
		validate_component_definition(component, require_digest=False)
		digest = component_source_digest(source_root, _value(component, "source_path"))
		if isinstance(component, dict):
			component["sha256"] = digest
		else:
			component.sha256 = digest
	return aggregate_release_digest(components)


def _parse_json_object(value, label):
	"""Parse a JSON object stored in a Frappe Code field or accept a mapping."""
	if isinstance(value, str):
		try:
			value = json.loads(value)
		except json.JSONDecodeError as exc:
			raise DockerHelperReleaseError(f"Invalid {label} JSON") from exc
	if not isinstance(value, dict):
		raise DockerHelperReleaseError(f"{label} must be a JSON object")
	return value


def validate_release_metadata(compatibility, lifecycle):
	"""Validate the versioned compatibility and lifecycle declarations."""
	compatibility = _parse_json_object(compatibility, "compatibility")
	if set(compatibility) != {*COMPATIBILITY_FIELDS, "upgrade_from"}:
		raise DockerHelperReleaseError("Invalid release compatibility fields")
	for name in COMPATIBILITY_FIELDS:
		version_range = compatibility[name]
		if not isinstance(version_range, dict) or set(version_range) != {"minimum", "maximum"}:
			raise DockerHelperReleaseError(f"Invalid compatibility range: {name}")
		for bound in ("minimum", "maximum"):
			if not isinstance(version_range[bound], str) or not _VERSION.fullmatch(version_range[bound]):
				raise DockerHelperReleaseError(f"Invalid compatibility version: {name}")
		upgrade_from = compatibility["upgrade_from"]
		if (
			not isinstance(upgrade_from, list)
			or any(not isinstance(item, str) or not _VERSION.fullmatch(item) for item in upgrade_from)
			or len(upgrade_from) != len(set(upgrade_from))
		):
			raise DockerHelperReleaseError("Invalid upgrade predecessor list")

	lifecycle = _parse_json_object(lifecycle, "lifecycle")
	if set(lifecycle) != set(LIFECYCLE_FIELDS):
		raise DockerHelperReleaseError("Invalid release lifecycle fields")
	for name in ("bootstrap", "upgrade", "reconcile", "verification"):
		if not isinstance(lifecycle[name], str) or not lifecycle[name].strip() or len(lifecycle[name]) > 1000:
			raise DockerHelperReleaseError(f"Invalid lifecycle declaration: {name}")
	activation_order = lifecycle["activation_order"]
	if (
		not isinstance(activation_order, list)
		or any(not isinstance(item, str) for item in activation_order)
		or len(activation_order) != len(MANIFEST_COMPONENT_IDS)
		or set(activation_order) != MANIFEST_COMPONENT_IDS
	):
		raise DockerHelperReleaseError(
			"Lifecycle activation order must contain each release component exactly once"
		)
	return compatibility, lifecycle


def build_release_manifest(release, *, source_root: str | Path | None = None) -> dict:
	"""Project one published catalog release into the installer manifest contract.

	The Frappe DocType remains authoritative. This JSON shape is a deployment
	artifact consumed by the existing installer, readiness probe and root helper.
	"""

	def get(field):
		return _value(release, field)

	if get("status") != "Published":
		raise DockerHelperReleaseError("Only a published Docker Helper Release can be deployed")
	if get("schema_version") != RELEASE_MANIFEST_SCHEMA_VERSION:
		raise DockerHelperReleaseError("Unsupported Docker Helper Release manifest schema version")
	release_id = get("release_id")
	version = get("version")
	channel = get("channel")
	if not isinstance(release_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,99}", release_id):
		raise DockerHelperReleaseError("Invalid release identifier")
	if not isinstance(version, str) or not _VERSION.fullmatch(version):
		raise DockerHelperReleaseError("Invalid release version")
	if channel not in {"stable", "beta", "edge"}:
		raise DockerHelperReleaseError("Invalid release channel")

	compatibility, lifecycle = validate_release_metadata(get("compatibility"), get("lifecycle"))
	components = get("components") or []
	if source_root is not None:
		components = [
			{field: _value(component, field) for field in COMPONENT_FINGERPRINT_FIELDS}
			for component in components
		]
		refresh_component_digests(components, source_root)
	components = canonical_component_records(components)
	if {row["component_id"] for row in components} != MANIFEST_COMPONENT_IDS:
		raise DockerHelperReleaseError("Docker Helper Release component inventory is incomplete")

	manifest_components = []
	for row in components:
		filename = PurePosixPath(row["source_path"]).name
		if PurePosixPath(row["install_path"]).name != filename:
			raise DockerHelperReleaseError(f"Source and install filenames differ: {row['component_id']}")
		if (row["file_owner"], row["file_group"], row["file_mode"]) != ("root", "root", "0755"):
			raise DockerHelperReleaseError(f"Unsupported deployment ownership or mode: {row['component_id']}")
		manifest_components.append(
			{
				"id": row["component_id"],
				"path": filename,
				"version": row["version"],
				"sha256": row["sha256"],
				"required": row["required"],
				"responsibility": row["responsibility"],
			}
		)
	versions = {row["id"]: row["version"] for row in manifest_components}
	if versions["lifecycle_helper"] != version:
		raise DockerHelperReleaseError("Lifecycle helper version must equal the release version")

	return {
		"schema_version": RELEASE_MANIFEST_SCHEMA_VERSION,
		"release": {"id": release_id, "version": version, "channel": channel, "status": "supported"},
		"compatibility": compatibility,
		"components": manifest_components,
		"lifecycle": lifecycle,
	}


def serialize_release_manifest(release, *, source_root: str | Path | None = None) -> str:
	"""Return deterministic UTF-8 JSON; optionally hash the current local source files."""
	manifest = build_release_manifest(release, source_root=source_root)
	return json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
