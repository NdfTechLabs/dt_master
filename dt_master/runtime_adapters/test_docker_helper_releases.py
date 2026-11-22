"""Unit tests for deterministic Docker Helper Release fingerprints."""

import tempfile
import unittest
from pathlib import Path

from dt_master.docker_helper_releases import (
    DockerHelperReleaseError,
    aggregate_release_digest,
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


if __name__ == "__main__":
    unittest.main()
