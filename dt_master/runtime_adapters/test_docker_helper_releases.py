"""Unit tests for deterministic Docker Helper Release fingerprints."""

import hashlib
import tempfile
import unittest
from pathlib import Path

from dt_master.api.docker_helper_releases import _next_patch_release_version
from dt_master.docker_helper_releases import (
	RELEASE_MANIFEST_SCHEMA_VERSION,
	DockerHelperReleaseError,
	aggregate_release_digest,
	build_release_manifest,
	compare_release_source_snapshot,
	inspect_release_source,
	component_source_digest,
	write_release_manifest,
)


def component(component_id, source_path, digest="a" * 64):
	return {
		"component_id": component_id,
		"version": "1.0.0",
		"source_path": source_path,
		"install_path": f"/usr/local/lib/dt-master/docker/{component_id}.py",
		"required": True,
		"file_owner": "root",
		"file_group": "root",
		"file_mode": "0755",
		"sha256": digest,
		"responsibility": f"{component_id} helper component",
	}


class TestDockerHelperReleaseFingerprints(unittest.TestCase):
	def test_source_drift_release_version_advances_patch_automatically(self):
		self.assertEqual(_next_patch_release_version("0.3.2"), "0.3.3")
		self.assertEqual(_next_patch_release_version("1.9.9"), "1.9.10")
		self.assertEqual(_next_patch_release_version("1.2.3-rc.1"), "1.2.4")

	def test_component_digest_uses_actual_source_bytes(self):
		with tempfile.TemporaryDirectory() as directory:
			source = Path(directory) / "scripts/bin/docker/helper.py"
			source.parent.mkdir(parents=True)
			source.write_bytes(b"first source")
			first = component_source_digest(directory, "scripts/bin/docker/helper.py")
			source.write_bytes(b"second source")
			second = component_source_digest(directory, "scripts/bin/docker/helper.py")
		self.assertNotEqual(first, second)

	def test_source_snapshot_comparison_reports_expected_and_observed_component_hashes(self):
		with tempfile.TemporaryDirectory() as directory:
			source = Path(directory) / "scripts/bin/docker/helper.py"
			source.parent.mkdir(parents=True)
			source.write_text(
				'HELPER_VERSION = "2.0.0"\nREQUEST_SCHEMA_VERSION = "1.0.0"\n', encoding="utf-8"
			)
			components = [component("lifecycle_helper", "scripts/bin/docker/helper.py")]
			compatibility = {
				"helper_protocol": {"minimum": "0.9.0", "maximum": "0.9.0"},
				"privilege_readiness_contract": {"minimum": "1.0.0", "maximum": "1.0.0"},
			}
			mismatches = compare_release_source_snapshot(components, directory, compatibility)
			observed_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()

		self.assertEqual(mismatches["_compatibility"]["helper_protocol"]["observed"]["minimum"], "1.0.0")
		self.assertEqual(mismatches["lifecycle_helper"]["expected"]["version"], "1.0.0")
		self.assertEqual(mismatches["lifecycle_helper"]["observed"]["version"], "2.0.0")
		self.assertEqual(mismatches["lifecycle_helper"]["observed"]["sha256"], observed_sha256)

	def test_source_inspection_extracts_actual_protocol_and_component_versions(self):
		with tempfile.TemporaryDirectory() as directory:
			source = Path(directory) / "scripts/bin/docker/helper.py"
			source.parent.mkdir(parents=True)
			source.write_text(
				'HELPER_VERSION = "2.0.0"\nREQUEST_SCHEMA_VERSION = "1.4.0"\n', encoding="utf-8"
			)
			inspection = inspect_release_source(
				[component("lifecycle_helper", "scripts/bin/docker/helper.py")], directory
			)
		self.assertEqual(inspection["components"]["lifecycle_helper"]["version"], "2.0.0")
		self.assertEqual(inspection["compatibility"]["helper_protocol"], "1.4.0")

	def test_component_digest_rejects_path_traversal(self):
		with tempfile.TemporaryDirectory() as directory:
			with self.assertRaises(DockerHelperReleaseError):
				component_source_digest(directory, "scripts/bin/docker/../../etc/passwd")

	def test_component_digest_rejects_symlinks(self):
		with tempfile.TemporaryDirectory() as directory:
			root = Path(directory)
			outside = root / "outside.py"
			outside.write_text("not a trusted component", encoding="utf-8")
			source = root / "scripts/bin/docker/helper.py"
			source.parent.mkdir(parents=True)
			source.symlink_to(outside)
			with self.assertRaises(DockerHelperReleaseError):
				component_source_digest(root, "scripts/bin/docker/helper.py")

	def test_aggregate_digest_is_independent_of_component_order(self):
		rows = [
			component("lifecycle_helper", "scripts/bin/docker/helper.py"),
			component("install_policy", "scripts/bin/docker/policy.py", "b" * 64),
		]
		self.assertEqual(aggregate_release_digest(rows), aggregate_release_digest(list(reversed(rows))))

	def test_aggregate_digest_changes_when_a_component_definition_changes(self):
		rows = [component("lifecycle_helper", "scripts/bin/docker/helper.py")]
		first = aggregate_release_digest(rows)
		rows[0]["file_mode"] = "0700"
		self.assertNotEqual(first, aggregate_release_digest(rows))

	def test_aggregate_digest_rejects_duplicate_component_ids(self):
		rows = [
			component("lifecycle_helper", "scripts/bin/docker/helper.py"),
			component("lifecycle_helper", "scripts/bin/docker/other.py", "b" * 64),
		]
		with self.assertRaises(DockerHelperReleaseError):
			aggregate_release_digest(rows)

	def test_release_manifest_projection_rejects_published_source_drift(self):
		compatibility = {
			"helper_protocol": {"minimum": "1.0.0", "maximum": "1.0.0"},
			"privilege_readiness_contract": {"minimum": "1.0.0", "maximum": "1.0.0"},
			"docker_install_plan": {"minimum": "1.2.0", "maximum": "1.2.0"},
			"docker_install_policy": {"minimum": "1.2.0", "maximum": "1.2.0"},
			"inventory_schema": {"minimum": "1.0.0", "maximum": "1.0.0"},
			"upgrade_from": ["0.3.0", "0.3.1", "0.3.2"],
		}
		lifecycle = {
			"bootstrap": "Install the complete release when no recognized helper exists.",
			"upgrade": "Upgrade a recognized older release without repeating host preparation.",
			"reconcile": "Repair only individually recognized and verifiable artifacts.",
			"activation_order": ["install_policy", "host_inventory", "lifecycle_helper"],
			"verification": "Verify all required digests, versions, ownership, modes and compatibility.",
		}
		components = []
		for component_id, filename, version in (
			("lifecycle_helper", "docker_lifecycle_helper.py", "0.3.3"),
			("install_policy", "docker_install_policy.py", "1.2.0"),
			("host_inventory", "docker_install_inventory.py", "1.0.0"),
		):
			components.append(
				{
					"component_id": component_id,
					"version": version,
					"source_path": f"scripts/bin/docker/{filename}",
					"install_path": f"/usr/local/lib/dt-master/docker/{filename}",
					"required": 1,
					"file_owner": "root",
					"file_group": "root",
					"file_mode": "0755",
					"sha256": "a" * 64,
					"responsibility": f"{component_id} responsibility",
				}
			)
		release = {
			"release_id": "dt-docker-helper-0.3.3",
			"version": "0.3.3",
			"channel": "stable",
			"status": "Published",
			"schema_version": RELEASE_MANIFEST_SCHEMA_VERSION,
			"compatibility": compatibility,
			"lifecycle": lifecycle,
			"components": components,
		}
		with tempfile.TemporaryDirectory() as directory:
			for component in components:
				source = Path(directory) / component["source_path"]
				source.parent.mkdir(parents=True, exist_ok=True)
				source.write_bytes(component["component_id"].encode("utf-8"))
			with self.assertRaisesRegex(DockerHelperReleaseError, "source drift"):
				build_release_manifest(release, source_root=directory)
			for component in components:
				component["sha256"] = hashlib.sha256(component["component_id"].encode("utf-8")).hexdigest()
			manifest = build_release_manifest(release, source_root=directory)
			for row in manifest["components"]:
				component_row = next(item for item in components if item["component_id"] == row["id"])
				self.assertEqual(row["sha256"], component_row["sha256"])
		self.assertEqual(manifest["release"]["id"], release["release_id"])
		self.assertEqual(
			[row["id"] for row in manifest["components"]],
			["host_inventory", "install_policy", "lifecycle_helper"],
		)
		self.assertEqual(manifest["compatibility"], compatibility)
		self.assertEqual(manifest["lifecycle"], lifecycle)

	def test_physical_manifest_generation_is_validated_and_idempotent(self):
		compatibility = {
			"helper_protocol": {"minimum": "1.0.0", "maximum": "1.0.0"},
			"privilege_readiness_contract": {"minimum": "1.0.0", "maximum": "1.0.0"},
			"docker_install_plan": {"minimum": "1.2.0", "maximum": "1.2.0"},
			"docker_install_policy": {"minimum": "1.2.0", "maximum": "1.2.0"},
			"inventory_schema": {"minimum": "1.0.0", "maximum": "1.0.0"},
			"upgrade_from": ["0.3.0"],
		}
		lifecycle = {
			"bootstrap": "Install the complete release.",
			"upgrade": "Upgrade a recognized release.",
			"reconcile": "Repair recognized release artifacts.",
			"activation_order": ["install_policy", "host_inventory", "lifecycle_helper"],
			"verification": "Verify every required component.",
		}
		components = []
		with tempfile.TemporaryDirectory() as directory:
			root = Path(directory)
			for component_id, filename, version in (
				("lifecycle_helper", "docker_lifecycle_helper.py", "0.3.3"),
				("install_policy", "docker_install_policy.py", "1.2.0"),
				("host_inventory", "docker_install_inventory.py", "1.0.0"),
			):
				source_path = f"scripts/bin/docker/{filename}"
				source = root / source_path
				source.parent.mkdir(parents=True, exist_ok=True)
				source.write_text(component_id, encoding="utf-8")
				components.append(
					{
						"component_id": component_id,
						"version": version,
						"source_path": source_path,
						"install_path": f"/usr/local/lib/dt-master/docker/{filename}",
						"required": True,
						"file_owner": "root",
						"file_group": "root",
						"file_mode": "0755",
						"sha256": hashlib.sha256(component_id.encode()).hexdigest(),
						"responsibility": f"{component_id} responsibility",
					}
				)
			release = {
				"release_id": "dt-docker-helper-0.3.3",
				"version": "0.3.3",
				"channel": "stable",
				"status": "Published",
				"schema_version": RELEASE_MANIFEST_SCHEMA_VERSION,
				"compatibility": compatibility,
				"lifecycle": lifecycle,
				"components": components,
			}
			manifest_path = root / "scripts/bin/docker/docker_helper_release.json"
			first = write_release_manifest(release, source_root=root)
			first_mtime = manifest_path.stat().st_mtime_ns
			second = write_release_manifest(release, source_root=root)
			self.assertEqual(first, second)
			self.assertEqual(manifest_path.read_text(encoding="utf-8").strip(), first)
			self.assertEqual(manifest_path.stat().st_mtime_ns, first_mtime)

	def test_release_manifest_projection_rejects_draft_releases(self):
		with self.assertRaises(DockerHelperReleaseError):
			build_release_manifest({"status": "Draft"})


if __name__ == "__main__":
	unittest.main()
