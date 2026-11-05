"""Opt-in helpers for the isolated Docker test sites. Never whitelisted."""

import json
import os
from pathlib import Path
import subprocess
import sys

import frappe


def _guard():
    if os.environ.get("DT_DOCKER_SANDBOX") != "1" or frappe.local.site != "master.localhost":
        raise RuntimeError("This helper can only run on the Docker sandbox master site")
    if frappe.conf.db_host != "db":
        raise RuntimeError("Sandbox database host must be the isolated db service")


def _ensure(doctype, filters, values):
    name = frappe.db.get_value(doctype, filters, "name")
    if name:
        return frappe.get_doc(doctype, name)
    return frappe.get_doc({"doctype": doctype, **values}).insert(ignore_permissions=True)


def seed():
    _guard()
    framework = _ensure("Frameworks", {"framework_name": "Frappe"}, {"framework_name": "Frappe"})
    version = _ensure("Framework Version", {"framework": framework.name, "version": "15"}, {
        "framework": framework.name, "version": "15", "python_version": f"{sys.version_info.major}.{sys.version_info.minor}",
        "node_version": subprocess.check_output(["node", "--version"], text=True).strip().lstrip("v"),
        "bench_package": "frappe-bench", "description": "Isolated Docker test snapshot",
    })
    node = _ensure("Infrastructure Node", {"node_name": "Docker Sandbox"}, {
        "node_name": "Docker Sandbox", "state": "Running", "status": "Active", "os_type": "Debian",
        "project_base_dir": "/home/frappe", "project_logs_dir": "/home/frappe/automation-logs",
        "scripts_base_dir": "/opt/dt-scripts", "ip_address": "runner", "ssh_user": "frappe", "ssh_port": 22,
        "access_verified": 1, "frappe_version": version.name,
        "access_notes": "Docker-only. Do not run OS provisioning, mail setup or bench code upgrades here.",
    })
    extension = _ensure("Extension", {"app_name": "dt_master"}, {
        "extension_name": "Sandbox dt_master", "app_name": "dt_master", "install_mode": "rolling",
        "default_branch": "main", "repository_url": "/home/frappe/bench/apps/dt_master", "status": "Approved",
    })
    extension_version = _ensure("Extension Version", {"extension": extension.name, "version": "sandbox"}, {
        "extension": extension.name, "version": "sandbox", "source_ref": "main", "ref_type": "branch",
        "install_strategy": "git_branch", "status": "Approved",
    })
    site_config = json.loads(Path("/home/frappe/bench/sites/tenant.localhost/site_config.json").read_text())
    tenant = _ensure("Tenant Site", {"site_name": "Sandbox Tenant"}, {
        "site_name": "Sandbox Tenant", "fqdn": "tenant.localhost", "node": node.name,
        "status": "Active", "automation_state": "ready", "protocol": "http", "port": "8000",
        "database_name": site_config["db_name"], "admin_password": os.environ["ADMIN_PASSWORD"],
        "mysql_root_user_name": "root", "mysql_root_user_password": os.environ["DB_ROOT_PASSWORD"],
        "installed_apps": [{"app_name": "dt_master", "app_type": "Internal", "extension": extension.name,
                            "extension_version": extension_version.name, "enabled": 1, "status": "Pending"}],
    })
    frappe.db.commit()
    return {"node": node.name, "tenant": tenant.name, "master_site": "master.localhost", "tenant_site": "tenant.localhost"}


def smoke():
    """Exercise the real Python → SSH → shell backup contract, non-destructively."""
    _guard()
    frappe.set_user("Administrator")
    from dt_master.api.infrastructure_node import backup_bench

    node = frappe.db.get_value("Infrastructure Node", {"node_name": "Docker Sandbox"}, "name")
    if not node:
        raise RuntimeError("Run the sandbox setup first")
    result = backup_bench(node)
    if int(result.get("backups_taken", 0)) != 2:
        raise RuntimeError(f"Expected two sandbox site backups, got {result}")
    return {"sandbox_smoke": "passed", **result}
