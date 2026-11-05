"""Shared Python → shell contract. No remote work happens during import."""

import base64
import json
import re
import shlex
import subprocess
from pathlib import PurePosixPath
from queue import Queue
from threading import Thread

import frappe


def path_environment(node):
    for field in ("scripts_base_dir", "project_base_dir", "project_logs_dir"):
        value = getattr(node, field, None)
        if not value or not str(value).startswith("/") or "\n" in str(value) or "\0" in str(value):
            frappe.throw(f"An absolute {field} must be configured on the node")
    return {
        "PROJECT_BASE_DIR": node.project_base_dir,
        "PROJECT_LOGS_DIR": node.project_logs_dir,
        "SCRIPT_LIB_DIR": f"{node.scripts_base_dir.rstrip('/')}/lib",
        "BASE_DIR": node.scripts_base_dir,
        "BENCH_DIR": f"{node.project_base_dir.rstrip('/')}/bench",
        "FRAPPE_HOME": "/home/frappe",
    }


def framework_environment(node):
    env = path_environment(node)
    version = frappe.get_doc("Framework Version", node.frappe_version)
    context = {
        key: getattr(version, key, None)
        for key in ("name", "framework", "version", "python_version", "node_version", "bench_package", "description")
    }
    env.update({
        "FRAPPE_VERSION": context["version"],
        "FRAMEWORK_NAME": context["framework"],
        "PYTHON_VERSION": context["python_version"],
        "NODE_VERSION": context["node_version"],
        "BENCH_PACKAGE": context["bench_package"],
        "FRAMEWORK_CONTEXT": json.dumps(context),
        "FRAMEWORK_CONTEXT_B64": base64.b64encode(json.dumps(context).encode()).decode(),
    })
    return env


def tenant_environment(node, tenant):
    env = framework_environment(node)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", tenant.fqdn or ""):
        frappe.throw("Invalid tenant site name")
    protocol = (tenant.protocol or "https").lower().removesuffix("://")
    if protocol not in {"http", "https"}:
        frappe.throw("Tenant protocol must be http or https")
    env.update({
        "SITE_NAME": tenant.fqdn,
        "DB_NAME": tenant.database_name,
        "ADMIN_PASSWORD": tenant.admin_password,
        "MYSQL_ROOT_USER": tenant.mysql_root_user_name,
        "MYSQL_ROOT_PASSWORD": tenant.mysql_root_user_password,
        "FRAPPE_UPSTREAM_PORT": tenant.port,
        "TENANT_PROTOCOL": protocol,
        "LOCAL_HOSTS_ENTRY": "true" if protocol == "http" else "false",
    })
    return env


def tag_ref(version):
    """Prefix numeric release versions once; preserve non-semver tag names."""
    value = (version or "").strip()
    if re.match(r"^\d+\.\d+\.\d+", value):
        return f"v{value}"
    return value


def validate_app_name(name):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name or ""):
        frappe.throw("Invalid app name")
    return name


def validate_app_rows(rows):
    seen = set()
    for row in rows or []:
        name = validate_app_name(row.get("app_name"))
        if name in seen:
            frappe.throw(f"App '{name}' may appear only once on a Tenant Site")
        seen.add(name)


def app_environment(node, tenant, row):
    env = tenant_environment(node, tenant)
    validate_app_name(row.app_name)
    extension_version = extension = None
    if row.extension_version:
        extension_version = frappe.get_doc("Extension Version", row.extension_version)
        extension = frappe.get_doc("Extension", extension_version.extension)
    env.update({
        "APP_NAME": row.app_name,
        "TARGET_VERSION": tag_ref(row.target_version) or (
            extension_version.source_ref if extension_version and extension_version.ref_type == "tag" else ""
        ),
        "INSTALL_MODE": extension.install_mode if extension else "",
        "SOURCE_REF": extension_version.source_ref if extension_version else "",
        "REF_TYPE": extension_version.ref_type if extension_version else "",
        "REPOSITORY_URL": extension.repository_url if extension else "",
    })
    return env


def mail_environment(node, server, mail_user=None):
    env = path_environment(node)
    for value in (server.domain, server.hostname):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", value or ""):
            frappe.throw("Invalid mail domain or hostname")
    smtp_port, imap_port = int(server.smtp_port or 587), int(server.imap_port or 993)
    if not (1 <= smtp_port <= 65535 and 1 <= imap_port <= 65535):
        frappe.throw("Mail ports must be between 1 and 65535")
    env.update({
        "MAIL_DOMAIN": server.domain,
        "MAIL_HOSTNAME": server.hostname,
        "MAIL_IP": node.ip_address,
        "SMTP_PORT": smtp_port,
        "IMAP_PORT": imap_port,
        "DKIM_SELECTOR": server.dkim_selector,
    })
    if server.tenant_site:
        tenant = frappe.get_doc("Tenant Site", server.tenant_site)
        env.update({
            "MYSQL_ROOT_USER": tenant.mysql_root_user_name,
            "MYSQL_ROOT_PASSWORD": tenant.mysql_root_user_password,
        })
    if mail_user:
        local, separator, domain = mail_user.email.partition("@")
        if not separator or domain.lower() != server.domain.lower() or not re.fullmatch(r"[A-Za-z0-9_.+-]+", local):
            frappe.throw("Mailbox address must belong to the mail server domain")
        env["MAIL_USER"] = local
        env["MAIL_PASSWORD"] = mail_user.get_password("password") if mail_user.password else ""
    return env


def parse_output(stdout):
    """Only accept bare contract lines, never timestamped logs or shell output."""
    result = {}
    for line in stdout.splitlines():
        match = re.fullmatch(r"([a-z][a-z0-9_]*)=(.*)", line)
        if match:
            result[match[1]] = match[2].strip()
    return result


def build_remote_command(env, script_path):
    exports = []
    for key, value in env.items():
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            raise ValueError(f"Invalid environment key: {key}")
        exports.append(f"export {key}={shlex.quote(str(value) if value is not None else '')}; ")
    payload = "".join(exports) + f"exec bash {shlex.quote(str(script_path))}"
    return f"sudo -n bash -c {shlex.quote(payload)}"


def run_remote_script(node, ssh, category, script_name, env, event, context):
    """Drain both pipes concurrently; preserve existing event names and result tuple."""
    if not node.scripts_base_dir or not str(node.scripts_base_dir).startswith("/"):
        frappe.throw("An absolute scripts base directory must be configured on the node")
    if category not in {"node", "site", "app", "mail"} or not re.fullmatch(r"[a-z_]+\.sh", script_name):
        raise ValueError("Invalid script path")
    for key in ("host", "user"):
        if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.:@-]*", ssh[key]):
            frappe.throw(f"Invalid SSH {key}")
    port = int(ssh["port"])
    if not 1 <= port <= 65535:
        frappe.throw("Invalid SSH port")
    script = PurePosixPath(node.scripts_base_dir) / "bin" / category / script_name
    env = dict(env, SCRIPTS_BASE_DIR=str(script.parent), SCRIPT_BASE_DIR=str(script.parent))
    command = ["ssh", "-o", "BatchMode=yes", "-p", str(port), f"{ssh['user']}@{ssh['host']}", build_remote_command(env, script)]
    proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
    queue = Queue()

    def drain(pipe, stream):
        try:
            for line in iter(pipe.readline, ""):
                queue.put((stream, line.rstrip("\r\n")))
        finally:
            pipe.close()
            queue.put((stream, None))

    threads = [Thread(target=drain, args=(getattr(proc, stream), stream), daemon=True) for stream in ("stdout", "stderr")]
    for thread in threads:
        thread.start()
    secrets = [str(value) for key, value in env.items() if value and any(word in key for word in ("PASSWORD", "SECRET", "TOKEN"))]
    output = {"stdout": [], "stderr": []}
    finished = 0
    while finished < 2:
        stream, line = queue.get()
        if line is None:
            finished += 1
            continue
        for secret in sorted(secrets, key=len, reverse=True):
            line = line.replace(secret, "[REDACTED]")
        output[stream].append(line)
        frappe.publish_realtime(event, {**context, "line": line, "stream": stream}, user=frappe.session.user)
    for thread in threads:
        thread.join()
    proc.wait()
    frappe.publish_realtime(event, {**context, "status": "Completed" if proc.returncode == 0 else "Failed"}, user=frappe.session.user)
    return proc.returncode, "\n".join(output["stdout"]), "\n".join(output["stderr"])
