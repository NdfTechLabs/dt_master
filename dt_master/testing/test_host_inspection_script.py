"""Static contract tests for the read-only Host discovery probe."""

import unittest
from pathlib import Path

SCRIPTS = Path(__file__).parents[1] / "scripts" / "bin" / "node"
WRAPPER = SCRIPTS / "inspect_host_runtime.sh"
PROBE = SCRIPTS / "inspect_host_discovery.py"


class TestHostInspectionScript(unittest.TestCase):
	def test_wrapper_invokes_versioned_discovery_probe(self):
		source = WRAPPER.read_text()
		self.assertIn('exec python3 "${BASE_DIR}/bin/node/inspect_host_discovery.py"', source)
		self.assertIn("HOST_DISCOVERY_PYTHON3_MISSING", source)

	def test_probe_emits_v2_schema_and_required_inventory_categories(self):
		source = PROBE.read_text()
		for token in (
			'"schema_version": "2.0.0"',
			'"identity":',
			'"os":',
			'"compute":',
			'"storage":',
			'"network":',
			'"tools":',
			'"services":',
			'"bench_available":',
			'"cpu_model":',
			'"memory_available_bytes":',
			'"swap_free_bytes":',
			'"virtualization":',
			'"filesystems":',
			'"interfaces":',
			'"default_routes":',
			'"dns_nameservers":',
			'"listening_sockets":',
		):
			self.assertIn(token, source)

	def test_probe_is_read_only_and_has_bounded_inventory(self):
		source = PROBE.read_text()
		for forbidden in (
			"sudo ", "apt install", "apt-get install", "docker run",
			"docker compose", "mkfs", "mount ",
		):
			self.assertNotIn(forbidden, source)
		self.assertIn("return rows[:64]", source)
		self.assertIn("listening[:256]", source)

	def test_probe_uses_only_standard_library(self):
		source = PROBE.read_text()
		self.assertNotIn("import psutil", source)
		self.assertNotIn("requests", source)


if __name__ == "__main__":
	unittest.main()
