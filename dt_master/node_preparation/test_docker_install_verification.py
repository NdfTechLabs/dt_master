"""Tests for independent Docker Engine install postcondition evaluation."""

import copy
import unittest

from dt_master.node_preparation.docker_install import ENGINE_PACKAGES
from dt_master.node_preparation.docker_install_verification import (
	evaluate_docker_engine_install_postcondition,
)


VERSION = "5:29.0.0-1~ubuntu.24.04~noble"
ORIGIN = "https://download.docker.com/linux/ubuntu noble/stable amd64 Packages"


def inventory():
	packages = []
	for name in (*ENGINE_PACKAGES, "docker.io", "docker-compose", "podman-docker", "containerd", "runc"):
		is_engine = name in ENGINE_PACKAGES
		packages.append(
			{
				"name": name,
				"installed_version": VERSION if is_engine else None,
				"installed_origin": ORIGIN if is_engine else None,
				"dpkg_status": "installed" if is_engine else "not-installed",
				"candidate_version": VERSION if is_engine else None,
				"candidate_origin": ORIGIN if is_engine else None,
				"held": False,
			}
		)
	return {
		"schema_version": "1.0.0",
		"observed_at": "2026-11-21T12:00:00Z",
		"host_os": {"id": "ubuntu", "version_id": "24.04", "codename": "noble", "architecture": "amd64"},
		"package_manager": "apt",
		"packages": packages,
		"repository": {
			"source_id": "download.docker.com",
			"keyring_path": "/etc/apt/keyrings/docker.asc",
			"fingerprints": ["0123456789ABCDEF0123456789ABCDEF01234567"],
		},
		"apt_metadata": {"status": "Fresh", "age_seconds": 3600},
		"package_manager_state": {"locked": False, "dpkg_audit_clean": True},
		"storage": {"root_available_bytes": 20 * 1024**3, "apt_cache_available_bytes": 2 * 1024**3},
		"services": {"docker": "active", "containerd": "active"},
		"workloads": {"available": True, "running_containers": 0, "total_containers": 0},
		"docker_engine_health": "healthy",
	}


def helper_result():
	return {
		"schema_version": "1.0.0",
		"helper_version": "0.1.0",
		"operation": "install_engine",
		"operation_id": "OP-0001",
		"status": "succeeded",
		"phase": "result_verification",
		"before_state_fingerprint": "a" * 64,
		"after_state_fingerprint": "b" * 64,
		"packages": [
			{
				"name": name,
				"requested_version": VERSION,
				"installed_version": VERSION,
				"status": "installed",
			}
			for name in ENGINE_PACKAGES
		],
		"package_manager_exit_code": 0,
		"daemon_action": "package_maintainer_scripts",
		"diagnostics": [],
	}


class TestDockerEngineInstallPostcondition(unittest.TestCase):
	def evaluate(self, observed):
		return evaluate_docker_engine_install_postcondition(
			"NODE-0001", observed, helper_result(), expected_operation_id="OP-0001"
		)

	def test_exact_versions_trusted_origins_and_healthy_engine_verify(self):
		result = self.evaluate(inventory())
		self.assertEqual(result["status"], "verified")
		self.assertEqual(len(result["packages"]), 4)
		self.assertTrue(all(row["status"] == "verified" for row in result["packages"]))
		self.assertEqual(result["services"], {"docker": "active", "containerd": "active"})
		self.assertEqual(len(result["state_fingerprint"]), 64)

	def test_version_mismatch_fails_postcondition(self):
		observed = inventory()
		observed["packages"][0]["installed_version"] = "5:28.0.0-1"
		result = self.evaluate(observed)
		self.assertEqual(result["status"], "failed")
		self.assertEqual(result["diagnostics"][0]["code"], "DOCKER_INSTALL_PACKAGE_POSTCONDITION_MISMATCH")

	def test_untrusted_installed_origin_fails_postcondition(self):
		observed = inventory()
		observed["packages"][1]["installed_origin"] = "mirror.example/ubuntu"
		result = self.evaluate(observed)
		self.assertEqual(result["status"], "failed")

	def test_incomplete_package_observation_does_not_verify(self):
		observed = inventory()
		observed["packages"][2]["dpkg_status"] = "unknown"
		observed["packages"][2]["installed_version"] = None
		result = self.evaluate(observed)
		self.assertEqual(result["status"], "incomplete")

	def test_broken_package_manager_or_conflicting_family_fails(self):
		observed = inventory()
		observed["package_manager_state"]["dpkg_audit_clean"] = False
		result = self.evaluate(observed)
		self.assertEqual(result["status"], "failed")

		observed = inventory()
		observed["packages"][-5]["dpkg_status"] = "installed"
		observed["packages"][-5]["installed_version"] = "1.0"
		result = self.evaluate(observed)
		self.assertEqual(result["status"], "failed")

	def test_unknown_engine_health_does_not_verify(self):
		observed = inventory()
		observed["docker_engine_health"] = "unavailable"
		result = self.evaluate(observed)
		self.assertEqual(result["status"], "incomplete")

	def test_invalid_helper_evidence_does_not_verify(self):
		helper = helper_result()
		helper["operation_id"] = "OTHER"
		result = evaluate_docker_engine_install_postcondition(
			"NODE-0001", inventory(), helper, expected_operation_id="OP-0001"
		)
		self.assertEqual(result["status"], "incomplete")
		self.assertEqual(result["diagnostics"][0]["code"], "DOCKER_INSTALL_HELPER_EVIDENCE_INVALID")

	def test_helper_pre_mutation_rejection_is_failed(self):
		helper = helper_result()
		helper["status"] = "failed_before_mutation"
		helper["phase"] = "host_validation"
		helper["package_manager_exit_code"] = 1
		helper["diagnostics"] = ["HOST_VALIDATION_FAILED"]
		result = evaluate_docker_engine_install_postcondition(
			"NODE-0001", inventory(), helper, expected_operation_id="OP-0001"
		)
		self.assertEqual(result["status"], "failed")


if __name__ == "__main__":
	unittest.main()
