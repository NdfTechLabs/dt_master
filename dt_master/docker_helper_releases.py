"""Docker Helper Release catalog fingerprinting.

The catalog is authoritative. Component hashes are calculated from the actual
application source files; the aggregate digest covers a canonical, sorted
representation of every deployable component definition.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path, PurePosixPath

_COMPONENT_ID = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_OWNER = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
_FILE_MODE = re.compile(r"^[0-7]{4}$")
_SOURCE_PREFIX = "scripts/bin/docker/"
_INSTALL_PREFIX = "/usr/local/lib/dt-master/docker/"

RELEASE_MANIFEST_SCHEMA_VERSION = "1.1.0"
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
	"accepted_predecessor_sha256",
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
	predecessors = _value(component, "accepted_predecessor_sha256")
	if predecessors is None:
		predecessors = []
	if isinstance(predecessors, str):
		try:
			predecessors = json.loads(predecessors or "[]")
		except json.JSONDecodeError as exc:
			raise DockerHelperReleaseError(f"Invalid accepted predecessor hashes: {component_id}") from exc
	if (
		not isinstance(predecessors, list)
		or any(not isinstance(value, str) or not _SHA256.fullmatch(value) for value in predecessors)
		or len(predecessors) != len(set(predecessors))
		or (isinstance(digest, str) and digest in predecessors)
	):
		raise DockerHelperReleaseError(f"Invalid accepted predecessor hashes: {component_id}")
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
		if record["accepted_predecessor_sha256"] is None:
			record["accepted_predecessor_sha256"] = []
		validate_component_definition(record)
		record["required"] = bool(record["required"])
		if isinstance(record["accepted_predecessor_sha256"], str):
			record["accepted_predecessor_sha256"] = json.loads(record["accepted_predecessor_sha256"] or "[]")
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


_SOURCE_VERSION_SYMBOLS = {
	"lifecycle_helper": {"version": "HELPER_VERSION", "protocol": "REQUEST_SCHEMA_VERSION"},
	"install_policy": {"version": "POLICY_VERSION"},
	"host_inventory": {"version": "INSTALL_INVENTORY_SCHEMA_VERSION"},
}


def _literal_module_constants(source_path: Path, names: set[str]) -> dict[str, str]:
	"""Read simple module-level string constants without importing executable source."""
	try:
		tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
	except (OSError, SyntaxError, UnicodeError) as exc:
		raise DockerHelperReleaseError(
			f"Cannot inspect component version declarations: {source_path.name}"
		) from exc
	values = {}
	for node in tree.body:
		if isinstance(node, ast.Assign):
			targets = node.targets
		elif isinstance(node, ast.AnnAssign):
			targets = [node.target]
		else:
			continue
		for target in targets:
			if isinstance(target, ast.Name) and target.id in names:
				try:
					value = ast.literal_eval(node.value)
				except (ValueError, TypeError):
					continue
				if isinstance(value, str):
					values[target.id] = value
	missing = names - values.keys()
	if missing:
		raise DockerHelperReleaseError(
			f"Component version declarations are missing or non-literal in {source_path.name}: {', '.join(sorted(missing))}"
		)
	return values


def inspect_release_source(components, source_root: str | Path) -> dict:
	"""Capture authoritative source hashes, component versions and compatibility contracts."""
	root = Path(source_root).resolve(strict=True)
	observed = {}
	compatibility = {}
	for component in components:
		validate_component_definition(component)
		component_id = _value(component, "component_id")
		if component_id in observed:
			raise DockerHelperReleaseError("Duplicate Docker Helper Release component")
		path = root.joinpath(*PurePosixPath(_value(component, "source_path")).parts)
		values = _literal_module_constants(path, set(_SOURCE_VERSION_SYMBOLS.get(component_id, {}).values()))
		version_symbol = _SOURCE_VERSION_SYMBOLS.get(component_id, {}).get("version")
		if not version_symbol:
			raise DockerHelperReleaseError(f"No source version contract is defined for {component_id}")
		version = values[version_symbol]
		if not _VERSION.fullmatch(version):
			raise DockerHelperReleaseError(f"Invalid source semantic version for {component_id}: {version}")
		observed[component_id] = {
			"version": version,
			"sha256": component_source_digest(root, _value(component, "source_path")),
		}
		if component_id == "lifecycle_helper":
			compatibility["helper_protocol"] = values["REQUEST_SCHEMA_VERSION"]
		elif component_id == "install_policy":
			policy_values = _literal_module_constants(path, {"PLAN_SCHEMA_VERSION", "POLICY_VERSION"})
			compatibility["docker_install_plan"] = policy_values["PLAN_SCHEMA_VERSION"]
			compatibility["docker_install_policy"] = policy_values["POLICY_VERSION"]
		elif component_id == "host_inventory":
			compatibility["inventory_schema"] = version
		compatibility["privilege_readiness_contract"] = "1.0.0"
	for name, version in compatibility.items():
		if not isinstance(version, str) or not _VERSION.fullmatch(version):
			raise DockerHelperReleaseError(f"Invalid source compatibility declaration: {name}")
	return {"components": observed, "compatibility": compatibility}


def compare_release_source_snapshot(components, source_root: str | Path, compatibility=None) -> dict:
	"""Compare Published component versions and hashes with actual source declarations."""
	inspection = inspect_release_source(components, source_root)
	differences = {}
	for component in components:
		component_id = _value(component, "component_id")
		expected = {"version": _value(component, "version"), "sha256": _value(component, "sha256")}
		actual = inspection["components"][component_id]
		if expected != actual:
			differences[component_id] = {"expected": expected, "observed": actual}
	if compatibility is not None:
		expected_compatibility = _parse_json_object(compatibility, "compatibility")
		for name, actual in inspection["compatibility"].items():
			declared = expected_compatibility.get(name)
			if declared != {"minimum": actual, "maximum": actual}:
				differences.setdefault("_compatibility", {})[name] = {
					"expected": declared,
					"observed": {"minimum": actual, "maximum": actual},
				}
	return differences


def capture_component_source_snapshot(components, source_root: str | Path) -> dict[str, str]:
	"""Capture SHA-256 digests for every component in a release snapshot."""
	observed = {}
	for component in components:
		validate_component_definition(component)
		component_id = _value(component, "component_id")
		if component_id in observed:
			raise DockerHelperReleaseError("Duplicate Docker Helper Release component")
		observed[component_id] = component_source_digest(source_root, _value(component, "source_path"))
	return observed


def verify_release_source_snapshot(components, source_root: str | Path) -> dict[str, str]:
	"""Verify local source files against immutable hashes in the release record."""
	expected = {_value(component, "component_id"): _value(component, "sha256") for component in components}
	observed = capture_component_source_snapshot(components, source_root)
	mismatches = {
		component_id: {"expected": expected[component_id], "observed": digest}
		for component_id, digest in observed.items()
		if expected.get(component_id) != digest
	}
	if mismatches:
		details = ", ".join(sorted(mismatches))
		raise DockerHelperReleaseError("Published release source drift detected for component(s): " + details)
	return observed


def refresh_component_digests(components, source_root: str | Path) -> str:
	"""Refresh hashes only while constructing or editing a Draft release."""
	for component in components:
		validate_component_definition(component, require_digest=False)
		digest = component_source_digest(source_root, _value(component, "source_path"))
		if isinstance(component, dict):
			component["sha256"] = digest
		else:
			component.sha256 = digest
	return aggregate_release_digest(components)


def _create_draft_release(template, source_root: str | Path, *, release_id: str, version: str):
	"""Create an idempotent Draft from metadata while hashing current source files."""
	if not isinstance(release_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,99}", release_id):
		raise DockerHelperReleaseError("Invalid generated Docker Helper Release identifier")
	if not isinstance(version, str) or not _VERSION.fullmatch(version):
		raise DockerHelperReleaseError("Docker Helper Release version must use semantic versioning")

	import frappe

	components = [
		{field: _value(component, field) for field in COMPONENT_FINGERPRINT_FIELDS if field != "sha256"}
		for component in (_value(template, "components") or [])
	]
	source_inspection = inspect_release_source(components, source_root)
	for component in components:
		component_id = component["component_id"]
		component["version"] = (
			version
			if component_id == "lifecycle_helper"
			else source_inspection["components"][component_id]["version"]
		)
		component["sha256"] = source_inspection["components"][component_id]["sha256"]
	aggregate_digest = aggregate_release_digest(components)
	existing = frappe.db.exists("Docker Helper Release", release_id)
	if existing:
		draft = frappe.get_doc("Docker Helper Release", release_id)
		if (
			draft.status == "Draft"
			and draft.version == version
			and draft.aggregate_digest == aggregate_digest
		):
			return draft, False
		raise DockerHelperReleaseError(
			"A Docker Helper Release with this identifier already exists and cannot be reused."
		)

	draft = frappe.new_doc("Docker Helper Release")
	draft.release_id = release_id
	draft.schema_version = RELEASE_MANIFEST_SCHEMA_VERSION
	draft.version = version
	draft.channel = _value(template, "channel") or "stable"
	draft.status = "Draft"
	compatibility = _parse_json_object(_value(template, "compatibility"), "compatibility")
	for field, actual in source_inspection["compatibility"].items():
		compatibility[field] = {"minimum": actual, "maximum": actual}
	draft.compatibility = json.dumps(compatibility, sort_keys=True)
	draft.lifecycle = _value(template, "lifecycle")
	for component in components:
		draft.append("components", component)
	draft.insert(ignore_permissions=True)
	return draft, True


def create_draft_release_from_source(release, source_root: str | Path, *, version: str | None = None):
	"""Create or reuse a source-drift Draft from an immutable Published release."""
	if _value(release, "status") != "Published":
		raise DockerHelperReleaseError("Source-drift drafts can only be created from a Published release.")
	digest_components = [
		{field: _value(component, field) for field in COMPONENT_FINGERPRINT_FIELDS}
		for component in (_value(release, "components") or [])
	]
	aggregate_digest = refresh_component_digests(digest_components, source_root)
	draft_id = f"{_value(release, 'release_id')[:60]}-draft-{(version or _value(release, 'version')).replace('.', '-')}-{aggregate_digest[:12]}"
	return _create_draft_release(
		release,
		source_root,
		release_id=draft_id,
		version=version or _value(release, "version"),
	)


def canonical_docker_helper_release_template():
	"""Return canonical release metadata; component hashes are intentionally absent."""
	return {
		"schema_version": RELEASE_MANIFEST_SCHEMA_VERSION,
		"channel": "stable",
		"compatibility": json.dumps(
			{
				"helper_protocol": {"minimum": "1.0.0", "maximum": "1.0.0"},
				"privilege_readiness_contract": {"minimum": "1.0.0", "maximum": "1.0.0"},
				"docker_install_plan": {"minimum": "1.1.0", "maximum": "1.1.0"},
				"docker_install_policy": {"minimum": "1.1.1", "maximum": "1.1.1"},
				"inventory_schema": {"minimum": "1.0.0", "maximum": "1.0.0"},
				"upgrade_from": ["0.3.0", "0.3.1", "0.3.2"],
			},
			sort_keys=True,
		),
		"lifecycle": json.dumps(
			{
				"bootstrap": "Install the release when no recognized helper release is present.",
				"upgrade": "Replace a recognized older release with this complete release without repeating host preparation.",
				"reconcile": "Repair only recognized, individually verifiable release artifacts; unknown or untrusted artifacts require operator intervention.",
				"activation_order": ["install_policy", "host_inventory", "lifecycle_helper"],
				"verification": "A release is current only when every required component digest, owner, group, mode, version and compatibility requirement passes.",
			},
			sort_keys=True,
		),
		"components": [
			{
				"component_id": "lifecycle_helper",
				"version": "0.3.2",
				"source_path": "scripts/bin/docker/docker_lifecycle_helper.py",
				"install_path": "/usr/local/lib/dt-master/docker/docker_lifecycle_helper.py",
				"required": 1,
				"file_owner": "root",
				"file_group": "root",
				"file_mode": "0755",
				"accepted_predecessor_sha256": json.dumps(
					[
						"b9deb8114d1efde9ef23f37dc3e327a913d5f0bcbfecc7ce9a2780e426391a9b",
						"1cf27d95c853ce1b46bbb47cc3982baa1f7dfddbe123909c7c5e564d1eaed8fe",
						"8ec811c5ef0ae8c6f3a7733bb17cdbd088d9b5db9ae206f942b0e9267a34bf2a",
						"afd072cd7d80a0182351ea6682779f703b95beef6c8695d8cee2a96dd59e9fa9",
						"06aa9fa0cf7218495b0dfde5667ae50ba00ed85ed45054606939d89fae2c47a2",
					]
				),
				"responsibility": "Root-owned privileged operation boundary; validates closed requests, verifies installed release dependencies and executes only explicitly supported helper operations.",
			},
			{
				"component_id": "install_policy",
				"version": "1.1.1",
				"source_path": "scripts/bin/docker/docker_install_policy.py",
				"install_path": "/usr/local/lib/dt-master/docker/docker_install_policy.py",
				"required": 1,
				"file_owner": "root",
				"file_group": "root",
				"file_mode": "0755",
				"accepted_predecessor_sha256": json.dumps(
					["3fb6c49de806d380d5101679ae0c7364957d6686df5c09ff4a60c453297797f0"]
				),
				"responsibility": "Pure deterministic Docker Engine installation planner and canonical host-state fingerprint implementation.",
			},
			{
				"component_id": "host_inventory",
				"version": "1.0.0",
				"source_path": "scripts/bin/docker/docker_install_inventory.py",
				"install_path": "/usr/local/lib/dt-master/docker/docker_install_inventory.py",
				"required": 1,
				"file_owner": "root",
				"file_group": "root",
				"file_mode": "0755",
				"accepted_predecessor_sha256": json.dumps(
					[
						"99863a6d5d78c95ba3111d6574c3ae5c3bd653de004b24c0859904f86ac641a6",
						"f558523497595bcd890450405789eda7d418807160c2fab8f4c11758f636502b",
					]
				),
				"responsibility": "Read-only host, package, repository, signing-key, service and workload inventory consumed by the planner and privileged helper.",
			},
		],
	}


def create_initial_draft_release(source_root: str | Path, *, version: str, release_id: str | None = None):
	"""Create the first Draft from canonical metadata and actual local source files."""
	release_id = release_id or f"docker-helper-{version}"
	return _create_draft_release(
		canonical_docker_helper_release_template(),
		source_root,
		release_id=release_id,
		version=version,
	)


def create_draft_release_from_template(template, source_root: str | Path, *, version: str, release_id: str):
	"""Create a replacement Draft from a prior release's metadata, never its hashes."""
	if _value(template, "status") not in {"Published", "Retired", None}:
		raise DockerHelperReleaseError("Only a Published or Retired release can be used as a template.")
	return _create_draft_release(template, source_root, release_id=release_id, version=version)


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
	components = canonical_component_records(components)
	if source_root is not None:
		# A Published release is immutable: local source is verified against its
		# catalog hashes, never used to rewrite the expected hashes.
		verify_release_source_snapshot(components, source_root)
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
				"accepted_predecessor_sha256": row["accepted_predecessor_sha256"],
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
	"""Return deterministic UTF-8 JSON after optionally verifying local source files."""
	manifest = build_release_manifest(release, source_root=source_root)
	return json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def write_release_manifest(release, *, source_root: str | Path) -> str:
	"""Atomically write the verified Published release projection to the shared app tree.

	Repeated assignments of the same release are idempotent: identical bytes do
	not rewrite the physical manifest or change its modification time.
	"""
	root = Path(source_root).resolve(strict=True)
	manifest_path = root / "scripts" / "bin" / "docker" / "docker_helper_release.json"
	parent = manifest_path.parent
	if not parent.is_dir() or parent.is_symlink():
		raise DockerHelperReleaseError("Docker helper manifest directory is missing or unsafe")
	if manifest_path.is_symlink():
		raise DockerHelperReleaseError("Docker helper manifest must not be a symlink")

	payload = serialize_release_manifest(release, source_root=root).encode("utf-8") + b"\n"
	if manifest_path.exists():
		if not manifest_path.is_file():
			raise DockerHelperReleaseError("Docker helper manifest path is not a regular file")
		if manifest_path.read_bytes() == payload:
			return payload.decode("utf-8").rstrip("\n")

		# Preserve the existing file mode while replacing its contents atomically.
		mode = manifest_path.stat().st_mode & 0o777
	else:
		mode = 0o644

	fd, temporary_name = tempfile.mkstemp(prefix=".docker_helper_release.", dir=parent)
	try:
		with os.fdopen(fd, "wb") as temporary:
			temporary.write(payload)
			temporary.flush()
			os.fsync(temporary.fileno())
		os.chmod(temporary_name, mode)
		os.replace(temporary_name, manifest_path)
		directory_fd = os.open(parent, os.O_RDONLY)
		try:
			os.fsync(directory_fd)
		finally:
			os.close(directory_fd)
	finally:
		if os.path.exists(temporary_name):
			os.unlink(temporary_name)

	# Recheck source after the write so a concurrent source change cannot leave
	# a manifest that falsely represents a mixed or stale source snapshot.
	verified = serialize_release_manifest(release, source_root=root).encode("utf-8") + b"\n"
	if verified != payload or manifest_path.read_bytes() != payload:
		raise DockerHelperReleaseError("Docker helper source changed during manifest generation")
	return payload.decode("utf-8").rstrip("\n")
