"""Static contract tests for the Host inspection shell probe."""

import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "bin" / "node" / "inspect_host_runtime.sh"


class TestHostInspectionScript(unittest.TestCase):
    def test_script_exists_and_emits_required_contract_fields(self):
        source = SCRIPT.read_text()
        for key in (
            "hostname=", "os=", "architecture=", "cpu_cores=",
            "memory_bytes=", "root_available_bytes=", "bench_available=",
        ):
            self.assertIn(key, source)

    def test_script_contains_no_mutating_or_privileged_commands(self):
        source = SCRIPT.read_text()
        forbidden = (
            "sudo ", "systemctl start", "systemctl stop", "systemctl restart",
            "apt install", "apt-get install", "docker run", "docker compose",
            "rm -", "mkfs", "mount ",
        )
        for token in forbidden:
            self.assertNotIn(token, source)
