"""Focused tests for the read-only N3.2.1 Docker host inventory probe."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

PROBE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "bin" / "docker" / "inspect_docker_host.py"
SPEC = importlib.util.spec_from_file_location("docker_host_inventory_probe", PROBE_PATH)
PROBE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROBE)


class TestDockerHostInventory(TestCase):
	def test_inventory_adds_planner_contract_without_changing_legacy_fields(self):
		host = {
			"id": "ubuntu",
			"version_id": "24.04",
			"version_codename": "noble",
			"architecture": "x86_64",
		}
		services = {"manager": "systemd", "docker": "inactive", "containerd": "inactive"}
		workloads = {"available": False, "running_containers": None, "total_containers": None}
		planner = {
			"schema_version": "1.0.0",
			"observed_at": "2026-11-21T10:00:00Z",
			"host_os": {"id": "ubuntu", "version_id": "24.04", "codename": "noble", "architecture": "x86_64"},
			"package_manager": "apt",
			"packages": [],
			"repository": {},
			"apt_metadata": {},
			"package_manager_state": {},
			"storage": {},
			"services": {"docker": "inactive", "containerd": "inactive"},
			"workloads": workloads,
			"docker_engine_health": "not_installed",
		}
		with (
			patch.object(PROBE, "_package_manager", return_value="dpkg"),
			patch.object(PROBE, "_os_release", return_value=host),
			patch.object(PROBE, "_service_state", return_value=services),
			patch.object(PROBE, "_workload_summary", return_value=workloads),
			patch.object(
				PROBE,
				"_installed_packages",
				return_value=[{"name": "docker-ce", "version": "1", "status": "installed"}],
			),
			patch.object(PROBE, "_install_inventory", return_value=planner),
		):
			result = PROBE.collect()
		self.assertEqual(result["schema_version"], "1.0.0")
		self.assertEqual(result["docker_packages"][0]["name"], "docker-ce")
		self.assertEqual(result["services"], services)
		self.assertEqual(result["workloads"], workloads)
		self.assertEqual(result["docker_install_inventory"], planner)

	def test_apt_package_rows_include_installed_candidate_origin_and_holds(self):
		calls = []

		def fake_run(argv, timeout=5):
			calls.append(argv)
			if argv[0] == "apt-mark":
				return 0, "docker-ce\n"
			if argv[0] == "dpkg-query":
				name = argv[-1]
				return (0, "5:29.0.0-1\tinstalled") if name == "docker-ce" else (1, "")
			if argv[0] == "apt-cache":
				name = argv[-1]
				if name == "docker-ce":
					return (
						0,
						"docker-ce:\n  Installed: 5:29.0.0-1\n  Candidate: 5:29.1.0-1\n  Version table:\n     5:29.1.0-1 500\n        500 https://download.docker.com/linux/ubuntu noble/stable amd64 Packages\n *** 5:29.0.0-1 100\n        100 /var/lib/dpkg/status",
					)
				return 0, f"{name}:\n  Candidate: (none)\n"
			self.fail(f"Unexpected probe command: {argv[0]}")

		with patch.object(PROBE, "_run", side_effect=fake_run):
			rows = PROBE._apt_package_rows()

		self.assertEqual(len(rows), len(PROBE.ENGINE_PACKAGES) + len(PROBE.CONFLICT_PACKAGES))
		docker_ce = next(row for row in rows if row["name"] == "docker-ce")
		self.assertEqual(docker_ce["dpkg_status"], "installed")
		self.assertEqual(docker_ce["installed_version"], "5:29.0.0-1")
		self.assertEqual(docker_ce["candidate_version"], "5:29.1.0-1")
		self.assertEqual(docker_ce["candidate_origin"], "https://download.docker.com/linux/ubuntu")
		self.assertIsNone(docker_ce["installed_origin"])
		self.assertTrue(docker_ce["held"])
		self.assertTrue(all(row["held"] is False for row in rows if row["name"] != "docker-ce"))
		self.assertTrue(all(argv[0] not in {"apt-get", "apt", "dpkg", "systemctl"} for argv in calls))

	def test_gpg_parser_separates_primary_and_subkey_fingerprints(self):
		output = "\n".join((
			"pub:-:4096:1:PRIMARYKEY:1700000000:::",
			"fpr:::::::::9DC858229FC7DD38854AE2D88D81803C0EBFCD88:",
			"uid:-::::1700000000::UIDHASH::Docker Release (CE deb) <docker@docker.com>:",
			"sub:-:4096:1:SUBKEYID:1700000000::::::",
			"fpr:::::::::0123456789ABCDEF0123456789ABCDEF01234567:",
		))
		fingerprints, primary_keys = PROBE._parse_gpg_key_inventory(output)
		self.assertEqual(fingerprints, [
			"9DC858229FC7DD38854AE2D88D81803C0EBFCD88",
			"0123456789ABCDEF0123456789ABCDEF01234567",
		])
		self.assertEqual(primary_keys, [{
			"fingerprint": "9DC858229FC7DD38854AE2D88D81803C0EBFCD88",
			"identity": "Docker Release (CE deb) <docker@docker.com>",
			"subkey_fingerprints": ["0123456789ABCDEF0123456789ABCDEF01234567"],
		}])

	def test_gpg_parser_does_not_promote_subkey_to_primary_key(self):
		output = "sub:-:4096:1:SUBKEYID:1700000000::::::\nfpr:::::::::0123456789ABCDEF0123456789ABCDEF01234567:"
		fingerprints, primary_keys = PROBE._parse_gpg_key_inventory(output)
		self.assertEqual(fingerprints, ["0123456789ABCDEF0123456789ABCDEF01234567"])
		self.assertEqual(primary_keys, [])

	def test_non_apt_inventory_fails_closed_with_empty_package_evidence(self):
		host = {"id": "fedora", "version_id": "42", "version_codename": None, "architecture": "x86_64"}
		services = {"manager": "systemd", "docker": "Unknown", "containerd": "Unknown"}
		workloads = {"available": False, "running_containers": None, "total_containers": None}
		with (
			patch.object(
				PROBE, "_storage", return_value={"root_available_bytes": 1, "apt_cache_available_bytes": 1}
			),
			patch.object(PROBE, "_docker_engine_health", return_value="unavailable"),
		):
			result = PROBE._install_inventory(host, "rpm", services, workloads)
		self.assertEqual(result["package_manager"], "rpm")
		self.assertEqual(result["packages"], [])
		self.assertEqual(result["apt_metadata"]["status"], "Unavailable")
		self.assertFalse(result["package_manager_state"]["dpkg_audit_clean"])

	def test_workload_probe_returns_counts_only(self):
		with (
			patch.object(PROBE.shutil, "which", return_value="/usr/bin/docker"),
			patch.object(PROBE, "_run", side_effect=[(0, "abc123\ndef456\n"), (0, "abc123\n")]),
		):
			result = PROBE._workload_summary()
		self.assertEqual(result, {"available": True, "running_containers": 1, "total_containers": 2})
		self.assertNotIn("ids", result)
		self.assertNotIn("names", result)

	def test_apt_metadata_age_is_classified_without_refresh(self):
		class FakeStat:
			st_mtime = 1_000_000

		class FakePath:
			def is_file(self):
				return True

			def stat(self):
				return FakeStat()

			@property
			def name(self):
				return "download.docker.com_linux_ubuntu_Packages"

		with (
			patch.object(PROBE, "APT_LISTS") as lists,
			patch.object(PROBE, "datetime") as clock,
		):
			lists.iterdir.return_value = [FakePath()]
			clock.now.return_value.timestamp.return_value = 1_000_000 + 90_000
			result = PROBE._apt_metadata()
		self.assertEqual(result, {"status": "Aging", "age_seconds": 90000})


if __name__ == "__main__":
	import unittest

	unittest.main()
