"""Unit tests for the pure Docker readiness evaluator."""
import unittest
from datetime import datetime, timezone

from dt_master.docker_readiness import (
    ReadinessContractError,
    evaluate_docker_readiness,
)


def snapshot(**overrides):
    value = {
        "schema_version": "1.0.0",
        "observed_at": "2026-11-18T08:00:00Z",
        "inspection_status": "Successful",
        "readiness": "Not Ready",  # Evaluator must not trust this field.
        "host": {
            "cpu": {"logical_cores": 8},
            "memory": {"total_bytes": 16_000_000_000, "available_bytes": 8_000_000_000},
        },
        "docker_cli": {"installed": True, "path": "/usr/bin/docker", "client_version": "29.0.0"},
        "engine": {
            "availability": "Available", "server_version": "29.0.0",
            "api_version": "1.52", "minimum_api_version": "1.44",
        },
        "compose": {"installed": True, "version": "2.30.0", "compatibility": "Compatible"},
        "storage": {
            "driver": "overlayfs", "data_root": "/var/lib/docker",
            "filesystem": {
                "mount_point": "/", "total_bytes": 100_000_000_000,
                "available_bytes": 50_000_000_000, "used_bytes": 50_000_000_000,
            },
        },
        "permissions": {"execution_identity": "docker-inspector", "inspection_access": "Allowed"},
        "diagnostics": [],
    }
    value.update(overrides)
    return value


def policy(checks, max_age=None):
    result = {"requirements_version": "1.0.0", "checks": checks}
    if max_age is not None:
        result["max_snapshot_age_seconds"] = max_age
    return result


def check(key, expected, severity="required"):
    return {"key": key, "expected": expected, "severity": severity}


class TestDockerReadiness(unittest.TestCase):
    def test_all_mandatory_requirements_pass(self):
        result = evaluate_docker_readiness(
            policy([
                check("docker_engine_required", True),
                check("docker_compose_required", True),
                check("min_cpu_cores", 4),
                check("min_available_storage_bytes", 20_000_000_000),
                check("min_engine_version", "28.0.0"),
            ]),
            snapshot(),
            evaluated_at=datetime(2026, 11, 18, 9, tzinfo=timezone.utc),
        )
        self.assertEqual(result["readiness"], "Ready")
        self.assertEqual(result["diagnostics"], [])

    def test_mandatory_failure_is_not_ready(self):
        result = evaluate_docker_readiness(
            policy([check("docker_engine_required", True)]),
            snapshot(engine={"availability": "Unavailable", "server_version": None,
                             "api_version": None, "minimum_api_version": None}),
        )
        self.assertEqual(result["readiness"], "Not Ready")
        self.assertEqual(result["diagnostics"][0]["code"], "DOCKER_REQUIREMENT_NOT_MET")

    def test_recommended_failure_is_degraded(self):
        result = evaluate_docker_readiness(
            policy([
                check("docker_engine_required", True),
                check("min_cpu_cores", 16, "recommended"),
            ]),
            snapshot(),
        )
        self.assertEqual(result["readiness"], "Degraded")

    def test_missing_required_evidence_is_unknown(self):
        data = snapshot()
        data["host"]["memory"]["available_bytes"] = None
        result = evaluate_docker_readiness(
            policy([check("min_available_memory_bytes", 1_000_000)]), data
        )
        self.assertEqual(result["readiness"], "Unknown")

    def test_failed_inspection_does_not_trust_fallback_false_values(self):
        data = snapshot(inspection_status="Failed")
        data["docker_cli"]["installed"] = False
        result = evaluate_docker_readiness(
            policy([check("docker_engine_required", True)]), data
        )
        self.assertEqual(result["readiness"], "Unknown")
        self.assertEqual(result["diagnostics"][0]["code"], "DOCKER_REQUIREMENT_EVIDENCE_MISSING")

    def test_partial_inspection_is_not_treated_as_complete_evidence(self):
        result = evaluate_docker_readiness(
            policy([check("docker_engine_required", True)]),
            snapshot(inspection_status="Partial"),
        )
        self.assertEqual(result["readiness"], "Unknown")

    def test_stale_snapshot_is_unknown_for_required_checks(self):
        result = evaluate_docker_readiness(
            policy([check("docker_engine_required", True)], max_age=60),
            snapshot(),
            evaluated_at=datetime(2026, 11, 18, 9, tzinfo=timezone.utc),
        )
        self.assertEqual(result["readiness"], "Unknown")
        self.assertEqual(result["diagnostics"][0]["code"], "DOCKER_INSPECTION_STALE")

    def test_malformed_policy_is_rejected(self):
        with self.assertRaises(ReadinessContractError):
            evaluate_docker_readiness({"requirements_version": "2.0.0", "checks": []}, snapshot())

    def test_duplicate_requirement_is_rejected(self):
        item = check("docker_engine_required", True)
        with self.assertRaises(ReadinessContractError):
            evaluate_docker_readiness(policy([item, item]), snapshot())

    def test_unconfigured_policy_is_rejected_not_ready(self):
        with self.assertRaises(ReadinessContractError):
            evaluate_docker_readiness(policy([]), snapshot())

    def test_snapshot_readiness_is_not_authoritative(self):
        data = snapshot(readiness="Ready")
        data["engine"]["availability"] = "Unavailable"
        result = evaluate_docker_readiness(
            policy([check("docker_engine_required", True)]), data
        )
        self.assertEqual(result["readiness"], "Not Ready")


if __name__ == "__main__":
    unittest.main()
