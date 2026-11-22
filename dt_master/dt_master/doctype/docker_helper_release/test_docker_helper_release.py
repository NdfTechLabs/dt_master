# Copyright (c) 2026, NDF Technology Labs Solutions LTD
# See license.txt

import json

import frappe
from frappe.tests import IntegrationTestCase

from dt_master.docker_helper_releases import build_release_manifest


class IntegrationTestDockerHelperRelease(IntegrationTestCase):
    def _new_release(self):
        release = frappe.new_doc("Docker Helper Release")
        release.release_id = f"dt-docker-helper-test-{frappe.generate_hash(length=8)}"
        release.version = "0.3.2"
        release.channel = "stable"
        release.status = "Draft"
        release.schema_version = "1.0.0"
        release.compatibility = json.dumps({
            "helper_protocol": {"minimum": "1.0.0", "maximum": "1.0.0"},
            "privilege_readiness_contract": {"minimum": "1.0.0", "maximum": "1.0.0"},
            "docker_install_plan": {"minimum": "1.1.0", "maximum": "1.1.0"},
            "docker_install_policy": {"minimum": "1.1.1", "maximum": "1.1.1"},
            "inventory_schema": {"minimum": "1.0.0", "maximum": "1.0.0"},
            "upgrade_from": ["0.3.0", "0.3.1", "0.3.2"],
        })
        release.lifecycle = json.dumps({
            "bootstrap": "Install the release when no recognized helper release is present.",
            "upgrade": "Replace a recognized older release with this complete release without repeating host preparation.",
            "reconcile": "Repair only recognized, individually verifiable release artifacts; unknown or untrusted artifacts require operator intervention.",
            "activation_order": ["install_policy", "host_inventory", "lifecycle_helper"],
            "verification": "A release is current only when every required component digest, owner, group, mode, version and compatibility requirement passes.",
        })
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
        self.assertEqual(manifest["lifecycle"]["activation_order"], ["install_policy", "host_inventory", "lifecycle_helper"])
    def test_published_release_is_immutable(self):
        release = self._new_release()
        release.insert(ignore_permissions=True)
        release.status = "Published"
        release.save(ignore_permissions=True)
        release.version = "0.3.3"
        with self.assertRaises(frappe.ValidationError):
            release.save(ignore_permissions=True)

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
