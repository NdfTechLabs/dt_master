"""Shared Python → shell contract. No remote work happens during import."""

import base64
import codecs
import json
import os
import re
import shlex
import signal
import subprocess
from pathlib import PurePosixPath
from queue import Empty, Queue
from threading import Thread
from time import monotonic

from dt_master.api.ssh_transport import build_ssh_argv

import frappe


def secret(doc, fieldname):
    """Decrypted value of a Password field; empty string when unset."""
    return doc.get_password(fieldname, raise_exception=False) or ""


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
        "ADMIN_PASSWORD": secret(tenant, "admin_password"),
        "MYSQL_ROOT_USER": tenant.mysql_root_user_name,
        "MYSQL_ROOT_PASSWORD": secret(tenant, "mysql_root_user_password"),
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
            "MYSQL_ROOT_PASSWORD": secret(tenant, "mysql_root_user_password"),
        })
    if mail_user:
        local, separator, domain = mail_user.email.partition("@")
        if not separator or domain.lower() != server.domain.lower() or not re.fullmatch(r"[A-Za-z0-9_.+-]+", local):
            frappe.throw("Mailbox address must belong to the mail server domain")
        env["MAIL_USER"] = local
        env["MAIL_PASSWORD"] = secret(mail_user, "password")
    return env


def parse_output(stdout):
    """Only accept bare contract lines, never timestamped logs or shell output."""
    result = {}
    for line in stdout.splitlines():
        match = re.fullmatch(r"([a-z][a-z0-9_]*)=(.*)", line)
        if match:
            result[match[1]] = match[2].strip()
    return result


def build_remote_command(env, script_path, use_sudo=True):
    exports = []
    for key, value in env.items():
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            raise ValueError(f"Invalid environment key: {key}")
        exports.append(f"export {key}={shlex.quote(str(value) if value is not None else '')}; ")
    payload = "".join(exports) + f"exec bash {shlex.quote(str(script_path))}"
    command = f"bash -c {shlex.quote(payload)}"
    return f"sudo -n {command}" if use_sudo else command


def run_remote_script(node, ssh, category, script_name, env, event, context, use_sudo=True, timeout_seconds=300, max_output_bytes=2_000_000):
    """Drain both pipes concurrently; preserve existing event names and result tuple."""
    if not node.scripts_base_dir or not str(node.scripts_base_dir).startswith("/"):
        frappe.throw("An absolute scripts base directory must be configured on the node")
    if category not in {"node", "site", "app", "mail", "docker"} or not re.fullmatch(r"[a-z_]+\.sh", script_name):
        raise ValueError("Invalid script path")
    if not ssh.get("host_alias"):
        for key in ("host", "user"):
            if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.:@-]*", str(ssh.get(key) or "")):
                frappe.throw(f"Invalid SSH {key}")
        try:
            port = int(ssh.get("port") or 22)
        except (TypeError, ValueError):
            frappe.throw("Invalid SSH port")
        if not 1 <= port <= 65535:
            frappe.throw("Invalid SSH port")
    script = PurePosixPath(node.scripts_base_dir) / "bin" / category / script_name
    env = dict(env, SCRIPTS_BASE_DIR=str(script.parent), SCRIPT_BASE_DIR=str(script.parent))
    command = build_ssh_argv(ssh, build_remote_command(env, script, use_sudo=use_sudo))
    if not isinstance(timeout_seconds, (int, float)) or not 1 <= timeout_seconds <= 3600:
        raise ValueError("Remote script timeout must be between 1 and 3600 seconds")
    if not isinstance(max_output_bytes, int) or not 1 <= max_output_bytes <= 20_000_000:
        raise ValueError("Remote script output limit must be between 1 and 20000000 bytes")
    proc = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        start_new_session=True,
    )
    # Bounded chunk queue prevents a noisy remote process from consuming unbounded memory.
    queue = Queue(maxsize=64)

    def drain(pipe, stream):
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        try:
            while True:
                raw = os.read(pipe.fileno(), 4096)
                if not raw:
                    break
                chunk = decoder.decode(raw)
                if chunk:
                    queue.put((stream, chunk))
            tail = decoder.decode(b"", final=True)
            if tail:
                queue.put((stream, tail))
        finally:
            pipe.close()
            queue.put((stream, None))

    threads = [Thread(target=drain, args=(getattr(proc, stream), stream), daemon=True) for stream in ("stdout", "stderr")]
    for thread in threads:
        thread.start()
    secrets = [str(value) for key, value in env.items() if value and any(word in key for word in ("PASSWORD", "SECRET", "TOKEN"))]
    output = {"stdout": [], "stderr": []}
    pending = {"stdout": "", "stderr": ""}
    output_bytes = 0
    finished = 0
    deadline = monotonic() + timeout_seconds
    failure_status = None
    try:
        while finished < 2:
            remaining = deadline - monotonic()
            if remaining <= 0:
                failure_status = "Timed Out"
                break
            try:
                stream, chunk = queue.get(timeout=min(0.25, remaining))
            except Empty:
                continue
            if chunk is None:
                finished += 1
                if pending[stream]:
                    line = pending[stream]
                    for secret in sorted(secrets, key=len, reverse=True):
                        line = line.replace(secret, "[REDACTED]")
                    output[stream].append(line)
                    frappe.publish_realtime(event, {**context, "line": line, "stream": stream}, user=frappe.session.user)
                    pending[stream] = ""
                continue
            chunk_size = len(chunk.encode("utf-8", errors="replace"))
            if output_bytes + chunk_size > max_output_bytes:
                failure_status = "Output Limit Exceeded"
                break
            output_bytes += chunk_size
            pending[stream] += chunk
            while "\n" in pending[stream]:
                line, pending[stream] = pending[stream].split("\n", 1)
                line = line.rstrip("\r")
                for secret in sorted(secrets, key=len, reverse=True):
                    line = line.replace(secret, "[REDACTED]")
                output[stream].append(line)
                frappe.publish_realtime(event, {**context, "line": line, "stream": stream}, user=frappe.session.user)
    finally:
        if failure_status:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait()
            # Release blocked bounded-queue writers after terminating the process.
            while any(thread.is_alive() for thread in threads):
                try:
                    queue.get(timeout=0.05)
                except Empty:
                    pass
                for thread in threads:
                    thread.join(timeout=0.05)
        else:
            proc.wait()
            for thread in threads:
                thread.join(timeout=2)

    if failure_status == "Timed Out":
        output["stderr"].append(f"Remote script exceeded {timeout_seconds}s execution timeout")
        return_code = 124
    elif failure_status == "Output Limit Exceeded":
        output["stderr"].append(f"Remote script output exceeded {max_output_bytes} byte limit")
        return_code = 125
    else:
        return_code = proc.returncode
    final_status = "Completed" if return_code == 0 else ("Timed Out" if return_code == 124 else "Failed")
    frappe.publish_realtime(event, {**context, "status": final_status}, user=frappe.session.user)
    return return_code, "\n".join(output["stdout"]), "\n".join(output["stderr"])
