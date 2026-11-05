#!/usr/bin/env python3
"""Approved foundation only: no production DB, archive decryption or traffic switch.

Run through apply_rootless.py; journal every step and retain partial state on
failure. Docker DEBs are verified/extracted, never installed as system services.
"""

from datetime import datetime, timezone
import email.utils
import fcntl
import hashlib
import json
import lzma
import os
import platform
from pathlib import Path
import pwd
import re
import shutil
import subprocess
import tempfile
import time
from urllib.request import Request, urlopen


HOST = "mail.ndftechlabs.com"
USER = "dt-containers"
HOME = Path("/home/dt-containers")
STATE = Path("/var/lib/ndf-rootless-migration")
BIN = Path("/opt/ndf-rootless/29.6.1/bin")
BUDGET = 150_000_000
PREREQUISITES = {"uidmap", "slirp4netns", "libsubid4", "libslirp0"}


def digest(path):
    value = hashlib.sha256()
    with open(path, "rb") as source:
        for data in iter(lambda: source.read(1024 * 1024), b""):
            value.update(data)
    return value.hexdigest()


def snapshot():
    paths = [Path("/etc/nginx/nginx.conf"), *Path("/etc/nginx/conf.d").glob("*.conf"),
             *Path("/etc/nginx/sites-enabled").glob("*"), Path("/home/frappe/bench/config/supervisor.conf"),
             Path("/home/frappe/bench/sites/common_site_config.json"), Path("/etc/cloudflared/config.yml"),
             *Path("/home/frappe/bench/sites").glob("*/site_config.json")]
    hashes = {str(path): digest(path) for path in paths if path.is_file()}
    units = ["nginx", "supervisor", "mariadb", "redis-server", "postfix@-", "dovecot", "rspamd",
             "opendkim", "cloudflared", "pm2-root", "netdata", "fail2ban", "ssh"]
    services = {unit: subprocess.run(["systemctl", "is-active", unit], capture_output=True, text=True).stdout.strip() for unit in units}
    status = subprocess.run(["supervisorctl", "status"], capture_output=True, text=True, check=True).stdout
    bench = [line for line in status.splitlines() if line.startswith(("bench-web:", "bench-workers:", "bench-redis:"))]
    routes = {}
    for family in ("-4", "-6"):
        rows = json.loads(subprocess.check_output(["ip", family, "-j", "route", "show"], text=True))
        routes[family] = sorted(json.dumps({key: row.get(key) for key in ("dst", "gateway", "dev", "table", "metric", "prefsrc")}, sort_keys=True) for row in rows)
    forward = subprocess.check_output(["iptables", "-S", "FORWARD"], text=True)
    if any(value != "active" for value in services.values()) or len(bench) != 7 or any("RUNNING" not in line for line in bench):
        raise RuntimeError("Protected production service/process baseline is not healthy")
    return {"hashes": hashes, "services": services, "routes": routes,
            "forward": forward, "bench_running": True}


class Foundation:
    def __init__(self, manifest):
        self.manifest = manifest
        self.plan_hash = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
        if os.geteuid() != 0 or subprocess.check_output(["hostname"], text=True).strip() != HOST:
            raise RuntimeError("Wrong host/account")
        os_release = Path("/etc/os-release").read_text()
        if platform.machine() != "x86_64" or 'VERSION_ID="24.04"' not in os_release or not re.search(r"^ID=ubuntu$", os_release, re.M):
            raise RuntimeError("Foundation plan is pinned to Ubuntu 24.04 amd64")
        if STATE.is_symlink():
            raise RuntimeError("Unsafe state directory")
        os.umask(0o077)
        STATE.mkdir(mode=0o700, exist_ok=True)
        if STATE.stat().st_uid != 0 or STATE.stat().st_mode & 0o077:
            raise RuntimeError("State directory must be root-private")
        self.lock = open(STATE / "install.lock", "a")
        fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.state_path = STATE / "state.json"
        if self.state_path.exists():
            self.state = json.loads(self.state_path.read_text())
            if self.state.get("plan_sha256") != self.plan_hash or self.state.get("host") != HOST:
                raise RuntimeError("Existing state belongs to a different plan")
        else:
            if HOME.exists() or Path("/opt/ndf-rootless").exists():
                raise RuntimeError("Pre-existing deployment paths require review")
            try:
                pwd.getpwnam(USER)
            except KeyError:
                pass
            else:
                raise RuntimeError("Deployment account already exists without our journal")
            self.state = {"host": HOST, "plan_sha256": self.plan_hash, "steps": [], "download_bytes": 0,
                          "started_at_utc": datetime.now(timezone.utc).isoformat()}
            self.save()
        self.log = open(STATE / "commands.log", "a", buffering=1)

    def save(self):
        path = STATE / "state.json.new"
        path.write_text(json.dumps(self.state, indent=2))
        path.replace(self.state_path)

    def run(self, args, timeout=120, env=None):
        self.log.write(json.dumps({"time": datetime.now(timezone.utc).isoformat(), "argv": [str(arg) for arg in args]}) + "\n")
        result = subprocess.run([str(arg) for arg in args], capture_output=True, text=True, timeout=timeout, env=env)
        self.log.write(result.stdout + result.stderr + "\n")
        if result.returncode:
            raise RuntimeError(f"Command failed ({Path(str(args[0])).name}); see root-private commands.log")
        return result.stdout

    def step(self, name, action):
        if name in self.state["steps"]:
            return
        print("Foundation step: " + name, flush=True)
        action()
        self.state["steps"].append(name)
        self.save()

    def fetch(self, url, path, expected_hash=None, expected_size=None):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if expected_hash and digest(path) != expected_hash:
                raise RuntimeError("Existing cached artifact checksum mismatch")
            if expected_size and path.stat().st_size != expected_size:
                raise RuntimeError("Existing cached artifact size mismatch")
            return path
        partial = path.with_suffix(path.suffix + ".download")
        if partial.exists():
            raise RuntimeError("Interrupted metadata/package download requires review")
        if expected_size and self.state["download_bytes"] + expected_size > BUDGET:
            raise RuntimeError("Declared download would exceed the approved budget")
        with urlopen(Request(url, headers={"User-Agent": "ndf-migration-foundation"}), timeout=30) as response, open(partial, "xb") as output:
            while True:
                remaining = BUDGET - self.state["download_bytes"]
                if remaining <= 0:
                    raise RuntimeError("Approved download budget exhausted")
                block = response.read(min(256 * 1024, remaining))
                if not block:
                    break
                self.state["download_bytes"] += len(block)
                self.save()
                if self.state["download_bytes"] > BUDGET or (expected_size and output.tell() + len(block) > expected_size):
                    raise RuntimeError("Approved download budget/size exceeded; stop")
                output.write(block)
        if expected_hash and digest(partial) != expected_hash:
            raise RuntimeError("Downloaded artifact checksum mismatch")
        if expected_size and partial.stat().st_size != expected_size:
            raise RuntimeError("Downloaded artifact size mismatch")
        partial.rename(path)
        return path

    def write_new(self, path, content, mode=0o600, account=False):
        path = Path(path)
        if path.is_symlink():
            raise RuntimeError("Unexpected configuration symlink")
        if path.exists():
            if path.read_text() != content:
                raise RuntimeError("Existing configuration differs; no overwrite")
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "x") as output:
            output.write(content)
        path.chmod(mode)
        if account:
            user = pwd.getpwnam(USER)
            # All paths here are new under the dedicated home.
            parent = path.parent
            while parent != HOME.parent:
                os.chown(parent, user.pw_uid, user.pw_gid)
                parent.chmod(0o700)
                if parent == HOME:
                    break
                parent = parent.parent
            os.chown(path, user.pw_uid, user.pw_gid)

    def protect_baseline(self):
        if shutil.disk_usage("/").free < 60 * 1024**3:
            raise RuntimeError("Insufficient host disk margin")
        self.state["before"] = snapshot()
        self.save()
        directory = STATE / "host-before"
        directory.mkdir(mode=0o700, exist_ok=True)
        for name in ("passwd", "group", "shadow", "gshadow", "subuid", "subgid"):
            path = Path("/etc") / name
            if path.exists() and not (directory / name).exists():
                shutil.copy2(path, directory / name)

    def ubuntu_packages(self):
        selected = {}
        for base, suites in (("https://archive.ubuntu.com/ubuntu", ("noble", "noble-updates")),
                             ("https://security.ubuntu.com/ubuntu", ("noble-security",))):
            for suite in suites:
                directory = STATE / "ubuntu" / suite
                release = self.fetch(base + "/dists/" + suite + "/InRelease", directory / "InRelease")
                authenticated = directory / "Release"
                if not authenticated.exists():
                    self.run(["gpgv", "--keyring", "/usr/share/keyrings/ubuntu-archive-keyring.gpg", "--output", authenticated, release])
                text = authenticated.read_text()
                fields = dict(line.split(": ", 1) for line in text.splitlines() if ": " in line and not line.startswith(" "))
                if fields.get("Origin") != "Ubuntu" or fields.get("Codename") != "noble" or fields.get("Suite") != suite:
                    raise RuntimeError("Unexpected Ubuntu release identity")
                expiry = next((line.split(": ", 1)[1] for line in text.splitlines() if line.startswith("Valid-Until: ")), None)
                if expiry and email.utils.parsedate_to_datetime(expiry) < datetime.now(timezone.utc):
                    raise RuntimeError("Ubuntu release metadata expired")
                for component in ("main", "universe"):
                    relative = component + "/binary-amd64/Packages.xz"
                    section = text.split("SHA256:\n", 1)[1]
                    entry = next(line.split() for line in section.splitlines() if line.strip().endswith(" " + relative))
                    index = self.fetch(base + "/dists/" + suite + "/" + relative,
                                       directory / (component + "-Packages.xz"), entry[0], int(entry[1]))
                    with lzma.open(index, "rt") as source:
                        block, last = {}, None
                        for line in source:
                            line = line.rstrip("\n")
                            if not line:
                                name = block.get("Package")
                                if name in PREREQUISITES and block.get("Architecture") == "amd64":
                                    old = selected.get(name)
                                    if old is None or subprocess.run(["dpkg", "--compare-versions", block["Version"], "gt", old["Version"]]).returncode == 0:
                                        selected[name] = {**block, "base": base}
                                block, last = {}, None
                            elif line.startswith(" "):
                                continue
                            elif ": " in line:
                                key, value = line.split(": ", 1)
                                if key in {"Package", "Version", "Architecture", "Filename", "SHA256", "Size", "Depends"}:
                                    block[key] = value
        if set(selected) != PREREQUISITES:
            raise RuntimeError("Could not resolve the four signed Ubuntu prerequisites")
        self.state["ubuntu_packages"] = selected
        self.save()
        for name, package in selected.items():
            package["local_path"] = str(self.fetch(package["base"] + "/" + package["Filename"],
                STATE / "ubuntu-debs" / (name + ".deb"), package["SHA256"], int(package["Size"])))
        self.state["ubuntu_packages"] = selected
        self.save()

    def download_docker(self):
        for package in self.manifest["packages"]:
            path = self.fetch(self.manifest["repository"] + "/" + package["filename"],
                              STATE / "docker-debs" / (package["name"] + ".deb"), package["sha256"], package["bytes"])
            destination = STATE / "extracted" / package["name"]
            if not destination.exists():
                destination.mkdir(parents=True)
                self.run(["dpkg-deb", "--extract", path, destination])

    def install_prerequisites(self):
        packages = [value["local_path"] for value in self.state["ubuntu_packages"].values()]
        base = ["apt-get", "--no-install-recommends", "--no-remove", "--no-upgrade"]
        env = dict(os.environ, DEBIAN_FRONTEND="noninteractive", NEEDRESTART_MODE="l", LC_ALL="C")
        if self.state.get("prerequisite_install_attempted"):
            versions = {name: subprocess.run(["dpkg-query", "-W", "-f=${Version}", name], capture_output=True, text=True).stdout.strip()
                        for name in PREREQUISITES}
            if all(versions[name] == self.state["ubuntu_packages"][name]["Version"] for name in PREREQUISITES):
                return
        plan = self.run(base + ["--simulate", "install", *packages], env=env)
        if "0 upgraded, 4 newly installed, 0 to remove" not in plan:
            raise RuntimeError("Prerequisite transaction changed; no packages installed")
        installed = set(re.findall(r"^Inst (\S+)", plan, re.M))
        if installed != PREREQUISITES:
            raise RuntimeError("Unexpected package transaction")
        self.state["prerequisite_install_attempted"] = True
        self.save()
        # APT --no-download refuses file acquisition for these private absolute
        # paths. The simulation above proves the complete four-package closure;
        # dpkg installs those exact signed files without any network acquisition.
        self.run(["dpkg", "--install", *packages], env=env)

    def create_account(self):
        try:
            user = pwd.getpwnam(USER)
        except KeyError:
            self.run(["useradd", "--create-home", "--user-group", "--shell", "/bin/bash", USER])
            user = pwd.getpwnam(USER)
        if user.pw_dir != str(HOME) or user.pw_uid < 1000:
            raise RuntimeError("Unexpected deployment account identity")
        HOME.chmod(0o700)
        if len(os.getgrouplist(USER, user.pw_gid)) != 1:
            raise RuntimeError("Deployment user has unexpected supplementary groups")
        for path in (Path("/etc/subuid"), Path("/etc/subgid")):
            rows = [line.split(":") for line in path.read_text().splitlines() if line]
            mine = [row for row in rows if row[0] == USER]
            if len(mine) != 1 or int(mine[0][2]) < 65536:
                raise RuntimeError("Missing/insufficient subordinate ranges")
            start, count = int(mine[0][1]), int(mine[0][2])
            if any(row[0] != USER and max(start, int(row[1])) < min(start + count, int(row[1]) + int(row[2])) for row in rows):
                raise RuntimeError("Overlapping subordinate range")
        self.state["account"] = {"uid": user.pw_uid, "gid": user.pw_gid}
        self.save()

    def install_binaries(self):
        user = pwd.getpwnam(USER)
        BIN.mkdir(parents=True, exist_ok=True)
        for directory in (BIN.parent.parent, BIN.parent, BIN):
            os.chown(directory, 0, user.pw_gid)
            directory.chmod(0o750)
        required = {"docker", "dockerd", "docker-init", "docker-proxy", "containerd", "containerd-shim-runc-v2",
                    "ctr", "runc", "rootlesskit", "dockerd-rootless.sh", "dockerd-rootless-setuptool.sh"}
        found = {}
        candidates = [*(STATE / "extracted").glob("*/usr/bin/*"),
                      *(STATE / "extracted").glob("*/usr/libexec/docker/*")]
        optional = {"rootlesskit-docker-proxy", "vpnkit"}
        for path in candidates:
            if path.is_file() and not path.is_symlink() and path.name in required | optional:
                found[path.name] = path
        if not required <= set(found):
            raise RuntimeError("Reviewed binary set not present in extracted packages")
        for name, path in found.items():
            target = BIN / name
            if target.exists() and digest(target) != digest(path):
                raise RuntimeError("Existing binary differs; no overwrite")
            if not target.exists():
                shutil.copyfile(path, target)
                os.chown(target, 0, user.pw_gid)
                target.chmod(0o750)
        compose = list((STATE / "extracted/docker-compose-plugin").glob("usr/libexec/docker/cli-plugins/docker-compose"))
        if len(compose) != 1:
            raise RuntimeError("Compose executable missing")
        target = BIN / "docker-compose"
        if not target.exists():
            shutil.copyfile(compose[0], target)
            os.chown(target, 0, user.pw_gid)
            target.chmod(0o750)
        plugin_dir = HOME / ".docker/cli-plugins"
        plugin_dir.mkdir(parents=True, exist_ok=True)
        for directory in (plugin_dir.parent, plugin_dir):
            os.chown(directory, user.pw_uid, user.pw_gid)
            directory.chmod(0o700)
        plugin = plugin_dir / "docker-compose"
        if not plugin.exists():
            plugin.symlink_to(target)
        elif plugin.resolve() != target:
            raise RuntimeError("Unexpected Compose plugin")
        for name in ("docker", "dockerd", "rootlesskit", "containerd", "runc"):
            result = subprocess.run(["ldd", str(BIN / name)], capture_output=True, text=True)
            if "not found" in result.stdout + result.stderr:
                raise RuntimeError("A pinned binary is missing a shared library")

    def security_and_limits(self):
        user = pwd.getpwnam(USER)
        profile = ("abi <abi/4.0>,\ninclude <tunables/global>\n"
                   f"profile ndf-rootlesskit-29_6_1 {BIN}/rootlesskit flags=(unconfined) {{\n  userns,\n}}\n")
        path = Path("/etc/apparmor.d/ndf-rootlesskit-29_6_1")
        self.write_new(path, profile, 0o644)
        profiles = Path("/sys/kernel/security/apparmor/profiles").read_text()
        if not any(line.startswith("ndf-rootlesskit-29_6_1 ") for line in profiles.splitlines()):
            self.run(["apparmor_parser", "--add", path])
        self.write_new(f"/etc/systemd/system/user-{user.pw_uid}.slice.d/50-ndf-limits.conf",
                       "[Slice]\nCPUQuota=200%\nMemoryHigh=2560M\nMemoryMax=3G\nMemorySwapMax=256M\nTasksMax=1024\n", 0o644)
        self.write_new(f"/etc/systemd/system/user@{user.pw_uid}.service.d/50-ndf-delegate.conf",
                       "[Service]\nDelegate=cpu cpuset io memory pids\n", 0o644)
        self.run(["systemctl", "daemon-reload"])
        self.run(["loginctl", "enable-linger", USER])
        self.run(["systemctl", "start", f"user@{user.pw_uid}.service"])

    def user_run(self, args, timeout=120):
        uid = pwd.getpwnam(USER).pw_uid
        return self.run(["sudo", "-n", "-H", "-u", USER, "env", f"HOME={HOME}",
                         f"XDG_RUNTIME_DIR=/run/user/{uid}", f"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus",
                         f"DOCKER_HOST=unix:///run/user/{uid}/docker.sock", f"PATH={BIN}:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
                         *args], timeout=timeout)

    def start_daemon(self):
        config = HOME / ".config/docker/daemon.json"
        self.write_new(config, json.dumps({"data-root": str(HOME / ".local/share/docker"),
                       "init-path": str(BIN / "docker-init"),
                       "exec-opts": ["native.cgroupdriver=systemd"], "log-driver": "json-file",
                       "log-opts": {"max-size": "10m", "max-file": "3"}}, indent=2) + "\n", account=True)
        unit = ("[Unit]\nDescription=NDF isolated rootless Docker\n\n[Service]\nType=notify\nNotifyAccess=all\n"
                f"Environment=PATH={BIN}:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin\n"
                "Environment=DOCKERD_ROOTLESS_ROOTLESSKIT_NET=slirp4netns\n"
                f"ExecStart={BIN}/dockerd-rootless.sh --config-file={config}\n"
                "Restart=on-failure\nRestartSec=5\nTimeoutStartSec=120\nTimeoutStopSec=30\n"
                "LimitNOFILE=1048576\nLimitCORE=0\nTasksMax=1024\nDelegate=yes\nKillMode=mixed\n\n[Install]\nWantedBy=default.target\n")
        self.write_new(HOME / ".config/systemd/user/docker.service", unit, account=True)
        self.user_run(["systemctl", "--user", "daemon-reload"])
        self.user_run(["systemctl", "--user", "enable", "--now", "docker.service"], timeout=150)
        info = json.loads(self.user_run([str(BIN / "docker"), "info", "--format", "{{json .}}"], timeout=30))
        if "name=rootless" not in info.get("SecurityOptions", []) or info.get("CgroupDriver") != "systemd" or info.get("DockerRootDir") != str(HOME / ".local/share/docker"):
            raise RuntimeError("Docker is not using the intended rootless/cgroup/data-root configuration")
        self.state["daemon"] = {key: info.get(key) for key in ("ServerVersion", "CgroupVersion", "CgroupDriver", "DockerRootDir", "SecurityOptions", "Driver")}
        self.save()

    def synthetic_checks(self):
        docker = str(BIN / "docker")
        # Reserve a conservative 10 MB for this tiny image/config/manifest pull.
        if self.state["download_bytes"] + 10_000_000 > BUDGET:
            raise RuntimeError("No remaining approved budget for synthetic image")
        self.user_run([docker, "pull", "busybox:1.37.0"], timeout=180)
        image = self.user_run([docker, "image", "inspect", "busybox:1.37.0", "--format", "{{index .RepoDigests 0}}"])
        image = image.strip()
        if "@sha256:" not in image:
            raise RuntimeError("Synthetic image digest not available")
        self.state["synthetic_image"] = image
        self.state["download_bytes"] += 10_000_000
        self.save()
        limits = self.user_run([docker, "run", "--rm", "--network", "none", "--memory", "64m", "--cpus", "0.25", "--pids-limit", "32",
                               image, "sh", "-c", "cat /sys/fs/cgroup/memory.max; cat /sys/fs/cgroup/cpu.max; cat /sys/fs/cgroup/pids.max"])
        lines = limits.strip().splitlines()
        if lines != ["67108864", "25000 100000", "32"]:
            raise RuntimeError("Container resource limits were not enforced")
        uid = pwd.getpwnam(USER).pw_uid
        path = Path(f"/sys/fs/cgroup/user.slice/user-{uid}.slice")
        if (path / "memory.max").read_text().strip() != "3221225472" or (path / "cpu.max").read_text().strip() != "200000 100000":
            raise RuntimeError("Aggregate dedicated-user resource limits were not enforced")
        network = "ndf-foundation-isolated"
        existing = self.user_run([docker, "network", "ls", "--filter", "name=^" + network + "$", "--format", "{{.Name}}"])
        if not existing.strip():
            self.user_run([docker, "network", "create", "--internal", "--label", "ndf.purpose=foundation", network])
        network_info = json.loads(self.user_run([docker, "network", "inspect", network]))[0]
        if not network_info.get("Internal") or network_info.get("Labels", {}).get("ndf.purpose") != "foundation":
            raise RuntimeError("Synthetic internal network not isolated")
        routes = self.user_run([docker, "run", "--rm", "--network", network, "--memory", "64m", "--pids-limit", "32",
                               image, "ip", "route"])
        if any(line.startswith("default ") for line in routes.splitlines()):
            raise RuntimeError("Synthetic internal container has an unexpected default route")
        self.state["synthetic_checks"] = {"container_limits": True, "aggregate_limits": True,
                                         "internal_network": True, "no_default_egress_route": True, "customer_data_loaded": False}
        self.save()

    def finish(self):
        after = snapshot()
        if after != self.state["before"]:
            raise RuntimeError("Protected production state changed; stop and review")
        self.state["complete"] = True
        if self.state.get("last_error"):
            self.state.setdefault("historical_errors", []).append(self.state.pop("last_error"))
        self.state["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        self.save()

    def apply(self):
        if self.state.get("complete"):
            # Never replay bootstrap or stop a daemon that may now serve real
            # applications. Later configuration deltas have separate approvals.
            print(json.dumps({"foundation_already_complete": True, "host": HOST,
                              "deployment_user": USER, "current_health_not_rechecked": True}))
            return
        try:
            self.step("baseline", self.protect_baseline)
            self.step("ubuntu_metadata_and_download", self.ubuntu_packages)
            self.step("docker_verified_extraction", self.download_docker)
            self.step("prerequisites_installed", self.install_prerequisites)
            self.step("dedicated_account", self.create_account)
            self.step("versioned_binaries", self.install_binaries)
            self.step("targeted_security_and_limits", self.security_and_limits)
            self.step("rootless_user_daemon", self.start_daemon)
            self.step("synthetic_checks", self.synthetic_checks)
            self.finish()
        except Exception as error:
            self.state["last_error"] = str(error)
            self.save()
            if "targeted_security_and_limits" in self.state["steps"]:
                try:
                    self.user_run(["systemctl", "--user", "stop", "docker.service"])
                except Exception:
                    pass
            raise
        print(json.dumps({"foundation_complete": True, "host": HOST, "deployment_user": USER,
                          "account": self.state["account"], "daemon": self.state["daemon"],
                          "synthetic_checks": self.state["synthetic_checks"],
                          "download_bytes_accounted": self.state["download_bytes"],
                          "native_state_unchanged": True, "application_data_loaded": False}))


if __name__ == "__main__":
    if "REQUEST" not in globals():
        raise SystemExit("Use apply_rootless.py with explicit approval")
    foundation = Foundation(REQUEST["manifest"])
    foundation.state.setdefault("executions", []).append({"script_sha256": REQUEST["script_sha256"],
        "time_utc": datetime.now(timezone.utc).isoformat()})
    foundation.save()
    foundation.apply()
