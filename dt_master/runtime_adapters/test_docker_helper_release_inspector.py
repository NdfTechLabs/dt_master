"""Tests for the remote Docker Helper Release manifest inspection contract."""

import base64
import importlib.util
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "bin" / "docker" / "inspect_docker_helper_release.py"
SPEC = importlib.util.spec_from_file_location("docker_helper_release_inspector", SCRIPT_PATH)
INSPECTOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INSPECTOR)


def manifest():
	component_paths = {
		"lifecycle_helper": "docker_lifecycle_helper.py",
		"install_policy": "docker_install_policy.py",
		"host_inventory": "docker_install_inventory.py",
	}
	current_components = [
		{
			"id": component_id,
			"path": path,
			"version": "0.3.4",
			"sha256": "a" * 64,
			"accepted_predecessor_sha256": [],
			"required": True,
			"responsibility": "Docker helper component",
		}
		for component_id, path in component_paths.items()
	]
	predecessor_components = [
		{"id": component_id, "version": "0.3.3", "sha256": digest}
		for component_id, digest in zip(component_paths, ("b" * 64, "c" * 64, "d" * 64), strict=True)
	]
	return {
		"schema_version": "1.1.0",
		"release": {
			"id": "docker-helper-0.3.4",
			"version": "0.3.4",
			"channel": "stable",
			"status": "supported",
		},
		"compatibility": {"upgrade_from": ["0.3.3"]},
		"predecessors": [
			{
				"release_id": "docker-helper-0.3.3",
				"version": "0.3.3",
				"components": predecessor_components,
			}
		],
		"components": current_components,
		"lifecycle": {},
	}


class TestDockerHelperReleaseManifestInspection(unittest.TestCase):
	def load(self, value):
		encoded = base64.b64encode(json.dumps(value).encode("utf-8")).decode("ascii")
		with patch.dict(os.environ, {"EXPECTED_RELEASE_MANIFEST_B64": encoded}):
			return INSPECTOR.load_expected_manifest()

	def test_accepts_generated_manifest_with_complete_predecessor_provenance(self):
		loaded, rows = self.load(manifest())
		self.assertEqual(loaded["predecessors"][0]["release_id"], "docker-helper-0.3.3")
		self.assertEqual(set(rows), INSPECTOR.COMPONENT_IDS)

	def test_rejects_predecessor_with_incomplete_component_inventory(self):
		value = manifest()
		value["predecessors"][0]["components"].pop()
		with self.assertRaisesRegex(ValueError, "incomplete predecessor component inventory"):
			self.load(value)

	def test_rejects_predecessor_not_declared_in_upgrade_compatibility(self):
		value = manifest()
		value["compatibility"]["upgrade_from"] = []
		with self.assertRaisesRegex(ValueError, "invalid predecessor identity or compatibility"):
			self.load(value)


if __name__ == "__main__":
	unittest.main()
