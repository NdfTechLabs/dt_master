"""Tests for the N2.4.2 trusted Docker helper bootstrap contract."""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts" / "bin" / "docker"
HELPER = SCRIPT_DIR / "docker_lifecycle_helper.py"
INSTALLER = SCRIPT_DIR / "install_docker_privilege_helper.sh"


class TestDockerPrivilegeHelper(unittest.TestCase):
    def run_helper(self, request, *, raw=None):
        return subprocess.run(
            [sys.executable, str(HELPER)],
            input=raw if raw is not None else json.dumps(request),
            text=True,
            capture_output=True,
            check=False,
        )

    def test_self_test_returns_bounded_identity_observation(self):
        result = self.run_helper({"schema_version": "1.0.0", "operation": "self_test"})
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["operation"], "self_test")
        self.assertEqual(payload["helper_version"], "0.1.0")
        self.assertEqual(payload["effective_uid"], os.geteuid())

    def test_rejects_unknown_operation(self):
        result = self.run_helper({"schema_version": "1.0.0", "operation": "install"})
        self.assertEqual(result.returncode, 2)
        self.assertIn("not enabled", result.stderr)

    def test_rejects_unknown_fields(self):
        result = self.run_helper({
            "schema_version": "1.0.0",
            "operation": "self_test",
            "command": "id",
        })
        self.assertEqual(result.returncode, 2)

    def test_rejects_unsupported_schema(self):
        result = self.run_helper({"schema_version": "9.0.0", "operation": "self_test"})
        self.assertEqual(result.returncode, 2)

    def test_rejects_duplicate_json_keys(self):
        result = self.run_helper({}, raw='{"schema_version":"1.0.0","operation":"self_test","operation":"install"}')
        self.assertEqual(result.returncode, 2)
        self.assertIn("Duplicate", result.stderr)

    def test_rejects_oversized_input(self):
        result = self.run_helper({}, raw=" " * (16 * 1024 + 1))
        self.assertEqual(result.returncode, 2)
        self.assertIn("size limit", result.stderr)

    def test_installer_pins_helper_digest_and_does_not_use_broad_sudo(self):
        source = INSTALLER.read_text()
        self.assertIn("HELPER_SHA256=", source)
        self.assertIn("sudo -n install -o root -g root -m 0755", source)
        self.assertNotIn("visudo", source)  # sudoers management is N2.4.3
        self.assertNotIn("NOPASSWD:ALL", source)
        self.assertNotIn("sudo -n bash", source)
        self.assertIn("Refusing to overwrite an unrecognized helper version", source)


if __name__ == "__main__":
    unittest.main()
