"""Real integration tests for the opt-in Docker sandbox, never whitelisted.

Uses the real API executors/SSH/scripts. Destructive tests target only a newly
created, uniquely marked Frappe site; never the master or existing tenant data.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
import re
from pathlib import Path
import subprocess
import time
import traceback
from unittest.mock import patch
from urllib.request import Request, urlopen
from uuid import uuid4

import frappe

from dt_master.api import infrastructure_node as nodes
from dt_master.api import tenant_installed_app_executor as apps
from dt_master.api import tenant_site_executor as sites
from dt_master.testing.docker_sandbox import _guard


def _scrub(value):
    text = str(value)
    for name in ("ADMIN_PASSWORD", "DB_ROOT_PASSWORD"):
        secret = os.environ.get(name)
        if secret:
            text = text.replace(secret, "[REDACTED]")
    return text


def _bench(site, *args, timeout=600):
    result = subprocess.run(
        ["bench", "--site", site, *args], cwd="/home/frappe/bench",
        text=True, capture_output=True, timeout=timeout,
    )
    if result.returncode:
        raise RuntimeError(_scrub((result.stderr or result.stdout)[-6000:]))
    return result.stdout.strip()


def _execute(site, method, **kwargs):
    output = _bench(site, "execute", method, "--kwargs", json.dumps(kwargs))
    return json.loads(output) if output else None


def _http(site):
    request = Request("http://127.0.0.1:8000/api/method/ping", headers={"Host": site})
    with urlopen(request, timeout=15) as response:
        assert json.load(response)["message"] == "pong", "HTTP ping failed"


def _row(tenant_name):
    tenant = frappe.get_doc("Tenant Site", tenant_name)
    return tenant, next(row for row in tenant.installed_apps if row.app_name == "dt_master")


def naming_regression():
    """Check the real DocType naming/validation transactionally, leaving no rows."""
    _guard()
    frappe.set_user("Administrator")
    token = uuid4().hex[:10]
    try:
        docs = []
        for suffix in ("a", "b"):
            doc = frappe.get_doc({
                "doctype": "Tenant Site", "site_name": f"Naming Regression {token} {suffix}",
                "fqdn": f"naming-{token}-{suffix}.localhost",
                "installed_apps": [{"app_name": "dt_master", "status": "Pending"}],
            }).insert(ignore_permissions=True)
            docs.append(doc)
        assert docs[0].installed_apps[0].name != docs[1].installed_apps[0].name, "Child names collided across tenants"
        docs[0].append("installed_apps", {"app_name": "dt_master", "status": "Pending"})
        try:
            docs[0].save(ignore_permissions=True)
        except frappe.ValidationError:
            return {"unique_child_names": "passed", "duplicate_app_rejected": "passed"}
        raise AssertionError("Duplicate apps were accepted within one tenant")
    finally:
        frappe.db.rollback()


def _assert_app(tenant_name, expected):
    tenant, row = _row(tenant_name)
    output = apps.check_app(tenant.name, row.name)
    assert output["installed"] == str(expected).lower(), "App inspection disagrees with site installation"
    assert frappe.get_doc("Tenant Site", tenant_name).installed_apps[0].status == (
        "Installed" if expected else "Uninstalled"
    ), "App row did not reconcile"
    return output


def _app_cycle(tenant_name, record):
    def install():
        tenant, row = _row(tenant_name)
        apps.install_app(tenant.name, row.name)
        return _assert_app(tenant_name, True)

    def uninstall():
        tenant, row = _row(tenant_name)
        apps.uninstall_app(tenant.name, row.name)
        return _assert_app(tenant_name, False)

    def check_version():
        tenant, row = _row(tenant_name)
        result = apps.check_version(tenant.name, row.name)
        assert result.get("installed_version"), "Installed version was not reported"
        return result

    tenant, row = _row(tenant_name)
    actual = {line.split()[0] for line in _bench(tenant.fqdn, "list-apps").splitlines() if line.split()}
    if "dt_master" in actual:
        # Only called on the uniquely marked disposable fixture, never the
        # original tenant. A resumed run may have stopped after installation.
        _assert_app(tenant_name, True)
        if not record("cleanup_prior_fixture_app", uninstall):
            return
    record("inspect_pending_fixture_app", lambda: _assert_app(tenant_name, False))

    if record("install_app", install):
        record("check_installed_version", check_version)
        if record("uninstall_app", uninstall):
            if record("install_previously_uninstalled_app", install):
                record("uninstall_test_app_final", uninstall)


@contextmanager
def _events(records):
    original = frappe.publish_realtime

    def publish(event, message=None, **kwargs):
        if event in {"tenant_app_log", "tenant_script_log", "script_execution_log", "mail_executor_log"}:
            records.append({"event": event, "keys": sorted((message or {}).keys()),
                            "status": (message or {}).get("status")})
        return original(event, message, **kwargs)

    with patch.object(frappe, "publish_realtime", publish):
        yield


def run(resume_run_id=None):
    _guard()
    frappe.set_user("Administrator")
    if resume_run_id and not re.fullmatch(r"[a-f0-9]{10}", resume_run_id):
        raise ValueError("Invalid integration run ID")
    run_id = resume_run_id or uuid4().hex[:10]
    fixture_site = f"integration-{run_id}.localhost"
    fixture_name = f"Integration {run_id}"
    report = {"run_id": run_id, "fixture_site": fixture_site,
              "started_at": datetime.now(timezone.utc).isoformat(), "tests": [],
              "not_covered": ["OS provisioning", "Git-based bench upgrades", "bench removal", "mail services", "Nginx/Supervisor configuration"]}
    events = []
    report_dir = Path("/home/frappe/automation-logs/integration")
    report_dir.mkdir(parents=True, exist_ok=True)
    report_file = report_dir / f"{run_id}-{int(time.time())}.json"
    if resume_run_id:
        config_path = Path(f"/home/frappe/bench/sites/{fixture_site}/site_config.json")
        if not config_path.is_file() or json.loads(config_path.read_text()).get("dt_integration_run") != run_id:
            raise RuntimeError("Cannot resume: disposable site is missing or its marker does not match")
        report["resumed_run_id"] = resume_run_id

    def record(name, operation):
        start = time.monotonic()
        try:
            result = operation()
        except Exception as exc:
            # Failed child inserts must not leave half-created master records
            # pending in the test session's transaction.
            frappe.db.rollback()
            report["tests"].append({"name": name, "status": "FAIL", "seconds": round(time.monotonic() - start, 2),
                                    "error": _scrub(exc), "traceback": _scrub(traceback.format_exc())})
            print(f"[integration] FAIL {name}: {_scrub(exc)}", flush=True)
            result = None
        else:
            report["tests"].append({"name": name, "status": "PASS", "seconds": round(time.monotonic() - start, 2)})
            print(f"[integration] PASS {name}", flush=True)
        report_file.write_text(json.dumps(report, indent=2))
        return result

    node_name = frappe.db.get_value("Infrastructure Node", {"node_name": "Docker Sandbox"}, "name")
    tenant_name = frappe.db.get_value("Tenant Site", {"fqdn": "tenant.localhost"}, "name")
    assert node_name and tenant_name, "Sandbox demo records are missing"
    assert frappe.get_doc("Tenant Site", tenant_name).node == node_name, "Tenant belongs to another node"

    with _events(events):
        record("master_http", lambda: _http("master.localhost"))
        record("tenant_http", lambda: _http("tenant.localhost"))
        backup = record("tenant_pretest_backup", lambda: nodes.backup_bench(node_name, tenant_name))
        if not backup or backup.get("backups_taken") != "1":
            raise RuntimeError(f"Pre-test backup failed; no mutations attempted. Report: {report_file}")

        actual_apps = {line.split()[0] for line in _bench("tenant.localhost", "list-apps").splitlines() if line.split()}
        originally_installed = "dt_master" in actual_apps
        report["tenant_app_installed_before"] = originally_installed
        record("inspect_existing_tenant_app", lambda: _assert_app(tenant_name, originally_installed))

        def provision_fixture():
            assert fixture_site.startswith("integration-") and fixture_site not in {"master.localhost", "tenant.localhost"}
            if not resume_run_id:
                result = subprocess.run([
                    "bench", "new-site", fixture_site, "--db-root-username", "root",
                    "--db-root-password", os.environ["DB_ROOT_PASSWORD"],
                    "--admin-password", os.environ["ADMIN_PASSWORD"], "--mariadb-user-host-login-scope", "%",
                    "--install-app", "erpnext",
                ], cwd="/home/frappe/bench", text=True, capture_output=True, timeout=600)
                if result.returncode:
                    raise RuntimeError(_scrub((result.stderr or result.stdout)[-6000:]))
                _bench(fixture_site, "set-config", "dt_integration_run", run_id)
            config = json.loads(Path(f"/home/frappe/bench/sites/{fixture_site}/site_config.json").read_text())
            values = {
                "doctype": "Tenant Site", "site_name": fixture_name, "fqdn": fixture_site,
                "node": node_name, "status": "Active", "automation_state": "ready", "protocol": "http", "port": "8000",
                "database_name": config["db_name"], "admin_password": os.environ["ADMIN_PASSWORD"],
                "mysql_root_user_name": "root", "mysql_root_user_password": os.environ["DB_ROOT_PASSWORD"],
                "installed_apps": [{"app_name": "dt_master", "app_type": "Internal", "enabled": 1, "status": "Pending",
                                    "extension": _row(tenant_name)[1].extension,
                                    "extension_version": _row(tenant_name)[1].extension_version}],
            }
            name = frappe.db.get_value("Tenant Site", {"fqdn": fixture_site}, "name")
            if name:
                doc = frappe.get_doc("Tenant Site", name)
                assert resume_run_id and doc.node == node_name, "Refusing to reuse an unrelated tenant record"
                _bench(fixture_site, "set-maintenance-mode", "off")
                _bench(fixture_site, "enable-scheduler")
                doc.automation_state, doc.status = "ready", "Active"
                doc.save(ignore_permissions=True)
            else:
                doc = frappe.get_doc(values).insert(ignore_permissions=True)
            frappe.db.commit()
            return doc.name

        fixture = record("provision_disposable_fixture", provision_fixture)
        if fixture:
            _app_cycle(fixture, record)
            token = f"contract-fixture-{run_id}"
            secret = uuid4().hex

            def seed_data():
                todo = _execute(fixture_site, "frappe.client.insert", doc={"doctype": "ToDo", "description": token})
                public = _execute(fixture_site, "frappe.client.insert", doc={"doctype": "File", "file_name": f"{token}-public.txt", "is_private": 0, "content": token + " public"})
                private = _execute(fixture_site, "frappe.client.insert", doc={"doctype": "File", "file_name": f"{token}-private.txt", "is_private": 1, "content": token + " private"})
                _execute(fixture_site, "frappe.utils.password.set_encrypted_password", doctype="User", name="Administrator", pwd=secret, fieldname="api_secret")
                return {"todo": todo["name"], "public": public, "private": private}

            data = record("seed_real_records_files_and_encrypted_secret", seed_data)

            def suspend_resume():
                sites.suspend_site(fixture)
                doc = frappe.get_doc("Tenant Site", fixture)
                assert (doc.automation_state, doc.status) == ("idle", "Suspended"), "Suspend transition failed"
                config = json.loads(Path(f"/home/frappe/bench/sites/{fixture_site}/site_config.json").read_text())
                assert config.get("maintenance_mode"), "Maintenance mode was not enabled"
                sites.resume_site(fixture)
                doc = frappe.get_doc("Tenant Site", fixture)
                assert (doc.automation_state, doc.status) == ("ready", "Active"), "Resume transition failed"
                _http(fixture_site)
                return True

            record("suspend_and_resume_fixture", suspend_resume)
            record("migrate_fixture_with_backup", lambda: nodes.migrate_bench(node_name, fixture))
            snapshot = record("backup_fixture_with_artifacts", lambda: nodes.backup_bench(node_name, fixture))
            restore_ok = False
            if data and snapshot:
                directory = snapshot.get("site_backup_dir")
                if not directory:
                    record("backup_returns_exact_restore_directory", lambda: (_ for _ in ()).throw(
                        AssertionError("Scoped backup did not return site_backup_dir")))
                else:
                    def reject_bad_restore():
                        try:
                            nodes.restore_backup(node_name, fixture, "/home/frappe/backups")
                        except frappe.ValidationError:
                            return True
                        raise AssertionError("Restore accepted an invalid/missing manifest")

                    record("reject_invalid_restore_manifest", reject_bad_restore)

                    def mutate_and_restore():
                        for file in (data["public"], data["private"]):
                            _execute(fixture_site, "frappe.client.delete", doctype="File", name=file["name"])
                        _execute(fixture_site, "frappe.client.delete", doctype="ToDo", name=data["todo"])
                        late = _execute(fixture_site, "frappe.client.insert", doc={"doctype": "ToDo", "description": token + " after backup"})
                        from cryptography.fernet import Fernet
                        _bench(fixture_site, "set-config", "encryption_key", Fernet.generate_key().decode())
                        result = nodes.restore_backup(node_name, fixture, directory)
                        assert result.get("site_restored") == "true", "Restore did not confirm success"
                        todo = _execute(fixture_site, "frappe.client.get", doctype="ToDo", name=data["todo"])
                        assert todo["description"] == token, "Original database record was not restored"
                        late_exists = _execute(fixture_site, "frappe.client.get_list", doctype="ToDo", filters={"name": late["name"]}, fields=["name"])
                        assert not late_exists, "Post-backup record survived database restore"
                        for file, suffix in ((data["public"], " public"), (data["private"], " private")):
                            restored = _execute(fixture_site, "frappe.client.get", doctype="File", name=file["name"])
                            path = Path(f"/home/frappe/bench/sites/{fixture_site}") / (
                                "private/files" if file["is_private"] else "public/files"
                            ) / Path(restored["file_url"]).name
                            assert path.read_text() == token + suffix, "Restored file content does not match"
                        decrypted = _execute(fixture_site, "frappe.utils.password.get_decrypted_password", doctype="User", name="Administrator", fieldname="api_secret")
                        assert decrypted == secret, "Original encryption key/secret was not recovered"
                        return True

                    restore_ok = bool(record("restore_database_public_private_files_and_encryption_key", mutate_and_restore))

            if restore_ok:
                def destroy_fixture():
                    config_path = Path(f"/home/frappe/bench/sites/{fixture_site}/site_config.json")
                    config = json.loads(config_path.read_text())
                    assert config.get("dt_integration_run") == run_id, "Refusing teardown: fixture marker mismatch"
                    sites.destroy_site(fixture)
                    doc = frappe.get_doc("Tenant Site", fixture)
                    assert (doc.automation_state, doc.status) == ("idle", "Archived"), "Teardown outcome was not persisted"
                    assert not config_path.exists(), "Site directory still exists"
                    # MariaDB is reached through Docker DNS; only check this
                    # run's database/account, never enumerate other data.
                    env = dict(os.environ, MYSQL_PWD=os.environ["DB_ROOT_PASSWORD"])
                    query = "SELECT COUNT(*) FROM information_schema.SCHEMATA WHERE SCHEMA_NAME='" + config["db_name"] + "'"
                    result = subprocess.run(["mysql", "-h", "db", "-u", "root", "-N", "-e", query],
                                            env=env, capture_output=True, text=True, check=True)
                    assert result.stdout.strip() == "0", "Database was not dropped"
                    return True

                record("destroy_only_marked_fixture", destroy_fixture)
            else:
                report["fixture_retained"] = "Restore was not verified; preserving fixture for investigation"

        def validate_events():
            expected = {"tenant_app_log": {"tenant", "row"}, "tenant_script_log": {"tenant"},
                        "script_execution_log": {"node", "execution"}}
            for event, fields in expected.items():
                observed = [item for item in events if item["event"] == event]
                assert observed, f"No realtime events for {event}"
                assert all(fields <= set(item["keys"]) for item in observed), f"Missing identity fields in {event}"
                assert any(item["status"] == "Completed" for item in observed), f"No completion event for {event}"
            return True

        record("real_realtime_event_contracts", validate_events)
        record("master_http_after_tests", lambda: _http("master.localhost"))
        record("tenant_http_after_tests", lambda: _http("tenant.localhost"))

    report["passed"] = sum(test["status"] == "PASS" for test in report["tests"])
    report["failed"] = sum(test["status"] == "FAIL" for test in report["tests"])
    report["report_file"] = str(report_file)
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    report_file.write_text(json.dumps(report, indent=2))
    if report["failed"]:
        raise RuntimeError(f"{report['failed']} integration tests failed; report: {report_file}")
    return {key: value for key, value in report.items() if key != "tests"}
