"""Static contract tests for the Host inspection shell probe."""

import unittest
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts" / "bin" / "node" / "inspect_host_runtime.sh"


class TestHostInspectionScript(unittest.TestCase):
	def test_script_exists_and_emits_required_contract_fields(self):
		source = SCRIPT.read_text()
		for key in (
			"hostname=",
			"os=",
			"os_id=",
			"os_version_id=",
			"os_version_codename=",
			"os_id_like=",
			"raw_architecture=",
			"architecture=",
			"package_manager=",
			"service_manager=",
			"cpu_cores=",
			"memory_bytes=",
			"root_available_bytes=",
			"bench_available=",
			"swap_total_bytes=",
			"python3_available=",
			"node_available=",
			"npm_available=",
			"yarn_available=",
			"nginx_available=",
			"supervisorctl_available=",
			"redis_cli_available=",
			"mariadb_available=",
		):
			self.assertIn(key, source)

	def test_script_contains_no_mutating_or_privileged_commands(self):
		source = SCRIPT.read_text()
		forbidden = (
			"sudo ",
			"systemctl start",
			"systemctl stop",
			"systemctl restart",
			"apt install",
			"apt-get install",
			"docker run",
			"docker compose",
			"rm -",
			"mkfs",
			"mount ",
		)
		for token in forbidden:
			self.assertNotIn(token, source)

	def test_script_normalizes_known_architectures(self):
		source = SCRIPT.read_text()
		self.assertIn('x86_64|x64) architecture="amd64"', source)
		self.assertIn('aarch64|arm64) architecture="arm64"', source)

	def test_script_only_reads_os_release_metadata(self):
		source = SCRIPT.read_text()
		self.assertIn("[ -r /etc/os-release ]", source)
		self.assertNotIn("apt-get update", source)
		self.assertNotIn("systemctl enable", source)


if __name__ == "__main__":
	unittest.main()
