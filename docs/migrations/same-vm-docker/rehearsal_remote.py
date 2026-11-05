#!/usr/bin/env python3
"""New private rehearsal only. Import is inert; never delete, overwrite or cut over."""

from datetime import datetime, timezone
import fcntl
import gzip
import hashlib
import http.client
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import shlex
import shutil
import signal
import socket
import stat
import subprocess
import sys
import threading
import time


HOST = "mail.ndftechlabs.com"
SITE = "dalali.ndftechlabs.com"
CLONE_SITE = "dalali-rehearsal.invalid"
BENCH = Path("/home/frappe/bench")
ROOT = Path("/var/lib/ndf-rootless-migration")
WORK = ROOT / "rehearsal-v1"
DEPLOY = Path("/home/dt-containers/stacks/dalali-rehearsal-v1")
PROJECT = "ndf-dalali-rehearsal-v1"
NETWORK = PROJECT + "-internal"
LABEL = "ndf.rehearsal=" + PROJECT
DOCKER = "/opt/ndf-rootless/29.6.1/bin/docker"
APP_IMAGE = "sha256:e8b4c638deb0c739fa7a2f932c5a79f6bd5a5be291393dd3f1d114840bf7e8eb"
DOWNLOAD_CAP = 180_000_000
DISK_CAP = 8 * 1024**3
FREE_FLOOR = 60 * 1024**3


def digest(path):
    value = hashlib.sha256()
    with open(path, "rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def file_manifest(root, allow_assets=False):
    rows = {}
    for parent, dirs, files in os.walk(root, followlinks=False):
        for name in sorted(dirs + files):
            path = Path(parent) / name
            info = path.lstat()
            relative = str(path.relative_to(root))
            if stat.S_ISLNK(info.st_mode):
                if not allow_assets or not any(path.resolve().is_relative_to(p) for p in (BENCH / "apps", BENCH / "sites/assets")):
                    raise RuntimeError("Unreviewed source data/assets symlink")
                rows[relative] = {"type": "symlink", "target": os.readlink(path)}
            elif stat.S_ISREG(info.st_mode):
                rows[relative] = {"type": "file", "bytes": info.st_size, "mode": stat.S_IMODE(info.st_mode), "sha256": digest(path)}
            elif stat.S_ISDIR(info.st_mode):
                rows[relative] = {"type": "directory", "mode": stat.S_IMODE(info.st_mode)}
            else:
                raise RuntimeError("Unreviewed special source file")
    return rows


def private_mysql_driver():
    # Import only a root-owned private pure-Python package, not a live virtualenv
    # or its executable .pth files. Do not prepend all native site-packages.
    root = Path("/opt/ndf-rootless/native-runtime-probe-v1/application-env/lib/python3.14/site-packages/pymysql")
    if root.is_symlink() or not root.is_dir():
        raise RuntimeError("Private MySQL driver copy unavailable")
    for parent, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            path = Path(parent) / name
            if path.is_symlink() or path.stat().st_uid != 0 or path.stat().st_mode & 0o022:
                raise RuntimeError("Private MySQL driver ownership changed")
    spec = importlib.util.spec_from_file_location("pymysql", root / "__init__.py", submodule_search_locations=[str(root)])
    module = importlib.util.module_from_spec(spec)
    sys.modules["pymysql"] = module
    sys.dont_write_bytecode = True
    spec.loader.exec_module(module)
    return module


class Rehearsal:
    def __init__(self, request):
        if os.geteuid() != 0 or subprocess.check_output(["hostname"], text=True).strip() != HOST:
            raise RuntimeError("Unexpected rehearsal host/account")
        user = pwd.getpwnam("dt-containers")
        if (user.pw_uid, user.pw_gid) != (5001, 5001):
            raise RuntimeError("Dedicated rootless identity changed")
        os.umask(0o077)
        self.request = request
        self.foundation = json.loads((ROOT / "state.json").read_text())
        self.build = json.loads((ROOT / "exact-runtime-v1/state.json").read_text())
        if not self.foundation.get("complete") or self.build.get("validated_image_id") != APP_IMAGE:
            raise RuntimeError("Validated application image/foundation required")
        if WORK.is_symlink() or (WORK.exists() and (WORK.stat().st_uid != 0 or WORK.stat().st_mode & 0o077)):
            raise RuntimeError("Unsafe rehearsal state path")
        WORK.mkdir(mode=0o700, exist_ok=True)
        self.lock = open(WORK / "rehearsal.lock", "a")
        fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.state_path = WORK / "state.json"
        if self.state_path.exists():
            self.state = json.loads(self.state_path.read_text())
            if self.state.get("project") != PROJECT:
                raise RuntimeError("Unexpected existing rehearsal journal")
        else:
            if DEPLOY.exists():
                raise RuntimeError("Pre-existing deployment requires review")
            self.state = {"project": PROJECT, "started_utc": datetime.now(timezone.utc).isoformat(),
                          "initial_free_bytes": shutil.disk_usage("/").free, "steps": [],
                          "resources": {"networks": {}, "volumes": {}, "containers": {}},
                          "download_accounted_bytes": 0, "approval": request["approval"]}
        self.log = open(WORK / "commands.log", "a", buffering=1)
        self.state.setdefault("executions", []).append({"phase": request["phase"], "script_sha256": request["script_sha256"],
            "time_utc": datetime.now(timezone.utc).isoformat()})
        self.save()
        self.guard()

    def save(self):
        path = WORK / "state.json.new"
        path.write_text(json.dumps(self.state, indent=2))
        path.replace(self.state_path)

    def progress(self, text):
        row = {"time_utc": datetime.now(timezone.utc).isoformat(), "progress": text}
        self.log.write(json.dumps(row) + "\n")
        try:
            print(text, flush=True)
        except BrokenPipeError:
            sys.stdout = open(os.devnull, "w")

    def guard(self, reserve=0):
        free = shutil.disk_usage("/").free
        used = max(0, self.state["initial_free_bytes"] - free)
        if free - reserve < FREE_FLOOR or used + reserve > DISK_CAP:
            raise RuntimeError("Rehearsal disk guard reached; no deletion/budget increase")

    def docker_args(self, args):
        return ["sudo", "-n", "-H", "-u", "dt-containers", "env", "XDG_RUNTIME_DIR=/run/user/5001",
                "DOCKER_HOST=unix:///run/user/5001/docker.sock",
                "PATH=/opt/ndf-rootless/29.6.1/bin:/usr/bin:/bin:/usr/sbin:/sbin", DOCKER, *map(str, args)]

    def run(self, args, timeout=60, input=None, logfile=None, env=None):
        self.log.write(json.dumps({"argv": list(map(str, args))}) + "\n")
        if logfile:
            with open(logfile, "a") as output:
                process = subprocess.Popen(list(map(str, args)), stdout=output, stderr=output, start_new_session=True, env=env)
                started = time.monotonic()
                last_health = started
                try:
                    while True:
                        try:
                            code = process.wait(timeout=5)
                            break
                        except subprocess.TimeoutExpired:
                            self.guard(reserve=128 * 1024**2)
                            if time.monotonic() - last_health >= 30:
                                self.health()
                                last_health = time.monotonic()
                            if time.monotonic() - started > timeout:
                                raise RuntimeError("Rehearsal command deadline")
                    if code:
                        raise RuntimeError("Rehearsal command failed; see private phase log")
                except BaseException:
                    if process.poll() is None:
                        os.killpg(process.pid, signal.SIGTERM)
                        try:
                            process.wait(timeout=15)
                        except subprocess.TimeoutExpired:
                            os.killpg(process.pid, signal.SIGKILL)
                            process.wait()
                    raise
            return ""
        result = subprocess.run(list(map(str, args)), input=input, capture_output=True, text=True, timeout=timeout, env=env)
        self.log.write(result.stdout + result.stderr + "\n")
        if result.returncode:
            raise RuntimeError("Rehearsal command failed; see private commands.log")
        return result.stdout

    def docker(self, args, **kwargs):
        return self.run(self.docker_args(args), **kwargs)

    def health(self):
        for path, expected in self.foundation["before"]["hashes"].items():
            if not Path(path).is_file() or digest(path) != expected:
                raise RuntimeError("Protected native configuration changed")
        for unit, expected in self.foundation["before"]["services"].items():
            current = subprocess.run(["systemctl", "is-active", unit], capture_output=True, text=True, timeout=15).stdout.strip()
            if current != expected:
                raise RuntimeError("Protected native service changed")
        status = subprocess.check_output(["supervisorctl", "status"], text=True, timeout=15).splitlines()
        native = [line for line in status if line.startswith(("bench-web:", "bench-workers:", "bench-redis:"))]
        if len(native) != 7 or any("RUNNING" not in line for line in native):
            raise RuntimeError("Native Bench health changed")
        pids = {line.split()[0]: re.search(r"pid (\d+)", line).group(1) for line in native}
        if pids != self.build["native_pids"]:
            raise RuntimeError("Native Bench process identity changed")
        if subprocess.check_output(["iptables", "-S", "FORWARD"], text=True, timeout=15) != self.foundation["before"]["forward"]:
            raise RuntimeError("Host forwarding changed")
        info = json.loads(self.docker(["info", "--format", "{{json .}} "]))
        if "name=rootless" not in info.get("SecurityOptions", []) or info.get("DockerRootDir") != "/home/dt-containers/.local/share/docker":
            raise RuntimeError("Wrong rehearsal daemon")
        cg = Path("/sys/fs/cgroup/user.slice/user-5001.slice")
        if (cg / "memory.max").read_text().strip() != "3221225472" or (cg / "cpu.max").read_text().strip() != "200000 100000":
            raise RuntimeError("Aggregate rehearsal resources not enforced")
        if json.loads((BENCH / "sites" / SITE / "site_config.json").read_text()).get("maintenance_mode") != 1:
            raise RuntimeError("Native pilot maintenance changed")
        self.guard()

    def step(self, name, action):
        if name in self.state["steps"]:
            return
        self.health()
        self.progress("Rehearsal step: " + name)
        action()
        self.state["steps"].append(name)
        self.save()
        self.health()

    def images(self):
        expected = {"mariadb": "10.11.14", "redis": "8.8.0"}
        metadata = self.request["images"]
        if sum(row["compressed_layer_bytes"] for row in metadata["images"]) != 159_639_248:
            raise RuntimeError("Unapproved image payload budget")
        for row in metadata["images"]:
            if expected.get(row["repository"]) != row["version"] or not re.fullmatch(r"sha256:[a-f0-9]{64}", row["amd64_digest"]):
                raise RuntimeError("Unapproved rehearsal image")
            reference = row["repository"] + "@" + row["amd64_digest"]
            known = self.state.setdefault("images", {}).get(row["repository"])
            if known:
                image = json.loads(self.docker(["image", "inspect", reference]))[0]
                if image["Id"] != known["id"]:
                    raise RuntimeError("Pinned rehearsal image changed")
                continue
            budget = row["compressed_layer_bytes"] + 5_000_000
            if self.state["download_accounted_bytes"] + budget > DOWNLOAD_CAP:
                raise RuntimeError("Approved image transfer budget exceeded")
            self.state["download_accounted_bytes"] += budget
            self.save()
            self.progress("Pulling pinned " + row["repository"] + " " + row["version"] + " directly on VM")
            self.docker(["pull", "--platform=linux/amd64", reference], timeout=900, logfile=WORK / (row["repository"] + "-pull.log"))
            image = json.loads(self.docker(["image", "inspect", reference]))[0]
            if reference not in image.get("RepoDigests", []) or image.get("Architecture") != "amd64" or image.get("Os") != "linux":
                raise RuntimeError("Image digest/platform mismatch")
            executable = "mariadbd" if row["repository"] == "mariadb" else "redis-server"
            version = self.docker(["run", "--rm", "--pull=never", "--network=none", "--read-only", "--cap-drop=ALL",
                                   "--security-opt=no-new-privileges", "--memory=256m", "--cpus=0.25", "--pids-limit=64",
                                   "--entrypoint", executable, reference, "--version"], timeout=120)
            pattern = r"\b10\.11\.14-MariaDB\b" if row["repository"] == "mariadb" else r"\bv=8\.8\.0(?:\s|$)"
            if not re.search(pattern, version):
                raise RuntimeError("Rehearsal image binary version mismatch")
            self.state["images"][row["repository"]] = {"reference": reference, "id": image["Id"], "version_output": version.strip()}
            self.save()
            self.progress("Pinned " + row["repository"] + " runtime verified")

    def inspect_optional(self, kind, name):
        if kind not in {"container", "network", "volume"}:
            raise RuntimeError("Unexpected resource type")
        result = subprocess.run(self.docker_args([kind, "inspect", name]), capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            return json.loads(result.stdout)[0]
        if not re.search(r"no such|not found", result.stderr, re.I):
            raise RuntimeError("Resource inspection failed; do not assume empty")
        return None

    def owned(self, kind, name):
        item = self.inspect_optional(kind, name)
        known = self.state["resources"][kind + "s"].get(name)
        if item is None or known is None:
            raise RuntimeError("Unrecognized/missing rehearsal resource")
        labels = item["Config"].get("Labels", {}) if kind == "container" else item.get("Labels", {})
        identifier = item["Name"] if kind == "volume" else item["Id"]
        if labels.get("ndf.rehearsal") != PROJECT or identifier != known["id"]:
            raise RuntimeError("Rehearsal ownership/identity mismatch")
        if kind == "volume" and item.get("CreatedAt") != known["created"]:
            raise RuntimeError("Rehearsal volume was replaced")
        return item

    def record_resource(self, kind, name):
        item = self.inspect_optional(kind, name)
        if item is None:
            raise RuntimeError("Created rehearsal resource missing")
        identifier = item["Name"] if kind == "volume" else item["Id"]
        self.state["resources"][kind + "s"][name] = {"id": identifier, "created": item.get("CreatedAt")}
        self.save()
        return self.owned(kind, name)

    def network(self):
        existing = self.inspect_optional("network", NETWORK)
        if existing is None:
            if NETWORK in self.state["resources"]["networks"]:
                raise RuntimeError("Rehearsal network vanished; no automatic recreation")
            self.docker(["network", "create", "--internal", "--label", LABEL, NETWORK])
            existing = self.record_resource("network", NETWORK)
        else:
            existing = self.owned("network", NETWORK)
        if not existing.get("Internal") or existing.get("EnableIPv6"):
            raise RuntimeError("Rehearsal network must be internal-only IPv4")

    def volume(self, role):
        name = PROJECT + "-" + role
        existing = self.inspect_optional("volume", name)
        if existing is None:
            if name in self.state["resources"]["volumes"]:
                raise RuntimeError("Rehearsal volume vanished; no recreation")
            self.docker(["volume", "create", "--label", LABEL, "--label", "ndf.role=" + role, name])
            self.record_resource("volume", name)
        else:
            self.owned("volume", name)
        return name

    def write_private(self, name, value, mode=0o600):
        if Path(name).name != name:
            raise RuntimeError("Unexpected private config name")
        path = DEPLOY / name
        if path.is_symlink():
            raise RuntimeError("Private configuration symlink")
        if path.exists():
            if path.read_text() != value:
                raise RuntimeError("Existing private config differs; no overwrite")
            return path
        with open(path, "x") as target:
            target.write(value)
        os.chown(path, 5001, 5001)
        path.chmod(mode)
        return path

    def deployment(self):
        if DEPLOY.is_symlink():
            raise RuntimeError("Unsafe deployment path")
        marker = DEPLOY / "rehearsal-marker.json"
        if DEPLOY.exists():
            if not marker.is_file() or json.loads(marker.read_text()).get("project") != PROJECT or DEPLOY.stat().st_uid != 5001:
                raise RuntimeError("Unknown existing deployment directory")
        else:
            if not DEPLOY.parent.exists():
                DEPLOY.parent.mkdir(mode=0o700)
                os.chown(DEPLOY.parent, 5001, 5001)
            DEPLOY.mkdir(mode=0o700)
            os.chown(DEPLOY, 5001, 5001)
            self.write_private(marker.name, json.dumps({"project": PROJECT, "purpose": "isolated rehearsal"}))
        credentials = WORK / "clone-credentials.json"
        if not credentials.exists():
            config = json.loads((BENCH / "sites" / SITE / "site_config.json").read_text())
            database = config["db_name"]
            if not re.fullmatch(r"[A-Za-z0-9_]+", database):
                raise RuntimeError("Unexpected source database identifier")
            credentials.write_text(json.dumps({"database": database, "user": "ndf_rehearsal",
                "root_password": secrets.token_hex(32), "app_password": secrets.token_hex(32),
                "redis_password": secrets.token_hex(32), "operator_password": secrets.token_hex(32)}, indent=2))
        value = json.loads(credentials.read_text())
        self.write_private("db-root-password", value["root_password"] + "\n")
        self.write_private("db-app-password", value["app_password"] + "\n")
        self.write_private("db-root-client.cnf", "[client]\nuser=root\npassword=" + value["root_password"] + "\nprotocol=socket\nsocket=/run/mysqld/mysqld.sock\n")
        for role, policy in (("redis-cache", "allkeys-lru"), ("redis-queue", "noeviction")):
            self.write_private(role + ".conf", "bind 0.0.0.0\nport 6379\nprotected-mode yes\nsave \"\"\nappendonly no\nmaxmemory 64mb\nmaxmemory-policy " + policy + "\nrequirepass " + value["redis_password"] + "\n", mode=0o444)
        self.write_private("synthetic-access.json", json.dumps({"database": value["database"], "user": value["user"],
            "password": value["app_password"], "redis_password": value["redis_password"]}))

    def start_role(self, role, image, args, command=(), entrypoint=None, memory="128m", cpus="0.25", generation=None):
        name = PROJECT + "-" + role + ("-" + generation if generation else "")
        existing = self.inspect_optional("container", name)
        if existing is not None:
            existing = self.owned("container", name)
            if existing["Image"] != image or not existing["State"].get("Running"):
                raise RuntimeError("Existing rehearsal role stopped or image differs; inspect before retry")
            if existing["HostConfig"]["NetworkMode"] != NETWORK or not existing["HostConfig"]["ReadonlyRootfs"]:
                raise RuntimeError("Existing rehearsal role isolation changed")
            if role in {"db", "redis-cache", "redis-queue"} and existing["HostConfig"].get("PortBindings"):
                raise RuntimeError("Rehearsal backend port published")
            return name
        if name in self.state["resources"]["containers"]:
            raise RuntimeError("Rehearsal container vanished; no automatic recreation")
        self.owned("network", NETWORK)
        flags = ["run", "--detach", "--name", name, "--hostname", name, "--label", LABEL,
                 "--label", "ndf.role=" + role, "--pull=never", "--network", NETWORK, "--network-alias", role,
                 "--dns=127.0.0.1", "--dns-option=timeout:1", "--dns-option=attempts:1", "--read-only",
                 "--security-opt=no-new-privileges", "--cap-drop=ALL", "--memory", memory, "--cpus", cpus,
                 "--pids-limit=128", "--log-driver=json-file", "--log-opt=max-size=5m", "--log-opt=max-file=2"]
        if entrypoint:
            flags += ["--entrypoint", entrypoint]
        self.docker(flags + list(args) + [image] + list(command), timeout=180)
        self.record_resource("container", name)
        self.state.setdefault("role_containers", {})[role] = name
        self.save()
        return name

    def db_query(self, sql):
        name = PROJECT + "-db"
        container = self.owned("container", name)
        if container["Image"] != self.state["images"]["mariadb"]["id"] or container["HostConfig"].get("PortBindings"):
            raise RuntimeError("Database container identity/exposure mismatch")
        if set(container["NetworkSettings"]["Networks"]) != {NETWORK} or container["HostConfig"]["NetworkMode"] != NETWORK:
            raise RuntimeError("Database attached to an unexpected network")
        return self.docker(["exec", name, "mariadb", "--defaults-extra-file=/run/secrets/db-root-client.cnf",
                            "--batch", "--skip-column-names", "--execute", sql], timeout=30)

    def backend_isolation(self):
        network = self.owned("network", NETWORK)
        if not network.get("Internal") or network.get("EnableIPv6"):
            raise RuntimeError("Rehearsal network isolation drift")
        for role in ("db", "redis-cache", "redis-queue"):
            name = self.state.get("role_containers", {}).get(role, PROJECT + "-" + role)
            item = self.owned("container", name)
            config = item["HostConfig"]
            if not item["State"].get("Running") or set(item["NetworkSettings"]["Networks"]) != {NETWORK}:
                raise RuntimeError("Backend stopped or attached to another network")
            if not config["ReadonlyRootfs"] or config.get("PortBindings") or config.get("Dns") != ["127.0.0.1"]:
                raise RuntimeError("Backend readonly/port/DNS isolation drift")
            if "ALL" not in config.get("CapDrop", []) or "no-new-privileges" not in config.get("SecurityOpt", []):
                raise RuntimeError("Backend privilege isolation drift")
            if any(mount["Type"] == "bind" and not Path(mount["Source"]).is_relative_to(DEPLOY) for mount in item["Mounts"]):
                raise RuntimeError("Backend bind source outside private deployment")

    def isolation(self):
        if "images_verified" not in self.state["steps"]:
            raise RuntimeError("Image verification must precede isolation")
        self.network()
        self.deployment()
        db = self.volume("db-data")
        common_caps = ["--cap-add=CHOWN", "--cap-add=FOWNER", "--cap-add=DAC_OVERRIDE", "--cap-add=SETUID", "--cap-add=SETGID"]
        timezone_name = Path("/etc/timezone").read_text().strip()
        if timezone_name != "Europe/Berlin":
            raise RuntimeError("Native server timezone drift; review clone settings")
        creds = json.loads((WORK / "clone-credentials.json").read_text())
        args = [*common_caps, "--mount", "type=volume,src=" + db + ",dst=/var/lib/mysql",
                "--tmpfs=/tmp:rw,nosuid,nodev,size=67108864,mode=1777",
                "--tmpfs=/run/mysqld:rw,nosuid,nodev,size=16777216,mode=0755",
                "--env=TZ=" + timezone_name, "--env=MARIADB_ROOT_PASSWORD_FILE=/run/secrets/db-root-password",
                "--env=MARIADB_PASSWORD_FILE=/run/secrets/db-app-password", "--env=MARIADB_USER=" + creds["user"],
                "--env=MARIADB_DATABASE=" + creds["database"]]
        for filename in ("db-root-password", "db-app-password", "db-root-client.cnf"):
            args += ["--mount", "type=bind,src=" + str(DEPLOY / filename) + ",dst=/run/secrets/" + filename + ",readonly"]
        self.start_role("db", self.state["images"]["mariadb"]["id"], args,
            ["--bind-address=0.0.0.0", "--skip-name-resolve", "--event-scheduler=OFF", "--server-id=65001",
             "--character-set-server=utf8mb4", "--collation-server=utf8mb4_general_ci", "--max-allowed-packet=16777216",
             "--innodb-buffer-pool-size=128M", "--sql-mode=STRICT_TRANS_TABLES,ERROR_FOR_DIVISION_BY_ZERO,NO_AUTO_CREATE_USER,NO_ENGINE_SUBSTITUTION"],
             memory="768m", cpus="0.75")
        for role in ("redis-cache", "redis-queue"):
            data = self.volume(role + "-data")
            # Official 8.8 entrypoint uses setpriv capability-bounding changes.
            # Bypass it rather than granting SETPCAP: no persistence/host writes
            # are required, so run the exact server directly as image user redis.
            self.start_role(role, self.state["images"]["redis"]["id"], ["--user=redis",
                "--mount", "type=volume,src=" + data + ",dst=/data", "--mount",
                "type=bind,src=" + str(DEPLOY / (role + ".conf")) + ",dst=/etc/ndf-redis.conf,readonly"],
                ["/etc/ndf-redis.conf"], entrypoint="redis-server", generation="nonroot")
        deadline = time.monotonic() + 180
        identity = None
        while time.monotonic() < deadline:
            self.guard(reserve=256 * 1024**2)
            try:
                value = self.db_query("SELECT VERSION(),@@hostname,@@server_id,@@sql_mode,@@character_set_server,@@collation_server,@@time_zone,@@max_allowed_packet,TIMESTAMPDIFF(SECOND,UTC_TIMESTAMP(),NOW());")
                identity = value.strip().split("\t")
                break
            except RuntimeError:
                container = self.owned("container", PROJECT + "-db")
                if not container["State"].get("Running"):
                    raise RuntimeError("Rehearsal database initialization stopped; inspect private container logs")
                time.sleep(2)
        if not identity or not identity[0].startswith("10.11.14-MariaDB") or identity[1] != PROJECT + "-db" or identity[2] != "65001":
            raise RuntimeError("Rehearsal database identity/readiness failed")
        if identity[3:] != ["STRICT_TRANS_TABLES,ERROR_FOR_DIVISION_BY_ZERO,NO_AUTO_CREATE_USER,NO_ENGINE_SUBSTITUTION",
                            "utf8mb4", "utf8mb4_general_ci", "SYSTEM", "16777216", "3600"]:
            raise RuntimeError("Rehearsal database settings differ from approved native baseline")
        empty = self.db_query("SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA='" + creds["database"] + "';")
        if empty.strip() != "0":
            raise RuntimeError("Rehearsal destination not empty; no overwrite")
        code = r'''import json,pathlib,socket,pymysql,redis
c=json.loads(pathlib.Path('/run/synthetic-access.json').read_text())
routes=pathlib.Path('/proc/net/route').read_text().splitlines()[1:]
if any(line.split()[1]=='00000000' for line in routes):raise RuntimeError('Unexpected runtime default route')
db=pymysql.connect(host='db',user=c['user'],password=c['password'],database=c['database'],connect_timeout=3)
with db.cursor() as cur:
 cur.execute('SELECT @@hostname,@@server_id');identity=cur.fetchone()
if identity!=('ndf-dalali-rehearsal-v1-db',65001):raise RuntimeError('Unexpected DB target')
db.close()
for host in ['redis-cache','redis-queue']:
 r=redis.Redis(host=host,password=c['redis_password'],socket_timeout=3)
 if not r.ping() or r.dbsize()!=0:raise RuntimeError('Redis not fresh/private')
for host,port in [('1.1.1.1',443),('10.0.0.1',3306),('127.0.0.1',8081),('127.0.0.1',11000),('127.0.0.1',13000)]:
 s=socket.socket();s.settimeout(1)
 try:connected=s.connect_ex((host,port))==0
 except OSError:connected=False
 finally:s.close()
 if connected:raise RuntimeError('Unexpected external/native reachability')
try:socket.getaddrinfo('example.com',443)
except socket.gaierror:pass
else:raise RuntimeError('Unexpected external DNS resolution')
print(json.dumps({'internal_db':True,'fresh_redis':True,'no_default_route':True,'negative_external_native_tcp':True,'external_dns_blocked':True,'customer_data_loaded':False}))'''
        output = self.docker(["run", "--rm", "--pull=never", "--label", LABEL, "--label=ndf.role=synthetic-probe",
            "--network", NETWORK, "--dns=127.0.0.1", "--dns-option=timeout:1", "--dns-option=attempts:1",
            "--read-only", "--user=0:0", "--cap-drop=ALL", "--security-opt=no-new-privileges",
            "--memory=256m", "--cpus=0.25", "--pids-limit=64", "--mount",
            "type=bind,src=" + str(DEPLOY / "synthetic-access.json") + ",dst=/run/synthetic-access.json,readonly",
            APP_IMAGE, "/home/frappe/bench/env/bin/python", "-c", code], timeout=120)
        proof = json.loads(output)
        self.state["isolation_proof"] = proof
        self.state["db_identity"] = identity
        (WORK / "isolation.json").write_text(json.dumps(proof, indent=2))
        self.save()
        self.progress("Synthetic private DB/Redis, resource and no-egress checks passed; no customer data copied")

    def snapshot(self):
        if "isolation_verified" not in self.state["steps"] or not all(self.state.get("isolation_proof", {}).get(key) for key in
                ("internal_db", "fresh_redis", "no_default_route", "negative_external_native_tcp", "external_dns_blocked")):
            raise RuntimeError("Passed synthetic isolation required before source collection")
        if self.state.get("source_lock_attempted"):
            raise RuntimeError("Approved single source-lock attempt already used; inspect before another pause")
        self.backend_isolation()
        stage = WORK / "source"
        if stage.exists() or stage.is_symlink():
            raise RuntimeError("Existing/partial source collection requires review; no overwrite")
        for app in self.request["sources"]["apps"]:
            path = BENCH / "apps" / app["app"]
            command = ["sudo", "-n", "-H", "-u", "frappe", "env", "GIT_OPTIONAL_LOCKS=0", "git", "--no-optional-locks",
                       "-c", "core.fsmonitor=false", "-C", str(path)]
            if self.run(command + ["rev-parse", "HEAD"]).strip() != app["commit"] or self.run(command + ["status", "--porcelain", "--untracked-files=normal"]).strip():
                raise RuntimeError("Pilot source revision/worktree drift before snapshot")
        config_path = BENCH / "sites" / SITE / "site_config.json"
        config = json.loads(config_path.read_text())
        common = json.loads((BENCH / "sites/common_site_config.json").read_text())
        if config.get("maintenance_mode") != 1 or not config.get("encryption_key"):
            raise RuntimeError("Source maintenance/key readiness changed")
        if (config.get("db_host") or common.get("db_host") or "localhost") not in {"localhost", "127.0.0.1"}:
            raise RuntimeError("Unexpected source DB host")
        database = config["db_name"]
        if not re.fullmatch(r"[A-Za-z0-9_]+", database):
            raise RuntimeError("Unexpected source DB identifier")
        driver = private_mysql_driver()
        def connect():
            return driver.connect(host="127.0.0.1", port=int(config.get("db_port") or common.get("db_port") or 3306),
                user=config.get("db_user") or database, password=config["db_password"], database=database,
                connect_timeout=3, read_timeout=6, write_timeout=2, autocommit=False)
        connection = connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT TABLE_NAME,ENGINE,TABLE_TYPE FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s ORDER BY TABLE_NAME", (database,))
                objects = cursor.fetchall()
                cursor.execute("SELECT (SELECT COUNT(*) FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=%s),"
                               "(SELECT COUNT(*) FROM information_schema.ROUTINES WHERE ROUTINE_SCHEMA=%s),"
                               "(SELECT COUNT(*) FROM information_schema.EVENTS WHERE EVENT_SCHEMA=%s)", (database, database, database))
                if cursor.fetchone() != (0, 0, 0):
                    raise RuntimeError("Unreviewed DB trigger/routine/event scope")
            connection.rollback()
        finally:
            connection.close()
        tables = [name for name, engine, kind in objects if kind == "BASE TABLE" and engine in {"InnoDB", "MyISAM"}]
        sequences = [name for name, engine, kind in objects if kind == "SEQUENCE" and engine == "InnoDB"]
        if len(objects) != 1010 or len(tables) != 1000 or len(sequences) != 10:
            raise RuntimeError("Approved 1000-table/10-sequence source scope changed")
        self.guard(reserve=2 * 1024**3)
        stage.mkdir(mode=0o700)
        files = stage / "files"
        files.mkdir(mode=0o700)
        before = {}
        for name in ("public", "private"):
            root = BENCH / "sites" / SITE / name / "files"
            if root.is_symlink() or not root.is_dir():
                raise RuntimeError("Unexpected source attachment root")
            before[name] = file_manifest(root)
            def copy(source, target):
                self.guard(reserve=Path(source).stat().st_size)
                return shutil.copy2(source, target)
            shutil.copytree(root, files / name, symlinks=True, copy_function=copy)
            if file_manifest(files / name) != before[name] or file_manifest(root) != before[name]:
                raise RuntimeError("Source attachments changed during private copy")
        assets = BENCH / "sites/assets"
        before["assets"] = file_manifest(assets, allow_assets=True)
        shutil.copytree(assets, stage / "assets", symlinks=True)
        # Copied absolute links must not be dereferenced on host. Compare their
        # raw targets and regular bytes against the approved source manifest.
        assets_copy = {}
        for parent, dirs, names in os.walk(stage / "assets", followlinks=False):
            for name in dirs + names:
                path = Path(parent) / name
                key = str(path.relative_to(stage / "assets"));info = path.lstat()
                if path.is_symlink():assets_copy[key] = {"type": "symlink", "target": os.readlink(path)}
                elif path.is_file():assets_copy[key] = {"type": "file", "bytes": info.st_size, "mode": stat.S_IMODE(info.st_mode), "sha256": digest(path)}
                elif path.is_dir():assets_copy[key] = {"type": "directory", "mode": stat.S_IMODE(info.st_mode)}
                else:raise RuntimeError("Unexpected copied asset type")
        if assets_copy != before["assets"] or file_manifest(assets, allow_assets=True) != before["assets"]:
            raise RuntimeError("Static assets changed during copy")
        source_config_hash = digest(config_path)
        shutil.copyfile(config_path, stage / "site_config.original.json")
        (stage / "file-manifests.json").write_text(json.dumps(before, sort_keys=True))
        (stage / "schema.json").write_text(json.dumps(objects))
        support = {"__name__": "ndf_scoped_dump_support"}
        exec(compile(self.request["checkpoint_support"], "checkpoint.py", "exec"), support)
        original_lock = support["ScopedSchemaLock"]
        counts = {}
        class CountingLock(original_lock):
            def __enter__(inner):
                super().__enter__()
                try:
                    with inner.connection.cursor() as cursor:
                        for name in inner.tables:
                            cursor.execute("SELECT COUNT(*) FROM " + support["identifier"](database) + "." + support["identifier"](name))
                            counts[name] = int(cursor.fetchone()[0])
                    return inner
                except BaseException:
                    super().__exit__(*sys.exc_info())
                    raise
        support["ScopedSchemaLock"] = CountingLock
        self.health()
        self.state["source_lock_attempted"] = True
        self.save()
        self.progress("Taking the single approved pilot-only bounded DB snapshot; native services stay running")
        env = dict(os.environ, MYSQL_PWD=config["db_password"])
        dump = shutil.which("mariadb-dump")
        args = [dump, "--host", "127.0.0.1", "--port", str(config.get("db_port") or common.get("db_port") or 3306),
                "--protocol=TCP", "--user", config.get("db_user") or database, "--skip-lock-tables", "--no-tablespaces",
                "--quick", "--hex-blob", "--routines", "--events", "--triggers", database]
        result = support["locked_dump"](connect, database, tables, args, env, stage / "database.sql.gz", stage / "dump.log", sequences=sequences)
        if len(counts) != 1000:
            raise RuntimeError("Locked source row-count manifest incomplete")
        for name in ("public", "private"):
            if file_manifest(BENCH / "sites" / SITE / name / "files") != before[name]:
                raise RuntimeError("Source attachments changed across DB snapshot; do not restore")
        if digest(config_path) != source_config_hash or file_manifest(assets, allow_assets=True) != before["assets"]:
            raise RuntimeError("Source configuration/assets drift across snapshot")
        (stage / "table-row-counts.json").write_text(json.dumps(counts, sort_keys=True))
        result.update({"database_gzip_sha256": digest(stage / "database.sql.gz"), "not_a_final_cutover_snapshot": True,
                       "source_file_bytes": sum(row.get("bytes", 0) for name in ("public", "private") for row in before[name].values()),
                       "source_file_count": sum(row["type"] == "file" for name in ("public", "private") for row in before[name].values()),
                       "native_config_unchanged": True})
        (stage / "snapshot-result.json").write_text(json.dumps(result, indent=2))
        self.state["snapshot"] = result
        self.save()
        self.progress("Pilot lock released; dump/count/file manifests verified (rehearsal copy only)")

    def pipe_files(self, source, destination, logfile, archive=False):
        """VM-local tar stream into a verified new container volume; never logs tar bytes."""
        self.guard(reserve=512 * 1024**2)
        with open(logfile, "ab") as log:
            command = ["gzip", "--decompress", "--stdout", str(source)] if archive else ["tar", "--create", "--file=-", "--numeric-owner", "-C", str(source), "."]
            producer = subprocess.Popen(command,
                                        stdout=subprocess.PIPE, stderr=log, start_new_session=True)
            consumer = subprocess.Popen(self.docker_args(["cp", "-", destination]), stdin=producer.stdout,
                                        stdout=log, stderr=log, start_new_session=True)
            producer.stdout.close()
            started = time.monotonic()
            try:
                while producer.poll() is None or consumer.poll() is None:
                    self.guard(reserve=128 * 1024**2)
                    if time.monotonic() - started > 600:
                        raise RuntimeError("Private volume copy deadline")
                    time.sleep(1)
                if producer.returncode or consumer.returncode:
                    raise RuntimeError("Private volume copy failed; no automatic replay")
            finally:
                for process in (producer, consumer):
                    if process.poll() is None:
                        os.killpg(process.pid, signal.SIGTERM)
                        try:process.wait(timeout=15)
                        except subprocess.TimeoutExpired:
                            os.killpg(process.pid, signal.SIGKILL)
                            process.wait()

    def restore_database(self):
        if "database_restored" in self.state["steps"]:
            return
        self.backend_isolation()
        stage = WORK / "source"
        if digest(stage / "database.sql.gz") != self.state["snapshot"]["database_gzip_sha256"]:
            raise RuntimeError("Source dump digest changed")
        creds = json.loads((WORK / "clone-credentials.json").read_text())
        if "database_import_completed" not in self.state["steps"]:
            if self.state.get("database_import_attempted"):
                raise RuntimeError("Partial clone import requires review; no overwrite/reimport")
            identity = self.db_query("SELECT @@hostname,@@server_id;").strip().split("\t")
            if identity != [PROJECT + "-db", "65001"]:
                raise RuntimeError("Unexpected restore server identity")
            if self.db_query("SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA='" + creds["database"] + "';").strip() != "0":
                raise RuntimeError("Restore target is not empty; no overwrite")
            self.state["database_import_attempted"] = True
            self.save()
            self.progress("Restoring approved dump into new private DB only; source DB connection is not used")
            errors = []
            with open(WORK / "restore-database.log", "ab") as log:
                process = subprocess.Popen(self.docker_args(["exec", "--interactive", PROJECT + "-db", "mariadb",
                    "--defaults-extra-file=/run/secrets/db-root-client.cnf", "--binary-mode=1", creds["database"]]),
                    stdin=subprocess.PIPE, stdout=log, stderr=log, start_new_session=True)
                def feed():
                    try:
                        with gzip.open(stage / "database.sql.gz", "rb") as source:
                            for block in iter(lambda: source.read(1024 * 1024), b""):
                                process.stdin.write(block)
                    except Exception as error:
                        errors.append(type(error).__name__)
                    finally:
                        try:process.stdin.close()
                        except OSError:pass
                writer = threading.Thread(target=feed, daemon=True)
                writer.start()
                started = time.monotonic()
                try:
                    while True:
                        try:
                            code = process.wait(timeout=2)
                            break
                        except subprocess.TimeoutExpired:
                            self.guard(reserve=128 * 1024**2)
                            if time.monotonic() - started > 900:
                                raise RuntimeError("Clone database restore deadline")
                    writer.join(timeout=10)
                    if code or writer.is_alive() or errors:
                        raise RuntimeError("Clone SQL restore failed; retain partial DB for review")
                finally:
                    if process.poll() is None:
                        os.killpg(process.pid, signal.SIGTERM)
                        try:process.wait(timeout=15)
                        except subprocess.TimeoutExpired:
                            os.killpg(process.pid, signal.SIGKILL)
                            process.wait()
            self.state["steps"].append("database_import_completed")
            self.save()
        boundaries = {}
        pattern = re.compile(r"^DO SETVAL\(`((?:``|[^`])+)`, (-?\d+), 0\);$")
        with gzip.open(stage / "database.sql.gz", "rt") as source:
            for line in source:
                match = pattern.match(line.strip())
                if match:
                    boundaries[match.group(1).replace("``", "`")] = int(match.group(2))
                    if len(boundaries) == 10:break
        if len(boundaries) != 10:
            raise RuntimeError("Dump sequence-boundary manifest incomplete")
        counts = json.loads((stage / "table-row-counts.json").read_text())
        self.write_private("restore-counts.json", json.dumps({"tables": counts, "sequences": boundaries}))
        code = r'''import json,pathlib,pymysql
c=json.loads(pathlib.Path('/run/access.json').read_text());expected=json.loads(pathlib.Path('/run/counts.json').read_text())
db=pymysql.connect(host='db',user=c['user'],password=c['password'],database=c['database'],connect_timeout=3,read_timeout=15)
with db.cursor() as cur:
 cur.execute('SELECT @@hostname,@@server_id')
 if cur.fetchone()!=('ndf-dalali-rehearsal-v1-db',65001):raise RuntimeError('Unexpected target DB')
 cur.execute('SELECT TABLE_NAME,TABLE_TYPE FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s',(c['database'],))
 rows=cur.fetchall();tables={n for n,k in rows if k=='BASE TABLE'};sequences={n for n,k in rows if k=='SEQUENCE'}
 if tables!=set(expected['tables']) or sequences!=set(expected['sequences']):raise RuntimeError('Restored object scope differs')
 for name,value in expected['tables'].items():
  cur.execute('SELECT COUNT(*) FROM `'+name.replace('`','``')+'`')
  if cur.fetchone()[0]!=value:raise RuntimeError('Restored row count differs')
 for name,value in expected['sequences'].items():
  cur.execute('SELECT next_not_cached_value FROM `'+name.replace('`','``')+'`')
  if cur.fetchone()[0]!=value:raise RuntimeError('Restored sequence boundary differs')
db.close()
print(json.dumps({'base_tables':len(tables),'sequences':len(sequences),'all_locked_row_counts_match':True,'all_sequence_boundaries_match':True}))'''
        output = self.docker(["run", "--rm", "--pull=never", "--label", LABEL, "--label=ndf.role=restore-count-probe",
            "--network", NETWORK, "--dns=127.0.0.1", "--dns-option=timeout:1", "--dns-option=attempts:1", "--read-only",
            "--user=0:0", "--cap-drop=ALL", "--security-opt=no-new-privileges", "--memory=256m", "--cpus=0.25", "--pids-limit=64",
            "--mount", "type=bind,src=" + str(DEPLOY / "synthetic-access.json") + ",dst=/run/access.json,readonly",
            "--mount", "type=bind,src=" + str(DEPLOY / "restore-counts.json") + ",dst=/run/counts.json,readonly",
            APP_IMAGE, "/home/frappe/bench/env/bin/python", "-c", code], timeout=300)
        result = json.loads(output)
        (WORK / "restore-database-result.json").write_text(json.dumps(result, indent=2))
        self.state["restore_database"] = result
        self.state["steps"].append("database_restored")
        self.save()
        self.progress("Clone DB restored: all table row counts and ten sequence boundaries match locked snapshot")

    def restore_files(self):
        if "site_files_restored" in self.state["steps"]:
            return
        self.backend_isolation()
        source = WORK / "source"
        expected = json.loads((source / "file-manifests.json").read_text())
        for name in ("public", "private"):
            if file_manifest(source / "files" / name) != expected[name]:
                raise RuntimeError("Private attachment staging changed")
        target = WORK / "restore-input"
        if not target.exists():
            target.mkdir(mode=0o700)
            site = target / CLONE_SITE
            site.mkdir(mode=0o700)
            for name in ("public", "private"):
                (site / name).mkdir(mode=0o755 if name == "public" else 0o700)
                shutil.copytree(source / "files" / name, site / name / "files", symlinks=True)
            shutil.copytree(source / "assets", target / "assets", symlinks=True)
            creds = json.loads((WORK / "clone-credentials.json").read_text())
            config = json.loads((source / "site_config.original.json").read_text())
            for name in ("db_socket", "db_root_password", "root_password", "db_ssl_ca", "db_ssl_cert", "db_ssl_key"):
                config.pop(name, None)
            endpoints = {"redis_cache": "redis://:" + creds["redis_password"] + "@redis-cache:6379",
                         "redis_queue": "redis://:" + creds["redis_password"] + "@redis-queue:6379",
                         "redis_socketio": "redis://:" + creds["redis_password"] + "@redis-cache:6379"}
            config.update({"db_host": "db", "db_port": 3306, "db_user": creds["user"], "db_password": creds["app_password"],
                "maintenance_mode": 1, "pause_scheduler": 1, "mute_emails": 1, "disable_email_queue": 1,
                "disable_webhooks": 1, "developer_mode": 0, "host_name": "http://" + CLONE_SITE + ":8080", **endpoints})
            (site / "site_config.json").write_text(json.dumps(config, indent=2))
            common = {"db_host": "db", "db_port": 3306, "pause_scheduler": 1, "developer_mode": 0,
                      "serve_default_site": True, "default_site": CLONE_SITE, "socketio_port": 9000, **endpoints}
            (target / "common_site_config.json").write_text(json.dumps(common, indent=2))
            apps = [line.strip() for line in (BENCH / "sites/apps.txt").read_text().splitlines() if line.strip()]
            if set(apps) != set(self.build["source_apps"]) or any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", app) for app in apps):
                raise RuntimeError("Native available-app list differs from preserved image")
            (target / "apps.txt").write_text("\n".join(apps) + "\n")
            known = {row["app"]: {key: row[key] for key in ("version", "branch", "commit")} for row in self.request["sources"]["apps"]}
            (target / "apps.json").write_text(json.dumps({app: known.get(app, {}) for app in apps}))
            (target / "currentsite.txt").write_text(CLONE_SITE + "\n")
            self.state["restore_input_complete"] = True
            self.save()
        elif not self.state.get("restore_input_complete"):
            raise RuntimeError("Partial restore input requires review; no overwrite")
        sites = self.volume("sites")
        logs = self.volume("app-logs")
        name = PROJECT + "-initialize-sites"
        item = self.inspect_optional("container", name)
        if item is None:
            if name in self.state["resources"]["containers"]:
                raise RuntimeError("Copy helper vanished; inspect before retry")
            self.docker(["run", "--detach", "--name", name, "--label", LABEL, "--label=ndf.role=initialize-sites",
                "--pull=never", "--network=none", "--read-only", "--user=0:0", "--cap-drop=ALL", "--cap-add=CHOWN",
                "--cap-add=FOWNER", "--cap-add=DAC_OVERRIDE", "--security-opt=no-new-privileges", "--memory=128m", "--cpus=0.25", "--pids-limit=32",
                "--mount", "type=volume,src=" + sites + ",dst=/home/frappe/bench/sites",
                "--mount", "type=volume,src=" + logs + ",dst=/home/frappe/bench/logs",
                "--tmpfs=/home/frappe/frappe-bench/sites:ro,nosuid,nodev,size=1048576",
                "--tmpfs=/home/frappe/frappe-bench/logs:ro,nosuid,nodev,size=1048576",
                APP_IMAGE, "/home/frappe/bench/env/bin/python", "-c", "import time; time.sleep(1800)"])
            item = self.record_resource("container", name)
        else:
            item = self.owned("container", name)
        if not item["State"].get("Running") or item["HostConfig"]["NetworkMode"] != "none" or any(m["Type"] == "bind" for m in item["Mounts"]):
            raise RuntimeError("Unexpected private copy helper isolation")
        if not self.state.get("site_files_copy_completed"):
            if self.state.get("site_files_copy_attempted"):
                raise RuntimeError("Partial file population requires review; no overwrite")
            empty = self.docker(["exec", name, "/home/frappe/bench/env/bin/python", "-c",
                                "import os; assert not os.listdir('/home/frappe/bench/sites'); print('empty')"])
            if empty.strip() != "empty":raise RuntimeError("New sites volume not empty")
            self.state["site_files_copy_attempted"] = True
            self.save()
            self.pipe_files(target, name + ":/home/frappe/bench/sites", WORK / "restore-files-copy.log")
            self.state["site_files_copy_completed"] = True
            self.save()
        setup = r'''import json,os,pathlib
for root in ['/home/frappe/bench/sites','/home/frappe/bench/logs']:
 for parent,dirs,files in os.walk(root,followlinks=False):
  os.chown(parent,1000,1000)
  for name in dirs+files:os.chown(os.path.join(parent,name),1000,1000,follow_symlinks=False)
for path in [pathlib.Path('/home/frappe/bench/sites/common_site_config.json'),pathlib.Path('/home/frappe/bench/sites/dalali-rehearsal.invalid/site_config.json')]:path.chmod(0o600)
root=pathlib.Path('/home/frappe/bench/sites/dalali-rehearsal.invalid/private')
for parent,dirs,files in os.walk(root):
 os.chmod(parent,0o700)
 for name in files:os.chmod(os.path.join(parent,name),0o600)
print('private permissions ready')'''
        self.docker(["exec", name, "/home/frappe/bench/env/bin/python", "-c", setup])
        check = r'''import hashlib,json,os,pathlib,sys
expected=json.load(sys.stdin);sites=pathlib.Path('/home/frappe/bench/sites');total=0
for name in ['public','private']:
 root=sites/'dalali-rehearsal.invalid'/name/'files';actual={}
 for parent,dirs,files in os.walk(root,followlinks=False):
  for n in files:
   p=pathlib.Path(parent)/n
   if p.is_symlink():raise RuntimeError('Unexpected copied attachment link')
   h=hashlib.sha256()
   with p.open('rb') as f:
    for block in iter(lambda:f.read(1048576),b''):h.update(block)
   actual[str(p.relative_to(root))]=(p.stat().st_size,h.hexdigest())
 wanted={p:(v['bytes'],v['sha256']) for p,v in expected[name].items() if v['type']=='file'}
 if actual!=wanted:raise RuntimeError('Restored attachment manifest differs')
 total+=len(actual)
for relative,row in expected['assets'].items():
 p=sites/'assets'/relative
 if row['type']=='symlink':
  if not p.is_symlink() or os.readlink(p)!=row['target'] or not p.resolve().exists():raise RuntimeError('Asset link unresolved/changed')
 elif row['type']=='file':
  if p.is_symlink() or hashlib.sha256(p.read_bytes()).hexdigest()!=row['sha256']:raise RuntimeError('Asset file changed')
config=json.loads((sites/'dalali-rehearsal.invalid/site_config.json').read_text())
if config.get('db_host')!='db' or config.get('pause_scheduler')!=1 or not config.get('encryption_key'):raise RuntimeError('Clone config isolation invalid')
print(json.dumps({'file_count':total,'all_attachment_bytes_match':True,'asset_links_resolved':True,'private_config_permissions':True}))'''
        output = self.docker(["exec", "--interactive", name, "/home/frappe/bench/env/bin/python", "-c", check],
                             input=json.dumps(expected), timeout=300)
        result = json.loads(output)
        self.owned("container", name)
        self.docker(["stop", "--time=5", name])
        self.state["restore_files"] = result
        self.state["steps"].append("site_files_restored")
        (WORK / "restore-files-result.json").write_text(json.dumps(result, indent=2))
        self.save()
        self.progress("All copied attachments/assets verified in new private sites volume; source unchanged")

    def restore(self):
        if "source_snapshot_created" not in self.state["steps"]:
            raise RuntimeError("Verified source snapshot required before restore")
        self.restore_database()
        self.health()
        self.restore_files()

    def pdf_mounts(self):
        if "pdf_runtime_verified" not in self.state["steps"]:
            raise RuntimeError("Native PDF runtime must pass before app mount")
        return ["--mount", "type=bind,src=" + str(DEPLOY / "pdf-runtime") + ",dst=/opt/ndf-pdf,readonly",
                "--mount", "type=bind,src=" + str(DEPLOY / "wkhtmltopdf") + ",dst=/opt/ndf-native/bin/wkhtmltopdf,readonly"]

    def pdf_runtime(self):
        if "isolation_verified" not in self.state["steps"]:
            raise RuntimeError("Passed isolation required before private PDF preparation")
        prefix = DEPLOY / "pdf-runtime"
        if not self.state.get("pdf_copy_complete"):
            if prefix.exists() or prefix.is_symlink():
                raise RuntimeError("Partial private PDF copy requires review; no overwrite")
            source = Path("/usr/bin/wkhtmltopdf")
            plugin = Path("/usr/lib/x86_64-linux-gnu/qt5/plugins/platforms/libqoffscreen.so")
            for path in (source, plugin):
                if not path.is_file() or path.stat().st_uid != 0:
                    raise RuntimeError("Expected native PDF binary/offscreen plugin missing")
            if self.run([str(source), "--version"]).strip() != "wkhtmltopdf 0.12.6":
                raise RuntimeError("Native PDF version drift")
            dependencies = {}
            excluded = {"libc.so.6", "libm.so.6", "libpthread.so.0", "libdl.so.2", "librt.so.1"}
            for program in (source, plugin):
                text = self.run(["ldd", str(program)])
                if "not found" in text:
                    raise RuntimeError("Native PDF dependency missing on source")
                for line in text.splitlines():
                    match = re.match(r"\s*(\S+) => (/\S+) ", line)
                    if not match or match.group(1) in excluded:
                        continue
                    name, original = match.groups()
                    original = Path(original).resolve()
                    if not original.is_relative_to(Path("/usr/lib")) or original.stat().st_uid != 0:
                        raise RuntimeError("Unreviewed native PDF dependency path")
                    dependencies[name] = original
            self.guard(reserve=sum(path.stat().st_size for path in dependencies.values()) + 64 * 1024**2)
            prefix.mkdir(mode=0o755)
            prefix.chmod(0o755)
            for name in ("bin", "lib", "plugins", "plugins/platforms"):
                directory = prefix / name
                directory.mkdir(mode=0o755, exist_ok=True)
                directory.chmod(0o755)
            copies = [(source, prefix / "bin/wkhtmltopdf"), (plugin, prefix / "plugins/platforms/libqoffscreen.so")]
            copies += [(path, prefix / "lib" / name) for name, path in dependencies.items()]
            evidence = {}
            for original, destination in copies:
                checksum = digest(original)
                self.guard(reserve=original.stat().st_size)
                shutil.copyfile(original, destination)
                destination.chmod(0o755 if destination.parent.name == "bin" else 0o644)
                if digest(original) != checksum or digest(destination) != checksum:
                    raise RuntimeError("PDF dependency changed during copy")
                evidence[str(destination.relative_to(prefix))] = {"source": str(original), "sha256": checksum}
            fonts = Path("/usr/share/fonts")
            if any(Path("/usr/local/share/fonts").rglob("*")):
                raise RuntimeError("Native custom-font scope changed; review before copy")
            original_fonts = file_manifest(fonts)
            for parent, dirs, files in os.walk(fonts, followlinks=False):
                for name in dirs + files:
                    if not (Path(parent) / name).resolve().is_relative_to(fonts):
                        raise RuntimeError("Unreviewed external font link")
            shutil.copytree(fonts, prefix / "fonts", symlinks=False)
            if file_manifest(fonts) != original_fonts or file_manifest(prefix / "fonts") != original_fonts:
                raise RuntimeError("Font tree changed during copy")
            for relative, row in original_fonts.items():
                if row["type"] == "file":
                    evidence["fonts/" + relative] = {"source": str(fonts / relative), "sha256": row["sha256"]}
            for parent, dirs, files in os.walk(prefix / "fonts"):
                os.chmod(parent, 0o755)
                for name in files:os.chmod(Path(parent) / name, 0o644)
            font_config = prefix / "fonts.conf"
            font_config.write_text('<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd"><fontconfig><dir>/opt/ndf-pdf/fonts</dir><cachedir>/tmp/ndf-font-cache</cachedir></fontconfig>')
            font_config.chmod(0o644)
            self.write_private("wkhtmltopdf", self.request["pdf_wrapper"], mode=0o555)
            (WORK / "pdf-copy-provenance.json").write_text(json.dumps(evidence, indent=2))
            self.state["pdf_copy_complete"] = True
            self.save()
        else:
            evidence = json.loads((WORK / "pdf-copy-provenance.json").read_text())
            if any(digest(prefix / name) != row["sha256"] for name, row in evidence.items()):
                raise RuntimeError("Private PDF runtime changed")
            if (DEPLOY / "wkhtmltopdf").read_text() != self.request["pdf_wrapper"]:
                raise RuntimeError("Private PDF wrapper changed")
        code = r'''import hashlib,io,json,shutil,subprocess
from pypdf import PdfReader
command=shutil.which('wkhtmltopdf')
if command!='/opt/ndf-native/bin/wkhtmltopdf':raise RuntimeError('Wrong PDF command path')
version=subprocess.check_output([command,'--version'],text=True,timeout=15).strip()
if version!='wkhtmltopdf 0.12.6':raise RuntimeError('PDF patch version differs')
html='<!doctype html><html><head><meta charset="utf-8"></head><body style="font-family:DejaVu Sans">Dalali rehearsal PDF check - Café Nairobi</body></html>'
p=subprocess.run([command,'--quiet','--disable-javascript','--disable-local-file-access','-','-'],input=html.encode(),capture_output=True,timeout=60)
if p.returncode or not p.stdout.startswith(b'%PDF-'):raise RuntimeError('Native copied PDF renderer failed')
reader=PdfReader(io.BytesIO(p.stdout));text=' '.join(page.extract_text() or '' for page in reader.pages)
if not all(word in text for word in ['Dalali','rehearsal','Café','Nairobi']):raise RuntimeError('PDF font/text smoke failed')
print(json.dumps({'version':version,'pages':len(reader.pages),'font_text_smoke':True,'native_copy_used':True,'synthetic_pdf_bytes':len(p.stdout)}))'''
        output = self.docker(["run", "--rm", "--pull=never", "--label", LABEL, "--label=ndf.role=pdf-probe",
            "--network=none", "--read-only", "--user=1000:1000", "--cap-drop=ALL", "--security-opt=no-new-privileges",
            "--memory=512m", "--cpus=0.5", "--pids-limit=96", "--tmpfs=/tmp:rw,nosuid,nodev,size=67108864,mode=1777",
            "--mount", "type=bind,src=" + str(prefix) + ",dst=/opt/ndf-pdf,readonly",
            "--mount", "type=bind,src=" + str(DEPLOY / "wkhtmltopdf") + ",dst=/opt/ndf-native/bin/wkhtmltopdf,readonly",
            APP_IMAGE, "/home/frappe/bench/env/bin/python", "-c", code], timeout=180)
        result = json.loads(output)
        self.state["pdf_runtime"] = result
        (WORK / "pdf-probe.json").write_text(json.dumps(result, indent=2))
        self.save()
        self.progress("Exact native PDF 0.12.6 synthetic rendering/font-text probe passed; no upgrade or live mounts")

    def app_mounts(self, readonly=False):
        sites = self.volume("sites")
        logs = self.volume("app-logs")
        return ["--user=1000:1000", "--env=TZ=Europe/Berlin", "--env=FRAPPE_BENCH_ROOT=/home/frappe/bench",
                "--env=VIRTUAL_ENV=/home/frappe/bench/env", "--env=FRAPPE_SITE=" + CLONE_SITE,
                "--env=FRAPPE_REDIS_CACHE=", "--env=FRAPPE_REDIS_QUEUE=",
                "--mount", "type=volume,src=" + sites + ",dst=/home/frappe/bench/sites" + (",readonly" if readonly else ""),
                "--mount", "type=volume,src=" + logs + ",dst=/home/frappe/bench/logs",
                "--tmpfs=/tmp:rw,nosuid,nodev,size=67108864,mode=1777",
                "--tmpfs=/home/frappe/frappe-bench/sites:ro,nosuid,nodev,size=1048576",
                "--tmpfs=/home/frappe/frappe-bench/logs:ro,nosuid,nodev,size=1048576", *self.pdf_mounts()]

    def rootless_port_api(self, method, path, body=None):
        if method not in {"GET", "POST"} or path != "/v1/ports":
            raise RuntimeError("Unexpected RootlessKit operation")
        api = Path("/run/user/5001/dockerd-rootless/api.sock")
        info = api.stat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != 5001:
            raise RuntimeError("Unexpected dedicated RootlessKit API ownership")
        class LocalConnection(http.client.HTTPConnection):
            def connect(inner):
                inner.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                inner.sock.settimeout(10)
                inner.sock.connect(str(api))
        connection = LocalConnection("rootlesskit")
        try:
            connection.request(method, path, body=json.dumps(body) if body is not None else None,
                               headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            data = response.read(65537)
            if response.status not in {200, 201} or len(data) > 65536:
                raise RuntimeError("Dedicated RootlessKit port API failed")
            return json.loads(data) if data else None
        finally:
            connection.close()

    def loopback_forward(self, frontend_name):
        frontend = self.owned("container", frontend_name)
        network = self.owned("network", NETWORK)
        if not frontend["State"].get("Running") or not network.get("Internal") or set(frontend["NetworkSettings"]["Networks"]) != {NETWORK}:
            raise RuntimeError("Frontend isolation changed before loopback publication")
        child_ip = frontend["NetworkSettings"]["Networks"][NETWORK]["IPAddress"]
        if not any(ipaddress.ip_address(child_ip) in ipaddress.ip_network(row["Subnet"]) for row in network["IPAM"]["Config"] if row.get("Subnet")):
            raise RuntimeError("Frontend address outside owned private network")
        expected = {"proto": "tcp", "parentIP": "127.0.0.1", "parentPort": 18088, "childIP": child_ip, "childPort": 8080}
        ports = self.rootless_port_api("GET", "/v1/ports") or []
        recorded = self.state.get("loopback_forward")
        if recorded:
            matching = [row for row in ports if row["id"] == recorded["id"]]
            if len(matching) != 1 or matching[0].get("spec") != expected:
                raise RuntimeError("Owned loopback mapping missing/changed; no replacement")
        else:
            if any(row.get("spec", {}).get("parentPort") == 18088 for row in ports):
                raise RuntimeError("Unknown existing RootlessKit mapping on rehearsal port")
            if self.run(["ss", "-H", "-lnt", "sport = :18088"]).strip():
                raise RuntimeError("Rehearsal port occupied before manual forwarding")
            created = self.rootless_port_api("POST", "/v1/ports", expected)
            if not created or created.get("spec") != expected:
                raise RuntimeError("Unexpected RootlessKit mapping result; inspect before retry")
            self.state["loopback_forward"] = {"id": created["id"], "spec": expected, "frontend_id": frontend["Id"]}
            self.save()
        addresses = [line.split()[3] for line in self.run(["ss", "-H", "-lnt", "sport = :18088"]).splitlines() if len(line.split()) >= 4]
        if addresses != ["127.0.0.1:18088"]:
            raise RuntimeError("Rehearsal listener not exclusively loopback")

    def application(self):
        if "restore_verified" not in self.state["steps"]:
            raise RuntimeError("Verified DB/file restore required before app start")
        self.backend_isolation()
        self.step("pdf_runtime_verified", self.pdf_runtime)
        control = self.start_role("control", APP_IMAGE, self.app_mounts(),
            ["/home/frappe/bench/env/bin/python", "-c", "import time; time.sleep(7200)"], memory="512m", cpus="0.5")
        if "clone_initialized" not in self.state["steps"]:
            creds = json.loads((WORK / "clone-credentials.json").read_text())
            code = r'''import hashlib,json,pathlib,sys,frappe
from urllib.parse import unquote
from frappe.utils.password import update_password,get_decrypted_password,encrypt,decrypt
payload=json.load(sys.stdin)
site_root=pathlib.Path('/home/frappe/bench/sites/dalali-rehearsal.invalid')
if site_root.is_symlink():raise RuntimeError('Unexpected clone root symlink')
for relative in ['logs','locks','private/backups']:
 directory=site_root/relative
 if directory.is_symlink():raise RuntimeError('Unexpected clone runtime-directory symlink')
 directory.mkdir(parents=True,exist_ok=True,mode=0o700)
frappe.init(site='dalali-rehearsal.invalid',sites_path='/home/frappe/bench/sites');frappe.connect()
identity=frappe.db.sql('SELECT @@hostname,@@server_id')[0]
if tuple(identity)!=('ndf-dalali-rehearsal-v1-db',65001) or frappe.conf.db_host!='db':raise RuntimeError('Unexpected DB before clone writes')
apps=frappe.get_installed_apps()
if apps!=payload['apps']:raise RuntimeError('Installed app order differs')
decoded=0
for doctype,name,field in frappe.db.sql('SELECT doctype,name,fieldname FROM `__Auth` WHERE encrypted=1'):
 value=get_decrypted_password(doctype,name,field)
 if value is None:raise RuntimeError('Encrypted field not decryptable')
 decoded+=1
if decrypt(encrypt('ndf-private-rehearsal-probe'))!='ndf-private-rehearsal-probe':raise RuntimeError('Encryption roundtrip failed')
update_password('Administrator',payload['password']);frappe.db.commit()
private_probe=None
for url, in frappe.db.sql("SELECT file_url FROM tabFile WHERE is_private=1 AND is_folder=0 AND file_url LIKE '/private/files/%%' LIMIT 20"):
 root=pathlib.Path('/home/frappe/bench/sites/dalali-rehearsal.invalid/private/files')
 path=(root.parent.parent/unquote(url).lstrip('/')).resolve()
 if path.is_relative_to(root) and path.is_file():
  private_probe={'url':url,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'bytes':path.stat().st_size};break
result={'installed_apps':apps,'decrypted_field_count':decoded,'encryption_roundtrip':True,'clone_operator_password_set':True,'private_probe':private_probe}
frappe.destroy()
path=pathlib.Path('/home/frappe/bench/sites/dalali-rehearsal.invalid/site_config.json');config=json.loads(path.read_text())
if config.get('db_host')!='db' or config.get('pause_scheduler')!=1:raise RuntimeError('Clone config guard')
config['maintenance_mode']=0
config['cookie_domain']=None
temporary=path.with_suffix('.json.new');temporary.write_text(json.dumps(config,indent=2));temporary.chmod(0o600);temporary.replace(path)
common=path.parent.parent/'common_site_config.json';settings=json.loads(common.read_text())
if settings.get('db_host')!='db' or settings.get('pause_scheduler')!=1:raise RuntimeError('Clone common config guard')
settings.update(webserver_host='web',webserver_port=8000)
temporary=common.with_suffix('.json.new');temporary.write_text(json.dumps(settings,indent=2));temporary.chmod(0o600);temporary.replace(common)
print(json.dumps(result))'''
            output = self.docker(["exec", "--interactive", "--workdir=/home/frappe/bench/sites", control,
                "/usr/local/bin/ndf-native-exec", "/home/frappe/bench/env/bin/python", "-c", code],
                input=json.dumps({"apps": [app["app"] for app in self.request["sources"]["apps"]], "password": creds["operator_password"]}), timeout=180)
            result = json.loads(output)
            self.state["clone_initialized"] = result
            self.state["steps"].append("clone_initialized")
            self.save()
            self.write_private("http-test-access.json", json.dumps({"user": "Administrator", "password": creds["operator_password"],
                "private_probe": result["private_probe"]}))
        self.start_role("web", APP_IMAGE, self.app_mounts(), ["/home/frappe/bench/env/bin/gunicorn", "--bind=0.0.0.0:8000",
            "--chdir=/home/frappe/bench/sites", "--workers=1", "--threads=2", "--timeout=60", "--access-logfile=-", "--error-logfile=-", "frappe.app:application"],
            memory="512m", cpus="0.5")
        self.start_role("socketio", APP_IMAGE, self.app_mounts(), ["node", "/home/frappe/bench/apps/frappe/socketio.js"], memory="128m", cpus="0.25")
        nginx = r'''pid /tmp/nginx.pid;
worker_processes 1;
error_log /dev/stderr warn;
events { worker_connections 256; }
http {
 include /etc/nginx/mime.types;
 default_type application/octet-stream;
 access_log off;
 sendfile on;
 client_body_temp_path /tmp/client_body;
 proxy_temp_path /tmp/proxy;
 fastcgi_temp_path /tmp/fastcgi;
 uwsgi_temp_path /tmp/uwsgi;
 scgi_temp_path /tmp/scgi;
 map $http_upgrade $connection_upgrade { default upgrade; '' close; }
 upstream ndf_web { server web:8000; }
 upstream ndf_socketio { server socketio:9000; }
 server {
  listen 8080;
  server_name _;
  root /home/frappe/bench/sites;
  client_max_body_size 25m;
  add_header X-NDF-Environment private-rehearsal always;
  add_header X-Content-Type-Options nosniff always;
  add_header Content-Security-Policy "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; form-action 'self'; frame-src 'none'; object-src 'none'; base-uri 'self'" always;
  location ~ (^|/)\. { return 404; }
  location ~* (site_config|common_site_config)\.json { return 404; }
  location ~ ^/protected/(.*)$ {
   internal;
   try_files /dalali-rehearsal.invalid/$1 =404;
  }
  location /assets/ { alias /home/frappe/bench/sites/assets/; }
  location /socket.io {
   proxy_http_version 1.1;
   proxy_set_header Upgrade $http_upgrade;
   proxy_set_header Connection $connection_upgrade;
   proxy_set_header Host $host;
   proxy_set_header X-Frappe-Site-Name dalali-rehearsal.invalid;
   proxy_set_header Origin http://$http_host;
   proxy_pass http://ndf_socketio;
  }
  location / {
   try_files /dalali-rehearsal.invalid/public/$uri @ndf_app;
  }
  location @ndf_app {
   proxy_http_version 1.1;
   proxy_set_header Host $http_host;
   proxy_set_header X-Forwarded-Proto http;
   proxy_set_header X-Forwarded-For $remote_addr;
   proxy_set_header X-Frappe-Site-Name dalali-rehearsal.invalid;
   proxy_set_header X-Use-X-Accel-Redirect True;
   proxy_read_timeout 60;
   proxy_redirect off;
   proxy_pass http://ndf_web;
  }
 }
}
'''
        config = self.write_private("nginx-tmpfs.conf", nginx, mode=0o444)
        frontend_name = PROJECT + "-frontend-tmpfs"
        if self.inspect_optional("container", frontend_name) is None:
            listeners = self.run(["ss", "-H", "-lnt", "sport = :18088"]).strip()
            if listeners:
                raise RuntimeError("Loopback rehearsal port is occupied")
        self.start_role("frontend", APP_IMAGE, self.app_mounts(readonly=True) + ["--network-alias=" + CLONE_SITE,
            "--publish=127.0.0.1:18088:8080", "--mount", "type=bind,src=" + str(config) + ",dst=/etc/ndf-rehearsal-nginx.conf,readonly"],
            ["-c", "/etc/ndf-rehearsal-nginx.conf", "-g", "daemon off;"], entrypoint="/usr/sbin/nginx", memory="64m", cpus="0.25", generation="tmpfs")
        frontend = self.owned("container", frontend_name)
        if frontend["HostConfig"]["PortBindings"] != {"8080/tcp": [{"HostIp": "127.0.0.1", "HostPort": "18088"}]}:
            raise RuntimeError("Frontend port not loopback-only")
        if any(entry.startswith("LD_LIBRARY_PATH=") for entry in frontend["Config"].get("Env", [])):
            raise RuntimeError("Vendor Nginx must not inherit native compatibility library override")
        self.loopback_forward(frontend_name)
        ready = False
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            item = self.owned("container", frontend_name)
            if not item["State"].get("Running"):
                raise RuntimeError("Private frontend exited; inspect logs before retry")
            try:
                status = self.run(["curl", "--silent", "--show-error", "--max-time", "5", "--header", "Host: " + CLONE_SITE,
                    "--output", "/dev/null", "--write-out", "%{http_code}", "http://127.0.0.1:18088/api/method/ping"], timeout=10).strip()
                if status == "200":
                    ready = True
                    break
            except RuntimeError:
                pass
            self.guard()
            time.sleep(1)
        if not ready:
            raise RuntimeError("Private frontend/backend HTTP readiness failed")
        self.state["frontend_url"] = "http://127.0.0.1:18088"
        self.save()
        self.progress("Clone application roles started privately; native Dalali still in maintenance, no workers/scheduler")

    def runtime_egress(self):
        self.backend_isolation()
        network = self.owned("network", NETWORK)
        known = {row["id"] for row in self.state["resources"]["containers"].values()}
        if not set(network.get("Containers", {})).issubset(known):
            raise RuntimeError("Unknown container attached to rehearsal network")
        for role in ("control", "web", "socketio", "frontend"):
            name = self.state["role_containers"][role]
            item = self.owned("container", name)
            config = item["HostConfig"]
            if not item["State"].get("Running") or set(item["NetworkSettings"]["Networks"]) != {NETWORK}:
                raise RuntimeError("App role stopped or joined unexpected network")
            if not config["ReadonlyRootfs"] or config.get("Privileged") or config.get("CapAdd") or "ALL" not in config.get("CapDrop", []):
                raise RuntimeError("App role privilege/readonly drift")
            if config.get("Dns") != ["127.0.0.1"] or (role != "frontend" and config.get("PortBindings")):
                raise RuntimeError("App role DNS/port drift")
            if any(mount["Type"] == "bind" and not Path(mount["Source"]).is_relative_to(DEPLOY) for mount in item["Mounts"]):
                raise RuntimeError("App bind source outside private copy/config scope")
        for name in self.state["resources"]["containers"]:
            item = self.owned("container", name)
            if not item["State"].get("Running"):
                continue
            # Inspect process command lines in memory only: never export/log
            # argv that might contain application-supplied sensitive arguments.
            processes = subprocess.run(self.docker_args(["top", name, "-eo", "pid,args"]), capture_output=True, text=True, timeout=20)
            if processes.returncode:
                if not self.owned("container", name)["State"].get("Running"):
                    continue
                raise RuntimeError("Cannot verify rehearsal process roles")
            for line in processes.stdout.splitlines()[1:]:
                try:words = shlex.split(line.split(None, 1)[1])
                except (ValueError, IndexError):raise RuntimeError("Unparseable rehearsal process role")
                if (("worker" in words or "schedule" in words) and any("frappe.utils.bench_helper" in word or Path(word).name in {"bench", "rq"} for word in words)):
                    raise RuntimeError("Unexpected background worker/scheduler in rehearsal")
        self.loopback_forward(self.state["role_containers"]["frontend"])
        code = r'''import json,pathlib,socket
if any(line.split()[1]=='00000000' for line in pathlib.Path('/proc/net/route').read_text().splitlines()[1:]):raise RuntimeError('Unexpected default route')
for host,port in [('1.1.1.1',443),('10.0.0.1',3306),('127.0.0.1',8081),('127.0.0.1',11000),('127.0.0.1',13000)]:
 s=socket.socket();s.settimeout(1)
 try:connected=s.connect_ex((host,port))==0
 except OSError:connected=False
 finally:s.close()
 if connected:raise RuntimeError('Unexpected external/native TCP reachability')
try:socket.getaddrinfo('example.com',443)
except socket.gaierror:pass
else:raise RuntimeError('External DNS unexpectedly available')
print(json.dumps({'no_default_route':True,'external_native_tcp_blocked':True,'external_dns_blocked':True}))'''
        output = self.docker(["exec", self.state["role_containers"]["control"], "/usr/local/bin/ndf-native-exec",
                             "/home/frappe/bench/env/bin/python", "-c", code], timeout=30)
        self.state["runtime_egress"] = json.loads(output)
        self.save()

    def application_checks(self):
        if "application_ready" not in self.state["steps"]:
            raise RuntimeError("HTTP-ready application required before acceptance probes")
        self.runtime_egress()
        control = self.state["role_containers"]["control"]
        if "clone_fixture_created" not in self.state["steps"]:
            self.state.setdefault("fixture_title", "NDF isolated rehearsal " + secrets.token_hex(8))
            self.save()
            code = r'''import hashlib,json,pathlib,sys,frappe
from urllib.parse import unquote
from frappe.utils.pdf import get_pdf
from pypdf import PdfReader
import io
p=json.load(sys.stdin);frappe.init(site='dalali-rehearsal.invalid',sites_path='/home/frappe/bench/sites');frappe.connect()
if tuple(frappe.db.sql('SELECT @@hostname,@@server_id')[0])!=('ndf-dalali-rehearsal-v1-db',65001):raise RuntimeError('Unexpected DB before test writes')
frappe.set_user('Administrator')
name=frappe.db.get_value('Note',{'title':p['title']},'name')
if name:doc=frappe.get_doc('Note',name)
else:doc=frappe.get_doc({'doctype':'Note','title':p['title'],'content':'Synthetic isolated rehearsal fixture. Not production data.','public':0}).insert(ignore_permissions=True)
frappe.db.commit()
private=p.get('private_probe')
if not private:
 file=frappe.get_doc({'doctype':'File','file_name':'ndf-rehearsal-private-proof.txt','is_private':1,
  'attached_to_doctype':'Note','attached_to_name':doc.name,'content':'Synthetic private access proof. Not production data.'}).insert(ignore_permissions=True)
 frappe.db.commit();url=file.file_url;path=(pathlib.Path('/home/frappe/bench/sites/dalali-rehearsal.invalid')/unquote(url).lstrip('/')).resolve()
 private={'url':url,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'bytes':path.stat().st_size}
asset_path=pathlib.Path('/home/frappe/bench/sites/assets/assets.json');assets=json.loads(asset_path.read_text());urls=sorted({v for v in assets.values() if isinstance(v,str) and v.startswith('/assets/')})
if not urls:raise RuntimeError('Compiled asset manifest empty')
counts={name:frappe.db.count(name) for name in ['Item','Website Item','Sales Order','Sales Invoice','Employee','Loan','User'] if frappe.db.exists('DocType',name)}
pdf=get_pdf('<!doctype html><html><head><meta charset="utf-8"></head><body>Dalali framework PDF check - Café Nairobi</body></html>')
reader=PdfReader(io.BytesIO(pdf));text=' '.join(page.extract_text() or '' for page in reader.pages)
if not pdf.startswith(b'%PDF-') or not all(word in text for word in ['Dalali','framework','Café','Nairobi']):raise RuntimeError('Frappe PDF/font integration failed')
result={'fixture_name':doc.name,'fixture_title':doc.title,'assets':urls,'doctype_counts':counts,'private_probe':private,
        'frappe_pdf_pages':len(reader.pages),'frappe_pdf_font_text':True}
frappe.destroy();print(json.dumps(result))'''
            payload = {"title": self.state["fixture_title"], "private_probe": self.state["clone_initialized"]["private_probe"]}
            output = self.docker(["exec", "--interactive", "--workdir=/home/frappe/bench/sites", control, "/usr/local/bin/ndf-native-exec",
                "/home/frappe/bench/env/bin/python", "-c", code], input=json.dumps(payload), timeout=180)
            result = json.loads(output)
            self.state["application_plan"] = result
            self.state["steps"].append("clone_fixture_created")
            self.save()
            self.write_private("http-test-plan.json", json.dumps({key: result[key] for key in ("fixture_name", "fixture_title", "assets", "doctype_counts")}))
            if result["private_probe"] != self.state["clone_initialized"]["private_probe"]:
                credentials = json.loads((WORK / "clone-credentials.json").read_text())
                self.write_private("http-test-fixture-access.json", json.dumps({"user": "Administrator", "password": credentials["operator_password"], "private_probe": result["private_probe"]}))
        access = DEPLOY / ("http-test-fixture-access.json" if (DEPLOY / "http-test-fixture-access.json").exists() else "http-test-access.json")
        output = self.docker(["run", "--rm", "--pull=never", "--label", LABEL, "--label=ndf.role=http-probe", "--network", NETWORK,
            "--dns=127.0.0.1", "--dns-option=timeout:1", "--dns-option=attempts:1", "--read-only", "--user=0:0",
            "--cap-drop=ALL", "--security-opt=no-new-privileges", "--memory=384m", "--cpus=0.25", "--pids-limit=96",
            "--mount", "type=bind,src=" + str(access) + ",dst=/run/ndf-http-auth.json,readonly",
            "--mount", "type=bind,src=" + str(DEPLOY / "http-test-plan.json") + ",dst=/run/ndf-http-plan.json,readonly",
            APP_IMAGE, "/home/frappe/bench/env/bin/python", "-c", self.request["http_probe"]], timeout=300)
        result = json.loads(output)
        self.state["http_checks"] = result
        (WORK / "http-checks.json").write_text(json.dumps(result, indent=2))
        self.save()
        self.progress("Private login/permissions/record counts/assets/attachment/Socket.IO checks passed")

    def stop_owned_role(self, role):
        name = self.state["role_containers"][role]
        item = self.owned("container", name)
        if item["Image"] != APP_IMAGE:
            raise RuntimeError("Unexpected role image before stop")
        if item["State"].get("Running"):
            self.docker(["stop", "--time=15", name], timeout=60)

    def resume_owned_role(self, role):
        name = self.state["role_containers"][role]
        item = self.owned("container", name)
        if item["Image"] != APP_IMAGE:
            raise RuntimeError("Unexpected role image before restart")
        if not item["State"].get("Running"):
            self.docker(["start", name], timeout=120)

    def refresh_private_frontend(self):
        name = self.state["role_containers"]["frontend"]
        self.owned("container", name)
        # New container IPs may be assigned after a stop. Reload only this private
        # Nginx to resolve owned service names again; never host Nginx.
        self.docker(["exec", name, "/usr/sbin/nginx", "-c", "/etc/ndf-rehearsal-nginx.conf", "-t"])
        self.docker(["exec", name, "/usr/sbin/nginx", "-c", "/etc/ndf-rehearsal-nginx.conf", "-s", "reload"])
        self.loopback_forward(name)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                status = self.run(["curl", "--silent", "--show-error", "--max-time", "5", "--header", "Host: " + CLONE_SITE,
                    "--output", "/dev/null", "--write-out", "%{http_code}", "http://127.0.0.1:18088/api/method/ping"], timeout=10).strip()
                if status == "200":return
            except RuntimeError:pass
            self.guard()
            time.sleep(1)
        raise RuntimeError("Private frontend did not recover HTTP readiness after reload")

    def compressed_stream(self, command, target, prefix=b""):
        """Create a new VM-private gzip stream, retaining partial failures."""
        if target.exists():
            raise RuntimeError("Existing checkpoint file; no overwrite")
        logpath = target.with_name(target.name + ".log")
        with open(target, "xb") as output, open(logpath, "ab") as log:
            if prefix:
                output.write(gzip.compress(prefix, compresslevel=1))
                output.flush()
            producer = subprocess.Popen(self.docker_args(command), stdout=subprocess.PIPE, stderr=log, start_new_session=True)
            compressor = subprocess.Popen(["gzip", "-1"], stdin=producer.stdout, stdout=output, stderr=log, start_new_session=True)
            producer.stdout.close()
            started = time.monotonic()
            try:
                while producer.poll() is None or compressor.poll() is None:
                    self.guard(reserve=128 * 1024**2)
                    if time.monotonic() - started > 900:
                        raise RuntimeError("Clone checkpoint deadline")
                    time.sleep(1)
                if producer.returncode or compressor.returncode:
                    raise RuntimeError("Clone checkpoint stream failed; no overwrite/replay")
            finally:
                for process in (producer, compressor):
                    if process.poll() is None:
                        os.killpg(process.pid, signal.SIGTERM)
                        try:process.wait(timeout=15)
                        except subprocess.TimeoutExpired:
                            os.killpg(process.pid, signal.SIGKILL)
                            process.wait()
        # Check every gzip member without loading customer contents into memory.
        with gzip.open(target, "rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                pass

    def recovery_query(self, sql):
        name = PROJECT + "-recovery-db"
        item = self.owned("container", name)
        if item["Image"] != self.state["images"]["mariadb"]["id"] or item["HostConfig"].get("PortBindings") or set(item["NetworkSettings"]["Networks"]) != {NETWORK}:
            raise RuntimeError("Recovery database identity/network mismatch")
        return self.docker(["exec", name, "mariadb", "--defaults-extra-file=/run/secrets/db-root-client.cnf",
                            "--batch", "--skip-column-names", "--execute", sql], timeout=30)

    def clone_recovery(self):
        if "application_ready" not in self.state["steps"] or "clone_fixture_created" not in self.state["steps"]:
            raise RuntimeError("Initialized clone and marked fixture required before recovery proof")
        if self.state.get("recovery_proof", {}).get("passed"):
            self.resume_owned_role("web")
            self.resume_owned_role("socketio")
            self.refresh_private_frontend()
            self.state["clone_quiesced_for_recovery"] = False
            self.save()
            return
        if not self.state.get("clone_quiesced_for_recovery"):
            self.runtime_egress()
        else:
            self.backend_isolation()
        checkpoint = WORK / "clone-checkpoint"
        creds = json.loads((WORK / "clone-credentials.json").read_text())
        control = self.state["role_containers"]["control"]
        # Only cloned app writers are paused. Frontend stays up (502 during this
        # phase), retaining its private address/loopback forwarding. Native untouched.
        self.stop_owned_role("web")
        self.stop_owned_role("socketio")
        self.state["clone_quiesced_for_recovery"] = True
        self.save()
        if "clone_checkpoint_created" not in self.state["steps"]:
            if checkpoint.exists():
                raise RuntimeError("Partial clone checkpoint requires review; no overwrite")
            checkpoint.mkdir(mode=0o700)
            code = r'''import json,pathlib,pymysql
c=json.loads(pathlib.Path('/run/access.json').read_text());db=pymysql.connect(host='db',user=c['user'],password=c['password'],database=c['database'],connect_timeout=3)
result={'tables':{},'sequences':{}}
with db.cursor() as cur:
 cur.execute('SELECT @@hostname,@@server_id')
 if cur.fetchone()!=('ndf-dalali-rehearsal-v1-db',65001):raise RuntimeError('Unexpected checkpoint DB')
 cur.execute('SELECT TABLE_NAME,TABLE_TYPE FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s ORDER BY TABLE_NAME',(c['database'],))
 objects=cur.fetchall()
 for name,kind in objects:
  q='`'+name.replace('`','``')+'`'
  if kind=='BASE TABLE':cur.execute('SELECT COUNT(*) FROM '+q);result['tables'][name]=cur.fetchone()[0]
  elif kind=='SEQUENCE':
   cur.execute('SHOW CREATE SEQUENCE '+q);ddl=cur.fetchone()[1]
   cur.execute('SELECT next_not_cached_value FROM '+q);result['sequences'][name]={'ddl':ddl,'boundary':int(cur.fetchone()[0])}
  else:raise RuntimeError('Unexpected clone object')
db.close();print(json.dumps(result))'''
            output = self.docker(["run", "--rm", "--pull=never", "--label", LABEL, "--label=ndf.role=clone-checkpoint-counts",
                "--network", NETWORK, "--dns=127.0.0.1", "--read-only", "--user=0:0", "--cap-drop=ALL", "--security-opt=no-new-privileges",
                "--memory=256m", "--cpus=0.25", "--pids-limit=64", "--mount",
                "type=bind,src=" + str(DEPLOY / "synthetic-access.json") + ",dst=/run/access.json,readonly",
                APP_IMAGE, "/home/frappe/bench/env/bin/python", "-c", code], timeout=300)
            expected = json.loads(output)
            if len(expected["tables"]) != 1000 or len(expected["sequences"]) != 10:
                raise RuntimeError("Unexpected pre-migration checkpoint scope")
            (checkpoint / "counts.json").write_text(json.dumps(expected, sort_keys=True))
            statements = []
            for name, info in expected["sequences"].items():
                quoted = "`" + name.replace("`", "``") + "`"
                statements += [info["ddl"].rstrip(";") + ";", "DO SETVAL(" + quoted + ", " + str(info["boundary"]) + ", 0);"]
            args = ["exec", PROJECT + "-db", "mariadb-dump", "--defaults-extra-file=/run/secrets/db-root-client.cnf",
                    "--skip-lock-tables", "--no-tablespaces", "--quick", "--hex-blob", "--routines", "--events", "--triggers"]
            args += ["--ignore-table=" + creds["database"] + "." + name for name in expected["sequences"]]
            args += [creds["database"]]
            self.compressed_stream(args, checkpoint / "database.sql.gz", ("\n".join(statements) + "\n").encode())
            selected = ["apps.txt", "apps.json", "common_site_config.json", "currentsite.txt", "assets",
                        CLONE_SITE + "/site_config.json", CLONE_SITE + "/public/files", CLONE_SITE + "/private/files"]
            self.compressed_stream(["exec", control, "/usr/bin/tar", "--create", "--file=-", "--numeric-owner", "-C", "/home/frappe/bench/sites", *selected], checkpoint / "sites.tar.gz")
            capture = r'''import hashlib,json,os,pathlib
root=pathlib.Path('/home/frappe/bench/sites/dalali-rehearsal.invalid');r={}
for kind in ['public','private']:
 base=root/kind/'files';r[kind]={}
 for parent,dirs,files in os.walk(base,followlinks=False):
  for name in files:
   p=pathlib.Path(parent)/name
   if p.is_symlink():raise RuntimeError('Unexpected clone attachment link')
   r[kind][str(p.relative_to(base))]={'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
print(json.dumps(r))'''
            manifest = self.docker(["exec", control, "/usr/local/bin/ndf-native-exec", "/home/frappe/bench/env/bin/python", "-c", capture], timeout=180)
            (checkpoint / "files.json").write_text(manifest)
            self.state["clone_checkpoint"] = {"database_sha256": digest(checkpoint / "database.sql.gz"),
                "sites_sha256": digest(checkpoint / "sites.tar.gz"), "source": "private-clone-only", "not_source_archive": True}
            self.state["steps"].append("clone_checkpoint_created")
            self.save()
            self.progress("Private clone checkpoint created; no second production dump or pause")
        for filename, key in (("database.sql.gz", "database_sha256"), ("sites.tar.gz", "sites_sha256")):
            if digest(checkpoint / filename) != self.state["clone_checkpoint"][key]:
                raise RuntimeError("Clone recovery checkpoint changed")
        volume = self.volume("recovery-db-data")
        args = ["--cap-add=CHOWN", "--cap-add=FOWNER", "--cap-add=DAC_OVERRIDE", "--cap-add=SETUID", "--cap-add=SETGID",
                "--mount", "type=volume,src=" + volume + ",dst=/var/lib/mysql",
                "--tmpfs=/tmp:rw,nosuid,nodev,size=67108864,mode=1777", "--tmpfs=/run/mysqld:rw,nosuid,nodev,size=16777216",
                "--env=TZ=Europe/Berlin", "--env=MARIADB_ROOT_PASSWORD_FILE=/run/secrets/db-root-password",
                "--env=MARIADB_PASSWORD_FILE=/run/secrets/db-app-password", "--env=MARIADB_USER=" + creds["user"], "--env=MARIADB_DATABASE=" + creds["database"]]
        for filename in ("db-root-password", "db-app-password", "db-root-client.cnf"):
            args += ["--mount", "type=bind,src=" + str(DEPLOY / filename) + ",dst=/run/secrets/" + filename + ",readonly"]
        self.start_role("recovery-db", self.state["images"]["mariadb"]["id"], args,
            ["--bind-address=0.0.0.0", "--skip-name-resolve", "--event-scheduler=OFF", "--server-id=65002", "--innodb-buffer-pool-size=128M",
             "--character-set-server=utf8mb4", "--collation-server=utf8mb4_general_ci", "--max-allowed-packet=16777216",
             "--sql-mode=STRICT_TRANS_TABLES,ERROR_FOR_DIVISION_BY_ZERO,NO_AUTO_CREATE_USER,NO_ENGINE_SUBSTITUTION"], memory="768m", cpus="0.75")
        deadline = time.monotonic() + 180
        while True:
            try:
                identity = self.recovery_query("SELECT @@hostname,@@server_id;").strip().split("\t")
                if identity != [PROJECT + "-recovery-db", "65002"]:
                    raise RuntimeError("Unexpected recovery server identity")
                break
            except RuntimeError:
                if time.monotonic() > deadline or not self.owned("container", PROJECT + "-recovery-db")["State"].get("Running"):
                    raise
                time.sleep(2)
        if "recovery_database_restored" not in self.state["steps"]:
            if self.state.get("recovery_import_attempted"):
                raise RuntimeError("Partial recovery import; no automatic overwrite/reimport")
            if self.recovery_query("SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA='" + creds["database"] + "';").strip() != "0":
                raise RuntimeError("Recovery DB not empty")
            self.state["recovery_import_attempted"] = True
            self.save()
            with open(WORK / "recovery-import.log", "ab") as log:
                process = subprocess.Popen(self.docker_args(["exec", "--interactive", PROJECT + "-recovery-db", "mariadb",
                    "--defaults-extra-file=/run/secrets/db-root-client.cnf", "--binary-mode=1", creds["database"]]),
                    stdin=subprocess.PIPE, stdout=log, stderr=log, start_new_session=True)
                errors = []
                def feed():
                    try:
                        with gzip.open(checkpoint / "database.sql.gz", "rb") as source:
                            for block in iter(lambda: source.read(1024 * 1024), b""):process.stdin.write(block)
                    except Exception as error:errors.append(type(error).__name__)
                    finally:
                        try:process.stdin.close()
                        except OSError:pass
                writer = threading.Thread(target=feed, daemon=True);writer.start();started = time.monotonic()
                try:
                    while True:
                        try:code = process.wait(timeout=2);break
                        except subprocess.TimeoutExpired:
                            self.guard(reserve=128 * 1024**2)
                            if time.monotonic() - started > 900:raise RuntimeError("Recovery DB restore deadline")
                    writer.join(timeout=10)
                    if code or errors or writer.is_alive():raise RuntimeError("Recovery DB import failed; retain for review")
                finally:
                    if process.poll() is None:
                        os.killpg(process.pid, signal.SIGTERM)
                        try:process.wait(timeout=15)
                        except subprocess.TimeoutExpired:
                            os.killpg(process.pid, signal.SIGKILL);process.wait()
            self.state["steps"].append("recovery_database_restored")
            self.save()
        self.restore_recovery_files(checkpoint)
        self.verify_recovery(checkpoint)
        # The recovery destination is retained, stopped to leave RAM for the
        # separately approved schema migration. No volume/container is removed.
        recovery = self.owned("container", PROJECT + "-recovery-db")
        if recovery["State"].get("Running"):
            self.docker(["stop", "--time=15", PROJECT + "-recovery-db"], timeout=60)
        self.resume_owned_role("web")
        self.resume_owned_role("socketio")
        self.refresh_private_frontend()
        self.state["clone_quiesced_for_recovery"] = False
        self.save()

    def restore_recovery_files(self, checkpoint):
        if "recovery_files_restored" in self.state["steps"]:
            return
        volume = self.volume("recovery-sites")
        name = PROJECT + "-initialize-recovery"
        item = self.inspect_optional("container", name)
        if item is None:
            if name in self.state["resources"]["containers"]:
                raise RuntimeError("Recovery copy helper vanished")
            self.docker(["run", "--detach", "--name", name, "--label", LABEL, "--label=ndf.role=initialize-recovery",
                "--pull=never", "--network=none", "--read-only", "--user=0:0", "--cap-drop=ALL", "--cap-add=CHOWN",
                "--cap-add=FOWNER", "--cap-add=DAC_OVERRIDE", "--security-opt=no-new-privileges", "--memory=128m", "--cpus=0.25", "--pids-limit=32",
                "--mount", "type=volume,src=" + volume + ",dst=/home/frappe/bench/sites",
                "--tmpfs=/home/frappe/frappe-bench/sites:ro,nosuid,nodev,size=1048576",
                "--tmpfs=/home/frappe/frappe-bench/logs:ro,nosuid,nodev,size=1048576",
                APP_IMAGE, "/home/frappe/bench/env/bin/python", "-c", "import time; time.sleep(1800)"])
            item = self.record_resource("container", name)
        else:
            item = self.owned("container", name)
        if not item["State"].get("Running") or item["HostConfig"]["NetworkMode"] != "none" or any(m["Type"] == "bind" for m in item["Mounts"]):
            raise RuntimeError("Recovery helper isolation changed")
        if not self.state.get("recovery_file_copy_completed"):
            if self.state.get("recovery_file_copy_attempted"):
                raise RuntimeError("Partial recovery file copy requires review")
            self.docker(["exec", name, "/home/frappe/bench/env/bin/python", "-c", "import os; assert not os.listdir('/home/frappe/bench/sites')"])
            self.state["recovery_file_copy_attempted"] = True
            self.save()
            self.pipe_files(checkpoint / "sites.tar.gz", name + ":/home/frappe/bench/sites", WORK / "recovery-files-copy.log", archive=True)
            self.state["recovery_file_copy_completed"] = True
            self.save()
        code = r'''import json,os,pathlib
root=pathlib.Path('/home/frappe/bench/sites');site=root/'dalali-rehearsal.invalid'
for path in [site/'site_config.json',root/'common_site_config.json']:
 c=json.loads(path.read_text());c.update(db_host='recovery-db',pause_scheduler=1,maintenance_mode=1,host_name='http://recovery-only.invalid')
 for key in ['redis_cache','redis_queue','redis_socketio']:c[key]='redis://127.0.0.1:1'
 path.write_text(json.dumps(c,indent=2));path.chmod(0o600)
for parent,dirs,files in os.walk(root,followlinks=False):
 os.chown(parent,1000,1000)
 for name in dirs+files:os.chown(os.path.join(parent,name),1000,1000,follow_symlinks=False)
print('recovery file configuration ready')'''
        self.docker(["exec", name, "/usr/local/bin/ndf-native-exec", "/home/frappe/bench/env/bin/python", "-c", code])
        self.owned("container", name)
        self.docker(["stop", "--time=5", name])
        self.state["steps"].append("recovery_files_restored")
        self.save()

    def verify_recovery(self, checkpoint):
        creds = json.loads((WORK / "clone-credentials.json").read_text())
        plan = {"database": creds["database"], "user": creds["user"], "password": creds["app_password"],
                "schema": json.loads((checkpoint / "counts.json").read_text()),
                "files": json.loads((checkpoint / "files.json").read_text()),
                "fixture_name": self.state["application_plan"]["fixture_name"], "fixture_title": self.state["fixture_title"]}
        self.write_private("recovery-verification.json", json.dumps(plan), mode=0o444)
        code = r'''import hashlib,json,os,pathlib,pymysql
from cryptography.fernet import Fernet
p=json.loads(pathlib.Path('/run/verification.json').read_text());sites=pathlib.Path('/run/recovery-sites');site=sites/'dalali-rehearsal.invalid';config=json.loads((site/'site_config.json').read_text())
if config.get('db_host')!='recovery-db' or config.get('maintenance_mode')!=1 or config.get('pause_scheduler')!=1:raise RuntimeError('Recovery config isolation failed')
db=pymysql.connect(host='recovery-db',user=p['user'],password=p['password'],database=p['database'],connect_timeout=3,read_timeout=15)
decoded=0
with db.cursor() as cur:
 cur.execute('SELECT @@hostname,@@server_id')
 if cur.fetchone()!=('ndf-dalali-rehearsal-v1-recovery-db',65002):raise RuntimeError('Wrong recovery server')
 cur.execute('SELECT TABLE_NAME,TABLE_TYPE FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s',(p['database'],));objects=cur.fetchall()
 if {n for n,k in objects if k=='BASE TABLE'}!=set(p['schema']['tables']) or {n for n,k in objects if k=='SEQUENCE'}!=set(p['schema']['sequences']):raise RuntimeError('Recovery scope mismatch')
 for name,count in p['schema']['tables'].items():
  cur.execute('SELECT COUNT(*) FROM `'+name.replace('`','``')+'`')
  if cur.fetchone()[0]!=count:raise RuntimeError('Recovery row count mismatch')
 for name,info in p['schema']['sequences'].items():
  cur.execute('SELECT next_not_cached_value FROM `'+name.replace('`','``')+'`')
  if cur.fetchone()[0]!=info['boundary']:raise RuntimeError('Recovery sequence boundary mismatch')
 cur.execute('SELECT title FROM tabNote WHERE name=%s',(p['fixture_name'],))
 if cur.fetchone()!=(p['fixture_title'],):raise RuntimeError('Clone-only test write did not recover')
 cur.execute('SELECT password FROM `__Auth` WHERE encrypted=1');cipher=Fernet(config['encryption_key'].encode())
 for value, in cur.fetchall():cipher.decrypt(value.encode() if isinstance(value,str) else value);decoded+=1
db.close();total=0
for kind in ['public','private']:
 root=site/kind/'files';actual={}
 for parent,dirs,files in os.walk(root,followlinks=False):
  for name in files:
   path=pathlib.Path(parent)/name
   if path.is_symlink():raise RuntimeError('Unexpected recovery attachment symlink')
   h=hashlib.sha256()
   with path.open('rb') as f:
    for block in iter(lambda:f.read(1048576),b''):h.update(block)
   actual[str(path.relative_to(root))]={'bytes':path.stat().st_size,'sha256':h.hexdigest()}
 if actual!=p['files'][kind]:raise RuntimeError('Recovery attachment hash mismatch')
 total+=len(actual)
print(json.dumps({'passed':True,'base_tables':len(p['schema']['tables']),'sequences':len(p['schema']['sequences']),'all_row_counts_match':True,'sequence_boundaries_match':True,'clone_fixture_recovered':True,'file_hashes_match':True,'file_count':total,'decrypted_field_count':decoded,'recovery_host_isolated':True}))'''
        output = self.docker(["run", "--rm", "--pull=never", "--label", LABEL, "--label=ndf.role=recovery-verification",
            "--network", NETWORK, "--dns=127.0.0.1", "--read-only", "--user=1000:1000", "--cap-drop=ALL",
            "--security-opt=no-new-privileges", "--memory=256m", "--cpus=0.25", "--pids-limit=64", "--mount",
            "type=bind,src=" + str(DEPLOY / "recovery-verification.json") + ",dst=/run/verification.json,readonly", "--mount",
            "type=volume,src=" + PROJECT + "-recovery-sites,dst=/run/recovery-sites,readonly",
            APP_IMAGE, "/home/frappe/bench/env/bin/python", "-c", code], timeout=300)
        result = json.loads(output)
        if result["decrypted_field_count"] != self.state["clone_initialized"]["decrypted_field_count"]:
            raise RuntimeError("Recovery encrypted-field count differs")
        self.state["recovery_proof"] = result
        (WORK / "recovery-result.json").write_text(json.dumps(result, indent=2))
        self.save()
        self.progress("Private clone checkpoint recovered into second new DB/files destination; rows/sequences/files/key/fixture verified")

    def native_gap_count(self):
        config = json.loads((BENCH / "sites" / SITE / "site_config.json").read_text())
        common = json.loads((BENCH / "sites/common_site_config.json").read_text())
        if (config.get("db_host") or common.get("db_host") or "localhost") not in {"localhost", "127.0.0.1"}:
            raise RuntimeError("Unexpected native metadata target")
        driver = private_mysql_driver()
        connection = driver.connect(host="127.0.0.1", port=int(config.get("db_port") or common.get("db_port") or 3306),
            user=config.get("db_user") or config["db_name"], password=config["db_password"], database=config["db_name"],
            connect_timeout=3, read_timeout=5, write_timeout=2, autocommit=False)
        try:
            with connection.cursor() as cursor:
                cursor.execute("START TRANSACTION READ ONLY")
                cursor.execute("SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='tabUser' AND COLUMN_NAME='send_read_receipt'")
                value = int(cursor.fetchone()[0])
            connection.rollback()
            return value
        finally:
            connection.close()

    def clone_migrate(self):
        if not self.request.get("clone_migration_approved"):
            raise RuntimeError("Separate explicit clone migration approval required")
        if "clone_recovery_verified" not in self.state["steps"] or not self.state.get("recovery_proof", {}).get("passed"):
            raise RuntimeError("Verified private clone recovery required before schema changes")
        if self.state.get("clone_migration_attempted"):
            raise RuntimeError("Clone migration attempt already used; inspect partial outcome, no automatic replay")
        self.backend_isolation()
        native_before = self.native_gap_count()
        if native_before != 0:
            raise RuntimeError("Native schema changed since approved gap review")
        identity = self.db_query("SELECT @@hostname,@@server_id;").strip().split("\t")
        if identity != [PROJECT + "-db", "65001"]:
            raise RuntimeError("Wrong migration target")
        # Bench recognizes apps/sites/config/logs/config-pids. Supply only fresh
        # empty config/pids, never the native Redis/Supervisor/Nginx configuration.
        bench_config = DEPLOY / "bench-config"
        if not bench_config.exists():
            bench_config.mkdir(mode=0o755)
            bench_config.chmod(0o755)
            (bench_config / "pids").mkdir(mode=0o755)
            (bench_config / "pids").chmod(0o755)
        if (bench_config.is_symlink() or bench_config.stat().st_uid != 0 or bench_config.stat().st_mode & 0o022
                or {path.name for path in bench_config.iterdir()} != {"pids"} or (bench_config / "pids").is_symlink()
                or not (bench_config / "pids").is_dir() or any((bench_config / "pids").iterdir())):
            raise RuntimeError("Unknown private bench config; do not adopt service settings")
        control = self.start_role("control", APP_IMAGE, self.app_mounts() + ["--mount",
            "type=bind,src=" + str(bench_config) + ",dst=/home/frappe/bench/config,readonly"], ["/usr/bin/sleep", "infinity"],
            memory="512m", cpus="0.5", generation="bench")
        item = self.owned("container", control)
        if item["Image"] != APP_IMAGE or set(item["NetworkSettings"]["Networks"]) != {NETWORK} or not item["HostConfig"]["ReadonlyRootfs"]:
            raise RuntimeError("Migration controller identity/isolation mismatch")
        self.docker(["exec", "--workdir=/home/frappe/bench", control, "/usr/local/bin/ndf-native-exec",
            "/home/frappe/.venv/bin/bench", "--site", CLONE_SITE, "migrate", "--help"], timeout=120)
        self.stop_owned_role("web")
        self.stop_owned_role("socketio")
        # This changes only a labelled clone helper; global 2-CPU/3-GiB budget
        # remains enforced. Stop recovery DB first (already retained/stopped).
        self.docker(["update", "--memory=1g", "--memory-swap=1g", "--cpus=1", control])
        self.state["clone_migration_attempted"] = True
        self.state["clone_migration_approval"] = {"time_utc": datetime.now(timezone.utc).isoformat(),
            "scope": "owned private clone only, frozen app code, no package upgrades/downloads", "server_id": 65001,
            "site": CLONE_SITE, "image": APP_IMAGE, "native_gap_before": native_before,
            "rollback_checkpoint": self.state["clone_checkpoint"], "source_modified": False}
        self.save()
        self.progress("Running approved bench migrate ONLY in isolated clone; native schema remains unchanged")
        self.docker(["exec", "--workdir=/home/frappe/bench", control, "/usr/local/bin/ndf-native-exec",
            "/home/frappe/.venv/bin/bench", "--site", CLONE_SITE, "migrate"], timeout=1800, logfile=WORK / "clone-migrate.log")
        column = self.db_query("SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA='" +
            json.loads((WORK / "clone-credentials.json").read_text())["database"] + "' AND TABLE_NAME='tabUser' AND COLUMN_NAME='send_read_receipt';").strip()
        if column != "1" or self.native_gap_count() != native_before:
            raise RuntimeError("Clone schema fix/native isolation verification failed")
        code = r'''import json,pathlib
root=pathlib.Path('/home/frappe/bench/sites')
for path in [root/'common_site_config.json',root/'dalali-rehearsal.invalid/site_config.json']:
 c=json.loads(path.read_text())
 if c.get('db_host')!='db':raise RuntimeError('Wrong clone config target')
 c.update(pause_scheduler=1,mute_emails=1,disable_email_queue=1,disable_webhooks=1,developer_mode=0)
 if path.name=='site_config.json':c.update(maintenance_mode=0,cookie_domain=None)
 temporary=path.with_suffix('.json.new');temporary.write_text(json.dumps(c,indent=2));temporary.chmod(0o600);temporary.replace(path)
print('clone-only safety overlays retained')'''
        self.docker(["exec", control, "/usr/local/bin/ndf-native-exec", "/home/frappe/bench/env/bin/python", "-c", code])
        self.docker(["update", "--memory=512m", "--memory-swap=512m", "--cpus=0.5", control])
        self.resume_owned_role("web")
        self.resume_owned_role("socketio")
        self.refresh_private_frontend()
        result = {"clone_migration_succeeded": True, "missing_column_fixed_in_clone": True,
                  "native_column_still_absent": True, "source_schema_modified": False,
                  "frozen_image": APP_IMAGE, "package_upgrades": False, "workers_scheduler_started": False}
        self.state["clone_migration"] = result
        (WORK / "clone-migration-result.json").write_text(json.dumps(result, indent=2))
        self.save()
        self.health()
        self.progress("Clone-only migration completed; native missing-column proof remains unchanged; app tests must be repeated")

    def validate(self):
        required = {"clone_recovery_verified", "clone_migration_completed", "application_checks_passed"}
        if not required.issubset(self.state["steps"]):
            raise RuntimeError("Recovery, approved clone schema sync and application checks must pass first")
        self.step("restart_verified", self.restart_checks)
        self.runtime_egress()
        control = self.state["role_containers"]["control"]
        code = r'''import json,frappe
from frappe.utils.password import get_decrypted_password,encrypt,decrypt
frappe.init(site='dalali-rehearsal.invalid',sites_path='/home/frappe/bench/sites');frappe.connect()
if tuple(frappe.db.sql('SELECT @@hostname,@@server_id')[0])!=('ndf-dalali-rehearsal-v1-db',65001):raise RuntimeError('Wrong final crypto target')
count=0
for doctype,name,field in frappe.db.sql('SELECT doctype,name,fieldname FROM `__Auth` WHERE encrypted=1'):
 value=get_decrypted_password(doctype,name,field)
 if value is None:raise RuntimeError('Encrypted field unavailable after migration/restart')
 count+=1
if decrypt(encrypt('ndf-final-rehearsal-crypto'))!='ndf-final-rehearsal-crypto':raise RuntimeError('Final crypto roundtrip failed')
frappe.destroy();print(json.dumps({'decrypted_field_count':count,'all_encrypted_fields_decrypt':True,'roundtrip':True}))'''
        output = self.docker(["exec", "--workdir=/home/frappe/bench/sites", control, "/usr/local/bin/ndf-native-exec",
            "/home/frappe/bench/env/bin/python", "-c", code], timeout=120)
        crypto = json.loads(output)
        if crypto["decrypted_field_count"] != self.state["clone_initialized"]["decrypted_field_count"]:
            raise RuntimeError("Final encrypted-field count changed; review schema migration effects")
        self.state["final_crypto"] = crypto
        self.health()
        native_http = {}
        expected = {SITE: "503", "demo.ndftechlabs.com": "200", "dulamanenterprise.ndftechlabs.com": "503", "erp.ndftechlabs.com": "200"}
        for name, code in expected.items():
            value = self.run(["curl", "--silent", "--show-error", "--max-time", "10", "--resolve", name + ":443:127.0.0.1",
                "--output", "/dev/null", "--write-out", "%{http_code}", "https://" + name + "/api/method/ping"], timeout=15).strip()
            native_http[name] = value
            if value != code:raise RuntimeError("Native site HTTP baseline changed")
        if self.native_gap_count() != 0:
            raise RuntimeError("Native schema metadata changed unexpectedly")
        free = shutil.disk_usage("/").free
        result = {"technical_rehearsal_passed": True, "business_ui_acceptance_pending": True,
            "source_snapshot_lock_seconds": self.state["snapshot"]["lock_held_seconds"],
            "source_tables_restored": self.state["restore_database"]["base_tables"], "source_sequences_restored": self.state["restore_database"]["sequences"],
            "source_files_verified": self.state["restore_files"]["file_count"],
            "source_schema_modified": False, "clone_schema_sync_approved_and_completed": True,
            "pdf_version": self.state["pdf_runtime"]["version"], "native_http": native_http,
            "http_checks": self.state["http_checks"], "recovery": self.state["recovery_proof"],
            "recovery_scope": "pre-migration private clone rollback checkpoint; current clone synchronized separately to frozen code",
            "final_crypto": crypto,
            "runtime_egress": self.state["runtime_egress"], "download_accounted_bytes": self.state["download_accounted_bytes"],
            "additional_disk_bytes": max(0, self.state["initial_free_bytes"] - free), "host_free_bytes": free,
            "public_cutover": False, "source_maintenance": 1, "cleanup_performed": False,
            "encrypted_archive_touched": False, "clone_url": "http://127.0.0.1:18088"}
        self.state["result"] = result
        (WORK / "result.json").write_text(json.dumps(result, indent=2))
        self.save()
        self.progress("Isolated technical restore rehearsal passed; business/UI/final-sync/cutover approvals remain separate")

    def restart_checks(self):
        self.runtime_egress()
        self.stop_owned_role("web")
        self.stop_owned_role("socketio")
        for role in ("db", "redis-cache", "redis-queue"):
            name = self.state.get("role_containers", {}).get(role, PROJECT + "-" + role)
            self.owned("container", name)
            self.docker(["restart", "--time=15", name], timeout=120)
        deadline = time.monotonic() + 120
        while True:
            try:
                identity = self.db_query("SELECT @@hostname,@@server_id;").strip().split("\t")
                if identity != [PROJECT + "-db", "65001"]:raise RuntimeError("Restart DB identity changed")
                break
            except RuntimeError:
                if time.monotonic() > deadline:raise
                time.sleep(2)
        control = self.state["role_containers"]["control"]
        code = r'''import json,pathlib,redis
c=json.loads(pathlib.Path('/home/frappe/bench/sites/common_site_config.json').read_text())
for key in ['redis_cache','redis_queue']:
 r=redis.Redis.from_url(c[key],socket_connect_timeout=3,socket_timeout=3)
 if not r.ping():raise RuntimeError('Redis restart failed')
print('private redis ready')'''
        self.docker(["exec", control, "/usr/local/bin/ndf-native-exec", "/home/frappe/bench/env/bin/python", "-c", code])
        self.resume_owned_role("web")
        self.resume_owned_role("socketio")
        self.refresh_private_frontend()
        # Test with a fresh HTTP session after cache/DB/process restart. Includes
        # the committed clone-only fixture and authenticated private file bytes.
        self.application_checks()
        self.state["restart_proof"] = {"database_restarted": True, "redis_restarted": True,
            "web_socketio_restarted": True, "fixture_and_http_auth_persisted": True,
            "source_processes_restarted": False, "volumes_removed": False}
        self.save()

    def apply(self):
        phases = {"images": ("images_verified", self.images), "isolation": ("isolation_verified", self.isolation),
                  "snapshot": ("source_snapshot_created", self.snapshot), "restore": ("restore_verified", self.restore),
                  "pdf": ("pdf_runtime_verified", self.pdf_runtime),
                  "application": ("application_ready", self.application),
                  "application-tests": ("application_checks_passed", self.application_checks),
                  "recovery": ("clone_recovery_verified", self.clone_recovery),
                  "clone-migrate": ("clone_migration_completed", self.clone_migrate),
                  "validate": ("rehearsal_checks_passed", self.validate)}
        if self.request["phase"] not in phases:
            raise RuntimeError("Requested rehearsal phase implementation is not yet reviewed")
        try:
            self.step(*phases[self.request["phase"]])
            if self.state.get("last_error"):
                self.state.setdefault("historical_errors", []).append(self.state.pop("last_error"))
            self.save()
        except Exception as error:
            self.state["last_error"] = str(error)
            self.save()
            raise
        self.progress(json.dumps({"rehearsal_phase_complete": self.request["phase"], "steps": self.state["steps"],
                                  "download_accounted_bytes": self.state["download_accounted_bytes"],
                                  "source_copy_created": "source_snapshot_created" in self.state["steps"],
                                  "customer_data_loaded": "database_restored" in self.state["steps"], "cleanup_performed": False}))


if __name__ == "__main__":
    if "REQUEST" not in globals():
        raise SystemExit("Use execute_rehearsal.py with explicit approval")
    Rehearsal(REQUEST).apply()
