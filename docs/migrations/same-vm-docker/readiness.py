#!/usr/bin/env python3
"""Additional read-only readiness checks; never exports credential values."""

import json
import os
from pathlib import Path
import shutil
import subprocess


BENCH = Path("/home/frappe/bench")


def run(args, env=None, timeout=15):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, env=env)
        return {"code": result.returncode, "output": result.stdout.strip()}
    except Exception as exc:
        return {"code": -1, "error": type(exc).__name__}


def query(site, sql):
    config = json.loads((BENCH / "sites" / site / "site_config.json").read_text())
    common = json.loads((BENCH / "sites/common_site_config.json").read_text())
    env = dict(os.environ, MYSQL_PWD=config["db_password"])
    client = shutil.which("mariadb") or shutil.which("mysql")
    result = run([client, "--connect-timeout=5", "--batch", "--skip-column-names",
                  "--host", str(config.get("db_host") or common.get("db_host") or "localhost"),
                  "--user", str(config.get("db_user") or config["db_name"]),
                  "--database", config["db_name"], "--execute", "START TRANSACTION READ ONLY; " + sql + "; COMMIT;"], env=env)
    if result["code"]:
        return {"available": False, "client_exit_code": result["code"]}
    return {"available": True, "rows": [row.split("\t") for row in result["output"].splitlines()]}


def main():
    pilot = "dalali.ndftechlabs.com"
    report = {"pilot": pilot, "read_only": True}
    queries = {
        "scheduler": "SELECT COUNT(*),SUM(stopped=0),MAX(last_execution) FROM `tabScheduled Job Type`",
        "timezone": "SELECT value FROM tabSingles WHERE doctype='System Settings' AND field='time_zone'",
        "outgoing_email_accounts": "SELECT COUNT(*) FROM `tabEmail Account` WHERE enable_outgoing=1",
        "enabled_webhooks": "SELECT COUNT(*) FROM tabWebhook WHERE enabled=1",
        "pending_email_queue": "SELECT status,COUNT(*) FROM `tabEmail Queue` WHERE status IN ('Not Sent','Sending') GROUP BY status",
        "database_settings": "SELECT VERSION(),@@sql_mode,@@character_set_server,@@collation_server,@@time_zone,@@lower_case_table_names,@@max_allowed_packet",
    }
    report["pilot_workload"] = {name: query(pilot, sql) for name, sql in queries.items()}
    report["control_plane"] = query("erp.ndftechlabs.com", "SELECT fqdn,node,automation_state,status FROM `tabTenant Site` WHERE fqdn IN ('dalali.ndftechlabs.com','demo.ndftechlabs.com','dulamanenterprise.ndftechlabs.com','erp.ndftechlabs.com')")
    report["native_nodes"] = query("erp.ndftechlabs.com", "SELECT name,project_base_dir,scripts_base_dir,access_verified,state,status FROM `tabInfrastructure Node` WHERE name IN (SELECT node FROM `tabTenant Site` WHERE fqdn IN ('dalali.ndftechlabs.com','demo.ndftechlabs.com','dulamanenterprise.ndftechlabs.com','erp.ndftechlabs.com'))")
    report["http_baseline"] = {}
    for site in (pilot, "demo.ndftechlabs.com", "dulamanenterprise.ndftechlabs.com", "erp.ndftechlabs.com"):
        # Normal read-only GET; no cookies, authentication or integration calls.
        report["http_baseline"][site] = run(["curl", "--silent", "--show-error", "--max-time", "10",
                                           "--resolve", f"{site}:443:127.0.0.1", "--output", "/dev/null",
                                           "--write-out", "%{http_code}", f"https://{site}/api/method/ping"])
    report["rootless"] = {}
    for name, path in {"cgroup_controllers": "/sys/fs/cgroup/cgroup.controllers",
                       "max_user_namespaces": "/proc/sys/user/max_user_namespaces",
                       "unprivileged_userns": "/proc/sys/kernel/unprivileged_userns_clone",
                       "apparmor_userns_restriction": "/proc/sys/kernel/apparmor_restrict_unprivileged_userns"}.items():
        file = Path(path)
        report["rootless"][name] = file.read_text().strip() if file.is_file() else None
    report["rootless"]["tools"] = {name: shutil.which(name) for name in ("newuidmap", "newgidmap", "slirp4netns", "rootlesskit", "fuse-overlayfs")}
    report["rootless"]["dedicated_user_exists"] = run(["id", "dt-containers"])["code"] == 0
    report["rootless"]["prerequisite_package_simulation"] = run(["apt-get", "--simulate", "--no-install-recommends", "install", "uidmap", "slirp4netns"], timeout=30)
    report["tool_versions"] = {name: run([name, "--version"]) if shutil.which(name) else None for name in ("wkhtmltopdf", "yarn", "cloudflared")}
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
