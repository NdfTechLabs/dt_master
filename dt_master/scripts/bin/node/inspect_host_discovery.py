#!/usr/bin/env python3
"""Read-only Linux VPS discovery. Emits one versioned JSON observation."""
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import time
from datetime import datetime, timezone


def command(args, timeout=4):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
        return result.stdout.strip() if result.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def number_file(path, key):
    try:
        with open(path, encoding="utf-8") as stream:
            for line in stream:
                if line.startswith(key + ":"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return 0


def parse_df():
    output = command(["df", "-P", "-B1", "-T"])
    rows = []
    for line in output.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 7:
            try:
                rows.append({
                    "filesystem": parts[0][:255], "type": parts[1][:40],
                    "size_bytes": int(parts[2]), "used_bytes": int(parts[3]),
                    "available_bytes": int(parts[4]),
                    "used_percent": int(parts[5].rstrip("%")), "mountpoint": " ".join(parts[6:])[:512],
                })
            except ValueError:
                continue
    return rows[:64]


def parse_interfaces():
    raw = command(["ip", "-j", "address", "show"])
    try:
        values = json.loads(raw) if raw else []
    except json.JSONDecodeError:
        values = []
    result = []
    for item in values:
        addresses = []
        for info in item.get("addr_info", []):
            local = info.get("local")
            if local:
                addresses.append({
                    "address": local, "family": info.get("family"),
                    "prefix_length": info.get("prefixlen"), "scope": info.get("scope"),
                })
        result.append({
            "name": item.get("ifname"), "state": item.get("operstate"),
            "mac_address": item.get("address"), "mtu": item.get("mtu"),
            "addresses": addresses,
        })
    return result[:32]


def parse_default_routes():
    raw = command(["ip", "-j", "route", "show", "default"])
    try:
        values = json.loads(raw) if raw else []
    except json.JSONDecodeError:
        values = []
    return [{
        "gateway": item.get("gateway"), "interface": item.get("dev"),
        "protocol": item.get("protocol"), "metric": item.get("metric"),
    } for item in values]


def parse_dns():
    values = []
    try:
        with open("/etc/resolv.conf", encoding="utf-8") as stream:
            for line in stream:
                match = re.match(r"\s*nameserver\s+(\S+)", line)
                if match:
                    values.append(match.group(1))
    except OSError:
        pass
    return values


def systemctl_state(action, name):
    try:
        result = subprocess.run(
            ["systemctl", action, name], capture_output=True, text=True,
            timeout=3, check=False,
        )
        # systemctl returns non-zero for valid states such as inactive,
        # disabled and not-found. Preserve stdout instead of treating those
        # observations as command failures.
        return (result.stdout or result.stderr).strip() or "unknown"
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"


def service_state(name):
    if not shutil.which("systemctl"):
        return {"manager": "unknown", "active": "unknown", "enabled": "unknown"}
    return {
        "manager": "systemd",
        "active": systemctl_state("is-active", name),
        "enabled": systemctl_state("is-enabled", name),
    }


def parse_cpu_topology():
    physical = set()
    try:
        with open("/proc/cpuinfo", encoding="utf-8", errors="replace") as stream:
            record = {}
            for line in stream:
                if not line.strip():
                    if "physical id" in record and "core id" in record:
                        physical.add((record["physical id"], record["core id"]))
                    record = {}
                elif ":" in line:
                    key, value = line.split(":", 1)
                    record[key.strip()] = value.strip()
            if "physical id" in record and "core id" in record:
                physical.add((record["physical id"], record["core id"]))
    except OSError:
        pass
    return len(physical) or None


def parse_block_devices():
    raw = command(["lsblk", "-b", "-J", "-o", "NAME,TYPE,SIZE,ROTA,MODEL,MOUNTPOINTS"])
    try:
        devices = json.loads(raw).get("blockdevices", []) if raw else []
    except (json.JSONDecodeError, AttributeError):
        devices = []
    return devices[:32]


def disk_bytes(devices):
    return sum(int(item.get("size") or 0) for item in devices if item.get("type") == "disk")


def main():
    mem_total = number_file("/proc/meminfo", "MemTotal")
    mem_available = number_file("/proc/meminfo", "MemAvailable")
    swap_total = number_file("/proc/meminfo", "SwapTotal")
    swap_free = number_file("/proc/meminfo", "SwapFree")
    cpu_model = ""
    try:
        with open("/proc/cpuinfo", encoding="utf-8", errors="replace") as stream:
            for line in stream:
                if line.lower().startswith(("model name", "hardware")) and ":" in line:
                    cpu_model = line.split(":", 1)[1].strip()
                    break
    except OSError:
        pass
    virt = command(["systemd-detect-virt"]) or "none-or-unknown"
    load = []
    try:
        with open("/proc/loadavg", encoding="ascii") as stream:
            load = [float(value) for value in stream.read().split()[:3]]
    except (OSError, ValueError):
        pass
    uptime_seconds = None
    try:
        with open("/proc/uptime", encoding="ascii") as stream:
            uptime_seconds = int(float(stream.read().split()[0]))
    except (OSError, ValueError, IndexError):
        pass
    os_release = {}
    try:
        with open("/etc/os-release", encoding="utf-8") as stream:
            for line in stream:
                key, separator, value = line.rstrip().partition("=")
                if separator:
                    os_release[key] = value.strip().strip('"')
    except OSError:
        pass
    tools = {name: shutil.which(binary) is not None for name, binary in {
        "python3": "python3", "node": "node", "npm": "npm", "yarn": "yarn",
        "nginx": "nginx", "supervisorctl": "supervisorctl", "redis_cli": "redis-cli",
        "mariadb": "mariadb",
    }.items()}
    tools["mariadb"] = tools["mariadb"] or shutil.which("mysql") is not None
    tools["bench"] = shutil.which("bench") is not None
    services = {}
    for name in ("ssh", "nginx", "mariadb", "mysql", "redis-server", "supervisor"):
        services[name] = service_state(name)
    listening = []
    for line in command(["ss", "-H", "-lntu"]).splitlines():
        parts = line.split()
        if len(parts) >= 5:
            listening.append({"protocol": parts[0], "local_address": parts[4]})
    df = parse_df()
    block_devices = parse_block_devices()
    root = next((row for row in df if row["mountpoint"] == "/"), {})
    result = {
        "schema_version": "2.0.0",
        "observed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "identity": {"hostname": socket.gethostname(), "fqdn": socket.getfqdn()},
        "os": {
            "pretty_name": os_release.get("PRETTY_NAME", platform.platform())[:200],
            "id": os_release.get("ID", "unknown").lower(),
            "version_id": os_release.get("VERSION_ID", "unknown"),
            "version_codename": os_release.get("VERSION_CODENAME", os_release.get("UBUNTU_CODENAME", "unknown")),
            "id_like": os_release.get("ID_LIKE", "unknown"),
            "kernel_release": platform.release(),
            "kernel_version": platform.version()[:255],
            "architecture": platform.machine(),
            "package_manager": next((name for name in ("apt-get", "dnf", "yum", "zypper", "apk", "pacman") if shutil.which(name)), "unknown"),
            "service_manager": "systemd" if os.path.isdir("/run/systemd/system") and shutil.which("systemctl") else "unknown",
            "timezone": time.tzname[0] if time.tzname else "unknown",
        },
        "compute": {
            "cpu_model": cpu_model[:255], "logical_cpu_cores": os.cpu_count() or 0, "physical_cpu_cores": parse_cpu_topology(),
            "memory_total_bytes": mem_total, "memory_available_bytes": mem_available,
            "swap_total_bytes": swap_total, "swap_free_bytes": swap_free,
            "load_average": load, "uptime_seconds": uptime_seconds,
            "virtualization": virt[:80],
        },
        "storage": {"filesystems": df, "block_devices": block_devices, "disk_total_bytes": disk_bytes(block_devices), "root_available_bytes": root.get("available_bytes", 0), "root_size_bytes": root.get("size_bytes", 0)},
        "network": {
            "interfaces": parse_interfaces(), "default_routes": parse_default_routes(),
            "dns_nameservers": parse_dns(), "listening_sockets": listening[:256],
        },
        "tools": tools,
        "services": services,
        "bench_available": tools["bench"],
    }
    print("discovery_json=" + json.dumps(result, separators=(",", ":"), sort_keys=True))


if __name__ == "__main__":
    main()
