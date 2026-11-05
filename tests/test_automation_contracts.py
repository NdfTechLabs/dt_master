"""Run with: python3 -m unittest discover -s tests -v

No Frappe site, SSH, database, sudo, package installation or service changes.
Demo DocTypes live in memory; all shell data lives in TemporaryDirectory.
"""

import ast
import base64
import copy
import importlib
import io
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from datetime import datetime
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "dt_master"
SCRIPTS = APP / "scripts"
SCHEMAS = APP / "dt_master" / "doctype"
DEMO = json.loads((ROOT / "tests/fixtures/automation_demo.json").read_text())


class DemoDoc(dict):
    def __getattr__(self, key):
        return self.get(key)

    def __setattr__(self, key, value):
        self[key] = value

    def save(self, **kwargs):
        return self

    def check_permission(self, permission):
        return None

    def get_password(self, field):
        return self[field]

    def set(self, key, value):
        self[key] = value

    def append(self, key, value):
        self.setdefault(key, []).append(value)


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.docs = {key: DemoDoc(copy.deepcopy(value)) for key, value in DEMO.items() if isinstance(value, dict)}
        self.docs["tenant"].installed_apps = [self.docs["app"]]
        self.docs["mail_server"].mail_users = [self.docs["mail_user"]]
        self.frappe = types.ModuleType("frappe")
        self.frappe.ValidationError = ValueError
        self.frappe.whitelist = lambda *a, **kw: lambda fn: fn
        self.frappe.throw = Mock(side_effect=ValueError)
        self.frappe.session = DemoDoc(user="demo@example.test")
        self.frappe.db = Mock()
        self.frappe.publish_realtime = Mock()
        self.frappe.only_for = Mock()
        self.frappe.logger = Mock()
        self.frappe.log_error = Mock()
        self.frappe.get_traceback = lambda: "Demo traceback"
        self.frappe.get_all = Mock(return_value=[])
        by_type = {"Infrastructure Node": "node", "Tenant Site": "tenant", "Framework Version": "framework",
                   "Extension": "extension", "Extension Version": "extension_version", "Mail Server": "mail_server",
                   "Master Mail Users": "mail_user"}
        self.frappe.get_doc = lambda doctype, name: self.docs[by_type[doctype]]
        utils = types.ModuleType("frappe.utils")
        utils.now_datetime = datetime.now
        modules = {"frappe": self.frappe, "frappe.utils": utils}
        # Requests is not used by the tests; importing the executors must not
        # require third-party dependencies in a plain Python environment.
        modules["requests"] = types.ModuleType("requests")
        self.module_patch = patch.dict(sys.modules, modules)
        self.module_patch.start()
        self.addCleanup(self.module_patch.stop)
        self.modules = {}
        for name in ("script_contract", "tenant_installed_app_executor", "tenant_site_executor", "mail_server_executor", "infrastructure_node"):
            module = importlib.import_module(f"dt_master.api.{name}")
            module.frappe = self.frappe
            self.modules[name] = module
        self.contract = self.modules["script_contract"]
        self.apps = self.modules["tenant_installed_app_executor"]
        self.sites = self.modules["tenant_site_executor"]
        self.mail = self.modules["mail_server_executor"]
        self.nodes = self.modules["infrastructure_node"]

    def schema(self, name):
        return json.loads((SCHEMAS / name / f"{name}.json").read_text())

    def choices(self, name, field):
        return set(next(f for f in self.schema(name)["fields"] if f["fieldname"] == field)["options"].splitlines())

    def test_demo_values_match_doctype_options(self):
        for key, doctype in (("node", "infrastructure_node"), ("tenant", "tenant_site"),
                             ("app", "tenant_installed_app"), ("mail_server", "mail_server"),
                             ("extension", "extension"), ("extension_version", "extension_version")):
            for field in self.schema(doctype)["fields"]:
                if field["fieldtype"] == "Select" and field["fieldname"] in self.docs[key]:
                    self.assertIn(self.docs[key][field["fieldname"]], field["options"].splitlines())

    def test_all_action_paths_exist(self):
        paths = [("app", action["script"]) for action in self.apps.APP_ACTIONS.values()]
        paths += [("site", action[1]) for action in self.sites.ACTION_SCRIPT_MAP.values()]
        paths += [("mail", action[1]) for action in self.mail.MAIL_ACTION_SCRIPT_MAP.values()]
        paths += [("node", action[0]) for action in self.nodes.BENCH_ACTIONS.values()]
        for category, script in paths:
            with self.subTest(script=script):
                self.assertTrue((SCRIPTS / "bin" / category / script).is_file())

    def test_all_lifecycle_outcomes_are_valid_doctype_options(self):
        for module, mapping, doctype, state_field in (
            (self.sites, self.sites.TENANT_SITE_TRANSITIONS, "tenant_site", "automation_state"),
            (self.mail, self.mail.MAIL_SERVER_TRANSITIONS, "mail_server", "automation_state"),
            (self.nodes, self.nodes.NODE_TRANSITIONS, "infrastructure_node", "state"),
        ):
            states, statuses = self.choices(doctype, state_field), self.choices(doctype, "status")
            for name, action in mapping.items():
                for state in action["from"]:
                    self.assertIn(state, states, (doctype, name))
                for result in ("success", "failure"):
                    if action.get(result):
                        state, status = action[result]
                        if state: self.assertIn(state, states, (doctype, name, result))
                        if status: self.assertIn(status, statuses, (doctype, name, result))

    def test_app_action_statuses_and_labels_match_schema(self):
        states = self.choices("tenant_installed_app", "status")
        labels = self.choices("tenant_installed_app", "last_action")
        for name, action in self.apps.APP_ACTIONS.items():
            self.assertIn(self.apps.ACTION_LABELS[name], labels)
            self.assertTrue(set(action["from_status"]) <= states)

    def test_app_child_names_do_not_depend_on_an_unnamed_parent(self):
        self.assertNotIn("{parent}", self.schema("tenant_installed_app").get("autoname", ""))

    def test_same_app_is_allowed_on_different_tenants(self):
        self.contract.validate_app_rows([{"app_name": "demo_app"}])
        self.contract.validate_app_rows([{"app_name": "demo_app"}])

    def test_duplicate_app_rows_are_rejected_within_one_tenant(self):
        with self.assertRaises(ValueError):
            self.contract.validate_app_rows([{"app_name": "demo_app"}, {"app_name": "demo_app"}])

    def test_invalid_app_row_is_rejected_before_shell_execution(self):
        with self.assertRaises(ValueError):
            self.contract.validate_app_rows([{"app_name": "../outside"}])

    def test_app_inspection_and_install_support_recovery_states(self):
        row = self.docs["app"]
        row.status = "Pending"
        self.apps._validate_action(row, "check_app")
        row.status = "Failed"
        self.apps._validate_action(row, "check_version")
        row.status = "Uninstalled"
        self.apps._validate_action(row, "install")

    def test_framework_env_has_matching_decodable_context(self):
        env = self.contract.framework_environment(self.docs["node"])
        self.assertEqual(json.loads(base64.b64decode(env["FRAMEWORK_CONTEXT_B64"])), self.docs["framework"])
        self.assertEqual(json.loads(env["FRAMEWORK_CONTEXT"]), self.docs["framework"])
        self.assertEqual(env["BENCH_DIR"], self.docs["node"].project_base_dir + "/bench")

    def test_target_tag_is_prefixed_only_once(self):
        for value, expected in (("1.2.3", "v1.2.3"), ("v1.2.3", "v1.2.3"), ("", ""), ("release-1", "release-1")):
            self.assertEqual(self.contract.tag_ref(value), expected)

    def test_docker_seed_cannot_run_without_sandbox_flag(self):
        module = importlib.import_module("dt_master.testing.docker_sandbox")
        module.frappe = self.frappe
        with patch.dict(os.environ, {"DT_DOCKER_SANDBOX": "0"}):
            with self.assertRaises(RuntimeError):
                module.seed()

    def test_integration_suite_cannot_run_without_sandbox_flag(self):
        module = importlib.import_module("dt_master.testing.integration")
        with patch.dict(os.environ, {"DT_DOCKER_SANDBOX": "0"}):
            with self.assertRaises(RuntimeError):
                module.run()

    def test_integration_reports_redact_environment_credentials(self):
        module = importlib.import_module("dt_master.testing.integration")
        with patch.dict(os.environ, {"ADMIN_PASSWORD": "demo-admin-secret", "DB_ROOT_PASSWORD": "demo-db-secret"}):
            self.assertEqual(module._scrub("demo-admin-secret / demo-db-secret"), "[REDACTED] / [REDACTED]")

    def test_docker_seed_rejects_other_sites_and_database_hosts(self):
        module = importlib.import_module("dt_master.testing.docker_sandbox")
        module.frappe = self.frappe
        self.frappe.local = DemoDoc(site="production.example.test")
        self.frappe.conf = DemoDoc(db_host="db")
        with patch.dict(os.environ, {"DT_DOCKER_SANDBOX": "1"}):
            with self.assertRaises(RuntimeError):
                module.seed()
            self.frappe.local.site = "master.localhost"
            self.frappe.conf.db_host = "production-db"
            with self.assertRaises(RuntimeError):
                module.seed()

    def test_demo_environment_covers_wrapper_requirements(self):
        node_env = self.contract.framework_environment(self.docs["node"])
        node_env.update(SYSTEM_DEPENDENCIES=json.dumps(DEMO["dependencies"]),
                        RESTORE_DIR="/srv/demo-backups/site/stamp", SITE_NAME=self.docs["tenant"].fqdn,
                        MYSQL_ROOT_USER=self.docs["tenant"].mysql_root_user_name,
                        MYSQL_ROOT_PASSWORD=self.docs["tenant"].mysql_root_user_password)
        site_env = self.contract.tenant_environment(self.docs["node"], self.docs["tenant"])
        site_env["APPS_JSON"] = json.dumps([{"app_name": "demo_app", "repo_url": "https://example.test/demo_app"}])
        environments = {
            "node": node_env, "site": site_env,
            "app": self.contract.app_environment(self.docs["node"], self.docs["tenant"], self.docs["app"]),
            "mail": self.contract.mail_environment(self.docs["node"], self.docs["mail_server"], self.docs["mail_user"]),
        }
        bootstrap = 'source "$1/core.sh"; for module in deps app_lifecycle site_lifecycle node_lifecycle mail_stack backup bench_lifecycle; do source "$1/$module.sh"; done; declare -f "$2"'
        for category, env in environments.items():
            for wrapper in (SCRIPTS / "bin" / category).glob("*.sh"):
                calls = re.findall(r"^([a-z]+::[a-z_]+)\s*$", wrapper.read_text(), re.M)
                self.assertEqual(len(calls), 1, str(wrapper))
                output = subprocess.run(["bash", "-c", bootstrap, "contract", str(SCRIPTS / "lib"), calls[0]],
                                        capture_output=True, text=True, check=True).stdout
                for match in re.finditer(r"require_(?:env|var)\s+([A-Z_][A-Z0-9_]*(?:\s+[A-Z_][A-Z0-9_]*)*)", output):
                    for name in match[1].split():
                        if name == "APPS" and env.get("APPS_JSON"):
                            continue
                        self.assertTrue(env.get(name), (wrapper.name, name))

    def test_fixed_app_target_defaults_to_extension_tag(self):
        self.docs["extension"].install_mode = "fixed"
        self.docs["extension_version"].ref_type = "tag"
        self.docs["extension_version"].source_ref = "v1.2.3"
        self.docs["app"].target_version = ""
        env = self.contract.app_environment(self.docs["node"], self.docs["tenant"], self.docs["app"])
        self.assertEqual(env["TARGET_VERSION"], "v1.2.3")

    def test_remote_environment_roundtrips_quotes_json_and_shell_punctuation(self):
        env = self.contract.app_environment(self.docs["node"], self.docs["tenant"], self.docs["app"])
        command = self.contract.build_remote_command(env, "/srv/demo scripts/bin/app/install_app.sh")
        payload = shlex.split(command)[-1]
        self.assertEqual(subprocess.run(["bash", "-n"], input=payload, text=True, capture_output=True).returncode, 0)
        # Run exports only, never sudo/SSH/the script. env -0 is unambiguous.
        exports = payload.rsplit("exec bash ", 1)[0]
        result = subprocess.run(["bash", "-c", exports + "env -0"], capture_output=True, check=True)
        actual = dict(item.decode().split("=", 1) for item in result.stdout.split(b"\0") if item)
        for key, value in env.items():
            self.assertEqual(actual[key], str(value) if value is not None else "")

    def test_mail_user_env_is_independent_of_tenant_link(self):
        self.docs["mail_server"].tenant_site = None
        env = self.contract.mail_environment(self.docs["node"], self.docs["mail_server"], self.docs["mail_user"])
        self.assertEqual(env["MAIL_USER"], "alice")
        self.assertEqual(env["MAIL_PASSWORD"], self.docs["mail_user"].password)

    def test_mail_database_action_without_credentials_fails_before_ssh(self):
        self.docs["mail_server"].tenant_site = None
        with patch.object(self.mail, "run_remote_script") as remote:
            with self.assertRaises(ValueError):
                self.mail._execute_mail_script(self.docs["node"], self.docs["mail_server"], "add_user.sh", self.docs["mail_user"])
            remote.assert_not_called()

    def test_mail_rejects_cross_domain_mailbox(self):
        self.docs["mail_user"].email = "alice@other.test"
        with self.assertRaises(ValueError):
            self.contract.mail_environment(self.docs["node"], self.docs["mail_server"], self.docs["mail_user"])

    def test_all_callers_preserve_event_names_and_identity_fields(self):
        cases = [
            (self.apps, lambda: self.apps._execute_ssh_script(self.docs["node"], self.docs["tenant"], self.docs["app"], "check_app.sh"), "tenant_app_log", {"tenant", "row"}),
            (self.sites, lambda: self.sites._execute_ssh_script(self.docs["node"], self.docs["tenant"], "install_apps.sh", []), "tenant_script_log", {"tenant"}),
            (self.mail, lambda: self.mail._execute_mail_script(self.docs["node"], self.docs["mail_server"], "list_users.sh"), "mail_executor_log", {"server"}),
            (self.nodes, lambda: self.nodes._execute_ssh_script(self.docs["node"], "install_deps.sh"), "script_execution_log", {"node", "execution"}),
        ]
        for module, invoke, event, keys in cases:
            with self.subTest(event=event), patch.object(module, "run_remote_script", return_value=(0, "", "")) as remote:
                invoke()
                args = remote.call_args.args
                self.assertEqual(args[-2], event)
                self.assertEqual(set(args[-1]), keys)
                self.assertIn('"' + event + '"', (SCHEMAS / {"tenant_app_log": "tenant_site", "tenant_script_log": "tenant_site", "mail_executor_log": "mail_server", "script_execution_log": "infrastructure_node"}[event] / ({"tenant_app_log": "tenant_site", "tenant_script_log": "tenant_site", "mail_executor_log": "mail_server", "script_execution_log": "infrastructure_node"}[event] + ".js")).read_text())

    def test_streaming_preserves_stderr_exit_code_and_redacts_secrets(self):
        secret = self.docs["tenant"].admin_password
        process = types.SimpleNamespace(stdout=io.StringIO("app_name=demo_app\n" + secret + "\n"),
                                        stderr=io.StringIO("failure\n"), returncode=7, wait=lambda: 7)
        with patch.object(self.contract.subprocess, "Popen", return_value=process) as popen:
            result = self.contract.run_remote_script(self.docs["node"], {"host": "192.0.2.10", "user": "demo", "port": 22},
                         "app", "check_app.sh", {"ADMIN_PASSWORD": secret}, "tenant_app_log", {"tenant": "demo", "row": "row"})
        self.assertEqual(result, (7, "app_name=demo_app\n[REDACTED]", "failure"))
        self.assertIsInstance(popen.call_args.args[0], list)
        self.assertNotIn("shell", popen.call_args.kwargs)
        self.assertEqual(self.frappe.publish_realtime.call_args.args[1]["status"], "Failed")
        self.assertNotIn(secret, str(self.frappe.publish_realtime.call_args_list))

    def test_large_stderr_does_not_deadlock_stdout(self):
        # Start a harmless local Python process, not the SSH command. Its
        # stderr exceeds a pipe buffer before stdout becomes available.
        process = subprocess.Popen([sys.executable, "-c", "import sys; sys.stderr.write('x'*262144+'\\n'); print('installed=true')"],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        with patch.object(self.contract.subprocess, "Popen", return_value=process):
            result = self.contract.run_remote_script(self.docs["node"], {"host": "192.0.2.10", "user": "demo", "port": 22},
                         "app", "check_app.sh", {}, "tenant_app_log", {"tenant": "demo", "row": "row"})
        self.assertEqual(result[0], 0)
        self.assertEqual(result[1], "installed=true")
        self.assertEqual(len(result[2]), 262144)

    def test_parser_ignores_timestamped_logs(self):
        parsed = self.contract.parse_output("[demo] installed=true\napp_name=demo_app\ninstalled=false\nnoise=x=y\n")
        self.assertEqual(parsed, {"app_name": "demo_app", "installed": "false", "noise": "x=y"})

    def test_check_app_accepts_legacy_head_key(self):
        result = {"exit_code": 0, "error": None, "stdout": "app_name=demo_app\ninstalled=true\nhead_state=branch\n"}
        self.apps._handle_readonly_result(self.docs["tenant"], self.docs["app"], result, "check_app")
        self.assertEqual(self.docs["app"].git_head_state, "branch")

    def test_uninstall_noop_does_not_invent_success_or_erase_git_state(self):
        result = {"exit_code": 0, "error": None, "stdout": "app_name=demo_app\nuninstalled=false\n"}
        self.apps._handle_mutating_result(self.docs["tenant"], self.docs["app"], self.apps.APP_ACTIONS["uninstall"], result)
        self.assertEqual(self.docs["app"].status, "Installed")
        self.assertEqual(self.docs["app"].git_commit, "abc1234")
        self.assertEqual(self.docs["app"].installed_version, "1.2.2")
        self.assertIsNone(self.docs["app"].installed_at)

    def test_uninstall_confirmed_absence_updates_status(self):
        result = {"exit_code": 0, "error": None, "stdout": "app_name=demo_app\nuninstalled=false\ninstalled=false\n"}
        self.apps._handle_mutating_result(self.docs["tenant"], self.docs["app"], self.apps.APP_ACTIONS["uninstall"], result)
        self.assertEqual(self.docs["app"].status, "Uninstalled")

    def test_failed_app_action_stays_failed(self):
        result = {"exit_code": 1, "error": "demo error", "stdout": ""}
        with self.assertRaises(ValueError):
            self.apps._handle_mutating_result(self.docs["tenant"], self.docs["app"], self.apps.APP_ACTIONS["install"], result)
        self.assertEqual(self.docs["app"].status, "Failed")

    def test_missing_app_output_is_not_success(self):
        with self.assertRaises(ValueError):
            self.apps._handle_mutating_result(self.docs["tenant"], self.docs["app"], self.apps.APP_ACTIONS["install"], {"exit_code": 0, "error": None, "stdout": "just logs"})

    def test_prefixed_versions_reconcile_major(self):
        self.assertEqual(self.apps.extract_major_versions({"installed_version": "v15.2.3", "next_major_version": "16.0.0"}), {15, 16})

    def test_structured_install_updates_app_rows(self):
        apps = [{"app_name": "demo_app"}]
        self.docs["app"].status = "Pending"
        outcome = self.sites._resolve_lifecycle_outcome(self.docs["tenant"], "install_apps", apps, self.sites.TENANT_SITE_TRANSITIONS["install_apps"], 0, None)
        self.assertEqual(outcome, ("ready", "Active", "Success"))
        self.assertEqual(self.docs["app"].status, "Installed")

    def test_site_lifecycle_create_install_suspend_resume_destroy(self):
        tenant = self.docs["tenant"]
        tenant.automation_state, tenant.status = "idle", "Requested"
        self.docs["app"].status = "Pending"
        with patch.object(self.sites, "_execute_action", return_value=(0, "demo output", "", None)):
            for action, state, status in (("create_site", "installing_apps", "Provisioning"),
                                          ("install_apps", "ready", "Active"),
                                          ("suspend_site", "idle", "Suspended"),
                                          ("resume_site", "ready", "Active"),
                                          ("destroy_site", "idle", "Archived")):
                self.sites._run_tenant_action(tenant.name, action)
                self.assertEqual((tenant.automation_state, tenant.status), (state, status), action)

    def test_empty_app_selection_fails_before_execution(self):
        with patch.object(self.sites, "_execute_action") as execute:
            with self.assertRaises(ValueError):
                self.sites._run_tenant_action(self.docs["tenant"].name, "install_apps")
            execute.assert_not_called()

    def test_site_teardown_failure_does_not_archive_site(self):
        with patch.object(self.sites, "_execute_action", return_value=(1, "", "demo failure", "demo failure")):
            with self.assertRaises(ValueError):
                self.sites._run_tenant_action(self.docs["tenant"].name, "destroy_site")
        self.assertEqual(self.docs["tenant"].status, "Active")
        self.assertEqual(self.docs["tenant"].automation_state, "failed")

    def test_mail_full_pipeline_can_advance(self):
        server = self.docs["mail_server"]
        server.automation_state, server.status = "idle", "Requested"
        with patch.object(self.mail, "_execute_mail_script", return_value=(0, "demo output", "")):
            for action in ("install_stack", "configure_postfix", "configure_dovecot", "configure_rspamd", "configure_opendkim", "configure_dns", "verify_mail"):
                self.mail._run_mail_action(server.name, action)
        self.assertEqual((server.automation_state, server.status), ("ready", "Active"))

    def test_mail_user_success_updates_actual_child_row(self):
        user = self.docs["mail_user"]
        with patch.object(self.mail, "_execute_mail_script", return_value=(0, "mail_user_created=true", "")):
            self.mail._run_mail_action(self.docs["mail_server"].name, "add_user", copy.deepcopy(user))
        self.assertEqual((user.status, user.active), ("Active", 1))

    def test_logs_use_only_real_child_doctype_fields(self):
        now = datetime.now()
        self.nodes._log_node_action(self.docs["node"], "Demo", "Bench", "Running", "Stopped", "Success", 0, "", "", now, now)
        self.sites._log_tenant_action(self.docs["tenant"], "Demo", "ready", "ready", "Success", 0, "", "", now, now)
        self.mail._log_mail_action(self.docs["mail_server"], "Demo", "Success", 0, "", "", now, now)
        for doc, table, schema in (("node", "node_action_logs", "node_action_log"), ("tenant", "automation_logs", "node_action_log"), ("mail_server", "automation_logs", "mail_server_log")):
            fields = {f["fieldname"] for f in self.schema(schema)["fields"]}
            self.assertTrue(set(self.docs[doc][table][-1]) <= fields, self.docs[doc][table][-1])

    def test_bench_removal_confirms_decommissioned_state(self):
        with patch.object(self.nodes, "run_remote_script", return_value=(0, "bench_removed=true\ndatabases_kept=true\n", "")):
            self.nodes.remove_bench("demo-node")
        self.assertEqual((self.docs["node"].state, self.docs["node"].status), ("Decommissioned", "Retired"))

    def test_bench_explicit_false_is_not_accepted_as_success(self):
        with patch.object(self.nodes, "run_remote_script", return_value=(0, "bench_updated=false\n", "")):
            with self.assertRaises(ValueError):
                self.nodes.update_bench("demo-node")
        self.assertEqual(self.docs["node"].state, "Error")

    def test_restore_rejects_tenant_from_other_node(self):
        self.docs["tenant"].node = "other-node"
        with patch.object(self.nodes, "run_remote_script") as remote:
            with self.assertRaises(ValueError):
                self.nodes.restore_backup("demo-node", "demo-tenant", "/srv/backups/demo")
            remote.assert_not_called()


class ShellTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="contract-", dir="/tmp/opencode" if Path("/tmp/opencode").is_dir() else None)
        self.addCleanup(self.directory.cleanup)
        self.base = Path(self.directory.name)
        (self.base / "bench/sites/demo.example.test/private/backups").mkdir(parents=True)
        (self.base / "bench/apps").mkdir()
        (self.base / "bench/sites/demo.example.test/site_config.json").write_text('{"db_name":"demo_db", "encryption_key":"demo-key"}')
        self.env = dict(os.environ, PROJECT_BASE_DIR=str(self.base), PROJECT_LOGS_DIR=str(self.base),
                        SCRIPT_LIB_DIR=str(SCRIPTS / "lib"), BENCH_DIR=str(self.base / "bench"),
                        SITE_NAME="demo.example.test", APP_NAME="demo_app", INSTALL_MODE="rolling",
                        REF_TYPE="branch", SOURCE_REF="main", TARGET_VERSION="v1.2.3",
                        REPOSITORY_URL="https://example.test/demo_app", FRAPPE_HOME="/home/frappe",
                        MYSQL_ROOT_USER="demo_admin", MYSQL_ROOT_PASSWORD="demo-password",
                        SUPERVISOR_CONF_DIR=str(self.base / "supervisor"), NGINX_CONF_DIR=str(self.base / "nginx"))
        self.env.pop("_DT_CORE_LOADED", None)
        self.env.pop("APP", None)

    def shell(self, body, expected=0):
        prelude = '''set -euo pipefail
source "$SCRIPT_LIB_DIR/core.sh"
for module in deps app_lifecycle site_lifecycle backup bench_lifecycle; do source "$SCRIPT_LIB_DIR/$module.sh"; done
require_root() { :; }
require_venv() { :; }
run_as_frappe() { "$@"; }
sudo() { echo "UNMOCKED sudo" >&2; return 98; }
mysql() { echo "UNMOCKED mysql" >&2; return 98; }
systemctl() { echo "MOCK systemctl $*" >&2; }
supervisorctl() { echo "MOCK supervisorctl $*" >&2; }
apt-get() { echo "MOCK apt-get $*" >&2; }
apt_install() { echo "UNMOCKED install" >&2; return 98; }
curl() { echo "UNMOCKED curl" >&2; return 98; }
bench_exec() { echo "UNMOCKED bench $*" >&2; return 98; }
'''
        result = subprocess.run(["bash", "-c", prelude + body], env=self.env, capture_output=True, text=True, timeout=15)
        if expected is not None:
            self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def test_all_shell_syntax(self):
        for file in SCRIPTS.rglob("*.sh"):
            result = subprocess.run(["bash", "-n", str(file)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, str(file) + result.stderr)

    def test_mysql_database_and_sql_forwarded_without_cli_password(self):
        result = self.shell('env() { printf "ARG=%s\\n" "$@"; }; mysql_query_db demo_db -N -e "SELECT 1"')
        for argument in ("ARG=demo_db", "ARG=-N", "ARG=-e", "ARG=SELECT 1", "ARG=MYSQL_PWD=demo-password"):
            self.assertIn(argument, result.stdout)
        self.assertNotIn("ARG=-pdemo-password", result.stdout)

    def test_database_admin_validation_never_drops_current_user(self):
        result = self.shell('mysql_exec() { printf "%s\\n" "$*" >&2; }; db_ensure_admin_user')
        self.assertIn("SELECT 1", result.stderr)
        self.assertNotIn("DROP USER", result.stderr)

    def test_app_install_uses_app_name_and_real_bench_tag_option(self):
        self.env.update(INSTALL_MODE="fixed", REF_TYPE="tag")
        result = self.shell('''
bench_exec() { printf 'BENCH=%s\\n' "$*"; }
clear_cache_and_reload() { :; }
app::detect_git_state() { app::reset_state; }
app::detect_installed_version() { echo v1.2.3; }
app::install
''')
        self.assertIn("--branch v1.2.3", result.stdout)
        self.assertNotIn("--tag", result.stdout)
        self.assertIn("installed=true", result.stdout)

    def test_check_app_is_site_scoped_and_keeps_both_head_keys(self):
        (self.base / "bench/apps/demo_app").mkdir()
        result = self.shell('''
list_installed_apps() { echo frappe; }
app::detect_git_state() { app::reset_state; GIT_HEAD_STATE=branch; }
app::detect_installed_version() { echo 1.2.3; }
app::check
''')
        self.assertIn("installed=false", result.stdout)
        self.assertIn("head_state=branch", result.stdout)
        self.assertIn("git_head_state=branch", result.stdout)

    @unittest.skipUnless(shutil.which("jq"), "jq required")
    def test_dependency_failure_propagates_to_python_caller(self):
        for method in ("apt", "pip", "npm", "nvm", "script"):
            with self.subTest(method=method):
                dependency = dict(DEMO["dependencies"][0], install_method=method, version="18.20.8")
                self.env["SYSTEM_DEPENDENCIES"] = json.dumps([dependency])
                result = self.shell(f'deps::_resolve_{method}() {{ return 42; }}; deps::resolve_all', expected=None)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("deps_resolved=true", result.stdout)

    def test_dependency_versions_are_passed_to_installers(self):
        result = self.shell('''
ensure_dpkg() { return 1; }
apt_install() { printf 'APT=%s\\n' "$*"; }
pip() { printf 'PIP=%s\\n' "$*"; }
deps::_resolve_apt demo-package '' 1.2.3
deps::_resolve_pip demo-package 1.2.3
''')
        self.assertIn("APT=demo-package=1.2.3", result.stdout)
        self.assertIn("PIP=install demo-package==1.2.3", result.stdout)

    def test_fixed_tag_upgrade_dispatches_to_tag_checkout(self):
        self.env.update(INSTALL_MODE="fixed", REF_TYPE="tag")
        self.env.pop("SOURCE_REF")
        result = self.shell('app::downgrade() { echo TAG_CHECKOUT; }; app::upgrade')
        self.assertIn("TAG_CHECKOUT", result.stdout)

    @unittest.skipUnless(shutil.which("jq"), "jq required")
    def test_site_install_reuses_code_shared_by_other_sites(self):
        code = self.base / "bench/apps/demo_app"
        code.mkdir()
        (code / "keep.txt").write_text("shared code")
        self.env["APPS_JSON"] = json.dumps([{"app_name": "demo_app", "repo_url": "https://example.test/demo_app", "ref_type": "branch", "install_strategy": "git_branch"}])
        result = self.shell('''
bench_exec() {
    if [ "$1" = version ]; then echo 'frappe 15.1.0';
    elif [[ "$*" = *list-apps* ]]; then echo frappe;
    else printf 'CALL=%s\\n' "$*"; fi
}
clear_cache_and_reload() { :; }
site::install_apps
''')
        self.assertTrue((code / "keep.txt").exists())
        self.assertIn("install-app demo_app", result.stdout)
        self.assertNotIn("get-app", result.stdout)

    def test_remove_app_uninstalls_data_before_deleting_code(self):
        code = self.base / "bench/apps/demo_app"
        code.mkdir()
        result = self.shell('''
list_installed_apps() { echo demo_app; }
bench_exec() {
    if [[ "$*" = *uninstall-app* ]]; then [ -d "$BENCH_DIR/apps/demo_app" ] || return 99; fi
    printf 'CALL=%s\\n' "$*"
}
clear_cache_and_reload() { :; }
app::remove
''')
        self.assertIn("uninstall-app demo_app --yes --force", result.stdout)
        self.assertIn("removed=true", result.stdout)
        self.assertFalse(code.exists())

    def test_remove_app_keeps_code_when_site_inspection_fails(self):
        code = self.base / "bench/apps/demo_app"
        code.mkdir()
        result = self.shell('list_installed_apps() { return 42; }; app::remove', expected=None)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(code.exists())

    def test_remove_app_rejects_path_traversal(self):
        self.env["APP_NAME"] = "../../other"
        result = self.shell('app::remove', expected=None)
        self.assertNotEqual(result.returncode, 0)

    @unittest.skipUnless(shutil.which("jq"), "jq required")
    def test_dependency_skip_and_count_outputs(self):
        self.env["SYSTEM_DEPENDENCIES"] = json.dumps(DEMO["dependencies"][:1])
        result = self.shell('deps::_resolve_apt() { DEPS_ACTION=already_installed; }; deps::resolve_all')
        self.assertIn("deps_skipped=1", result.stdout)
        self.assertIn("deps_total=1", result.stdout)

    @unittest.skipUnless(shutil.which("jq"), "jq required")
    def test_invalid_dependencies_are_not_reported_successful(self):
        self.env["SYSTEM_DEPENDENCIES"] = '{"not":"an array"}'
        result = self.shell('deps::resolve_all', expected=None)
        self.assertNotEqual(result.returncode, 0)

    def test_site_enumeration_ignores_metadata_and_assets(self):
        (self.base / "bench/sites/apps.txt").write_text("frappe\n")
        (self.base / "bench/sites/assets").mkdir()
        (self.base / "bench/sites/currentsite.txt").write_text("demo.example.test")
        (self.base / "bench/sites/not-a-site").mkdir()
        self.assertEqual(self.shell('list_sites').stdout.strip(), "demo.example.test")

    def test_fresh_backup_covers_site_without_apps_txt_and_saves_keys(self):
        result = self.shell('''
bench_exec() {
    local target="${@: -1}"
    case "$target" in */private/backups/*) return 98 ;; esac
    printf demo > "$target/demo-database.sql.gz"
    printf demo > "$target/demo-files.tar"
    printf demo > "$target/demo-private-files.tar"
}
backup::all_sites
''')
        self.assertEqual(result.stdout.strip(), "1")
        backups = list((self.base / "backups/demo.example.test").iterdir())
        self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0] / "site_config.json").read_text(), '{"db_name":"demo_db", "encryption_key":"demo-key"}')
        self.assertEqual((backups[0] / "site_name").read_text().strip(), "demo.example.test")
        self.assertEqual(backups[0].stat().st_mode & 0o077, 0)

    def test_incomplete_backup_is_failure(self):
        result = self.shell('bench_exec() { :; }; backup::all_sites', expected=None)
        self.assertNotEqual(result.returncode, 0)

    def test_update_never_runs_after_failed_backup(self):
        result = self.shell('''
backup::all_sites() { return 42; }
bench_exec() { echo SHOULD_NOT_RUN; }
bench::update
''', expected=None)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("SHOULD_NOT_RUN", result.stdout)
        self.assertNotIn("bench_updated=true", result.stdout)

    def test_scoped_backup_returns_restore_directory(self):
        result = self.shell('backup::site() { echo /sandbox/snapshot; }; backup::run')
        self.assertIn("site_backup_dir=/sandbox/snapshot", result.stdout)
        self.assertIn("backups_taken=1", result.stdout)

    def test_destroy_does_not_clear_cache_on_deleted_site_or_drop_sql_twice(self):
        result = self.shell('''
init_site_log() { :; }
chown() { :; }
bench_exec() { [ "$1" = drop-site ] || return 98; }
db_drop_database_and_user() { echo DUPLICATE_SQL_DROP; return 98; }
reload_services() { echo SERVICES_RELOADED; }
site::destroy
''')
        self.assertIn("SERVICES_RELOADED", result.stdout)
        self.assertNotIn("DUPLICATE_SQL_DROP", result.stdout)

    def test_backup_pruning_is_site_scoped_and_keeps_latest(self):
        result = self.shell('''
for site in demo.example.test other.example.test; do
    for stamp in 20260101000000 20260102000000; do
        mkdir -p "$PROJECT_BASE_DIR/backups/$site/$stamp"
        echo "$site" > "$PROJECT_BASE_DIR/backups/$site/$stamp/site_name"
    done
done
backup::prune demo.example.test 1
''')
        self.assertFalse((self.base / "backups/demo.example.test/20260101000000").exists())
        self.assertTrue((self.base / "backups/demo.example.test/20260102000000").exists())
        self.assertTrue((self.base / "backups/other.example.test/20260101000000").exists())

    def test_bench_removal_keeps_databases_and_unrelated_nginx(self):
        (self.base / "nginx").mkdir()
        own = self.base / "nginx/tenant_demo.conf"
        own.write_text(str(self.base / "bench/sites"))
        unrelated = self.base / "nginx/other.conf"
        unrelated.write_text("/srv/other/bench/sites")
        result = self.shell('''
nginx() { :; }
mysql_exec() { echo SQL_SHOULD_NOT_RUN; return 98; }
bench_exec() { echo BENCH_SHOULD_NOT_RUN; return 98; }
bench::remove
''')
        self.assertIn("databases_kept=true", result.stdout)
        self.assertNotIn("SHOULD_NOT_RUN", result.stdout)
        self.assertFalse((self.base / "bench").exists())
        self.assertFalse(own.exists())
        self.assertTrue(unrelated.exists())
        self.assertTrue(list((self.base / "backups/removed-bench").glob("*/demo.example.test.json")))

    def test_remove_stop_only_keeps_files(self):
        self.env["REMOVE_STOP_ONLY"] = "true"
        result = self.shell('bench::remove')
        self.assertIn("stop_only=true", result.stdout)
        self.assertTrue((self.base / "bench").exists())

    def test_remove_rejects_unsafe_root(self):
        self.env["PROJECT_BASE_DIR"] = "/"
        result = self.shell('bench::remove', expected=None)
        self.assertNotEqual(result.returncode, 0)

    @unittest.skipUnless(shutil.which("jq"), "jq required")
    def test_restore_uses_real_bench_flags_and_restores_encryption_key(self):
        restore = self.base / "restore"
        restore.mkdir()
        for name in ("demo-database.sql.gz", "demo-files.tar", "demo-private-files.tar"):
            (restore / name).write_text("demo")
        (restore / "site_name").write_text("demo.example.test")
        (restore / "site_config.json").write_text('{"encryption_key":"original-key"}')
        self.env.update(RESTORE_DIR=str(restore), RESTORE_SKIP_BACKUP="true")
        result = self.shell('''
install() { local source="${@: -2:1}" target="${@: -1}"; cp -- "$source" "$target"; }
bench_exec() { printf 'BENCH=%s\\n' "$*"; }
clear_cache_and_reload() { :; }
bench::restore
''')
        self.assertIn("--with-public-files", result.stdout)
        self.assertIn("--with-private-files", result.stdout)
        self.assertIn("set-config encryption_key original-key", result.stdout)
        self.assertIn("site_restored=true", result.stdout)


if __name__ == "__main__":
    unittest.main()
