#!/usr/bin/env python3
"""Read-only production inventory. Run via SSH with sudo python3 on stdin.

No installs, service changes, cache clears, Frappe imports or traffic changes.
Only allowlisted configuration values leave the server; passwords, keys, tokens
and raw configuration files are never included in the JSON output.
"""

import ast
import configparser
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
from urllib.parse import urlsplit


BENCH = Path("/home/frappe/bench")
PILOT = "dalali.ndftechlabs.com"


def command(args, timeout=15, env=None):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, env=env)
        return result.returncode, result.stdout.strip()
    except Exception:
        return -1, ""


def digest(path):
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def endpoint(value):
    if not value:
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme == "http_status":
            return "http_status:" + parsed.path
        return {"scheme": parsed.scheme, "host": parsed.hostname, "port": parsed.port}
    except (ValueError, TypeError):
        return {"configured": True, "details_omitted": True}


def json_file(path):
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (ValueError, OSError):
        return {}


def git(path, *args):
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0", GIT_PAGER="cat")
    return command(["git", "--no-optional-locks", "-c", f"safe.directory={path}",
                    "-c", "core.fsmonitor=false", "-C", str(path), *args], env=env)


def version_from_source(path):
    try:
        tree = ast.parse((path / path.name.replace("-", "_") / "__init__.py").read_text())
        for statement in tree.body:
            if isinstance(statement, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__version__" for t in statement.targets):
                if isinstance(statement.value, ast.Constant):
                    return statement.value.value
    except (OSError, SyntaxError):
        pass
    return None


def database_metadata(site, common):
    database = site.get("db_name", "")
    user = site.get("db_user") or database
    if not re.fullmatch(r"[A-Za-z0-9_]+", database) or not user or not site.get("db_password"):
        return {"available": False}
    client = shutil.which("mariadb") or shutil.which("mysql")
    if not client:
        return {"available": False}
    host = site.get("db_host") or common.get("db_host") or "localhost"
    port = str(site.get("db_port") or common.get("db_port") or 3306)
    env = dict(os.environ, MYSQL_PWD=site["db_password"])
    args = [client, "--connect-timeout=5", "--batch", "--skip-column-names", "--host", str(host),
            "--port", port, "--user", str(user), "--database", database]
    query = ("START TRANSACTION READ ONLY; "
             "SELECT COUNT(*),COALESCE(SUM(DATA_LENGTH+INDEX_LENGTH),0) FROM information_schema.TABLES "
             f"WHERE TABLE_SCHEMA='{database}'; COMMIT;")
    code, output = command(args + ["--execute", query], timeout=20, env=env)
    result = {"available": code == 0}
    if code == 0:
        fields = output.split()
        if len(fields) == 2 and all(field.isdigit() for field in fields):
            result.update(table_count=int(fields[0]), estimated_bytes=int(fields[1]))
    code, output = command(args + ["--execute", "START TRANSACTION READ ONLY; SELECT defvalue FROM tabDefaultValue WHERE defkey='installed_apps'; COMMIT;"], timeout=20, env=env)
    if code == 0:
        for line in output.splitlines():
            try:
                apps = json.loads(line)
                if isinstance(apps, list) and all(isinstance(app, str) and re.fullmatch(r"[A-Za-z0-9_]+", app) for app in apps):
                    result["installed_apps"] = apps
            except ValueError:
                continue
    code, output = command(args + ["--execute", "START TRANSACTION READ ONLY; SELECT TABLE_TYPE,COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() GROUP BY TABLE_TYPE; COMMIT;"], timeout=20, env=env)
    if code == 0:
        rows = [line.split("\t") for line in output.splitlines()]
        if rows and all(len(row) == 2 and row[1].isdigit() for row in rows):
            result["object_type_counts"] = {row[0]: int(row[1]) for row in rows}
    return result


def main():
    report = {"observed_at_utc": datetime.now(timezone.utc).isoformat(), "read_only": True,
              "hostname": command(["hostname"])[1], "pilot": PILOT, "bench": str(BENCH)}
    report["os"] = Path("/etc/os-release").read_text().splitlines()
    report["kernel"] = command(["uname", "-r"])[1]
    report["cpu_count"] = os.cpu_count()
    report["memory"] = {line.split(":")[0]: line.split(":", 1)[1].strip()
                        for line in Path("/proc/meminfo").read_text().splitlines()
                        if line.split(":")[0] in {"MemTotal", "MemAvailable", "SwapTotal", "SwapFree"}}
    report["load"] = list(os.getloadavg())
    usage = shutil.disk_usage("/")
    report["disk_bytes"] = {"total": usage.total, "used": usage.used, "free": usage.free}
    report["reboot_required"] = Path("/var/run/reboot-required").exists()
    report["tools"] = {name: shutil.which(name) for name in ("docker", "podman", "ufw", "nft", "iptables", "cloudflared")}
    report["services"] = {}
    for service in ("nginx", "supervisor", "mariadb", "redis-server", "postfix", "dovecot", "rspamd", "opendkim", "cloudflared", "pm2-root", "netdata", "fail2ban"):
        code, output = command(["systemctl", "show", service, "--property=Id,LoadState,ActiveState,SubState,MainPID,ActiveEnterTimestamp"])
        report["services"][service] = dict(line.split("=", 1) for line in output.splitlines() if "=" in line) if code == 0 else {"unavailable": True}
    report["supervisor_status"] = command(["supervisorctl", "status"])[1].splitlines()
    report["listeners"] = command(["ss", "-lntup"])[1].splitlines()
    report["routes"] = {}
    for family in ("-4", "-6"):
        code, output = command(["ip", family, "-j", "route", "show"])
        report["routes"][family] = json.loads(output) if code == 0 else []
    report["ip_forward"] = Path("/proc/sys/net/ipv4/ip_forward").read_text().strip()
    if shutil.which("ufw"):
        report["ufw_status"] = command(["ufw", "status"])[1].splitlines()
    report["iptables"] = {}
    if shutil.which("iptables"):
        report["iptables"]["version"] = command(["iptables", "--version"])[1]
        for chain in ("INPUT", "FORWARD", "OUTPUT"):
            code, output = command(["iptables", "-S", chain])
            report["iptables"][chain] = {"policy": next((line for line in output.splitlines() if line.startswith("-P ")), None),
                                          "rule_count": sum(line.startswith("-A ") for line in output.splitlines()), "available": code == 0}
    common = json_file(BENCH / "sites/common_site_config.json")
    report["common_config"] = {key: common[key] for key in (
        "db_host", "db_port", "webserver_port", "socketio_port", "background_workers", "gunicorn_workers",
        "dns_multitenant", "serve_default_site", "restart_supervisor_on_update", "developer_mode"
    ) if key in common}
    report["redis_endpoints"] = {key: endpoint(common.get(key)) for key in ("redis_cache", "redis_queue", "redis_socketio")}
    report["common_config_sha256"] = digest(BENCH / "sites/common_site_config.json")
    report["sites"] = []
    for directory in sorted((BENCH / "sites").iterdir()):
        path = directory / "site_config.json"
        if not path.is_file() or directory.is_symlink():
            continue
        config = json_file(path)
        item = {"site": directory.name, "config_sha256": digest(path), "database": config.get("db_name"),
                "config": {key: config[key] for key in ("db_type", "db_host", "db_port", "developer_mode", "maintenance_mode", "pause_scheduler", "disable_scheduler", "server_script_enabled") if key in config},
                "secret_presence": {key: bool(config.get(key)) for key in ("encryption_key", "backup_encryption_key", "db_password")},
                "database_metadata": database_metadata(config, common)}
        if directory.name == PILOT:
            item["file_bytes"] = {}
            for subdirectory in ("public/files", "private/files", "private/backups"):
                target = directory / subdirectory
                if target.is_dir():
                    code, output = command(["nice", "-n", "15", "ionice", "-c", "3", "du", "-s", "-B1", str(target)], timeout=20)
                    item["file_bytes"][subdirectory] = int(output.split()[0]) if code == 0 and output.split()[0].isdigit() else "unavailable"
        report["sites"].append(item)
    report["apps"] = []
    for path in sorted((BENCH / "apps").iterdir()):
        if not path.is_dir() or path.name.startswith("."):
            continue
        status_code, status = git(path, "status", "--porcelain=v1", "--untracked-files=normal")
        item = {"app": path.name, "source_version": version_from_source(path), "path": str(path),
                "symlink": path.is_symlink(), "git": git(path, "rev-parse", "--is-inside-work-tree")[0] == 0,
                "commit": git(path, "rev-parse", "HEAD")[1] or None,
                "branch": git(path, "branch", "--show-current")[1] or None,
                "dirty": bool(status) if status_code == 0 else None,
                "tracked_changes": sum(not line.startswith("??") for line in status.splitlines()),
                "untracked_changes": sum(line.startswith("??") for line in status.splitlines()),
                "dependency_file_hashes": {name: digest(path / name) for name in ("pyproject.toml", "requirements.txt", "package.json", "yarn.lock") if (path / name).is_file()}}
        report["apps"].append(item)
    python = BENCH / "env/bin/python"
    report["python_version"] = command([str(python), "--version"])[1]
    code, output = command([str(python), "-m", "pip", "list", "--format=json"], timeout=30)
    report["python_packages"] = json.loads(output) if code == 0 else []
    report["node_version_system"] = command(["node", "--version"])[1]
    report["supervisor_configuration"] = []
    parser = configparser.RawConfigParser()
    config_path = BENCH / "config/supervisor.conf"
    parser.read(config_path)
    for section in parser.sections():
        item = {"section": section}
        for key in ("directory", "user", "autostart", "autorestart", "numprocs", "stopasgroup", "killasgroup"):
            if parser.has_option(section, key):
                item[key] = parser.get(section, key)
        if parser.has_option(section, "command"):
            text = parser.get(section, "command")
            try:
                item["executable"] = shlex.split(text)[0]
            except ValueError:
                pass
            item["queue"] = (re.findall(r"--queue\s+([A-Za-z0-9_,.-]+)", text) or [None])[0]
            item["bind"] = (re.findall(r"(?:--bind[= ]+|-b\s+)([A-Za-z0-9_.:-]+)", text) or [None])[0]
        report["supervisor_configuration"].append(item)
    report["supervisor_config_sha256"] = digest(config_path)
    report["nginx_configs"] = []
    nginx_root = Path("/etc/nginx")
    paths = [nginx_root / "nginx.conf", *sorted((nginx_root / "conf.d").glob("*.conf")), *sorted((nginx_root / "sites-enabled").glob("*"))]
    for path in paths:
        if not path.is_file():
            continue
        directives = []
        for line in path.read_text().splitlines():
            line = line.strip()
            if re.match(r"^(listen|server_name|proxy_pass|alias|root|upstream)\s", line):
                if line.startswith("proxy_pass"):
                    directives.append({"proxy_pass": endpoint(line.split(None, 1)[1].rstrip(";"))})
                else:
                    directives.append(line)
        report["nginx_configs"].append({"path": str(path), "resolved": str(path.resolve()), "sha256": digest(path), "directives": directives})
    report["cloudflare"] = {"config_files": []}
    for path in (Path("/etc/cloudflared/config.yml"), Path("/etc/cloudflared/config.yaml")):
        if path.is_file():
            routes = []
            for line in path.read_text().splitlines():
                match = re.match(r"\s*-?\s*(hostname|service):\s*(.+?)\s*$", line)
                if match:
                    key, value = match.groups()
                    value = value.strip("\"'")
                    routes.append({key: endpoint(value) if key == "service" else value})
            report["cloudflare"]["config_files"].append({"path": str(path), "sha256": digest(path), "routes": routes})
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
