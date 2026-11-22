"""Unit tests for the pure Docker readiness evaluator."""
import unittest
from datetime import datetime, timezone
from pathlib import Path

from dt_master.docker_readiness import (
    ReadinessContractError,
    build_node_readiness_requirements,
    evaluate_docker_readiness,
    validate_docker_readiness_requirements,
)


def snapshot(**overrides):
    value = {
        "schema_version": "1.0.0",
        "observed_at": "2026-11-18T08:00:00Z",
        "inspection_status": "Successful",
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
        self.assertEqual(result["diagnostics"][0]["severity"], "Error")

    def test_failed_inspection_does_not_trust_fallback_false_values(self):
        data = snapshot(inspection_status="Failed")
        data["docker_cli"]["installed"] = False
        result = evaluate_docker_readiness(
            policy([check("docker_engine_required", True)]), data
        )
        self.assertEqual(result["readiness"], "Unknown")
        self.assertEqual(result["diagnostics"][0]["code"], "DOCKER_REQUIREMENT_EVIDENCE_MISSING")

    def test_unknown_engine_observation_is_not_a_confirmed_failure(self):
        data = snapshot()
        data["engine"]["availability"] = "Unknown"
        result = evaluate_docker_readiness(
            policy([check("docker_engine_required", True)]), data
        )
        self.assertEqual(result["readiness"], "Unknown")

    def test_unavailable_compose_probe_is_not_a_confirmed_failure(self):
        data = snapshot()
        data["compose"]["compatibility"] = "Unavailable"
        result = evaluate_docker_readiness(
            policy([check("docker_compose_required", True)]), data
        )
        self.assertEqual(result["readiness"], "Unknown")

    def test_versions_compare_with_zero_padded_components(self):
        result = evaluate_docker_readiness(
            policy([check("min_engine_version", "29.0")]),
            snapshot(),
        )
        self.assertEqual(result["readiness"], "Ready")

    def test_partial_inspection_is_not_treated_as_complete_evidence(self):
        result = evaluate_docker_readiness(
            policy([check("docker_engine_required", True)]),
            snapshot(inspection_status="Partial"),
        )
        self.assertEqual(result["readiness"], "Unknown")

    def test_missing_recommended_evidence_is_not_ready(self):
        data = snapshot()
        data["host"]["cpu"]["logical_cores"] = None
        result = evaluate_docker_readiness(
            policy([check("min_cpu_cores", 16, "recommended")]), data
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

    def test_unconfigured_node_requirements_result_in_unknown(self):
        result = evaluate_docker_readiness(None, snapshot())
        self.assertEqual(result["readiness"], "Unknown")
        self.assertEqual(result["diagnostics"][0]["code"], "DOCKER_READINESS_REQUIREMENTS_UNCONFIGURED")

    def test_empty_node_requirements_result_in_unknown(self):
        result = evaluate_docker_readiness({}, snapshot())
        self.assertEqual(result["readiness"], "Unknown")

    def test_node_requirements_validator_accepts_explicit_policy(self):
        validate_docker_readiness_requirements(policy([check("docker_engine_required", True)]))


    def test_node_fields_normalize_to_required_and_recommended_checks(self):
        class Node:
            def __init__(self):
                self.values = {
                    "docker_engine_required": 1,
                    "docker_engine_severity": "Required",
                    "docker_min_cpu_cores": 8,
                    "docker_min_cpu_cores_severity": "Recommended",
                    "docker_max_snapshot_age_seconds": 3600,
                }

            def get(self, key):
                return self.values.get(key)

        result = build_node_readiness_requirements(Node())
        self.assertEqual(result["requirements_version"], "1.0.0")
        self.assertEqual(
            result["checks"],
            [
                {"key": "docker_engine_required", "severity": "required", "expected": True},
                {"key": "min_cpu_cores", "severity": "recommended", "expected": 8},
            ],
        )
        self.assertEqual(result["max_snapshot_age_seconds"], 3600)


    def test_node_gib_requirements_normalize_to_snapshot_bytes(self):
        class Node:
            def __init__(self):
                self.values = {
                    "docker_min_total_memory_gib": 8,
                    "docker_min_total_memory_severity": "Required",
                    "docker_min_available_memory_gib": 2.5,
                    "docker_min_available_memory_severity": "Recommended",
                    "docker_min_available_storage_gib": 20,
                    "docker_min_available_storage_severity": "Required",
                }

            def get(self, key):
                return self.values.get(key)

        result = build_node_readiness_requirements(Node())
        self.assertEqual(
            [item["expected"] for item in result["checks"]],
            [8 * 1024**3, round(2.5 * 1024**3), 20 * 1024**3],
        )

    def test_node_fields_reject_severity_without_requirement(self):
        class Node:
            def get(self, key):
                return "Required" if key == "docker_engine_severity" else None

        with self.assertRaises(ReadinessContractError):
            build_node_readiness_requirements(Node())

    def test_node_fields_reject_configured_requirement_without_severity(self):
        class Node:
            def get(self, key):
                return 4 if key == "docker_min_cpu_cores" else None

        with self.assertRaises(ReadinessContractError):
            build_node_readiness_requirements(Node())

    def test_recommended_requirement_failure_is_degraded_when_all_evidence_is_present(self):
        result = evaluate_docker_readiness(
            policy([check("min_cpu_cores", 16, "recommended")]),
            snapshot(),
        )
        self.assertEqual(result["readiness"], "Degraded")

    def test_inspection_script_does_not_emit_readiness_decisions(self):
        script = Path(__file__).parents[1] / "scripts" / "bin" / "docker" / "inspect_docker.sh"
        content = script.read_text(encoding="utf-8")
        self.assertNotIn('"readiness":', content)
        self.assertNotIn('readiness="Not Ready"', content)

    def test_snapshot_does_not_require_readiness_field(self):
        data = snapshot()
        self.assertNotIn("readiness", data)
        result = evaluate_docker_readiness(
            policy([check("docker_engine_required", True)]), data
        )
        self.assertEqual(result["readiness"], "Ready")

    def test_legacy_snapshot_readiness_is_ignored(self):
        data = snapshot(readiness="Not Ready")
        result = evaluate_docker_readiness(
            policy([check("docker_engine_required", True)]), data
        )
        self.assertEqual(result["readiness"], "Ready")


    def test_insufficient_available_storage_fails_required_capacity(self):
        data = snapshot()
        data["storage"]["filesystem"]["available_bytes"] = 5_000_000_000
        result = evaluate_docker_readiness(
            policy([check("min_available_storage_bytes", 20_000_000_000)]),
            data,
        )
        self.assertEqual(result["readiness"], "Not Ready")
        self.assertEqual(result["diagnostics"][0]["code"], "DOCKER_REQUIREMENT_NOT_MET")

    def test_insufficient_available_memory_fails_required_capacity(self):
        data = snapshot()
        data["host"]["memory"]["available_bytes"] = 512_000_000
        result = evaluate_docker_readiness(
            policy([check("min_available_memory_bytes", 2_000_000_000)]),
            data,
        )
        self.assertEqual(result["readiness"], "Not Ready")
        self.assertEqual(result["diagnostics"][0]["code"], "DOCKER_REQUIREMENT_NOT_MET")

    def test_incompatible_compose_version_fails_minimum_version_requirement(self):
        data = snapshot()
        data["compose"]["version"] = "2.10.0"
        result = evaluate_docker_readiness(
            policy([check("min_compose_version", "2.20.0")]),
            data,
        )
        self.assertEqual(result["readiness"], "Not Ready")
        self.assertEqual(result["diagnostics"][0]["requirement"], "min_compose_version")

    def test_compose_requirements_are_not_applicable_before_engine_is_available(self):
        for engine_state in ("Not Installed", "Unavailable", "Unknown"):
            with self.subTest(engine_state=engine_state):
                data = snapshot(
                    engine={
                        "availability": engine_state,
                        "server_version": None,
                        "api_version": None,
                        "minimum_api_version": None,
                    },
                    compose={"installed": False, "version": None, "compatibility": "Unavailable"},
                )
                result = evaluate_docker_readiness(
                    policy([
                        check("docker_compose_required", True),
                        check("min_compose_version", "2.20.0"),
                    ]),
                    data,
                )
                self.assertEqual(result["readiness"], "Ready")
                self.assertEqual(result["diagnostics"], [])

    def test_compose_requirements_are_enforced_when_engine_is_available(self):
        data = snapshot(
            compose={"installed": False, "version": None, "compatibility": "Unavailable"}
        )
        result = evaluate_docker_readiness(
            policy([check("docker_compose_required", True)]), data
        )
        self.assertEqual(result["readiness"], "Unknown")
        self.assertEqual(result["diagnostics"][0]["requirement"], "docker_compose_required")

    def test_missing_storage_evidence_remains_unknown_before_engine_installation(self):
        data = snapshot(
            engine={
                "availability": "Not Installed",
                "server_version": None,
                "api_version": None,
                "minimum_api_version": None,
            }
        )
        data["storage"]["filesystem"]["available_bytes"] = None
        result = evaluate_docker_readiness(
            policy([check("min_available_storage_bytes", 20_000_000_000)]), data
        )
        self.assertEqual(result["readiness"], "Unknown")
        self.assertEqual(result["diagnostics"][0]["requirement"], "min_available_storage_bytes")


if __name__ == "__main__":
    unittest.main()
