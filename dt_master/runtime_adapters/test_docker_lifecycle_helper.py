"""Focused tests for the standalone trusted Docker lifecycle helper."""

import importlib.util
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

HELPER_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "bin"
    / "docker"
    / "docker_lifecycle_helper.py"
)
SPEC = importlib.util.spec_from_file_location("docker_lifecycle_helper_under_test", HELPER_PATH)
helper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(helper)


def request():
    return {
        "schema_version": "1.0.0",
        "operation": "install_engine",
        "operation_id": "OP-0001",
        "target": {"doctype": "Infrastructure Node", "name": "NODE-0001"},
        "plan": {
            "schema_version": "1.1.0",
            "policy_version": "1.1.0",
            "plan_digest": "a" * 64,
            "packages": [
                {"name": name, "version": "5:29.0.0-1~ubuntu.24.04~noble"}
                for name in helper.ENGINE_PACKAGES
            ],
        },
        "expected_state_fingerprint": "b" * 64,
        "operator_impact_approval": False,
    }


def inventory():
    return {
        "packages": [
            {"name": name, "installed_version": None}
            for name in helper.ENGINE_PACKAGES
        ]
    }


class TestDockerLifecycleHelper(unittest.TestCase):
    def test_root_side_policy_artifacts_match_authoritative_sources(self):
        root = Path(__file__).resolve().parents[1]
        self.assertEqual(
            (root / "node_preparation" / "docker_install.py").read_bytes(),
            (root / "scripts" / "bin" / "docker" / "docker_install_policy.py").read_bytes(),
        )
        self.assertEqual(
            (root / "scripts" / "bin" / "docker" / "inspect_docker_host.py").read_bytes(),
            (root / "scripts" / "bin" / "docker" / "docker_install_inventory.py").read_bytes(),
        )

    def test_signing_key_inventory_rejects_expired_primary_and_revoked_subkey(self):
        inventory_path = HELPER_PATH.with_name("docker_install_inventory.py")
        inventory_spec = importlib.util.spec_from_file_location("docker_install_inventory_key_test", inventory_path)
        inventory_module = importlib.util.module_from_spec(inventory_spec)
        inventory_spec.loader.exec_module(inventory_module)
        primary = "A" * 40
        subkey = "B" * 40
        valid = "\\n".join(
            [
                "pub:-:4096:1:KEYID:0:0:::::",
                f"fpr:::::::::{primary}:",
                "uid:::::::::Docker Release (CE deb) <docker@docker.com>:",
                "sub:-:4096:1:SUBKEY:0:0:::::",
                f"fpr:::::::::{subkey}:",
            ]
        )
        fingerprints, keys = inventory_module._parse_gpg_key_inventory(valid)
        self.assertEqual(fingerprints, [primary, subkey])
        self.assertEqual(keys[0]["fingerprint"], primary)
        self.assertEqual(keys[0]["subkey_fingerprints"], [subkey])

        expired = f"pub:e:4096:1:KEYID:0:0:::::\\nfpr:::::::::{primary}:"
        _fingerprints, expired_keys = inventory_module._parse_gpg_key_inventory(expired)
        self.assertEqual(expired_keys, [])

        revoked_subkey = "\\n".join(
            [
                "pub:-:4096:1:KEYID:0:0:::::",
                f"fpr:::::::::{primary}:",
                "uid:::::::::Docker Release (CE deb) <docker@docker.com>:",
                "sub:r:4096:1:SUBKEY:0:0:::::",
                f"fpr:::::::::{subkey}:",
            ]
        )
        _fingerprints, revoked_keys = inventory_module._parse_gpg_key_inventory(revoked_subkey)
        self.assertIsNone(revoked_keys[0]["fingerprint"])

    def test_root_inventory_runs_workload_queries_as_ssh_identity(self):
        inventory_path = HELPER_PATH.with_name("docker_install_inventory.py")
        inventory_spec = importlib.util.spec_from_file_location("docker_install_inventory_under_test", inventory_path)
        inventory_module = importlib.util.module_from_spec(inventory_spec)
        inventory_spec.loader.exec_module(inventory_module)
        with (
            patch.dict(helper.os.environ, {"DT_DOCKER_WORKLOAD_USER": "deploy"}),
            patch.object(inventory_module.shutil, "which", return_value="/usr/sbin/runuser"),
            patch.object(inventory_module, "_run", return_value=(0, "")) as run,
        ):
            inventory_module._docker_query(["docker", "ps", "--quiet"])
        run.assert_called_once_with(
            ["runuser", "-u", "deploy", "--", "docker", "ps", "--quiet"],
            timeout=8,
        )

    def test_workload_observation_uses_validated_sudo_identity(self):
        account = SimpleNamespace(pw_uid=1001)
        with (
            patch.object(helper.pwd, "getpwnam", return_value=account),
            patch.dict(helper.os.environ, {}, clear=True),
        ):
            helper._configure_workload_identity("deploy")
            self.assertEqual(__import__("os").environ["DT_DOCKER_WORKLOAD_USER"], "deploy")

    def test_workload_observation_rejects_root_or_unknown_identity(self):
        with patch.object(helper.pwd, "getpwnam", return_value=SimpleNamespace(pw_uid=0)):
            with self.assertRaisesRegex(helper.HelperError, "SUDO_IDENTITY_INVALID"):
                helper._configure_workload_identity("root")
        with patch.object(helper.pwd, "getpwnam", side_effect=KeyError):
            with self.assertRaisesRegex(helper.HelperError, "SUDO_IDENTITY_UNKNOWN"):
                helper._configure_workload_identity("missing")

    def test_self_test_request_is_preserved(self):
        self.assertEqual(
            helper._request(b'{"schema_version":"1.0.0","operation":"self_test"}')["operation"],
            "self_test",
        )

    def test_install_request_rejects_unknown_fields_and_duplicate_keys(self):
        value = request()
        value["command"] = "apt install docker"
        import json
        with self.assertRaises(helper.HelperError):
            helper._request(json.dumps(value).encode())
        with self.assertRaises(helper.HelperError):
            helper._request(
                b'{"schema_version":"1.0.0","operation":"self_test","operation":"install_engine"}'
            )

    def test_install_request_rejects_bad_package_order_and_boolean_coercion(self):
        import json
        value = request()
        value["plan"]["packages"].reverse()
        with self.assertRaises(helper.HelperError):
            helper._request(json.dumps(value).encode())
        value = request()
        value["operator_impact_approval"] = 1
        with self.assertRaises(helper.HelperError):
            helper._request(json.dumps(value).encode())

    def test_request_size_limit_is_enforced(self):
        with self.assertRaises(helper.HelperError):
            helper._request(b" " * (helper.MAX_REQUEST_BYTES + 1))

    def test_apt_simulation_uses_fixed_exact_versions_and_allowlist(self):
        output = "\n".join(
            f"Inst {name} (5:29.0.0-1~ubuntu.24.04~noble Docker:stable [amd64])"
            for name in helper.ENGINE_PACKAGES
        )
        with patch.object(helper, "_run", return_value=(0, output)) as run:
            helper._simulate_exact_install(
                ["5:29.0.0-1~ubuntu.24.04~noble"] * 4
            )
        argv = run.call_args.args[0]
        self.assertEqual(argv[0], "/usr/bin/apt-get")
        self.assertIn("--no-upgrade", argv)
        self.assertIn("--no-remove", argv)
        self.assertEqual(
            argv[-4:],
            [
                f"{name}=5:29.0.0-1~ubuntu.24.04~noble"
                for name in helper.ENGINE_PACKAGES
            ],
        )

    def test_apt_simulation_rejects_removals_and_unapproved_installs(self):
        versions = ["5:29.0.0-1~ubuntu.24.04~noble"] * 4
        with patch.object(
            helper, "_run", return_value=(0, "Remv docker.io [24.0]\n")
        ):
            with self.assertRaisesRegex(helper.HelperError, "APT_SIMULATION_OUT_OF_POLICY"):
                helper._simulate_exact_install(versions)
        output = "\n".join(
            [f"Inst {name} ({versions[0]} Docker:stable [amd64])" for name in helper.ENGINE_PACKAGES]
            + ["Inst libc6 (2.39 Ubuntu:24.04 [amd64])"]
        )
        with patch.object(helper, "_run", return_value=(0, output)):
            with self.assertRaisesRegex(helper.HelperError, "APT_SIMULATION_OUT_OF_POLICY"):
                helper._simulate_exact_install(versions)

    def test_apt_simulation_rejects_missing_or_changed_package_versions(self):
        versions = ["5:29.0.0-1~ubuntu.24.04~noble"] * 4
        with patch.object(helper, "_run", return_value=(0, "Inst docker-ce (5:29.0.0-1~ubuntu.24.04~noble)")):
            with self.assertRaisesRegex(helper.HelperError, "APT_SIMULATION_PACKAGE_SET_MISMATCH"):
                helper._simulate_exact_install(versions)
        output = "\n".join(
            f"Inst {name} (5:28.0.0-1~ubuntu.24.04~noble Docker:stable [amd64])"
            for name in helper.ENGINE_PACKAGES
        )
        with patch.object(helper, "_run", return_value=(0, output)):
            with self.assertRaisesRegex(helper.HelperError, "APT_SIMULATION_VERSION_MISMATCH"):
                helper._simulate_exact_install(versions)

    def test_apt_simulation_rejects_upgrade_even_for_an_allowed_package(self):
        versions = ["5:29.0.0-1~ubuntu.24.04~noble"] * 4
        output = "\\n".join(
            f"Inst {name} [5:28.0.0-1~ubuntu.24.04~noble] ({version} Docker:stable [amd64])"
            for name, version in zip(helper.ENGINE_PACKAGES, versions, strict=True)
        )
        with patch.object(helper, "_run", return_value=(0, output)):
            with self.assertRaisesRegex(helper.HelperError, "APT_SIMULATION_UPGRADE_DETECTED"):
                helper._simulate_exact_install(versions)

    def test_state_change_after_simulation_blocks_install(self):
        value = request()
        plan = {
            "plan_digest": "a" * 64,
            "status": "ready",
            "mutation_authorized": False,
            "target": {"doctype": "Infrastructure Node", "name": "NODE-0001"},
            "workload_impact": {"operator_impact_approval_required": False},
            "packages": [
                {"name": name, "action": "install", "candidate_version": row["version"]}
                for name, row in zip(helper.ENGINE_PACKAGES, value["plan"]["packages"], strict=True)
            ],
        }
        fake_policy = SimpleNamespace(
            build_docker_engine_install_plan=lambda _name, _inventory: plan,
            validate_docker_engine_install_plan=lambda _plan: None,
            docker_engine_install_state_fingerprint=Mock(side_effect=["b" * 64, "c" * 64]),
        )
        fake_inventory = SimpleNamespace(
            collect=lambda: {"docker_install_inventory": inventory()}
        )
        with (
            patch.object(helper, "_load_policy", return_value=(fake_inventory, fake_policy)),
            patch.object(helper, "_simulate_exact_install") as simulate,
        ):
            with self.assertRaisesRegex(helper.HelperError, "STATE_CHANGED_AFTER_SIMULATION"):
                helper._install(value)
        simulate.assert_called_once()

    def test_closed_mutation_gate_returns_pre_mutation_failure(self):
        value = request()
        plan = {
            "plan_digest": "a" * 64,
            "status": "ready",
            "mutation_authorized": False,
            "target": {"doctype": "Infrastructure Node", "name": "NODE-0001"},
            "workload_impact": {"operator_impact_approval_required": False},
            "packages": [
                {"name": name, "action": "install", "candidate_version": row["version"]}
                for name, row in zip(helper.ENGINE_PACKAGES, value["plan"]["packages"], strict=True)
            ],
        }
        fake_policy = SimpleNamespace(
            build_docker_engine_install_plan=lambda _name, _inventory: plan,
            validate_docker_engine_install_plan=lambda _plan: None,
            docker_engine_install_state_fingerprint=lambda _inventory: "b" * 64,
        )
        fake_inventory = SimpleNamespace(collect=lambda: {"docker_install_inventory": inventory()})
        with (
            patch.object(helper, "_load_policy", return_value=(fake_inventory, fake_policy)),
            patch.object(helper, "_simulate_exact_install") as simulate,
            patch.object(helper, "INSTALL_MUTATION_ENABLED", False),
        ):
            result = helper._install(value)
        simulate.assert_called_once()
        self.assertEqual(result["status"], "failed_before_mutation")
        self.assertEqual(result["diagnostics"], ["INSTALL_EXECUTION_DISABLED"])
        self.assertEqual(result["daemon_action"], "not_requested")
        self.assertEqual(result["before_state_fingerprint"], "b" * 64)
        self.assertEqual(result["after_state_fingerprint"], "b" * 64)

    def test_running_workload_requires_explicit_operator_approval(self):
        value = request()
        plan = {
            "plan_digest": "a" * 64,
            "status": "ready",
            "mutation_authorized": False,
            "target": {"doctype": "Infrastructure Node", "name": "NODE-0001"},
            "workload_impact": {"operator_impact_approval_required": True},
            "packages": [
                {"name": name, "action": "install", "candidate_version": row["version"]}
                for name, row in zip(helper.ENGINE_PACKAGES, value["plan"]["packages"], strict=True)
            ],
        }
        fake_policy = SimpleNamespace(
            build_docker_engine_install_plan=lambda _name, _inventory: plan,
            validate_docker_engine_install_plan=lambda _plan: None,
            docker_engine_install_state_fingerprint=lambda _inventory: "b" * 64,
        )
        fake_inventory = SimpleNamespace(collect=lambda: {"docker_install_inventory": inventory()})
        with (
            patch.object(helper, "_load_policy", return_value=(fake_inventory, fake_policy)),
            patch.object(helper, "_simulate_exact_install") as simulate,
        ):
            with self.assertRaisesRegex(helper.HelperError, "WORKLOAD_IMPACT_APPROVAL_MISMATCH"):
                helper._install(value)
        simulate.assert_not_called()

    def test_explicit_workload_approval_passes_the_helper_gate(self):
        value = request()
        value["operator_impact_approval"] = True
        plan = {
            "plan_digest": "a" * 64,
            "status": "ready",
            "mutation_authorized": False,
            "target": {"doctype": "Infrastructure Node", "name": "NODE-0001"},
            "workload_impact": {"operator_impact_approval_required": True},
            "packages": [
                {"name": name, "action": "install", "candidate_version": row["version"]}
                for name, row in zip(helper.ENGINE_PACKAGES, value["plan"]["packages"], strict=True)
            ],
        }
        fake_policy = SimpleNamespace(
            build_docker_engine_install_plan=lambda _name, _inventory: plan,
            validate_docker_engine_install_plan=lambda _plan: None,
            docker_engine_install_state_fingerprint=lambda _inventory: "b" * 64,
        )
        fake_inventory = SimpleNamespace(collect=lambda: {"docker_install_inventory": inventory()})
        with (
            patch.object(helper, "_load_policy", return_value=(fake_inventory, fake_policy)),
            patch.object(helper, "_simulate_exact_install") as simulate,
            patch.object(helper, "INSTALL_MUTATION_ENABLED", False),
        ):
            result = helper._install(value)
        simulate.assert_called_once()
        self.assertEqual(result["status"], "failed_before_mutation")
        self.assertEqual(result["diagnostics"], ["INSTALL_EXECUTION_DISABLED"])

    def test_stale_fingerprint_is_rejected_before_apt_simulation(self):
        value = request()
        fake_policy = SimpleNamespace(
            build_docker_engine_install_plan=lambda _name, _inventory: {},
            validate_docker_engine_install_plan=lambda _plan: None,
            docker_engine_install_state_fingerprint=lambda _inventory: "c" * 64,
        )
        fake_inventory = SimpleNamespace(collect=lambda: {"docker_install_inventory": inventory()})
        with (
            patch.object(helper, "_load_policy", return_value=(fake_inventory, fake_policy)),
            patch.object(helper, "_simulate_exact_install") as simulate,
        ):
            with self.assertRaisesRegex(helper.HelperError, "STATE_FINGERPRINT_MISMATCH"):
                helper._install(value)
        simulate.assert_not_called()

    def test_result_is_correlated_and_reports_unattempted_packages(self):
        value = request()
        result = helper._result(
            value,
            status="failed_before_mutation",
            phase="state_validation",
            before="b" * 64,
            after="b" * 64,
            installed={},
            exit_code=0,
            daemon_action="not_requested",
            diagnostics=["INSTALL_EXECUTION_DISABLED"],
        )
        self.assertEqual(result["operation_id"], "OP-0001")
        self.assertEqual(len(result["packages"]), 4)
        self.assertTrue(all(row["status"] == "not_attempted" for row in result["packages"]))
        self.assertTrue(all(row["installed_version"] is None for row in result["packages"]))


if __name__ == "__main__":
    unittest.main()
