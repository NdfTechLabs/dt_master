"""Tests for the NP.2 baseline policy and deterministic plan contract."""

import copy
import unittest

from dt_master.node_preparation.baseline import (
    BaselinePolicyError,
    build_baseline_preparation_plan,
    validate_baseline_preparation_plan,
)


def host_observation(**overrides):
    values = {
        "inspection_status": "Successful",
        "os_id": "ubuntu",
        "os_version_id": "24.04",
        "architecture": "amd64",
        "cpu_cores": 8,
        "memory_bytes": 16 * 1024**3,
        "root_available_bytes": 100 * 1024**3,
        "package_manager": "apt",
        "service_manager": "systemd",
    }
    values.update(overrides)
    return values


def docker_observation(**overrides):
    values = {
        "inspection_status": "Successful",
        "readiness": "Ready",
        "snapshot": {"inspection_status": "Successful", "engine": {"availability": "Available"}},
    }
    values.update(overrides)
    return values


class TestBaselinePreparationPlan(unittest.TestCase):
    def test_plan_is_deterministic_for_identical_observations(self):
        first = build_baseline_preparation_plan(
            "NODE-0001", host_observation(), docker_observation()
        )
        second = build_baseline_preparation_plan(
            "NODE-0001", host_observation(), docker_observation()
        )
        self.assertEqual(first, second)
        self.assertEqual(first["plan_digest"], second["plan_digest"])

    def test_supported_os_matrix_matches_accepted_np1_scope(self):
        for os_id, version, architecture in (
            ("ubuntu", "22.04", "amd64"),
            ("ubuntu", "24.04", "amd64"),
            ("debian", "12", "amd64"),
        ):
            with self.subTest(os_id=os_id, version=version):
                plan = build_baseline_preparation_plan(
                    "NODE-0001",
                    host_observation(os_id=os_id, os_version_id=version, architecture=architecture),
                    docker_observation(),
                )
                self.assertEqual(plan["observations"]["host_support"], "supported")

    def test_unsupported_and_unknown_hosts_are_blocked(self):
        unsupported = build_baseline_preparation_plan(
            "NODE-0001", host_observation(os_id="ubuntu", os_version_id="26.04"), docker_observation()
        )
        unknown = build_baseline_preparation_plan(
            "NODE-0001", host_observation(os_id="unknown"), docker_observation()
        )
        self.assertEqual(unsupported["status"], "blocked")
        self.assertEqual(unsupported["observations"]["host_support"], "unsupported")
        self.assertEqual(unknown["observations"]["host_support"], "unknown")

    def test_missing_or_incomplete_docker_evidence_blocks_plan(self):
        plan = build_baseline_preparation_plan("NODE-0001", host_observation())
        self.assertEqual(plan["status"], "blocked")
        self.assertEqual(plan["observations"]["docker_evidence"], "missing")

    def test_successful_evidence_still_requires_execution_identity_verification(self):
        plan = build_baseline_preparation_plan(
            "NODE-0001", host_observation(), docker_observation()
        )
        self.assertEqual(plan["status"], "verification_required")
        action = next(item for item in plan["actions"] if item["action_id"] == "verify_execution_identity")
        self.assertEqual(action["status"], "requires_verification")

    def test_plan_is_explicitly_non_mutating_and_preserves_existing_data_root(self):
        plan = build_baseline_preparation_plan(
            "NODE-0001", host_observation(), docker_observation()
        )
        self.assertEqual(plan["mode"], "plan_only")
        self.assertFalse(plan["mutation_authorized"])
        self.assertEqual(plan["storage_policy"]["existing_docker_data_root"], "preserve")
        self.assertFalse(plan["storage_policy"]["relocation_permitted"])
        self.assertTrue(all(item["kind"] == "verification" for item in plan["actions"]))

    def test_caller_supplied_commands_and_sensitive_values_are_rejected(self):
        for observation in (
            host_observation(command="apt install docker"),
            host_observation(ssh_private_key="not-allowed"),
            host_observation(ip_address="192.0.2.1"),
        ):
            with self.subTest(observation=observation), self.assertRaises(BaselinePolicyError):
                build_baseline_preparation_plan("NODE-0001", observation, docker_observation())

    def test_invalid_capacity_evidence_fails_closed(self):
        plan = build_baseline_preparation_plan(
            "NODE-0001", host_observation(cpu_cores=True), docker_observation()
        )
        self.assertEqual(plan["observations"]["host_evidence"], "invalid")
        self.assertEqual(plan["status"], "blocked")

    def test_plan_validation_detects_tampering(self):
        plan = build_baseline_preparation_plan(
            "NODE-0001", host_observation(), docker_observation()
        )
        tampered = copy.deepcopy(plan)
        tampered["mutation_authorized"] = True
        with self.assertRaises(BaselinePolicyError):
            validate_baseline_preparation_plan(tampered)

    def test_plan_validation_rejects_unknown_or_mutating_actions(self):
        plan = build_baseline_preparation_plan(
            "NODE-0001", host_observation(), docker_observation()
        )
        tampered = copy.deepcopy(plan)
        tampered["actions"][0]["kind"] = "install_package"
        unsigned = dict(tampered)
        unsigned.pop("plan_digest")
        import hashlib
        import json
        tampered["plan_digest"] = hashlib.sha256(
            json.dumps(unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        with self.assertRaises(BaselinePolicyError):
            validate_baseline_preparation_plan(tampered)

    def test_invalid_node_identity_is_rejected(self):
        with self.assertRaises(BaselinePolicyError):
            build_baseline_preparation_plan("../NODE", host_observation(), docker_observation())


if __name__ == "__main__":
    unittest.main()
