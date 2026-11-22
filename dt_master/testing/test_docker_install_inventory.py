"""Tests for the read-only Docker install inventory evidence probes."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

MODULE_PATH = Path(__file__).parents[1] / "scripts" / "bin" / "docker" / "docker_install_inventory.py"
SPEC = importlib.util.spec_from_file_location("docker_install_inventory_under_test", MODULE_PATH)
INVENTORY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INVENTORY)


class DockerInstallInventoryEvidenceTests(TestCase):
    def test_missing_engine_with_inactive_services_has_zero_workloads(self):
        with (
            patch.object(INVENTORY, "_package_manager", return_value="dpkg"),
            patch.object(INVENTORY, "_os_release", return_value={"id": "ubuntu"}),
            patch.object(INVENTORY, "_service_state", return_value={
                "manager": "systemd", "docker": "inactive", "containerd": "inactive"
            }),
            patch.object(INVENTORY, "_workload_summary", return_value={
                "available": True, "running_containers": 0, "total_containers": 0
            }),
            patch.object(INVENTORY, "_installed_packages", return_value=[]),
            patch.object(INVENTORY, "_install_inventory", side_effect=lambda host, manager, services, workloads: {
                "workloads": workloads
            }),
            patch.object(INVENTORY.shutil, "which", return_value=None),
        ):
            result = INVENTORY.collect()
        self.assertEqual(result["workloads"], {
            "available": True, "running_containers": 0, "total_containers": 0
        })
        self.assertEqual(result["docker_install_inventory"]["workloads"], result["workloads"])

    def test_apt_metadata_requires_root_owned_refresh_marker(self):
        marker_stat = SimpleNamespace(st_uid=0, st_gid=0, st_mode=0o100644, st_mtime=INVENTORY.datetime.now(INVENTORY.timezone.utc).timestamp())
        with (
            patch.object(type(INVENTORY.APT_REFRESH_MARKER), "lstat", return_value=marker_stat),
            patch.object(type(INVENTORY.APT_REFRESH_MARKER), "is_file", return_value=True),
        ):
            result = INVENTORY._apt_metadata()
        self.assertEqual(result["status"], "Fresh")
        self.assertIsInstance(result["age_seconds"], int)

    def test_missing_apt_refresh_marker_is_unavailable(self):
        with patch.object(type(INVENTORY.APT_REFRESH_MARKER), "lstat", side_effect=FileNotFoundError):
            result = INVENTORY._apt_metadata()
        self.assertEqual(result, {"status": "Unavailable", "age_seconds": None})
