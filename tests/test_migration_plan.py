"""Local-only safety checks for production migration discovery/documentation."""

import importlib.util
import hashlib
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "docs/migrations/same-vm-docker"


def load_audit():
    spec = importlib.util.spec_from_file_location("migration_audit", PLAN / "audit.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_tool(name):
    spec = importlib.util.spec_from_file_location("migration_" + name, PLAN / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MigrationPlanTests(unittest.TestCase):
    def test_foundation_import_does_not_run_host_commands(self):
        with patch("subprocess.run", side_effect=AssertionError("command during import")):
            load_tool("rootless_foundation")

    def test_base_image_helper_import_does_not_pull_images(self):
        with patch("subprocess.run", side_effect=AssertionError("image pull during import")):
            module = load_tool("prepare_base_image")
        compile("REQUEST = {}\n" + module.REMOTE, "remote_inspect", "exec")

    def test_native_runtime_probe_uses_copies_not_production_mounts(self):
        with patch("subprocess.run", side_effect=AssertionError("probe during import")):
            module = load_tool("probe_native_runtime")
        compile(module.REMOTE, "runtime_probe", "exec")
        self.assertIn("/opt/ndf-rootless/native-runtime-probe-v1", module.REMOTE)
        self.assertIn("--network','none", module.REMOTE)
        self.assertIn("--pull','never", module.REMOTE)
        self.assertNotIn("src=/home/frappe", module.REMOTE)

    def test_exact_runtime_draft_has_no_acquisition_or_service_startup(self):
        recipe = (PLAN / "exact-runtime/Dockerfile").read_text()
        instructions = [line.strip() for line in recipe.splitlines() if line.strip() and not line.startswith("#")]
        self.assertTrue(instructions[0].startswith("FROM frappe/erpnext@sha256:"))
        self.assertFalse(any(line.startswith(("RUN ", "ADD ")) for line in instructions))
        self.assertIn("WORKDIR /home/frappe/bench", instructions)
        self.assertIn('CMD ["/home/frappe/bench/env/bin/python", "--version"]', instructions)
        self.assertNotIn("# syntax=", recipe)
        self.assertNotIn("ENV LD_LIBRARY_PATH", recipe)
        self.assertNotIn("sites/", recipe)

    def test_build_tool_metadata_is_pinned_and_separately_budgeted(self):
        data = json.loads((PLAN / "build-tool.json").read_text())
        self.assertEqual(data["name"], "docker-buildx-plugin")
        self.assertEqual(data["bytes"], 17800928)
        self.assertLess(data["bytes"], data["proposed_stage_download_cap_bytes"])
        self.assertRegex(data["sha256"], r"^[a-f0-9]{64}$")
        self.assertEqual(data["signing_fingerprint"], load_tool("repository_metadata").SIGNER)

    def test_native_library_override_is_command_scoped(self):
        wrapper = (PLAN / "exact-runtime/ndf-native-exec").read_text()
        self.assertIn("export LD_LIBRARY_PATH=/opt/ndf-native/lib", wrapper)
        self.assertIn('exec "$@"', wrapper)
        self.assertIn("/home/frappe/.nvm/versions/node/v24.14.0/bin", wrapper)

    def test_exact_runtime_builder_import_is_inert(self):
        with patch("subprocess.run", side_effect=AssertionError("build during import")):
            load_tool("build_exact_runtime")
            load_tool("exact_runtime_remote")

    def test_exact_runtime_filter_excludes_credentials_not_app_modules(self):
        module = load_tool("exact_runtime_remote")
        for name in (".git", ".env", ".env.production", ".npmrc", ".netrc", "direct_url.json", "id_rsa", "site_config.json", "backup.sql.gz", "private.pem"):
            self.assertTrue(module.excluded(name), name)
        for name in ("hooks.py", "package.json", "frappe", "fixtures", "node_modules"):
            self.assertFalse(module.excluded(name), name)

    def test_exact_runtime_manifest_never_follows_symlinks(self):
        module = load_tool("exact_runtime_remote")
        with tempfile.TemporaryDirectory(dir="/tmp/opencode") as temporary:
            root = Path(temporary)
            (root / "link").symlink_to("/unapproved/private/path")
            rows = module.manifest(root)
        self.assertEqual(rows["link"]["type"], "symlink")
        self.assertNotIn("sha256", rows["link"])

    def test_exact_runtime_secret_scan_flags_private_key_markers(self):
        module = load_tool("exact_runtime_remote")
        pattern = dict(module.SECRET_PATTERNS)["private-key"]
        self.assertIsNotNone(pattern.search(b"-----BEGIN OPENSSH PRIVATE KEY-----"))
        self.assertIsNone(pattern.search(b"-----BEGIN PUBLIC KEY-----"))

    def test_native_app_link_maps_to_snapshot_without_live_dereference(self):
        module = load_tool("exact_runtime_remote")
        with tempfile.TemporaryDirectory(dir="/tmp/opencode") as temporary:
            root = Path(temporary)
            snapshot, context = root / "snapshot", root / "context"
            for directory in (snapshot / "app/public", snapshot / "app/node_modules", context / "apps/app/public", context / "apps/app/node_modules"):
                directory.mkdir(parents=True, exist_ok=True)
            source = snapshot / "app/public/node_modules"
            source.symlink_to("/home/frappe/bench/apps/app/node_modules")
            target, reason = module.staged_link(source, context / "apps/app/public/node_modules", [(snapshot, context / "apps")], root)
            self.assertEqual(target, "../node_modules")
            self.assertEqual(reason, "native-app-to-snapshot")

    def test_unreviewed_external_link_is_still_rejected(self):
        module = load_tool("exact_runtime_remote")
        with tempfile.TemporaryDirectory(dir="/tmp/opencode") as temporary:
            root = Path(temporary)
            source = root / "bad-link"
            source.symlink_to("/etc/shadow")
            with self.assertRaisesRegex(RuntimeError, "Escaping"):
                module.staged_link(source, root / "destination", [(root, root / "context")], root)

    def test_python_support_links_require_private_copy(self):
        module = load_tool("exact_runtime_remote")
        with tempfile.TemporaryDirectory(dir="/tmp/opencode") as temporary:
            root = Path(temporary)
            probe = root / "probe"
            (probe / "stdlib").mkdir(parents=True)
            link = probe / "stdlib/sitecustomize.py"
            link.symlink_to("/etc/python3.14/sitecustomize.py")
            with patch.object(module, "PROBE", probe):
                with self.assertRaisesRegex(RuntimeError, "Missing private"):
                    module.staged_link(link, root / "destination", [], root)
                (root / "sitecustomize.py").write_text("")
                target, reason = module.staged_link(link, root / "destination", [], root)
            self.assertEqual(target, "/etc/python3.14/sitecustomize.py")
            self.assertEqual(reason, "private-python-support")

    def test_all_reviewed_interpreter_aliases_map_to_exact_python(self):
        module = load_tool("exact_runtime_remote")
        with tempfile.TemporaryDirectory(dir="/tmp/opencode") as temporary:
            root = Path(temporary)
            (root / "bench-env/bin").mkdir(parents=True)
            link = root / "bench-env/bin/𝜋thon"
            link.symlink_to("/usr/bin/python3.14")
            with patch.object(module, "PROBE", root):
                target, reason = module.staged_link(link, root / "destination", [], root)
            self.assertEqual(target, "/usr/bin/python3.14")
            self.assertEqual(reason, "native-interpreter-alias")

    def test_libpython_link_uses_private_compatibility_library(self):
        module = load_tool("exact_runtime_remote")
        with tempfile.TemporaryDirectory(dir="/tmp/opencode") as temporary:
            root = Path(temporary)
            probe = root / "probe"
            parent = probe / "stdlib/config-3.14-x86_64-linux-gnu"
            parent.mkdir(parents=True)
            link = parent / "libpython3.14.so"
            link.symlink_to("../../x86_64-linux-gnu/libpython3.14.so.1")
            (root / "libpython3.14.so.1.0").write_bytes(b"fixture library")
            with patch.object(module, "PROBE", probe):
                target, reason = module.staged_link(link, root / "destination", [], root)
            self.assertEqual(target, "/opt/ndf-native/lib/libpython3.14.so.1")
            self.assertEqual(reason, "private-python-support")

    def test_native_app_mapping_cannot_escape_snapshot_with_parent_segments(self):
        module = load_tool("exact_runtime_remote")
        with tempfile.TemporaryDirectory(dir="/tmp/opencode") as temporary:
            root = Path(temporary)
            snapshot = root / "snapshot"
            snapshot.mkdir()
            link = snapshot / "bad-link"
            link.symlink_to("/home/frappe/bench/apps/../../sites/site_config.json")
            with self.assertRaisesRegex(RuntimeError, "Escaping"):
                module.staged_link(link, root / "destination", [(snapshot, root / "context")], root)

    def test_filtered_copy_preserves_source_and_omits_credential_files(self):
        module = load_tool("exact_runtime_remote")
        build = module.EvaluationBuild.__new__(module.EvaluationBuild)
        build.guard = lambda **kwargs: None
        with tempfile.TemporaryDirectory(dir="/tmp/opencode") as temporary:
            root = Path(temporary)
            source, destination = root / "source", root / "destination"
            source.mkdir()
            (source / "hooks.py").write_text("# uncommitted source fixture\n")
            (source / ".env").write_text("credential fixture, never copied\n")
            (source / "tool").write_text("#!/bin/sh\nexit 0\n")
            (source / "tool").chmod(0o750)
            before = module.manifest(source)
            exclusions, links = [], []
            build.copy_filtered(source, destination, exclusions, links)
            self.assertEqual(module.manifest(source), before)
            self.assertFalse((destination / ".env").exists())
            self.assertEqual((destination / "hooks.py").read_text(), (source / "hooks.py").read_text())
            self.assertEqual((destination / "tool").stat().st_mode & 0o777, 0o755)
            self.assertEqual(exclusions, [str(source / ".env")])

    def test_link_to_excluded_context_target_is_rejected(self):
        module = load_tool("exact_runtime_remote")
        with tempfile.TemporaryDirectory(dir="/tmp/opencode") as temporary:
            root = Path(temporary)
            snapshot, context = root / "snapshot", root / "context"
            snapshot.mkdir()
            context.mkdir()
            (snapshot / ".env").write_text("fixture")
            link = snapshot / "alias"
            link.symlink_to(".env")
            with self.assertRaisesRegex(RuntimeError, "excluded/missing"):
                module.staged_link(link, context / "alias", [(snapshot, context)], root)

    def test_operator_hold_blocks_build_before_any_host_action(self):
        module = load_tool("exact_runtime_remote")
        build = module.EvaluationBuild.__new__(module.EvaluationBuild)
        build.request = {"prepare_only": False}
        with tempfile.TemporaryDirectory(dir="/tmp/opencode") as temporary:
            work = Path(temporary)
            (work / "hold-before-build").write_text("wait for operator")
            with patch.object(module, "WORK", work), patch.object(build, "health", side_effect=AssertionError("host action after hold")):
                with self.assertRaisesRegex(RuntimeError, "Operator hold"):
                    build.build()

    def test_prepare_only_never_schedules_build_or_validation(self):
        module = load_tool("exact_runtime_remote")
        build = module.EvaluationBuild.__new__(module.EvaluationBuild)
        build.request = {"prepare_only": True}
        build.state = {"steps": [], "download_bytes": 0}
        build.health = lambda: None
        build.save = lambda: None
        build.guard = lambda: None
        with patch.object(build, "plugin"), patch.object(build, "snapshot"), patch.object(build, "context"), patch.object(build, "build", side_effect=AssertionError("build forbidden")), patch.object(build, "validate", side_effect=AssertionError("validation forbidden")), patch("sys.stdout", io.StringIO()):
            build.apply()
        self.assertTrue(build.state["prepared"])
        self.assertFalse(build.state["complete"])
        self.assertEqual(build.state["steps"], ["plugin", "snapshot", "context"])

    def test_review_accepts_only_exact_content_path_and_indicator(self):
        module = load_tool("exact_runtime_remote")
        actual = {"library.py": {"sha256": "fixture-hash"}}
        report = [{"path": "library.py", "sha256": "fixture-hash", "aws_hits": 0}]
        module.verify_review([{"path": "library.py", "indicator": "private-key"}], actual, report, 1)
        with self.assertRaisesRegex(RuntimeError, "content changed"):
            module.verify_review([], {"library.py": {"sha256": "changed"}}, report, 1)
        with self.assertRaisesRegex(RuntimeError, "New, missing or changed"):
            module.verify_review([{"path": "library.py", "indicator": "github-token"}], actual, report, 1)

    def test_review_does_not_allow_new_file_or_missing_finding(self):
        module = load_tool("exact_runtime_remote")
        report = [{"path": "library.py", "sha256": "fixture-hash", "aws_hits": 0}]
        actual = {"library.py": {"sha256": "fixture-hash"}}
        for findings in ([], [{"path": "new.py", "indicator": "private-key"}]):
            with self.assertRaisesRegex(RuntimeError, "New, missing or changed"):
                module.verify_review(findings, actual, report, 1)

    def test_review_rejects_duplicate_and_escaping_paths(self):
        module = load_tool("exact_runtime_remote")
        row = {"path": "library.py", "sha256": "fixture-hash", "aws_hits": 0}
        with self.assertRaisesRegex(RuntimeError, "uniqueness"):
            module.verify_review([], {}, [row, row], 2)
        row["path"] = "../private-file"
        with self.assertRaisesRegex(RuntimeError, "Unsafe"):
            module.verify_review([], {}, [row], 1)

    def test_inspected_manifest_hashes_and_flags_but_never_follows_links(self):
        module = load_tool("exact_runtime_remote")
        with tempfile.TemporaryDirectory(dir="/tmp/opencode") as temporary:
            root = Path(temporary)
            data = b"format delimiter fixture: -----BEGIN PRIVATE KEY-----"
            (root / "library.py").write_bytes(data)
            (root / "link").symlink_to("/unapproved/private/path")
            rows, findings = module.inspected_manifest(root)
        self.assertEqual(rows["library.py"]["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(rows["link"]["type"], "symlink")
        self.assertEqual(findings, [{"path": "library.py", "indicator": "private-key"}])

    def test_credential_review_pins_report_not_directory_wildcards(self):
        decision = json.loads((PLAN / "credential-review.json").read_text())
        self.assertEqual(decision["reviewed_file_count"], 51)
        self.assertEqual(decision["installed_record_matches"], 44)
        self.assertRegex(decision["report_sha256"], r"^[a-f0-9]{64}$")
        self.assertNotIn("*", decision["report"])

    def test_build_go_ahead_archives_hold_without_deleting_evidence(self):
        module = load_tool("exact_runtime_remote")
        build = module.EvaluationBuild.__new__(module.EvaluationBuild)
        build.request = {"approve_build": True}
        build.state = {"prepared": True, "steps": ["context"]}
        build.save = lambda: None
        with tempfile.TemporaryDirectory(dir="/tmp/opencode") as temporary:
            work = Path(temporary)
            (work / "hold-before-build").write_text("original operator hold")
            (work / "credential-review-acceptance-v2.json").write_text(json.dumps({"context_matches_inputs": True, "unresolved_findings": 0}))
            (work / "filtered-manifest.json").write_text("{}")
            with patch.object(module, "WORK", work), patch("sys.stdout", io.StringIO()):
                build.approve_build()
            archives = list(work.glob("hold-before-build.released-*"))
            self.assertEqual(len(archives), 1)
            self.assertEqual(archives[0].read_text(), "original operator hold")
            self.assertFalse((work / "hold-before-build").exists())
            self.assertFalse(build.state["build_approval"]["customer_data_mount_approved"])

    def test_build_approval_requires_explicit_go_ahead_and_passed_preparation(self):
        module = load_tool("exact_runtime_remote")
        build = module.EvaluationBuild.__new__(module.EvaluationBuild)
        build.request = {}
        with self.assertRaisesRegex(RuntimeError, "Explicit operator"):
            build.approve_build()
        build.request = {"approve_build": True}
        build.state = {"prepared": False, "steps": []}
        with self.assertRaisesRegex(RuntimeError, "Preparation must pass"):
            build.approve_build()

    def test_image_validation_checks_commands_inventory_and_import_paths(self):
        module = load_tool("exact_runtime_remote")
        expected = json.loads((PLAN / "source-manifest.json").read_text())["runtime"]
        commands = {"node": {"code": 0, "version": "v24.14.0"}, "yarn": {"code": 0, "version": "1.22.22"},
                    "bench": {"code": 0, "version": "fixture-bench"}, "mariadb": {"code": 0, "version": "10.11.14-MariaDB"},
                    "mariadb-dump": {"code": 0, "version": "10.11.14-MariaDB"}}
        result = {"python": "3.14.3", "openssl": "OpenSSL 3.0.13 fixture", "libpython": "3.14.3 fixture",
                  "commands": commands, "pilot_apps": ["frappe", "erpnext"], "packages": {"fixture": "1"},
                  "imports": {app: {"ok": True, "file": f"/home/frappe/bench/apps/{app}/{app}/__init__.py", "version": expected[app]}
                              for app in ("frappe", "erpnext")}}
        module.assert_validation(result, expected, {"fixture": "1"}, "fixture-bench")
        with self.assertRaisesRegex(RuntimeError, "inventory"):
            module.assert_validation(result, expected, {"fixture": "2"}, "fixture-bench")
        commands["yarn"]["code"] = None
        with self.assertRaisesRegex(RuntimeError, "yarn"):
            module.assert_validation(result, expected, {"fixture": "1"}, "fixture-bench")
        commands["yarn"]["code"] = 0
        result["imports"]["frappe"]["file"] = "/home/frappe/frappe-bench/apps/frappe/frappe/__init__.py"
        with self.assertRaisesRegex(RuntimeError, "source import path"):
            module.assert_validation(result, expected, {"fixture": "1"}, "fixture-bench")

    def test_completed_image_recovery_never_rebuilds_or_adopts_other_tag(self):
        module = load_tool("exact_runtime_remote")
        build = module.EvaluationBuild.__new__(module.EvaluationBuild)
        build.request = {}
        build.health = lambda: None
        build.guard = lambda **kwargs: None
        with tempfile.TemporaryDirectory(dir="/tmp/opencode") as temporary:
            work = Path(temporary)
            manifest_path = work / "filtered-manifest.json"
            manifest_path.write_text("{}")
            build.state = {"image": "sha256:our-image", "build_approval": {"context_manifest_sha256": module.digest(manifest_path)}}
            info = [{"Id": "sha256:our-image", "Config": {"Entrypoint": ["/usr/local/bin/ndf-native-exec"]}}]
            with patch.object(module, "WORK", work), patch.object(build, "docker", return_value=json.dumps(info)) as docker, patch("sys.stdout", io.StringIO()):
                build.build()
            docker.assert_called_once_with(["image", "inspect", module.TAG])
            info[0]["Id"] = "sha256:somebody-elses-image"
            with patch.object(module, "WORK", work), patch.object(build, "docker", return_value=json.dumps(info)):
                with self.assertRaisesRegex(RuntimeError, "no longer matches"):
                    build.build()

    def test_progress_broken_pipe_is_not_a_build_failure(self):
        module = load_tool("exact_runtime_remote")
        build = module.EvaluationBuild.__new__(module.EvaluationBuild)
        build.log = io.StringIO()
        broken = unittest.mock.Mock()
        broken.write.side_effect = BrokenPipeError("SSH disconnected")
        sink = io.StringIO()
        with patch("sys.stdout", broken), patch("builtins.open", return_value=sink):
            build.progress("Image created")
        self.assertIn("Image created", build.log.getvalue())

    def test_retained_image_must_match_export_log_base_and_configuration(self):
        module = load_tool("exact_runtime_remote")
        image_id = "sha256:" + "c" * 64
        image = {"Id": image_id, "RootFS": {"Layers": ["base-layer", "our-layer"]},
                 "Config": {"User": "frappe", "WorkingDir": "/home/frappe/bench",
                            "Entrypoint": ["/usr/local/bin/ndf-native-exec"],
                            "Cmd": ["/home/frappe/bench/env/bin/python", "--version"]}}
        base = {"RootFS": {"Layers": ["base-layer"]}}
        log = f"#17 exporting manifest {image_id} 0.0s done\n#17 unpacking to docker.io/{module.TAG} 237.2s done\n#17 CANCELED\n"
        module.verify_retained_export(image, base, image_id, log)
        with self.assertRaisesRegex(RuntimeError, "pinned export"):
            module.verify_retained_export(image, base, "sha256:" + "d" * 64, log)
        with self.assertRaisesRegex(RuntimeError, "export log"):
            module.verify_retained_export(image, base, image_id, "no export proof")
        with self.assertRaisesRegex(RuntimeError, "unpack completion"):
            module.verify_retained_export(image, base, image_id, f"exporting manifest {image_id}")
        with self.assertRaisesRegex(RuntimeError, "approved cached base"):
            module.verify_retained_export(image, {"RootFS": {"Layers": ["different-base"]}}, image_id, log)

    def test_retained_image_mode_never_schedules_build(self):
        module = load_tool("exact_runtime_remote")
        build = module.EvaluationBuild.__new__(module.EvaluationBuild)
        build.request = {"validate_retained_image": True}
        with patch.object(build, "validate_retained_image") as validate, patch.object(build, "build", side_effect=AssertionError("rebuild forbidden")):
            build.apply()
        validate.assert_called_once()

    def test_retained_image_blocks_default_automatic_rebuild(self):
        module = load_tool("exact_runtime_remote")
        build = module.EvaluationBuild.__new__(module.EvaluationBuild)
        build.request = {}
        build.state = {"retained_image_id": "sha256:retained", "steps": ["context", "retained_image_verified"]}
        with self.assertRaisesRegex(RuntimeError, "do not rebuild automatically"):
            build.apply()

    def test_native_yarn_helpers_are_inert_on_import(self):
        with patch("subprocess.run", side_effect=AssertionError("Yarn build during import")):
            load_tool("add_native_yarn")
            load_tool("native_yarn_remote")

    def test_native_yarn_files_require_exact_hashes_and_no_links(self):
        module = load_tool("native_yarn_remote")
        rows = {"lib": {"type": "directory"}, "lib/cli.js": {"type": "file", "sha256": "fixture"}}
        module.verify_native_files(rows, {"lib/cli.js": "fixture"})
        with self.assertRaisesRegex(RuntimeError, "exact reviewed"):
            module.verify_native_files(rows, {"lib/cli.js": "changed"})
        rows["link"] = {"type": "symlink", "target": "/etc/shadow"}
        with self.assertRaisesRegex(RuntimeError, "symlink/special"):
            module.verify_native_files(rows, {"lib/cli.js": "fixture"})

    def test_yarn_layer_only_adds_pinned_native_package_and_wrapper(self):
        recipe = (PLAN / "yarn-layer/Dockerfile").read_text()
        instructions = [line for line in recipe.splitlines() if line and not line.startswith("#")]
        self.assertTrue(instructions[0].startswith("FROM ndf-local/exact-runtime@sha256:"))
        self.assertFalse(any(line.startswith(("RUN ", "ADD ")) for line in instructions))
        self.assertEqual(sum(line.startswith("COPY ") for line in instructions), 2)
        wrapper = (PLAN / "yarn-layer/ndf-native-exec").read_text()
        self.assertIn("/opt/ndf-native/yarn/bin", wrapper)
        self.assertIn("/home/frappe/.nvm/versions/node/v24.14.0/bin", wrapper)

    def test_yarn_source_metadata_has_small_cap_and_pinned_files(self):
        data = json.loads((PLAN / "native-yarn.json").read_text())
        self.assertEqual(data["source"], "/usr/lib/node_modules/yarn")
        self.assertEqual(data["version"], "1.22.22")
        self.assertEqual(data["added_disk_cap_bytes"], 100_000_000)
        self.assertEqual(len(data["files"]), 11)
        for value in data["files"].values():
            self.assertRegex(value, r"^[a-f0-9]{64}$")

    def test_yarn_local_parent_recipe_has_no_acquisition_and_preserves_layers(self):
        recipe = (PLAN / "yarn-layer/Dockerfile.local-parent").read_text()
        instructions = [line for line in recipe.splitlines() if line and not line.startswith("#")]
        self.assertEqual(instructions[0], "FROM ndf-local/exact-runtime:dalali-v1")
        self.assertFalse(any(line.startswith(("RUN ", "ADD ")) for line in instructions))
        code = (PLAN / "native_yarn_remote.py").read_text()
        self.assertIn('parent_after["Id"] != parent', code)
        self.assertIn('layers[:len(parent_layers)] != parent_layers', code)
        self.assertIn('"--pull=false"', code)

    def test_rehearsal_discovery_is_inert_and_read_only(self):
        with patch("subprocess.run", side_effect=AssertionError("rehearsal during import")):
            module = load_tool("rehearsal_preflight")
            load_tool("rehearsal_image_metadata")
        compile(module.REMOTE, "rehearsal_preflight_remote", "exec")
        self.assertIn("START TRANSACTION READ ONLY", module.REMOTE)
        for forbidden in ("LOCK TABLES", "DROP DATABASE", "mariadb-dump", "'pull'", "'restore'"):
            self.assertNotIn(forbidden, module.REMOTE)

    def test_rehearsal_images_are_pinned_and_separately_budgeted(self):
        data = json.loads((PLAN / "rehearsal-images.json").read_text())
        self.assertEqual(data["platform"], "linux/amd64")
        self.assertEqual(sum(row["compressed_layer_bytes"] for row in data["images"]), data["compressed_layers_total_bytes"])
        self.assertLess(data["compressed_layers_total_bytes"], data["additional_download_cap_bytes"])
        self.assertEqual({row["repository"]: row["version"] for row in data["images"]}, {"mariadb": "10.11.14", "redis": "8.8.0"})
        for row in data["images"]:
            self.assertRegex(row["amd64_digest"], r"^sha256:[a-f0-9]{64}$")

    def test_registry_metadata_reader_caps_response_and_downloads_no_layers(self):
        module = load_tool("rehearsal_image_metadata")
        with patch.object(module, "urlopen", return_value=io.BytesIO(b'{"metadata":true}')):
            data, raw = module.fetch_json("https://example.test/manifest", limit=100)
        self.assertTrue(data["metadata"])
        with patch.object(module, "urlopen", return_value=io.BytesIO(b"x" * 11)):
            with self.assertRaisesRegex(RuntimeError, "Unexpectedly large"):
                module.fetch_json("https://example.test/manifest", limit=10)

    def test_rehearsal_plan_preserves_data_and_cutover_gates(self):
        text = (PLAN / "REHEARSAL_PLAN.md").read_text()
        for required in ("55-second maximum hold", "60 GiB host free", "8 GiB additional disk",
                         "180,000,000 bytes", "No automatic down -v/prune/remove", "No scheduler",
                         "Native Dalali maintenance remains **1**", "full rehearsal acceptance stays open"):
            self.assertIn(required, text)

    def test_rehearsal_execution_helpers_are_inert_on_import(self):
        with patch("subprocess.run", side_effect=AssertionError("rehearsal during import")):
            load_tool("execute_rehearsal")
            module = load_tool("rehearsal_remote")
        self.assertEqual(module.DISK_CAP, 8 * 1024**3)
        self.assertEqual(module.FREE_FLOOR, 60 * 1024**3)
        self.assertEqual(module.DOWNLOAD_CAP, 180_000_000)

    def test_rehearsal_disk_floor_and_incremental_cap_stop_without_cleanup(self):
        module = load_tool("rehearsal_remote")
        rehearsal = module.Rehearsal.__new__(module.Rehearsal)
        rehearsal.state = {"initial_free_bytes": 80 * 1024**3}
        with patch.object(module.shutil, "disk_usage", return_value=SimpleNamespace(free=75 * 1024**3)):
            rehearsal.guard()
        with patch.object(module.shutil, "disk_usage", return_value=SimpleNamespace(free=71 * 1024**3)):
            with self.assertRaisesRegex(RuntimeError, "disk guard"):
                rehearsal.guard()
        with patch.object(module.shutil, "disk_usage", return_value=SimpleNamespace(free=59 * 1024**3)):
            with self.assertRaisesRegex(RuntimeError, "disk guard"):
                rehearsal.guard()

    def test_rehearsal_resource_ownership_never_adopts_unrecognized_storage(self):
        module = load_tool("rehearsal_remote")
        rehearsal = module.Rehearsal.__new__(module.Rehearsal)
        name = module.PROJECT + "-db-data"
        rehearsal.state = {"resources": {"volumes": {name: {"id": name, "created": "original"}}}}
        item = {"Name": name, "CreatedAt": "original", "Labels": {"ndf.rehearsal": module.PROJECT}}
        with patch.object(rehearsal, "inspect_optional", return_value=item):
            self.assertEqual(rehearsal.owned("volume", name), item)
            item["CreatedAt"] = "recreated"
            with self.assertRaisesRegex(RuntimeError, "was replaced"):
                rehearsal.owned("volume", name)
            item["Labels"] = {}
            with self.assertRaisesRegex(RuntimeError, "ownership/identity"):
                rehearsal.owned("volume", name)

    def test_rehearsal_unimplemented_phase_does_not_run_data_operations(self):
        module = load_tool("rehearsal_remote")
        rehearsal = module.Rehearsal.__new__(module.Rehearsal)
        rehearsal.request = {"phase": "not-reviewed"}
        with self.assertRaisesRegex(RuntimeError, "not yet reviewed"):
            rehearsal.apply()

    def test_rehearsal_attachment_manifest_refuses_external_links(self):
        module = load_tool("rehearsal_remote")
        with tempfile.TemporaryDirectory(dir="/tmp/opencode") as temporary:
            root = Path(temporary)
            (root / "attachment").write_bytes(b"fixture file")
            rows = module.file_manifest(root)
            self.assertEqual(rows["attachment"]["sha256"], hashlib.sha256(b"fixture file").hexdigest())
            (root / "bad-link").symlink_to("/unapproved/private/path")
            with self.assertRaisesRegex(RuntimeError, "symlink"):
                module.file_manifest(root)

    def test_rehearsal_source_copy_requires_isolation_and_single_pause(self):
        module = load_tool("rehearsal_remote")
        rehearsal = module.Rehearsal.__new__(module.Rehearsal)
        rehearsal.state = {"steps": []}
        with self.assertRaisesRegex(RuntimeError, "Passed synthetic isolation"):
            rehearsal.snapshot()

    def test_rehearsal_restore_requires_verified_snapshot(self):
        module = load_tool("rehearsal_remote")
        rehearsal = module.Rehearsal.__new__(module.Rehearsal)
        rehearsal.state = {"steps": ["isolation_verified"]}
        with patch.object(rehearsal, "restore_database", side_effect=AssertionError("unapproved restore")):
            with self.assertRaisesRegex(RuntimeError, "Verified source snapshot"):
                rehearsal.restore()

    def test_rehearsal_app_cannot_start_before_verified_restore(self):
        module = load_tool("rehearsal_remote")
        rehearsal = module.Rehearsal.__new__(module.Rehearsal)
        rehearsal.state = {"steps": ["source_snapshot_created"]}
        with self.assertRaisesRegex(RuntimeError, "Verified DB/file restore"):
            rehearsal.application()

    def test_rehearsal_pdf_paths_require_verified_runtime_and_use_copies(self):
        module = load_tool("rehearsal_remote")
        rehearsal = module.Rehearsal.__new__(module.Rehearsal)
        rehearsal.state = {"steps": []}
        with self.assertRaisesRegex(RuntimeError, "Native PDF runtime"):
            rehearsal.pdf_mounts()
        rehearsal.state["steps"] = ["pdf_runtime_verified"]
        mounts = rehearsal.pdf_mounts()
        self.assertTrue(any(str(module.DEPLOY / "pdf-runtime") in value for value in mounts))
        self.assertFalse(any("src=/usr/" in value or "src=/home/frappe/bench" in value for value in mounts))
        self.assertTrue(all(value.endswith(",readonly") for value in mounts if value.startswith("type=bind")))

    def test_rehearsal_pdf_wrapper_scopes_native_libraries_and_fonts(self):
        text = (PLAN / "rehearsal-pdf-wrapper").read_text()
        self.assertIn("QT_QPA_PLATFORM=offscreen", text)
        self.assertIn("FONTCONFIG_FILE=/opt/ndf-pdf/fonts.conf", text)
        self.assertIn('exec /opt/ndf-pdf/bin/wkhtmltopdf "$@"', text)
        self.assertNotIn("/usr/local/bin/wkhtmltopdf", text)

    def test_rehearsal_control_exec_reapplies_native_runtime_environment(self):
        code = (PLAN / "rehearsal_remote.py").read_text()
        self.assertIn('"/usr/local/bin/ndf-native-exec", "/home/frappe/bench/env/bin/python"', code)
        self.assertIn('"--workdir=/home/frappe/bench/sites", control', code)
        self.assertIn('"--chdir=/home/frappe/bench/sites"', code)

    def test_rehearsal_frontend_has_tmpfs_paths_and_http_readiness_gate(self):
        code = (PLAN / "rehearsal_remote.py").read_text()
        for path in ("client_body", "proxy", "fastcgi", "uwsgi", "scgi"):
            self.assertIn("/tmp/" + path, code)
        self.assertIn('if status == "200"', code)
        self.assertIn('"application": ("application_ready", self.application)', code)
        self.assertNotIn("--read-only=false", code)

    def test_rehearsal_rootless_port_api_is_restricted_to_approved_operations(self):
        module = load_tool("rehearsal_remote")
        rehearsal = module.Rehearsal.__new__(module.Rehearsal)
        with self.assertRaisesRegex(RuntimeError, "Unexpected RootlessKit"):
            rehearsal.rootless_port_api("DELETE", "/v1/ports/1")
        with self.assertRaisesRegex(RuntimeError, "Unexpected RootlessKit"):
            rehearsal.rootless_port_api("POST", "/v1/other")

    def test_rehearsal_manual_forward_only_publishes_loopback_without_new_network(self):
        module = load_tool("rehearsal_remote")
        rehearsal = module.Rehearsal.__new__(module.Rehearsal)
        rehearsal.state = {}
        rehearsal.save = lambda: None
        frontend = {"Id": "our-frontend", "State": {"Running": True},
                    "NetworkSettings": {"Networks": {module.NETWORK: {"IPAddress": "172.19.0.8"}}}}
        network = {"Internal": True, "IPAM": {"Config": [{"Subnet": "172.19.0.0/16"}]}}
        spec = {"proto": "tcp", "parentIP": "127.0.0.1", "parentPort": 18088, "childIP": "172.19.0.8", "childPort": 8080}
        with patch.object(rehearsal, "owned", side_effect=[frontend, network]), patch.object(rehearsal, "rootless_port_api", side_effect=[[], {"id": 7, "spec": spec}]) as api, patch.object(rehearsal, "run", side_effect=["", "LISTEN 0 4096 127.0.0.1:18088 0.0.0.0:*\n"]):
            rehearsal.loopback_forward("our-frontend")
        self.assertEqual(rehearsal.state["loopback_forward"]["spec"], spec)
        api.assert_called_with("POST", "/v1/ports", spec)

    def test_rehearsal_http_probe_is_inert_and_rejects_external_paths(self):
        with patch("subprocess.run", side_effect=AssertionError("HTTP probe during import")):
            module = load_tool("rehearsal_http_probe")
        self.assertEqual(module.safe_path("/assets/frappe/dist/test.js"), "/assets/frappe/dist/test.js")
        for path in ("https://example.test/", "//example.test/", "/assets/%2e%2e/site_config.json", "/\\example.test/"):
            with self.assertRaisesRegex(RuntimeError, "Unsafe"):
                module.safe_path(path)

    def test_rehearsal_http_probe_never_follows_external_redirects(self):
        code = (PLAN / "rehearsal_http_probe.py").read_text()
        self.assertIn("value.trust_env = False", code)
        self.assertIn("allow_redirects=False", code)
        self.assertNotIn("allow_redirects=True", code)
        self.assertIn("http://frontend:8080", code)
        self.assertIn('input=json.dumps({"cookie": "sid=" + sid, "transport": transport})', code)
        self.assertIn('for transport in ("polling", "websocket")', code)

    def test_rehearsal_redirects_only_allow_internal_http_paths(self):
        module = load_tool("rehearsal_http_probe")
        self.assertEqual(module.internal_redirect_path("/desk"), "/desk")
        self.assertEqual(module.internal_redirect_path("http://dalali-rehearsal.invalid:8080/desk"), "/desk")
        for target in ("https://dalali.ndftechlabs.com/", "//example.test/", "http://frontend:3306/", "javascript:alert(1)"):
            with self.assertRaises(RuntimeError):
                module.internal_redirect_path(target)

    def test_ui_browser_artifacts_have_explicit_new_budget_and_pins(self):
        data = json.loads((PLAN / "ui-browser.json").read_text())
        self.assertEqual(data["browser"]["bytes"] + sum(p["bytes"] for p in data["support_packages"]), data["total_artifact_bytes"])
        self.assertLess(data["total_artifact_bytes"], data["proposed_additional_download_cap_bytes"])
        self.assertEqual(data["proposed_additional_download_cap_bytes"], 150_000_000)
        self.assertEqual(data["keep_existing_rehearsal_disk_cap_gib"], 8)
        self.assertEqual(data["host_free_floor_gib"], 60)
        self.assertTrue(data["browser"]["object_generation"].isdigit())
        for package in data["support_packages"]:
            self.assertRegex(package["sha256"], r"^[a-f0-9]{64}$")

    def test_ui_plan_preserves_browser_and_final_sync_approval_gates(self):
        text = (PLAN / "UI_ACCEPTANCE_PLAN.md").read_text()
        for required in ("not self-issued business sign-off", "No silent --no-sandbox", "no default route",
                         "no DOM dumps", "not authorization for another production pause", "Unknown\n   writers block final sync",
                         "separate pilot route", "No final synchronization has executed"):
            self.assertIn(required, text)

    def test_ui_seccomp_preserves_default_rules_and_limits_namespace_flags(self):
        module = load_tool("ui_seccomp")
        baseline = {"defaultAction": "SCMP_ACT_ERRNO", "defaultErrnoRet": 1,
                    "syscalls": [{"names": ["clone3"], "action": "SCMP_ACT_ERRNO", "errnoRet": 38}]}
        raw = json.dumps(baseline).encode()
        with patch.object(module.hashlib, "sha256", return_value=SimpleNamespace(hexdigest=lambda: module.BASELINE_SHA256)):
            profile, additions = module.browser_profile(raw)
        self.assertEqual(profile["syscalls"][:len(baseline["syscalls"])], baseline["syscalls"])
        self.assertEqual(profile["defaultAction"], "SCMP_ACT_ERRNO")
        self.assertFalse(any("clone3" in r["names"] for r in additions))
        unshare = next(r for r in additions if r["names"] == ["unshare"])["args"][0]
        for flags in (module.NEWUSER, module.ALLOWED_NAMESPACE_FLAGS):
            self.assertEqual(flags & unshare["value"], 0)
        for flags in (module.NEWUSER | 0x20000, module.NEWUSER | 0x4000000, module.NEWUSER | 0x8000000):
            self.assertNotEqual(flags & unshare["value"], 0)
        self.assertTrue(all(r["includes"]["arches"] == ["amd64"] for r in additions))

    def test_ui_policy_checksum_architecture_and_import_guards(self):
        with patch("subprocess.run", side_effect=AssertionError("UI during import")):
            load_tool("ui_acceptance")
            load_tool("ui_acceptance_remote")
            module = load_tool("ui_seccomp")
        with self.assertRaisesRegex(RuntimeError, "amd64 only"):
            module.browser_profile(b"{}", "aarch64")
        with self.assertRaisesRegex(RuntimeError, "checksum"):
            module.browser_profile(b"{}")

    def test_ui_crc32c_matches_castagnoli_known_vector_and_streaming(self):
        module = load_tool("ui_tools")
        value = module.crc32c_update(0xFFFFFFFF, b"123456789") ^ 0xFFFFFFFF
        self.assertEqual(value, 0xE3069283)
        split = module.crc32c_update(module.crc32c_update(0xFFFFFFFF, b"1234"), b"56789") ^ 0xFFFFFFFF
        self.assertEqual(split, value)

    def test_ui_archive_paths_reject_traversal_and_symlinks(self):
        import stat
        module = load_tool("ui_tools")
        self.assertEqual(str(module.safe_zip_member("chrome-headless-shell-linux64/chrome", stat.S_IFREG)), "chrome-headless-shell-linux64/chrome")
        for path, mode in (("../private", stat.S_IFREG), ("/etc/passwd", stat.S_IFREG), ("dir\\escape", stat.S_IFREG), ("safe-link", stat.S_IFLNK)):
            with self.assertRaisesRegex(RuntimeError, "Unsafe"):
                module.safe_zip_member(path, mode)

    def test_ui_proxy_rejects_external_origins_and_financial_actions(self):
        import shutil
        import subprocess
        node = shutil.which("node")
        if not node:self.skipTest("Node unavailable for proxy policy unit test")
        source = r'''const p=require(process.argv[1]);
const plan={users:['synthetic'],fixture_name:'fixture',fixture_title:'UI fixture'};
for(const url of ['http://example.com/','https://dalali.ndftechlabs.com/','http://127.0.0.1:8081/','http://dalali-rehearsal.invalid:3306/','http://dalali-rehearsal.invalid:8080/assets/%2e%2e/private'])if(p.localURL(url)!==null)throw Error('origin/path escape');
const url=p.localURL('http://dalali-rehearsal.invalid:8080/api/method/frappe.desk.form.save.savedocs');
const req={method:'POST',headers:{'content-type':'application/x-www-form-urlencoded'}};
function body(doctype){return Buffer.from(new URLSearchParams({action:'Save',doc:JSON.stringify({doctype,name:'fixture',title:'UI fixture',content:'UI rehearsal save proof',docstatus:0,public:0})}).toString())}
if(!p.allowed(req,url,body('Note'),plan)||p.allowed(req,url,body('Sales Invoice'),plan))throw Error('action escape');
if(!p.safeSocketPacket('42/dalali-rehearsal.invalid,["doctype_subscribe","Item"]')||p.safeSocketPacket('42/dalali-rehearsal.invalid,["submit_invoice","Sales Invoice"]'))throw Error('socket escape');
for(const [method,args,want] of [
 ['frappe.desk.reportview.get_list',{doctype:'Item'},true],
 ['frappe.desk.reportview.get_list',{doctype:'List Filter'},true],
 ['frappe.desk.reportview.get_list',{doctype:'Unreviewed Virtual'},false],
 ['frappe.client.validate_link_and_fetch',{doctype:'User'},true],
 ['frappe.client.validate_link_and_fetch',{doctype:'User',query:'unreviewed.method'},false],
 ['frappe.desk.doctype.route_history.route_history.deferred_insert',{},false]]){
 const u=p.localURL('http://dalali-rehearsal.invalid:8080/api/method/'+method);
 if(p.allowed(req,u,Buffer.from(new URLSearchParams(args).toString()),plan)!==want)throw Error('read argument escape');
}
for(const method of ['frappe.model.utils.user_settings.save','erpnext.stock.doctype.stock_entry.stock_entry.make_stock_entry',
 'erpnext.accounts.doctype.unreconcile_payment.unreconcile_payment.unreconcile_transactions',
 'erpnext.controllers.taxes_and_totals.get_round_off_applicable_accounts']){
 const u=p.localURL('http://dalali-rehearsal.invalid:8080/api/method/'+method);
 if(p.allowed(req,u,Buffer.from(''),plan))throw Error('unreviewed mutation/dispatch allowed');
}
console.log('policy passed');'''
        result = subprocess.run([node, "-e", source, str(PLAN / "ui_proxy.cjs")], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_ui_runner_keeps_browser_sandbox_and_no_sensitive_artifacts(self):
        code = (PLAN / "ui_browser_runner.cjs").read_text()
        self.assertNotIn("--no-sandbox", code)
        self.assertNotIn("Page.captureScreenshot", code)
        self.assertNotIn("Tracing.start", code)
        self.assertIn('behavior: "deny"', code)
        self.assertIn('"--proxy-bypass-list=<-loopback>"', code)
        self.assertIn("dom_credentials_records_exported: false", code)

    def test_ui_csp_change_is_exact_and_rejects_unknown_policy(self):
        module = load_tool("ui_csp")
        original = ('prefix\nadd_header Content-Security-Policy "' + module.ORIGINAL_CSP + '" always;\nsuffix\n').encode()
        changed = module.approved_config(original)
        self.assertEqual(changed.replace(b" 'unsafe-eval'", b"", 1), original)
        self.assertEqual(changed.count(b"unsafe-eval"), 1)
        for unknown in (b"", changed, original + original, original.replace(b"frame-src 'none'", b"frame-src *")):
            with self.assertRaisesRegex(RuntimeError, "Unexpected clone CSP"):
                module.approved_config(unknown)

    def test_final_sync_audit_is_inert_readonly_and_never_unpickles(self):
        with patch("subprocess.run", side_effect=AssertionError("source mutation during import")):
            module = load_tool("final_sync_audit")
        compile(module.REMOTE, "final_sync_audit", "exec")
        self.assertIn("START TRANSACTION READ ONLY", module.REMOTE)
        self.assertNotIn("pickle.loads", module.REMOTE)
        self.assertNotIn("LOCK TABLES", module.REMOTE)
        self.assertNotIn("--remove", module.REMOTE)

    def test_staging_access_boundary_with_synthetic_keys_only(self):
        import shutil
        import subprocess
        node = shutil.which("node")
        if not node:self.skipTest("Node unavailable for staging identity unit tests")
        result = subprocess.run([node, "--test", "--test-concurrency=1", str(ROOT / "tests/stage_access.test.cjs"),
            str(ROOT / "tests/stage_gateway.test.cjs"), str(ROOT / "tests/stage_reverse.test.cjs")], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_stage_public_keys_are_bounded_and_private_material_rejected(self):
        module = load_tool("stage_jwks")
        key = {"kid": "synthetic", "kty": "RSA", "n": "public-modulus", "e": "AQAB"}
        result = module.public_snapshot(json.dumps({"keys": [key]}).encode(), 1800000000)
        self.assertEqual(result, {"fetched_at": 1800000000, "keys": [key]})
        for keys in ([], [key, key], [{**key, "d": "not-allowed"}], [{**key, "kty": "oct"}], [{**key, "alg": "HS256"}]):
            with self.assertRaises(RuntimeError):
                module.public_snapshot(json.dumps({"keys": keys}).encode(), 1800000000)
        with self.assertRaises(RuntimeError):module.public_snapshot(b" " * 65537, 1800000000)

    def test_staging_preparation_is_inert_and_does_not_start_existing_tunnel(self):
        with patch("subprocess.run", side_effect=AssertionError("staging side effect during import")):
            load_tool("stage_review")
            load_tool("stage_jwks")
            module = load_tool("stage_prepare_remote")
        units = module.units()
        self.assertTrue(all(name.startswith("ndf-dalali-stage-") for name in units))
        for name in ("ndf-dalali-stage-keys.service", "ndf-dalali-stage-tunnel.service"):
            self.assertIn("User=dt-containers", units[name])
            self.assertIn("Slice=user-5001.slice", units[name])
            self.assertIn("MemorySwapMax=0", units[name])
            self.assertIn("ProtectHome=yes", units[name])
        connector = units["ndf-dalali-stage-tunnel.service"]
        self.assertIn("--token-file %d/tunnel-token", connector)
        self.assertIn("--no-autoupdate", connector)
        self.assertIn("ConditionPathExists=/etc/ndf-dalali-stage/connector-approved", connector)
        self.assertNotIn("/etc/cloudflared/config.yml", connector)

    def test_stage_tunnel_token_entry_rejects_shared_or_wrong_tunnel_without_side_effects(self):
        import base64
        with patch("subprocess.run", side_effect=AssertionError("credential side effect during import")):
            module = load_tool("stage_store_token")
        identifier = "12345678-1234-4234-8234-123456789012"
        payload = {"a": "a" * 32, "t": identifier, "s": base64.b64encode(b"x" * 32).decode()}
        token = base64.b64encode(json.dumps(payload).encode()).decode()
        self.assertEqual(module.validate_token(token, identifier, "tunnel: unrelated"), identifier)
        for value, expected, config in ((token, identifier, "tunnel: " + identifier),
            (token, "22345678-1234-4234-8234-123456789012", ""), ("not a token", identifier, "")):
            with self.assertRaisesRegex(RuntimeError, "nothing stored"):
                module.validate_token(value, expected, config)

    def test_stage_token_diagnostics_distinguish_clipboard_and_id_errors_without_token_output(self):
        import base64
        module = load_tool("stage_store_token")
        identifier = "12345678-1234-4234-8234-123456789012"
        payload = {"a": "a" * 32, "t": identifier, "s": base64.b64encode(b"x" * 32).decode()}
        token = base64.b64encode(json.dumps(payload).encode()).decode()
        cases = (("", "empty-or-size"), (" " + token, "clipboard-whitespace"),
            ("'" + token + "'", "clipboard-quotes"), ("cloudflared tunnel run --token " + token, "whole-command"),
            ("\x1b[200~" + token + "\x1b[201~", "terminal-escape"), ("not-valid-token", "encoding"))
        for value, category in cases:
            with self.subTest(category=category), self.assertRaises(module.TokenInputError) as caught:
                module.validate_token(value, identifier, "")
            self.assertIn("[" + category + "]", str(caught.exception))
            self.assertNotIn(token, str(caught.exception))
            self.assertNotIn(payload["s"], str(caught.exception))
        with self.assertRaises(module.TokenInputError) as caught:
            module.validate_token(token, "22345678-1234-4234-8234-123456789012", "")
        self.assertIn("[tunnel-id-mismatch]", str(caught.exception))
        self.assertIn(identifier, str(caught.exception))
        self.assertNotIn(token, str(caught.exception))

    def test_stage_token_diagnostics_preserve_account_encoding_and_minimum_secret_checks(self):
        import base64
        module = load_tool("stage_store_token")
        identifier = "12345678-1234-4234-8234-123456789012"
        original = {"a": "a" * 32, "t": identifier, "s": base64.b64encode(b"x" * 32).decode()}
        for fields, category in (({"a": "wrong-format"}, "account-format"), ({"s": "bad-base64"}, "secret-format"),
            ({"s": base64.b64encode(b"x" * 31).decode()}, "secret-length")):
            value = base64.b64encode(json.dumps({**original, **fields}).encode()).decode()
            with self.assertRaises(module.TokenInputError) as caught:
                module.validate_token(value, identifier, "")
            self.assertIn("[" + category + "]", str(caught.exception))
            self.assertNotIn(value, str(caught.exception))

    def test_stage_tunnel_secret_supports_documented_minimum_not_exact_length(self):
        import base64
        module = load_tool("stage_store_token")
        identifier = "12345678-1234-4234-8234-123456789012"
        for size in (32, 48, 64, 128, 512, 8192):
            with self.subTest(secret_bytes=size):
                payload = {"a": "a" * 32, "t": identifier, "s": base64.b64encode(b"x" * size).decode()}
                value = base64.b64encode(json.dumps(payload).encode()).decode()
                self.assertEqual(module.validate_token(value, identifier, ""), identifier)
                with self.assertRaisesRegex(module.TokenInputError, "protected-tunnel"):
                    module.validate_token(value, identifier, "tunnel: " + identifier)
                with self.assertRaisesRegex(module.TokenInputError, "tunnel-id-mismatch"):
                    module.validate_token(value, "22345678-1234-4234-8234-123456789012", "")
        for size in (0, 1, 16, 31):
            payload = {"a": "a" * 32, "t": identifier, "s": base64.b64encode(b"x" * size).decode()}
            value = base64.b64encode(json.dumps(payload).encode()).decode()
            with self.assertRaisesRegex(module.TokenInputError, "secret-length"):
                module.validate_token(value, identifier, "")
        payload = {"a": "a" * 32, "t": identifier, "s": base64.b64encode(b"x" * 10000).decode()}
        oversized = base64.b64encode(json.dumps(payload).encode()).decode()
        with self.assertRaisesRegex(module.TokenInputError, "empty-or-size"):
            module.validate_token(oversized, identifier, "")

    def test_stage_received_route_requires_single_guarded_hostname_and_access(self):
        import copy
        module = load_tool("stage_route_policy")
        audience = "a" * 64
        source = {"version": 3, "config": {"ingress": [
            {"hostname": "d-stage.ndftechlabs.com", "path": None, "service": "http://127.0.0.1:18089",
             "originRequest": {"access": {"required": True, "teamName": "ndftech", "audTag": [audience]}}},
            {"hostname": "", "path": None, "service": "http_status:404"}], "warp-routing": {"enabled": False}}}
        self.assertTrue(module.validate_config(source, audience)["protect_with_access"])
        self.assertEqual(module.validate_config(source, audience)["service"], "http://127.0.0.1:18089")
        mutations = [
            lambda d: d["config"]["ingress"][0].update(hostname="dalali.ndftechlabs.com"),
            lambda d: d["config"]["ingress"][0].update(service="http://127.0.0.1:18088"),
            lambda d: d["config"]["ingress"][0].update(path="/desk.*"),
            lambda d: d["config"]["ingress"][0]["originRequest"]["access"].update(required=False),
            lambda d: d["config"]["ingress"][0]["originRequest"]["access"].update(teamName="other-team"),
            lambda d: d["config"]["ingress"][0]["originRequest"]["access"].update(audTag=[audience, "b" * 64]),
            lambda d: d["config"]["ingress"][0]["originRequest"]["access"].update(environment="testing"),
            lambda d: d["config"]["ingress"][0]["originRequest"].update(noTLSVerify=True),
            lambda d: d["config"]["ingress"][0]["originRequest"].update(proxyType="socks"),
            lambda d: d["config"]["ingress"][1].update(service="http://127.0.0.1:18088"),
            lambda d: d["config"]["ingress"].append({"hostname": "other.ndftechlabs.com", "service": "http://127.0.0.1:8081"}),
            lambda d: d["config"]["warp-routing"].update(enabled=True),
            lambda d: d.update(version=-1),
            lambda d: d.update(version=True),
        ]
        for mutate in mutations:
            document = copy.deepcopy(source);mutate(document)
            with self.assertRaisesRegex(RuntimeError, "differs from the approved"):
                module.validate_config(document, audience)

    def test_stage_connector_imports_are_inert(self):
        with patch("subprocess.run", side_effect=AssertionError("connector start during import")):
            load_tool("stage_connect")
            load_tool("stage_connect_remote")
            load_tool("stage_route_policy")

    def test_stage_connector_execute_requires_operator_route_confirmation(self):
        import contextlib
        module = load_tool("stage_connect")
        with patch("sys.argv", ["stage_connect.py", "--execute"]), contextlib.redirect_stderr(io.StringIO()), \
                patch("subprocess.run", side_effect=AssertionError("connector started without route confirmation")):
            with self.assertRaises(SystemExit) as caught:module.main()
        self.assertEqual(caught.exception.code, 2)

    def test_stage_guest_review_entrypoint_remains_explicit_and_never_forwards_browser_credentials(self):
        gateway = (PLAN / "stage_gateway.cjs").read_text()
        code = (PLAN / "stage_reverse.cjs").read_text()
        policy = (PLAN / "stage_policy.cjs").read_text()
        self.assertIn('config.origin_enabled === true', gateway)
        self.assertIn('config.review_mode !== "guest-website-v1"', code)
        self.assertIn('config.review_site !== "dalali-rehearsal.invalid"', code)
        self.assertIn('hostname: "frontend", port: 8080', code)
        self.assertNotIn('...request.headers', code)
        self.assertNotIn('savedocs', policy)
        self.assertNotIn('get_dynamic_sections', policy)

    def test_stage_publish_imports_are_inert_and_need_readiness_confirmation(self):
        import contextlib
        with patch("subprocess.run", side_effect=AssertionError("public connection during import")):
            module = load_tool("stage_publish")
            load_tool("stage_publish_remote")
        with patch("sys.argv", ["stage_publish.py", "--execute"]), contextlib.redirect_stderr(io.StringIO()), \
                patch("subprocess.run", side_effect=AssertionError("public connection without readiness confirmation")):
            with self.assertRaises(SystemExit) as caught:module.main()
        self.assertEqual(caught.exception.code, 2)

    def test_stage_publish_has_readonly_guest_probe_and_original_config_rollback(self):
        code = (PLAN / "stage_publish_remote.py").read_text()
        for required in ("allow_reads_during_maintenance=1", "maintenance_mode=1", "read_from_replica=0",
            "UPDATE `tabUser` SET enabled=enabled WHERE 1=0", "frappe.session.user!='Guest'",
            "gateway-config-before-guest.json", "guest_rollback_verified", "Unexpected gateway config drift",
            "new_site_database_or_data_copy", '"--network=none"'):
            self.assertIn(required, code)
        self.assertNotIn("CREATE DATABASE", code)
        self.assertNotIn("DROP DATABASE", code)
        self.assertNotIn("--no-sandbox", code)

    def test_stage_proof_requires_all_checks_across_node_reporters(self):
        module = load_tool("stage_publish_remote")
        lines = ["tests 32", "pass 32", "fail 0", "cancelled 0", "skipped 0"]
        for prefix in ("# ", "ℹ ", "\x1b[34mℹ \x1b[39m"):
            self.assertTrue(module.synthetic_proof_passed("\n".join(prefix + line for line in lines)))
        for output in ("# tests 32\n# fail 0", "\n".join(lines).replace("pass 32", "pass 31"),
            "\n".join(lines).replace("fail 0", "fail 1"), "\n".join(lines).replace("skipped 0", "skipped 1")):
            self.assertFalse(module.synthetic_proof_passed(output))

    def test_stage_loopback_retarget_rejects_unknown_identity_or_broader_binds(self):
        import copy
        module = load_tool("stage_mapping")
        record = {"id": 2, "gateway_id": "same-owned-gateway", "spec": {"proto": "tcp", "parentIP": "127.0.0.1",
            "parentPort": 18089, "childIP": "172.19.0.5", "childPort": 18089}}
        container = {"Id": "same-owned-gateway", "State": {"Running": True},
            "NetworkSettings": {"Networks": {"internal": {"IPAddress": "172.19.0.6"}}}}
        network = {"Name": "internal", "Internal": True, "EnableIPv6": False,
            "IPAM": {"Config": [{"Subnet": "172.19.0.0/24"}]}}
        ports = [{"id": 2, "spec": record["spec"].copy()}]
        target = module.expected_spec(record, container, network, ports)
        self.assertEqual(target, {**record["spec"], "childIP": "172.19.0.6"})
        for kind in ("id", "bind", "port", "container", "network", "extra", "outside"):
            rec, obj, net, rows = copy.deepcopy((record, container, network, ports))
            if kind == "id":rows[0]["id"] = 99
            elif kind == "bind":rec["spec"]["parentIP"] = rows[0]["spec"]["parentIP"] = "0.0.0.0"
            elif kind == "port":rec["spec"]["parentPort"] = rows[0]["spec"]["parentPort"] = 18088
            elif kind == "container":obj["Id"] = "other"
            elif kind == "network":net["Internal"] = False
            elif kind == "extra":rows.append({"id": 3, "spec": record["spec"].copy()})
            elif kind == "outside":obj["NetworkSettings"]["Networks"]["internal"]["IPAddress"] = "10.0.0.1"
            with self.assertRaisesRegex(RuntimeError, "no unknown mapping"):
                module.expected_spec(rec, obj, net, rows)

    def test_stage_loopback_retarget_requires_explicit_permission_before_api_removal(self):
        module = load_tool("stage_mapping")
        record = {"id": 2, "gateway_id": "same", "spec": {"proto": "tcp", "parentIP": "127.0.0.1",
            "parentPort": 18089, "childIP": "172.19.0.5", "childPort": 18089}}
        obj = {"Id": "same", "Image": "synthetic", "State": {"Running": True},
            "NetworkSettings": {"Networks": {"internal": {"IPAddress": "172.19.0.6"}}}}
        net = {"Name": "internal", "Internal": True, "EnableIPv6": False, "IPAM": {"Config": [{"Subnet": "172.19.0.0/24"}]}}
        r = SimpleNamespace(state={"staging": {"loopback_mapping": record}},
            owned=lambda kind,name: obj if kind == "container" else net,
            rootless_port_api=lambda method,path: [{"id":2,"spec":record["spec"]}])
        with self.assertRaisesRegex(RuntimeError, "explicit loopback retarget"):
            module.reconcile(r,{"APP_IMAGE":"synthetic","NETWORK":"internal"})

    def test_clone_recovery_requires_initialized_clone_and_fixture(self):
        module = load_tool("rehearsal_remote")
        rehearsal = module.Rehearsal.__new__(module.Rehearsal)
        rehearsal.state = {"steps": ["restore_verified"]}
        with self.assertRaisesRegex(RuntimeError, "Initialized clone"):
            rehearsal.clone_recovery()

    def test_clone_migration_requires_new_approval_recovery_and_single_attempt(self):
        module = load_tool("rehearsal_remote")
        rehearsal = module.Rehearsal.__new__(module.Rehearsal)
        rehearsal.request = {}
        with self.assertRaisesRegex(RuntimeError, "Separate explicit"):
            rehearsal.clone_migrate()
        rehearsal.request = {"clone_migration_approved": True}
        rehearsal.state = {"steps": [], "recovery_proof": {}}
        with self.assertRaisesRegex(RuntimeError, "Verified private clone recovery"):
            rehearsal.clone_migrate()
        rehearsal.state = {"steps": ["clone_recovery_verified"], "recovery_proof": {"passed": True}, "clone_migration_attempted": True}
        with self.assertRaisesRegex(RuntimeError, "attempt already used"):
            rehearsal.clone_migrate()

    def test_clone_migration_is_fixed_target_without_failure_skipping_or_downloads(self):
        code = (PLAN / "rehearsal_remote.py").read_text()
        self.assertIn('"--site", CLONE_SITE, "migrate"', code)
        self.assertNotIn("--skip-failing", code)
        self.assertNotIn("DROP DATABASE", code)
        self.assertIn("START TRANSACTION READ ONLY", code)
        self.assertIn('"source_schema_modified": False', code)

    def test_full_rehearsal_cannot_pass_only_restore_or_http_readiness(self):
        module = load_tool("rehearsal_remote")
        rehearsal = module.Rehearsal.__new__(module.Rehearsal)
        rehearsal.state = {"steps": ["restore_verified", "application_ready"]}
        with self.assertRaisesRegex(RuntimeError, "must pass first"):
            rehearsal.validate()

    def test_clone_bench_layout_uses_fresh_private_config_not_native_services(self):
        code = (PLAN / "rehearsal_remote.py").read_text()
        self.assertIn('bench_config = DEPLOY / "bench-config"', code)
        self.assertIn('",dst=/home/frappe/bench/config,readonly"', code)
        self.assertNotIn("src=/home/frappe/bench/config", code)
        self.assertIn('"migrate", "--help"', code)

    def test_rehearsal_database_client_rejects_network_exposure_before_sql(self):
        module = load_tool("rehearsal_remote")
        rehearsal = module.Rehearsal.__new__(module.Rehearsal)
        rehearsal.state = {"images": {"mariadb": {"id": "our-db-image"}}}
        item = {"Image": "our-db-image", "HostConfig": {"PortBindings": {"3306/tcp": [{}]}}}
        with patch.object(rehearsal, "owned", return_value=item), patch.object(rehearsal, "docker", side_effect=AssertionError("SQL must not run")):
            with self.assertRaisesRegex(RuntimeError, "identity/exposure"):
                rehearsal.db_query("SELECT 1")
        rehearsal.state = {"steps": ["isolation_verified"], "source_lock_attempted": True,
            "isolation_proof": dict.fromkeys(["internal_db", "fresh_redis", "no_default_route", "negative_external_native_tcp", "external_dns_blocked"], True)}
        with self.assertRaisesRegex(RuntimeError, "single source-lock attempt"):
            rehearsal.snapshot()

    def test_base_inspection_is_read_only_and_network_disabled(self):
        module = load_tool("prepare_base_image")
        self.assertIn("'--network','none'", module.REMOTE)
        self.assertIn("'--read-only'", module.REMOTE)
        self.assertIn("'--cap-drop','ALL'", module.REMOTE)
        self.assertNotIn("'--volume'", module.REMOTE)

    def test_completed_foundation_is_never_replayed_or_stopped(self):
        module = load_tool("rootless_foundation")
        foundation = module.Foundation.__new__(module.Foundation)
        foundation.state = {"complete": True}
        with patch.object(foundation, "step", side_effect=AssertionError("bootstrap replay")), patch.object(foundation, "finish", side_effect=AssertionError("baseline replay")), patch("sys.stdout", io.StringIO()) as output:
            foundation.apply()
        self.assertTrue(json.loads(output.getvalue())["foundation_already_complete"])

    def test_foundation_package_plan_has_verified_hashes_and_exact_total(self):
        data = json.loads((PLAN / "rootless-packages.json").read_text())
        self.assertEqual(sum(package["bytes"] for package in data["packages"]), data["total_package_bytes"])
        self.assertLess(data["total_package_bytes"], 100_000_000)
        for package in data["packages"]:
            self.assertRegex(package["sha256"], r"^[a-f0-9]{64}$")
            self.assertTrue(package["filename"].startswith("dists/noble/pool/stable/amd64/"))

    def test_foundation_never_overwrites_unknown_configuration(self):
        module = load_tool("rootless_foundation")
        foundation = module.Foundation.__new__(module.Foundation)
        with tempfile.TemporaryDirectory(dir="/tmp/opencode" if Path("/tmp/opencode").is_dir() else None) as directory:
            path = Path(directory) / "config"
            path.write_text("existing user configuration")
            with self.assertRaises(RuntimeError):
                foundation.write_new(path, "replacement")
            self.assertEqual(path.read_text(), "existing user configuration")

    def test_foundation_download_checks_hash_and_size(self):
        module = load_tool("rootless_foundation")
        foundation = module.Foundation.__new__(module.Foundation)
        foundation.state = {"download_bytes": 0}
        foundation.save = lambda: None
        data = b"public signed package fixture"
        with tempfile.TemporaryDirectory(dir="/tmp/opencode" if Path("/tmp/opencode").is_dir() else None) as directory:
            path = Path(directory) / "package.deb"
            with patch.object(module, "urlopen", return_value=io.BytesIO(data)):
                foundation.fetch("https://example.test/package", path, hashlib.sha256(data).hexdigest(), len(data))
            self.assertEqual(path.read_bytes(), data)

    def test_foundation_declared_download_budget_is_checked_before_network(self):
        module = load_tool("rootless_foundation")
        foundation = module.Foundation.__new__(module.Foundation)
        foundation.state = {"download_bytes": module.BUDGET - 10}
        foundation.save = lambda: None
        with tempfile.TemporaryDirectory(dir="/tmp/opencode" if Path("/tmp/opencode").is_dir() else None) as directory:
            with patch.object(module, "urlopen", side_effect=AssertionError("network must not run")):
                with self.assertRaises(RuntimeError):
                    foundation.fetch("https://example.test/package", Path(directory) / "package", expected_size=100)

    def test_resume_remote_hashes_prefix_without_sending_file_data(self):
        module = load_tool("resume_checkpoint")
        with tempfile.TemporaryDirectory(dir="/tmp/opencode" if Path("/tmp/opencode").is_dir() else None) as directory:
            path = Path(directory) / "fixture.gpg"
            data = b"ciphertext fixture" * 100
            path.write_bytes(data)
            path.chmod(0o600)
            fstat = os.fstat
            def root_stat(fd):
                value = fstat(fd)
                return SimpleNamespace(st_mode=value.st_mode, st_uid=0, st_size=value.st_size,
                                       st_dev=value.st_dev, st_ino=value.st_ino,
                                       st_mtime_ns=value.st_mtime_ns, st_ctime_ns=value.st_ctime_ns)
            request = {"host": "mail.ndftechlabs.com", "artifact": str(path), "bytes": len(data), "offset": 37, "mode": "metadata"}
            output = io.StringIO()
            with patch("os.geteuid", return_value=0), patch("os.fstat", root_stat), patch("subprocess.check_output", return_value="mail.ndftechlabs.com\n"), patch("sys.stdout", output):
                exec(module.remote_program(request), {})
            result = json.loads(output.getvalue())
            self.assertEqual(result["sha256"], hashlib.sha256(data).hexdigest())
            self.assertEqual(result["prefix_sha256"], hashlib.sha256(data[:37]).hexdigest())

    def test_resume_remote_sends_only_suffix(self):
        module = load_tool("resume_checkpoint")
        with tempfile.TemporaryDirectory(dir="/tmp/opencode" if Path("/tmp/opencode").is_dir() else None) as directory:
            path = Path(directory) / "fixture.gpg"
            data = b"prefix-ciphertext-remaining-bytes"
            path.write_bytes(data)
            path.chmod(0o600)
            value = path.stat()
            signature = {"dev": value.st_dev, "ino": value.st_ino, "mtime_ns": value.st_mtime_ns, "ctime_ns": value.st_ctime_ns}
            fstat = os.fstat
            def root_stat(fd):
                value = fstat(fd)
                return SimpleNamespace(st_mode=value.st_mode, st_uid=0, st_size=value.st_size,
                                       st_dev=value.st_dev, st_ino=value.st_ino,
                                       st_mtime_ns=value.st_mtime_ns, st_ctime_ns=value.st_ctime_ns)
            request = {"host": "mail.ndftechlabs.com", "artifact": str(path), "bytes": len(data), "offset": 7,
                       "mode": "suffix", "signature": signature}
            output = SimpleNamespace(buffer=io.BytesIO())
            with patch("os.geteuid", return_value=0), patch("os.fstat", root_stat), patch("subprocess.check_output", return_value="mail.ndftechlabs.com\n"), patch("sys.stdout", output):
                exec(module.remote_program(request), {})
            self.assertEqual(output.buffer.getvalue(), data[7:])

    def test_checkpoint_import_has_no_remote_or_local_command_side_effect(self):
        with patch("subprocess.run", side_effect=AssertionError("command during import")):
            load_tool("checkpoint")
            load_tool("collect_checkpoint")

    def test_lock_rejects_unbounded_or_empty_scope(self):
        module = load_tool("checkpoint")
        for tables, acquire, hold in (([], 5, 55), (["table"], 6, 55), (["table"], 5, 60)):
            with self.assertRaises(ValueError):
                module.ScopedSchemaLock(lambda: None, "pilot", tables, acquire, hold)

    def test_sql_scope_quotes_identifiers(self):
        module = load_tool("checkpoint")
        self.assertEqual(module.identifier("table`name"), "`table``name`")
        with self.assertRaises(ValueError):
            module.identifier("invalid\0name")

    def test_cipher_fetch_rejects_other_host_or_path(self):
        module = load_tool("collect_checkpoint")
        receipt = {"checkpoint_id": "20261025T010000Z-dalali-abcd1234", "recipient": module.RECIPIENT,
                   "host": "mail.ndftechlabs.com", "site": "dalali.ndftechlabs.com", "bytes": 100,
                   "sha256": "a" * 64,
                   "artifact": "/var/backups/ndf-migration/20261025T010000Z-dalali-abcd1234/checkpoint.tar.gz.gpg"}
        module.validate_receipt(receipt)
        with self.assertRaises(ValueError):
            module.validate_receipt(dict(receipt, artifact="/etc/shadow"))
        with self.assertRaises(ValueError):
            module.validate_receipt(dict(receipt, host="different-host"))

    def test_public_recipient_rejects_private_key_before_calling_gpg(self):
        module = load_tool("collect_checkpoint")
        with tempfile.TemporaryDirectory(dir="/tmp/opencode" if Path("/tmp/opencode").is_dir() else None) as directory:
            path = Path(directory) / "wrong-key.asc"
            path.write_text("-----BEGIN PGP PRIVATE KEY BLOCK-----\nnot-a-key\n")
            with patch("subprocess.run", side_effect=AssertionError("GPG must not inspect a private export")):
                with self.assertRaises(ValueError):
                    module.public_recipient(path)

    def test_backup_destination_cannot_be_the_repository(self):
        module = load_tool("collect_checkpoint")
        with self.assertRaises(ValueError):
            module.private_directory(ROOT / "unsafe-backup-directory")

    def test_source_manifest_pins_every_pilot_app(self):
        manifest = json.loads((PLAN / "source-manifest.json").read_text())
        self.assertEqual(manifest["pilot"], "dalali.ndftechlabs.com")
        self.assertEqual(len(manifest["apps"]), 12)
        names = [app["app"] for app in manifest["apps"]]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(names[:2], ["frappe", "erpnext"])
        for app in manifest["apps"]:
            self.assertRegex(app["commit"], r"^[a-f0-9]{40}$")
            self.assertIs(app["dirty"], False)
        self.assertEqual(manifest["runtime"]["frappe"], manifest["apps"][0]["version"])
        self.assertEqual(manifest["runtime"]["erpnext"], manifest["apps"][1]["version"])

    def test_audit_import_does_not_execute_commands(self):
        with patch("subprocess.run", side_effect=AssertionError("Command during import")):
            load_audit()

    def test_endpoint_inventory_removes_credentials_paths_and_query(self):
        audit = load_audit()
        result = audit.endpoint("redis://username:secret@127.0.0.1:11000/0?token=private")
        self.assertEqual(result, {"scheme": "redis", "host": "127.0.0.1", "port": 11000})
        self.assertNotIn("secret", json.dumps(result))
        self.assertNotIn("private", json.dumps(result))

    def test_database_metadata_uses_selects_and_keeps_password_out_of_argv(self):
        audit = load_audit()
        calls = []

        def command(args, timeout=15, env=None):
            calls.append((args, env))
            self.assertIn("START TRANSACTION READ ONLY", args[-1])
            self.assertNotIn("demo-secret", str(args))
            self.assertEqual(env["MYSQL_PWD"], "demo-secret")
            if "information_schema" in args[-1]:
                if "TABLE_TYPE" in args[-1]:
                    return 0, "BASE TABLE\t10"
                return 0, "10\t1000"
            return 0, '["frappe", "erpnext"]'

        with patch.object(audit.shutil, "which", return_value="/usr/bin/mariadb"), patch.object(audit, "command", command):
            result = audit.database_metadata({"db_name": "demo_db", "db_password": "demo-secret"}, {})
        self.assertEqual(result["installed_apps"], ["frappe", "erpnext"])
        self.assertEqual(result["estimated_bytes"], 1000)
        self.assertEqual(len(calls), 3)
        self.assertNotIn("demo-secret", json.dumps(result))

    def test_failed_database_check_does_not_export_raw_client_output(self):
        audit = load_audit()
        with patch.object(audit.shutil, "which", return_value="/usr/bin/mariadb"), patch.object(audit, "command", return_value=(1, "failure: demo-secret")):
            result = audit.database_metadata({"db_name": "demo_db", "db_password": "demo-secret"}, {})
        self.assertFalse(result["available"])
        self.assertNotIn("demo-secret", json.dumps(result))

    def test_version_detection_parses_source_without_importing_app(self):
        audit = load_audit()
        temp_root = "/tmp/opencode" if Path("/tmp/opencode").is_dir() else None
        with tempfile.TemporaryDirectory(dir=temp_root) as temporary:
            app = Path(temporary) / "demo_app"
            package = app / "demo_app"
            package.mkdir(parents=True)
            (package / "__init__.py").write_text("__version__ = '1.2.3'\nraise RuntimeError('must not execute')\n")
            self.assertEqual(audit.version_from_source(app), "1.2.3")
