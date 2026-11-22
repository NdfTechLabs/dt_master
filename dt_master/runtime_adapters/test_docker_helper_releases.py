"""Unit tests for deterministic Docker Helper Release fingerprints."""

import tempfile
import unittest
from pathlib import Path

from dt_master.docker_helper_releases import (
	DockerHelperReleaseError,
	aggregate_release_digest,
	build_release_manifest,
	component_source_digest,
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
	def test_component_digest_uses_actual_source_bytes(self):
		with tempfile.TemporaryDirectory() as directory:
			source = Path(directory) / "scripts/bin/docker/helper.py"
			source.parent.mkdir(parents=True)
			source.write_bytes(b"first source")
			first = component_source_digest(directory, "scripts/bin/docker/helper.py")
			source.write_bytes(b"second source")
			second = component_source_digest(directory, "scripts/bin/docker/helper.py")
		self.assertNotEqual(first, second)

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


	def test_release_manifest_projection_is_deterministic_and_uses_catalog_metadata(self):
		compatibility = {
			"helper_protocol": {"minimum": "1.0.0", "maximum": "1.0.0"},
			"privilege_readiness_contract": {"minimum": "1.0.0", "maximum": "1.0.0"},
			"docker_install_plan": {"minimum": "1.1.0", "maximum": "1.1.0"},
			"docker_install_policy": {"minimum": "1.1.1", "maximum": "1.1.1"},
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
			("lifecycle_helper", "docker_lifecycle_helper.py", "0.3.2"),
			("install_policy", "docker_install_policy.py", "1.1.1"),
			("host_inventory", "docker_install_inventory.py", "1.0.0"),
		):
			components.append({
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
			})
		release = {
			"release_id": "dt-docker-helper-0.3.2",
			"version": "0.3.2",
			"channel": "stable",
			"status": "Published",
			"schema_version": "1.0.0",
			"compatibility": compatibility,
			"lifecycle": lifecycle,
			"components": components,
		}
		manifest = build_release_manifest(release)
		self.assertEqual(manifest["release"]["id"], release["release_id"])
		self.assertEqual([row["id"] for row in manifest["components"]], [
			"host_inventory", "install_policy", "lifecycle_helper"
		])
		self.assertEqual(manifest["compatibility"], compatibility)
		self.assertEqual(manifest["lifecycle"], lifecycle)

	def test_release_manifest_projection_rejects_draft_releases(self):
		with self.assertRaises(DockerHelperReleaseError):
			build_release_manifest({"status": "Draft"})


if __name__ == "__main__":
	unittest.main()
