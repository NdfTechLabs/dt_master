"""Shared OpenSSH transport builders for Infrastructure Node operations.

Gateway aliases and ProxyCommand rules live in an administrator-managed
OpenSSH config. They are never accepted as arbitrary DocType shell fragments.
"""
import re
import subprocess

import frappe


_ALIAS = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
_HOST = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,252}")
_USER = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}")


def _config_args():
    """Return the managed OpenSSH config argument, if configured."""
    config = getattr(frappe, "conf", {}).get("dt_master_ssh_config")
    if not config:
        return []
    if not isinstance(config, str) or not config.startswith("/") or any(c in config for c in "\n\r\0"):
        frappe.throw("dt_master_ssh_config must be an absolute path")
    return ["-F", config]


def _target(ssh):
    alias = ssh.get("host_alias")
    if alias:
        if not _ALIAS.fullmatch(alias):
            frappe.throw("Invalid SSH host alias")
        return alias, None
    host, user = ssh.get("host"), ssh.get("user")
    if not isinstance(host, str) or not _HOST.fullmatch(host):
        frappe.throw("Invalid SSH host")
    if not isinstance(user, str) or not _USER.fullmatch(user):
        frappe.throw("Invalid SSH user")
    try:
        port = int(ssh.get("port") or 22)
    except (TypeError, ValueError):
        frappe.throw("Invalid SSH port")
    if not 1 <= port <= 65535:
        frappe.throw("Invalid SSH port")
    return f"{user}@{host}", port


def build_ssh_argv(ssh, remote_command):
    target, port = _target(ssh)
    command = ["ssh", *_config_args(), "-o", "BatchMode=yes", "-o", "ConnectTimeout=15"]
    if port is not None:
        command.extend(["-p", str(port)])
    command.extend([target, remote_command])
    return command


def build_scp_argv(ssh, source, destination):
    target, port = _target(ssh)
    command = ["scp", *_config_args(), "-o", "BatchMode=yes", "-o", "ConnectTimeout=15"]
    if port is not None:
        command.extend(["-P", str(port)])
    command.extend(["-r", str(source), f"{target}:{destination}"])
    return command


def run_bounded(command, *, timeout=60, max_output=2_000_000):
    """Run an SSH/SCP command with a hard timeout and bounded captured output."""
    if not isinstance(timeout, (int, float)) or timeout <= 0 or timeout > 3600:
        raise ValueError("SSH/SCP timeout must be between 1 and 3600 seconds")
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError("Remote operation timed out") from exc
    stdout = result.stdout or ""
    stderr = result.stderr or ""
    if len(stdout) + len(stderr) > max_output:
        raise RuntimeError("Remote operation output exceeded the configured limit")
    return result.returncode, stdout, stderr
