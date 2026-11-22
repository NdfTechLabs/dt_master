# Copyright (c) 2026, NDF Technology Labs Solutions LTD
# See license.txt

import json

import frappe
from frappe.tests import IntegrationTestCase


class IntegrationTestDockerHelperRelease(IntegrationTestCase):
    def _new_release(self):
        release = frappe.new_doc("Docker Helper Release")
        release.release_id = f"dt-docker-helper-test-{frappe.generate_hash(length=8)}"
        release.version = "0.3.2"
        release.channel = "stable"
        release.status = "Draft"
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
