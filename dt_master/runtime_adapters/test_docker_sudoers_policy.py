"""Tests for the N2.4.3 Docker sudoers policy manager contract."""

import subprocess
import sys
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts" / "bin" / "docker"
GENERATOR = SCRIPT_DIR / "docker_sudoers_policy.py"
INSTALLER = SCRIPT_DIR / "install_docker_sudoers.sh"
HELPER_PATH = "/usr/local/lib/dt-master/docker/docker_lifecycle_helper.py"


class TestDockerSudoersPolicy(unittest.TestCase):
	def render(self, username):
		return subprocess.run(
			[sys.executable, str(GENERATOR), username], text=True, capture_output=True, check=False
		)

	def test_generates_exact_no_argument_helper_rule(self):
		result = self.render("frank")
		self.assertEqual(result.returncode, 0, result.stderr)
		self.assertEqual(result.stdout, f'frank ALL=(root) NOPASSWD: {HELPER_PATH} ""\n')

	def test_accepts_linux_dollar_suffixed_account(self):
		self.assertEqual(self.render("service$").returncode, 0)

	def test_rejects_invalid_or_injectable_usernames(self):
		for username in ("", "Frank", "user name", "user,ALL", "user\nALL", "user;id", "-option"):
			with self.subTest(username=username):
				self.assertEqual(self.render(username).returncode, 2)

	def test_policy_contains_no_wildcards_or_interpreters(self):
		result = self.render("frank")
		self.assertEqual(result.returncode, 0)
		self.assertNotIn("*", result.stdout)
		self.assertNotIn("bash", result.stdout)
		self.assertNotIn("python", result.stdout)
		self.assertIn(f'{HELPER_PATH} ""', result.stdout)

	def test_installer_is_atomic_fail_closed_and_scoped(self):
		source = INSTALLER.read_text()
		self.assertIn("visudo -cf", source)
		self.assertIn("sudo -n mv", source)
		self.assertIn("sudo -n -l -U", source)
		self.assertIn('sudo -n -l -U "$username" "$HELPER_PATH" ""', source)
		self.assertIn("Refusing to overwrite an unrecognized sudoers policy", source)
		self.assertIn('sudo -n rm -f -- "$POLICY_FILE"', source)
		self.assertNotIn("NOPASSWD:ALL", source)
		self.assertNotIn("sudo -n bash", source)
		syntax = subprocess.run(["bash", "-n", str(INSTALLER)], capture_output=True, text=True)
		self.assertEqual(syntax.returncode, 0, syntax.stderr)


if __name__ == "__main__":
	unittest.main()
