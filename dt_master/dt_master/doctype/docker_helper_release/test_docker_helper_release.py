# Copyright (c) 2026, NDF Technology Labs Solutions LTD
# See license.txt

import json
from pathlib import Path
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from dt_master.docker_helper_releases import (
	aggregate_release_digest,
	build_release_manifest,
	canonical_docker_helper_release_template,
	component_source_digest,
	create_draft_release_from_source,
	create_draft_release_from_template,
	create_initial_draft_release,
	inspect_release_source,
)


class IntegrationTestDockerHelperRelease(IntegrationTestCase):
	def _new_release(self):
		release = frappe.new_doc("Docker Helper Release")
		release.release_id = f"dt-docker-helper-test-{frappe.generate_hash(length=8)}"
		release.version = "0.3.2"
		release.channel = "stable"
		release.status = "Draft"
		release.schema_version = "1.1.0"
		release.compatibility = json.dumps(
			{
				"helper_protocol": {"minimum": "1.0.0", "maximum": "1.0.0"},
				"privilege_readiness_contract": {"minimum": "1.0.0", "maximum": "1.0.0"},
				"docker_install_plan": {"minimum": "1.1.0", "maximum": "1.1.0"},
				"docker_install_policy": {"minimum": "1.1.1", "maximum": "1.1.1"},
				"inventory_schema": {"minimum": "1.0.0", "maximum": "1.0.0"},
				"upgrade_from": ["0.3.0", "0.3.1", "0.3.2"],
			}
		)
		release.lifecycle = json.dumps(
			{
				"bootstrap": "Install the release when no recognized helper release is present.",
				"upgrade": "Replace a recognized older release with this complete release without repeating host preparation.",
				"reconcile": "Repair only recognized, individually verifiable release artifacts; unknown or untrusted artifacts require operator intervention.",
				"activation_order": ["install_policy", "host_inventory", "lifecycle_helper"],
				"verification": "A release is current only when every required component digest, owner, group, mode, version and compatibility requirement passes.",
			}
		)
		for component_id, filename, version, responsibility in (
			(
				"lifecycle_helper",
				"docker_lifecycle_helper.py",
				"0.3.2",
				"Privileged Docker lifecycle operation boundary.",
			),
			(
				"install_policy",
				"docker_install_policy.py",
				"1.1.1",
				"Deterministic Docker Engine installation policy.",
			),
			(
				"host_inventory",
				"docker_install_inventory.py",
				"1.0.0",
				"Read-only Docker host inventory.",
			),
		):
			release.append(
				"components",
				{
					"component_id": component_id,
					"version": version,
					"source_path": f"scripts/bin/docker/{filename}",
					"install_path": f"/usr/local/lib/dt-master/docker/{filename}",
					"required": 1,
					"file_owner": "root",
					"file_group": "root",
					"file_mode": "0755",
					"responsibility": responsibility,
				},
			)
		return release

	def test_draft_release_hashes_actual_component_sources(self):
		release = self._new_release()
		release.validate()
		self.assertEqual(len(release.aggregate_digest), 64)
		self.assertTrue(all(len(row.sha256) == 64 for row in release.components))
		self.assertNotEqual(release.components[0].sha256, "0" * 64)

	def test_published_release_projects_the_catalog_manifest(self):
		release = self._new_release()
		release.status = "Published"
		release.validate()
		manifest = build_release_manifest(release)
		self.assertEqual(manifest["release"]["id"], release.release_id)
		self.assertEqual(manifest["release"]["version"], release.version)
		self.assertEqual(len(manifest["components"]), 3)
		self.assertEqual(
			manifest["lifecycle"]["activation_order"],
			["install_policy", "host_inventory", "lifecycle_helper"],
		)

	def test_publication_refreshes_stale_draft_component_hashes(self):
		release = self._new_release()
		release.insert(ignore_permissions=True)
		component_name = release.components[0].name
		frappe.db.set_value(
			"Docker Helper Release Component",
			component_name,
			"sha256",
			"0" * 64,
			update_modified=False,
		)

		release.reload()
		release.status = "Published"
		release.save(ignore_permissions=True)
		release.reload()

		self.assertEqual(release.status, "Published")
		self.assertEqual(release.aggregate_digest, aggregate_release_digest(release.components))
		self.assertNotEqual(release.components[0].sha256, "0" * 64)

	def test_published_release_is_immutable(self):
		release = self._new_release()
		release.insert(ignore_permissions=True)
		release.status = "Published"
		release.save(ignore_permissions=True)
		release.version = "0.3.3"
		with self.assertRaises(frappe.ValidationError):
			release.save(ignore_permissions=True)

	def test_assigned_release_can_be_retired_to_signal_node_migration(self):
		release = self._new_release()
		release.insert(ignore_permissions=True)
		release.status = "Published"
		release.save(ignore_permissions=True)
		release.reload()
		release.status = "Retired"
		with patch.object(frappe.db, "exists", return_value=True):
			release.save(ignore_permissions=True)
		release.reload()
		self.assertEqual(release.status, "Retired")
		self.assertTrue(release.retired_on)

	def test_source_drift_draft_creation_is_idempotent_and_preserves_published_release(self):
		release = self._new_release()
		release.insert(ignore_permissions=True)
		release.status = "Published"
		release.save(ignore_permissions=True)
		original_digest = release.aggregate_digest
		source_root = Path(__file__).resolve().parents[3]

		source_version = inspect_release_source(release.components, source_root)["components"]["lifecycle_helper"]["version"]
		first, created = create_draft_release_from_source(release, source_root, version=source_version)
		second, created_again = create_draft_release_from_source(release, source_root, version=source_version)

		self.assertTrue(created)
		self.assertEqual(first.release_id, f"docker-helper-{source_version}")
		self.assertEqual(
			json.loads(first.predecessors),
			[
				{
					"release_id": release.release_id,
					"version": release.version,
					"components": sorted(
						[
							{"id": row.component_id, "version": row.version, "sha256": row.sha256}
							for row in release.components
						],
						key=lambda row: row["id"],
					),
				}
			],
		)
		self.assertFalse(created_again)
		self.assertEqual(first.name, second.name)
		self.assertEqual(first.status, "Draft")
		release.reload()
		self.assertEqual(release.status, "Published")
		self.assertEqual(release.aggregate_digest, original_digest)

	def test_initial_draft_uses_canonical_metadata_and_hashes_actual_files(self):
		source_root = Path(__file__).resolve().parents[3]
		template = canonical_docker_helper_release_template()
		self.assertTrue(all("sha256" not in component for component in template["components"]))

		draft, created = create_initial_draft_release(
			source_root,
			version="0.3.3",
			release_id=f"docker-helper-test-{frappe.generate_hash(length=8)}",
		)

		self.assertTrue(created)
		self.assertEqual(draft.status, "Draft")
		self.assertEqual(draft.version, "0.3.3")
		self.assertEqual(draft.aggregate_digest, aggregate_release_digest(draft.components))
		for component in draft.components:
			self.assertEqual(
				component.sha256,
				component_source_digest(source_root, component.source_path),
			)
		self.assertEqual(
			next(row.version for row in draft.components if row.component_id == "lifecycle_helper"),
			"0.3.3",
		)

	def test_retired_release_can_be_used_as_template_without_reactivation(self):
		template = self._new_release()
		template.insert(ignore_permissions=True)
		template.status = "Published"
		template.save(ignore_permissions=True)
		template.reload()
		template.status = "Retired"
		template.save(ignore_permissions=True)
		template.reload()
		original_digest = template.aggregate_digest
		source_root = Path(__file__).resolve().parents[3]

		source_version = inspect_release_source(template.components, source_root)["components"]["lifecycle_helper"]["version"]
		draft, created = create_draft_release_from_template(
			template,
			source_root,
			version=source_version,
			release_id=f"docker-helper-{source_version}-{frappe.generate_hash(length=8)}",
		)

		self.assertTrue(created)
		self.assertEqual(
			json.loads(draft.predecessors)[0]["release_id"],
			template.release_id,
		)
		self.assertEqual(draft.status, "Draft")
		self.assertEqual(draft.version, "0.3.3")
		template.reload()
		self.assertEqual(template.status, "Retired")
		self.assertEqual(template.aggregate_digest, original_digest)
		for component in draft.components:
			self.assertEqual(
				component.sha256,
				component_source_digest(source_root, component.source_path),
			)

	def test_published_release_can_only_be_retired_without_definition_changes(self):
		release = self._new_release()
		release.insert(ignore_permissions=True)
		release.status = "Published"
		release.save(ignore_permissions=True)
		release.status = "Retired"
		release.save(ignore_permissions=True)
		self.assertEqual(release.status, "Retired")
		self.assertTrue(release.retired_on)


if __name__ == "__main__":
	pass
