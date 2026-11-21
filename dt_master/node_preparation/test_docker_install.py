"""Tests for the pure N3.2 Docker Engine installation planner."""

import copy
import unittest

from dt_master.node_preparation.docker_install import (
	ENGINE_PACKAGES,
	DockerInstallPlanError,
	build_docker_engine_install_plan,
	docker_engine_install_state_fingerprint,
	validate_docker_engine_install_plan,
)


def inventory(**overrides):
	packages = []
	for name in (*ENGINE_PACKAGES, "docker.io", "docker-compose", "podman-docker", "containerd", "runc"):
		packages.append(
			{
				"name": name,
				"installed_version": None,
				"installed_origin": None,
				"dpkg_status": "not-installed",
				"candidate_version": "5:29.0.0-1~ubuntu.24.04~noble" if name in ENGINE_PACKAGES else None,
				"candidate_origin": "https://download.docker.com/linux/ubuntu noble/stable amd64 Packages"
				if name in ENGINE_PACKAGES
				else None,
				"held": False,
			}
		)
	values = {
		"schema_version": "1.0.0",
		"observed_at": "2026-11-21T10:00:00Z",
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
		"services": {"docker": "Unknown", "containerd": "Unknown"},
		"workloads": {"available": True, "running_containers": 0, "total_containers": 0},
		"docker_engine_health": "not_installed",
	}
	values.update(overrides)
	return values


class TestDockerEngineInstallPlan(unittest.TestCase):

	def test_state_fingerprint_ignores_observation_clock_but_binds_host_state(self):
		first = inventory()
		second = copy.deepcopy(first)
		second["observed_at"] = "2026-11-21T11:00:00Z"
		second["apt_metadata"]["age_seconds"] = 7200
		self.assertEqual(
			docker_engine_install_state_fingerprint(first),
			docker_engine_install_state_fingerprint(second),
		)

	def test_state_fingerprint_changes_when_package_or_workload_state_changes(self):
		original = inventory()
		package_change = copy.deepcopy(original)
		package_change["packages"][0]["candidate_version"] = "5:30.0.0-1~ubuntu.24.04~noble"
		workload_change = copy.deepcopy(original)
		workload_change["workloads"]["running_containers"] = 1
		self.assertNotEqual(
			docker_engine_install_state_fingerprint(original),
			docker_engine_install_state_fingerprint(package_change),
		)
		self.assertNotEqual(
			docker_engine_install_state_fingerprint(original),
			docker_engine_install_state_fingerprint(workload_change),
		)

	def test_state_fingerprint_rejects_unknown_or_sensitive_inventory_fields(self):
		bad = inventory()
		bad["ssh_host"] = "host.example"
		with self.assertRaises(DockerInstallPlanError):
			docker_engine_install_state_fingerprint(bad)
		with self.assertRaises(DockerInstallPlanError):
			docker_engine_install_state_fingerprint({"schema_version": "1.0.0"})

	def test_supported_platforms_are_accepted(self):
		for os_id, version in (("ubuntu", "22.04"), ("ubuntu", "24.04"), ("debian", "12")):
			host = {"id": os_id, "version_id": version, "codename": "stable", "architecture": "amd64"}
			self.assertEqual(
				build_docker_engine_install_plan("NODE-1", inventory(host_os=host))["target"]["name"],
				"NODE-1",
			)

	def test_unsupported_platform_is_blocked(self):
		plan = build_docker_engine_install_plan(
			"NODE-1",
			inventory(
				host_os={"id": "ubuntu", "version_id": "26.04", "codename": "x", "architecture": "amd64"}
			),
		)
		self.assertEqual(plan["status"], "blocked")
		self.assertIn("HOST_UNSUPPORTED", plan["reason_codes"])

	def test_fresh_metadata_without_key_policy_is_not_executable(self):
		plan = build_docker_engine_install_plan("NODE-1", inventory())
		self.assertFalse(plan["mutation_authorized"])
		self.assertEqual(plan["mode"], "plan_only")
		self.assertIn("DOCKER_SIGNING_KEY_UNVERIFIED", plan["reason_codes"])
		self.assertNotEqual(plan["status"], "ready")

	def test_structured_key_matching_reviewed_policy_is_trusted(self):
		repository = copy.deepcopy(inventory()["repository"])
		repository["fingerprints"] = [
			"9DC858229FC7DD38854AE2D88D81803C0EBFCD88",
			"0123456789ABCDEF0123456789ABCDEF01234567",
		]
		repository["primary_keys"] = [
			{
				"fingerprint": "9DC858229FC7DD38854AE2D88D81803C0EBFCD88",
				"identity": "Docker Release (CE deb) <docker@docker.com>",
				"subkey_fingerprints": ["0123456789ABCDEF0123456789ABCDEF01234567"],
			}
		]
		plan = build_docker_engine_install_plan("NODE-1", inventory(repository=repository))
		self.assertNotIn("DOCKER_SIGNING_KEY_MISMATCH", plan["reason_codes"])
		self.assertNotIn("DOCKER_SIGNING_KEY_UNVERIFIED", plan["reason_codes"])
		self.assertTrue(plan["repository"]["signing_key_policy"]["trusted"])
		self.assertEqual(plan["repository"]["signing_key_policy"]["version"], "1.0.0")
		self.assertEqual(
			plan["repository"]["signing_key_policy"]["key_url"],
			"https://download.docker.com/linux/ubuntu/gpg",
		)

	def test_structured_key_with_wrong_primary_fingerprint_is_rejected(self):
		repository = copy.deepcopy(inventory()["repository"])
		repository["primary_keys"] = [
			{
				"fingerprint": "0123456789ABCDEF0123456789ABCDEF01234567",
				"identity": "Docker Release (CE deb) <docker@docker.com>",
				"subkey_fingerprints": [],
			}
		]
		plan = build_docker_engine_install_plan("NODE-1", inventory(repository=repository))
		self.assertIn("DOCKER_SIGNING_KEY_MISMATCH", plan["reason_codes"])
		self.assertEqual(plan["status"], "blocked")

	def test_structured_key_with_wrong_identity_is_rejected(self):
		repository = copy.deepcopy(inventory()["repository"])
		repository["primary_keys"] = [
			{
				"fingerprint": "9DC858229FC7DD38854AE2D88D81803C0EBFCD88",
				"identity": "Untrusted identity",
				"subkey_fingerprints": [],
			}
		]
		plan = build_docker_engine_install_plan("NODE-1", inventory(repository=repository))
		self.assertIn("DOCKER_SIGNING_KEY_MISMATCH", plan["reason_codes"])

	def test_multiple_primary_keys_are_rejected(self):
		repository = copy.deepcopy(inventory()["repository"])
		repository["primary_keys"] = [
			{
				"fingerprint": "9DC858229FC7DD38854AE2D88D81803C0EBFCD88",
				"identity": "Docker Release (CE deb) <docker@docker.com>",
				"subkey_fingerprints": [],
			},
			{
				"fingerprint": "0123456789ABCDEF0123456789ABCDEF01234567",
				"identity": "Unexpected key",
				"subkey_fingerprints": [],
			},
		]
		plan = build_docker_engine_install_plan("NODE-1", inventory(repository=repository))
		self.assertIn("DOCKER_SIGNING_KEY_MISMATCH", plan["reason_codes"])

	def test_malformed_structured_key_evidence_is_rejected(self):
		repository = copy.deepcopy(inventory()["repository"])
		repository["primary_keys"] = [
			{"fingerprint": "not-a-fingerprint", "identity": "Docker", "subkey_fingerprints": []}
		]
		with self.assertRaises(DockerInstallPlanError):
			build_docker_engine_install_plan("NODE-1", inventory(repository=repository))

	def test_stale_metadata_requires_explicit_preparation(self):
		plan = build_docker_engine_install_plan(
			"NODE-1", inventory(apt_metadata={"status": "Stale", "age_seconds": 900000})
		)
		self.assertIn("refresh_apt_metadata", plan["prerequisites"])
		self.assertNotEqual(plan["status"], "ready")

	def test_package_family_conflict_blocks(self):
		rows = inventory()["packages"]
		rows = copy.deepcopy(rows)
		rows[-5]["dpkg_status"] = "installed"
		rows[-5]["installed_version"] = "1.0"
		plan = build_docker_engine_install_plan("NODE-1", inventory(packages=rows))
		self.assertIn("CONFLICTING_PACKAGE_FAMILY", plan["reason_codes"])
		self.assertEqual(plan["status"], "blocked")

	def test_active_workloads_require_impact_approval(self):
		plan = build_docker_engine_install_plan(
			"NODE-1", inventory(workloads={"available": True, "running_containers": 2, "total_containers": 3})
		)
		self.assertIn("ACTIVE_WORKLOADS_IMPACT_APPROVAL_REQUIRED", plan["reason_codes"])
		self.assertTrue(plan["workload_impact"]["operator_impact_approval_required"])
		self.assertEqual(plan["status"], "blocked")

	def test_package_manager_lock_blocks(self):
		plan = build_docker_engine_install_plan(
			"NODE-1", inventory(package_manager_state={"locked": True, "dpkg_audit_clean": True})
		)
		self.assertIn("PACKAGE_MANAGER_BUSY", plan["reason_codes"])

	def test_broken_dpkg_state_blocks(self):
		plan = build_docker_engine_install_plan(
			"NODE-1", inventory(package_manager_state={"locked": False, "dpkg_audit_clean": False})
		)
		self.assertIn("DPKG_STATE_BROKEN", plan["reason_codes"])

	def test_buildx_required_compose_excluded(self):
		plan = build_docker_engine_install_plan("NODE-1", inventory())
		self.assertEqual(plan["package_set"], list(ENGINE_PACKAGES))
		self.assertIn("docker-buildx-plugin", plan["package_set"])
		self.assertNotIn("docker-compose-plugin", plan["package_set"])

	def test_no_removal_actions_are_emitted(self):
		plan = build_docker_engine_install_plan("NODE-1", inventory())
		self.assertTrue(all(row["action"] in {"install", "keep"} for row in plan["packages"]))

	def test_deterministic_digest_ignores_observation_timestamp(self):
		first = build_docker_engine_install_plan("NODE-1", inventory())
		second_inventory = inventory(observed_at="2026-11-21T11:00:00Z")
		second = build_docker_engine_install_plan("NODE-1", second_inventory)
		self.assertEqual(first, second)
		self.assertEqual(first["plan_digest"], second["plan_digest"])

	def test_plan_exposes_inventory_state_fingerprint_and_binds_it_into_digest(self):
		host_inventory = inventory()
		plan = build_docker_engine_install_plan("NODE-1", host_inventory)
		self.assertEqual(plan["state_fingerprint"], docker_engine_install_state_fingerprint(host_inventory))
		tampered = copy.deepcopy(plan)
		tampered["state_fingerprint"] = "f" * 64
		with self.assertRaises(DockerInstallPlanError):
			validate_docker_engine_install_plan(tampered)

	def test_plan_tampering_is_rejected(self):
		plan = build_docker_engine_install_plan("NODE-1", inventory())
		tampered = copy.deepcopy(plan)
		tampered["mutation_authorized"] = True
		with self.assertRaises(DockerInstallPlanError):
			validate_docker_engine_install_plan(tampered)

	def test_unknown_and_sensitive_inventory_fields_are_rejected(self):
		bad = inventory()
		bad["ssh_host"] = "host.example"
		with self.assertRaises(DockerInstallPlanError):
			build_docker_engine_install_plan("NODE-1", bad)

	def test_duplicate_or_unknown_package_is_rejected(self):
		rows = inventory()["packages"]
		with self.assertRaises(DockerInstallPlanError):
			build_docker_engine_install_plan("NODE-1", inventory(packages=[*rows, rows[0]]))
		bad_rows = copy.deepcopy(rows)
		bad_rows[0]["name"] = "apt-get"
		with self.assertRaises(DockerInstallPlanError):
			build_docker_engine_install_plan("NODE-1", inventory(packages=bad_rows))

	def test_invalid_workload_counts_are_rejected(self):
		with self.assertRaises(DockerInstallPlanError):
			build_docker_engine_install_plan(
				"NODE-1",
				inventory(workloads={"available": True, "running_containers": 4, "total_containers": 2}),
			)

	def test_complete_healthy_docker_ce_install_is_no_op(self):
		rows = copy.deepcopy(inventory()["packages"])
		for row in rows[:4]:
			row["installed_version"] = "5:29.0.0-1~ubuntu.24.04~noble"
			row["installed_origin"] = "https://download.docker.com/linux/ubuntu noble/stable amd64 Packages"
			row["dpkg_status"] = "installed"
		plan = build_docker_engine_install_plan(
			"NODE-1",
			inventory(packages=rows, docker_engine_health="healthy"),
		)
		self.assertEqual(plan["status"], "no_op")
		self.assertTrue(all(row["action"] == "keep" for row in plan["packages"]))

	def test_partial_docker_ce_installation_is_blocked(self):
		rows = copy.deepcopy(inventory()["packages"])
		rows[0]["installed_version"] = "5:29.0.0-1"
		rows[0]["installed_origin"] = "https://download.docker.com/linux/ubuntu"
		rows[0]["dpkg_status"] = "installed"
		plan = build_docker_engine_install_plan("NODE-1", inventory(packages=rows))
		self.assertIn("PARTIAL_DOCKER_CE_INSTALLATION", plan["reason_codes"])
		self.assertEqual(plan["status"], "blocked")

	def test_invalid_node_name_is_rejected(self):
		with self.assertRaises(DockerInstallPlanError):
			build_docker_engine_install_plan("../node", inventory())


if __name__ == "__main__":
	unittest.main()
