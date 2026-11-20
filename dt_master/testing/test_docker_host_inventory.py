"""Tests for the read-only Docker host inventory probe."""
import unittest
from unittest.mock import patch

from dt_master.scripts.bin.docker import inspect_docker_host


class TestDockerHostInventory(unittest.TestCase):
    def test_inventory_reports_supported_os_and_installed_packages(self):
        with (
            patch.object(inspect_docker_host, "_os_release", return_value={
                "id": "ubuntu", "id_like": "debian", "name": "Ubuntu",
                "version_id": "24.04", "version_codename": "noble",
                "pretty_name": "Ubuntu 24.04 LTS", "architecture": "x86_64",
                "kernel_release": "6.8.0",
            }),
            patch.object(inspect_docker_host, "_package_manager", return_value="dpkg"),
            patch.object(inspect_docker_host, "_installed_packages", return_value=[
                {"name": "docker-ce", "version": "5:29.0.0-1~ubuntu.24.04~noble", "status": "installed"},
                {"name": "docker-compose-plugin", "version": "2.30.0-1~ubuntu.24.04~noble", "status": "installed"},
            ]),
            patch.object(inspect_docker_host, "_service_state", return_value={
                "manager": "systemd", "docker": "active", "containerd": "active",
            }),
            patch.object(inspect_docker_host, "_workload_summary", return_value={
                "available": True, "running_containers": 2, "total_containers": 4,
            }),
        ):
            result = inspect_docker_host.collect()

        self.assertEqual(result["schema_version"], "1.0.0")
        self.assertEqual(result["host_os"]["id"], "ubuntu")
        self.assertEqual(result["package_manager"], "dpkg")
        self.assertEqual([item["name"] for item in result["docker_packages"]], [
            "docker-ce", "docker-compose-plugin",
        ])
        self.assertEqual(result["services"]["docker"], "active")
        self.assertEqual(result["workloads"]["running_containers"], 2)

    def test_unsupported_package_manager_is_nonfatal(self):
        with (
            patch.object(inspect_docker_host, "_os_release", return_value={
                "id": "alpine", "id_like": None, "name": "Alpine",
                "version_id": "3.21", "version_codename": None,
                "pretty_name": "Alpine Linux", "architecture": "x86_64",
                "kernel_release": "6.12",
            }),
            patch.object(inspect_docker_host, "_package_manager", return_value="apk"),
            patch.object(inspect_docker_host, "_installed_packages", return_value=[]),
            patch.object(inspect_docker_host, "_service_state", return_value={
                "manager": "Unknown", "docker": "Unknown", "containerd": "Unknown",
            }),
            patch.object(inspect_docker_host, "_workload_summary", return_value={
                "available": False, "running_containers": None, "total_containers": None,
            }),
        ):
            result = inspect_docker_host.collect()

        self.assertEqual(result["package_manager"], "apk")
        self.assertEqual(result["docker_packages"], [])
        self.assertFalse(result["workloads"]["available"])

    def test_package_inventory_never_accepts_unexpected_package_rows(self):
        with (
            patch.object(inspect_docker_host, "_package_manager", return_value="dpkg"),
            patch.object(inspect_docker_host.shutil, "which", return_value="/usr/bin/dpkg-query"),
            patch.object(inspect_docker_host, "_run", return_value=(
                0, "docker-ce\t29.0\tinstalled\nmalicious\t1\tinstalled\n"
            )),
        ):
            packages = inspect_docker_host._installed_packages("dpkg")

        self.assertEqual(packages, [
            {"name": "docker-ce", "version": "29.0", "status": "installed"},
        ])


if __name__ == "__main__":
    unittest.main()
