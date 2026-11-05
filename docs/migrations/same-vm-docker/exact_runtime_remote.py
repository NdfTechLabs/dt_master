#!/usr/bin/env python3
"""VM implementation for the approved offline evaluation build; import is inert."""

import base64
from datetime import datetime, timezone
import fcntl
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import stat
import subprocess
import sys
from urllib.request import urlopen


STATE = Path("/var/lib/ndf-rootless-migration")
WORK = STATE / "exact-runtime-v1"
STAGE = Path("/opt/ndf-rootless/exact-runtime-v2")
BENCH = Path("/home/frappe/bench")
PROBE = Path("/opt/ndf-rootless/native-runtime-probe-v1")
BIN = Path("/opt/ndf-rootless/29.6.1/bin/docker")
TAG = "ndf-local/exact-runtime:dalali-v1"
BASE = "frappe/erpnext@sha256:eaf1da1478c2b99339a0a8bc37af723a67ecf9cd4f84b313a0c3765743486fcc"
DOWNLOAD_CAP = 20_000_000
DISK_CAP = 35 * 1024**3
FREE_FLOOR = 60 * 1024**3
EXCLUDED_NAMES = {".git", ".ssh", ".gnupg", ".aws", ".azure", ".config", ".env", ".npmrc", "npmrc",
                  "pip.conf", ".pypirc", ".netrc", "direct_url.json", "__pycache__", ".cache", ".pytest_cache",
                  "site_config.json", "common_site_config.json", "id_rsa", "id_ed25519", "id_ecdsa", "logs"}
SECRET_PATTERNS = (
    ("private-key", re.compile(rb"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----")),
    ("aws-access-key", re.compile(rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("github-token", re.compile(rb"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{50,})\b")),
)


def digest(path):
    value = hashlib.sha256()
    with open(path, "rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def excluded(name):
    return (name in EXCLUDED_NAMES or name.startswith(".env.") or
            name.endswith((".pyc", ".key", ".pem", ".p12", ".pfx", ".sql", ".sql.gz", ".zip", ".tar", ".tar.gz", ".tgz", ".gpg")))


def manifest(root):
    """Never follow a source symlink or special file."""
    result = {}
    for parent, dirs, files in os.walk(root, followlinks=False):
        for name in sorted(dirs + files):
            path = Path(parent) / name
            info = path.lstat()
            row = {"mode": stat.S_IMODE(info.st_mode)}
            if stat.S_ISLNK(info.st_mode):
                row.update(type="symlink", target=os.readlink(path))
            elif stat.S_ISREG(info.st_mode):
                row.update(type="file", bytes=info.st_size, sha256=digest(path))
            elif stat.S_ISDIR(info.st_mode):
                row.update(type="directory")
            else:
                raise RuntimeError("Special file in source snapshot; private review required")
            result[str(path.relative_to(root))] = row
    return result


def staged_link(source, destination, roots, support):
    """Return an image link target using reviewed copies, never live app files."""
    raw = os.readlink(source)
    interpreter_dirs = {PROBE / "application-env/bin", PROBE / "bench-env/bin"}
    if source.parent in interpreter_dirs and source.resolve() == Path("/usr/bin/python3.14"):
        return "/usr/bin/python3.14", "native-interpreter-alias"
    if source == PROBE / "stdlib/sitecustomize.py" and raw == "/etc/python3.14/sitecustomize.py":
        if not (support / "sitecustomize.py").is_file():
            raise RuntimeError("Missing private Python sitecustomize copy")
        return "/etc/python3.14/sitecustomize.py", "private-python-support"
    if source == PROBE / "stdlib/config-3.14-x86_64-linux-gnu/libpython3.14.so" and raw == "../../x86_64-linux-gnu/libpython3.14.so.1":
        if not (support / "libpython3.14.so.1.0").is_file():
            raise RuntimeError("Missing private libpython copy")
        return "/opt/ndf-native/lib/libpython3.14.so.1", "private-python-support"
    # Absolute native app links are mapped lexically into the verified snapshot
    # before resolving. Do not dereference the original production app target.
    if Path(raw).is_absolute() and Path(raw).is_relative_to(BENCH / "apps"):
        snapshot = roots[0][0]
        resolved = (snapshot / Path(raw).relative_to(BENCH / "apps")).resolve()
        reason = "native-app-to-snapshot"
    else:
        resolved = source.resolve()
        reason = "within-private-copy"
    for source_root, destination_root in roots:
        if resolved.is_relative_to(source_root):
            mapped = destination_root / resolved.relative_to(source_root)
            if not mapped.exists() or mapped.is_symlink():
                raise RuntimeError("Symlink points to excluded/missing staged input")
            return os.path.relpath(mapped, destination.parent), reason
    raise RuntimeError("Escaping source/dependency symlink; review private logs")


def inspected_manifest(root):
    """Hash/scan regular files once, never following directory/file links."""
    rows, findings = {}, []
    for parent, dirs, files in os.walk(root, followlinks=False):
        for name in sorted(dirs + files):
            path = Path(parent) / name
            relative = str(path.relative_to(root))
            info = path.lstat()
            row = {"mode": stat.S_IMODE(info.st_mode)}
            if stat.S_ISLNK(info.st_mode):
                row.update(type="symlink", target=os.readlink(path))
            elif stat.S_ISDIR(info.st_mode):
                row.update(type="directory")
            elif stat.S_ISREG(info.st_mode):
                checksum, labels, tail = hashlib.sha256(), set(), b""
                with open(path, "rb") as source:
                    for block in iter(lambda: source.read(1024 * 1024), b""):
                        checksum.update(block)
                        for label, pattern in SECRET_PATTERNS:
                            if pattern.search(tail + block):
                                labels.add(label)
                        tail = (tail + block)[-512:]
                    after = os.fstat(source.fileno())
                if (info.st_ino, info.st_size, info.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
                    raise RuntimeError("Context file changed during review")
                row.update(type="file", bytes=info.st_size, sha256=checksum.hexdigest())
                findings.extend({"path": relative, "indicator": label} for label in sorted(labels))
            else:
                raise RuntimeError("Special file in retained context")
            rows[relative] = row
    return rows, findings


def verify_review(findings, actual, report, expected_count):
    """Accept only exact reviewed path/content/indicator tuples, never a glob."""
    if len(report) != expected_count or len({row["path"] for row in report}) != expected_count:
        raise RuntimeError("Review report file count/uniqueness mismatch")
    allowed = {}
    for row in report:
        path = row["path"]
        if Path(path).is_absolute() or ".." in Path(path).parts:
            raise RuntimeError("Unsafe reviewed path")
        if actual.get(path, {}).get("sha256") != row["sha256"]:
            raise RuntimeError("Reviewed file content changed")
        allowed[path] = "aws-access-key" if row["aws_hits"] else "private-key"
    observed = {(row["path"], row["indicator"]) for row in findings}
    if observed != set(allowed.items()):
        raise RuntimeError("New, missing or changed credential findings; review required")


def package_inventory(site_packages):
    return {re.sub(r"[-_.]+", "-", distribution.metadata["Name"]).lower(): distribution.version
            for distribution in importlib.metadata.distributions(path=[str(site_packages)])}


def assert_validation(result, expected, packages, bench_version):
    if result["python"] != expected["python"] or not result["openssl"].startswith("OpenSSL 3.0.13 "):
        raise RuntimeError("Evaluation Python/OpenSSL mismatch")
    if not result["libpython"].startswith(expected["python"] + " "):
        raise RuntimeError("Evaluation shared Python library mismatch")
    commands = result["commands"]
    for name, version in (("node", "v" + expected["node"]), ("yarn", expected["yarn"]), ("bench", bench_version)):
        if commands[name]["code"] != 0 or commands[name]["version"] != version:
            raise RuntimeError("Evaluation command/version mismatch: " + name)
    for name in ("mariadb", "mariadb-dump"):
        if commands[name]["code"] != 0 or "10.11.14-MariaDB" not in commands[name]["version"]:
            raise RuntimeError("Evaluation MariaDB client mismatch")
    if any(not row["ok"] for row in result["imports"].values()):
        raise RuntimeError("No-data imports incomplete; review validation.json")
    for name in result["pilot_apps"]:
        if not (result["imports"][name].get("file") or "").startswith("/home/frappe/bench/apps/" + name + "/"):
            raise RuntimeError("Unexpected pilot source import path")
    for name in ("frappe", "erpnext"):
        if result["imports"][name].get("version") != expected[name]:
            raise RuntimeError("Evaluation application version mismatch")
    if result["packages"] != packages:
        raise RuntimeError("Evaluation package inventory differs from native copy")


def verify_retained_export(image, base, expected_id, build_log):
    if image["Id"] != expected_id:
        raise RuntimeError("Retained image differs from pinned export digest")
    manifests = re.findall(r"exporting manifest (sha256:[a-f0-9]{64})", build_log)
    if not manifests or manifests[-1] != expected_id:
        raise RuntimeError("Retained image not proven by original export log")
    if not re.search(r"unpacking to docker\.io/" + re.escape(TAG) + r" [0-9.]+s done", build_log):
        raise RuntimeError("Original image unpack completion not recorded")
    expected_config = {"User": "frappe", "WorkingDir": "/home/frappe/bench",
                       "Entrypoint": ["/usr/local/bin/ndf-native-exec"],
                       "Cmd": ["/home/frappe/bench/env/bin/python", "--version"]}
    if any(image["Config"].get(key) != value for key, value in expected_config.items()):
        raise RuntimeError("Retained image startup/configuration mismatch")
    base_layers = base["RootFS"]["Layers"]
    layers = image["RootFS"]["Layers"]
    if not base_layers or len(layers) <= len(base_layers) or layers[:len(base_layers)] != base_layers:
        raise RuntimeError("Retained image does not extend the approved cached base")


class EvaluationBuild:
    def __init__(self, request):
        self.request = request
        if os.geteuid() != 0 or subprocess.check_output(["hostname"], text=True).strip() != "mail.ndftechlabs.com":
            raise RuntimeError("Wrong host/account")
        os.umask(0o077)
        foundation = json.loads((STATE / "state.json").read_text())
        self.user = pwd.getpwnam("dt-containers")
        if not foundation.get("complete") or self.user.pw_uid != 5001 or self.user.pw_gid != 5001:
            raise RuntimeError("Verified dedicated foundation required")
        self.foundation = foundation
        for path in (WORK, STAGE):
            if path.is_symlink():
                raise RuntimeError("Unsafe stage path")
            if path.exists() and path.stat().st_uid != 0:
                raise RuntimeError("Stage must be root-owned")
        WORK.mkdir(mode=0o700, exist_ok=True)
        self.lock = open(WORK / "build.lock", "a")
        fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.state_path = WORK / "state.json"
        if self.state_path.exists():
            self.state = json.loads(self.state_path.read_text())
        else:
            self.state = {"started_at_utc": datetime.now(timezone.utc).isoformat(), "steps": [],
                          "initial_free_bytes": shutil.disk_usage("/").free, "download_bytes": 0}
        self.state.setdefault("executions", []).append({"script_sha256": request.get("script_sha256"),
            "time_utc": datetime.now(timezone.utc).isoformat(), "plugin_only": request.get("plugin_only"),
            "prepare_only": request.get("prepare_only"), "approve_build": request.get("approve_build")})
        self.log = open(WORK / "commands.log", "a", buffering=1)
        self.save()
        self.guard()

    def save(self):
        temporary = WORK / "state.json.new"
        temporary.write_text(json.dumps(self.state, indent=2))
        temporary.replace(self.state_path)

    def progress(self, message):
        # Progress transport must not turn a successful image creation into a
        # failed journal step when Cloudflare/SSH disconnects mid-build.
        if getattr(self, "log", None) is not None:
            self.log.write(json.dumps({"time": datetime.now(timezone.utc).isoformat(), "progress": message}) + "\n")
        try:
            print(message, flush=True)
        except BrokenPipeError:
            sys.stdout = open(os.devnull, "w")

    def run(self, args, timeout=120, logfile=None):
        self.log.write(json.dumps({"time": datetime.now(timezone.utc).isoformat(), "argv": list(map(str, args))}) + "\n")
        if logfile:
            with open(logfile, "a") as stream:
                process = subprocess.Popen(list(map(str, args)), stdout=stream, stderr=stream)
                elapsed = 0
                try:
                    while True:
                        try:
                            process.wait(timeout=10)
                            break
                        except subprocess.TimeoutExpired:
                            elapsed += 10
                            self.guard(reserve=1024**3)
                            self.health()
                            if elapsed >= timeout:
                                raise RuntimeError("Evaluation build time limit reached")
                except BaseException:
                    # Cancel this build client, never the shared user daemon or
                    # any native process. Retain layers/logs for review.
                    process.terminate()
                    try:
                        process.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                    raise
                result = process
        else:
            try:
                result = subprocess.run(list(map(str, args)), capture_output=True, text=True, timeout=timeout)
            except subprocess.TimeoutExpired as error:
                for partial in (error.stdout, error.stderr):
                    if partial:
                        self.log.write(partial.decode(errors="replace") if isinstance(partial, bytes) else partial)
                raise
            self.log.write(result.stdout + result.stderr + "\n")
        if result.returncode:
            raise RuntimeError("Command failed; inspect root-private evaluation-build logs")
        return result.stdout if not logfile else ""

    def docker(self, args, **kwargs):
        return self.run(["sudo", "-n", "-H", "-u", "dt-containers", "env", "XDG_RUNTIME_DIR=/run/user/5001",
                         "DOCKER_HOST=unix:///run/user/5001/docker.sock",
                         "PATH=/opt/ndf-rootless/29.6.1/bin:/usr/bin:/bin:/usr/sbin:/sbin", str(BIN), *args], **kwargs)

    def guard(self, reserve=0):
        free = shutil.disk_usage("/").free
        used = max(0, self.state["initial_free_bytes"] - free)
        if free - reserve < FREE_FLOOR or used + reserve > DISK_CAP:
            raise RuntimeError("Evaluation-build disk guard reached; no automatic deletion")

    def health(self):
        for path, expected in self.foundation["before"]["hashes"].items():
            if not Path(path).is_file() or digest(path) != expected:
                raise RuntimeError("Protected native configuration changed")
        for unit, expected in self.foundation["before"]["services"].items():
            actual = subprocess.run(["systemctl", "is-active", unit], capture_output=True, text=True).stdout.strip()
            if actual != expected:
                raise RuntimeError("Protected native service health changed")
        lines = subprocess.check_output(["supervisorctl", "status"], text=True).splitlines()
        native = [line for line in lines if line.startswith(("bench-web:", "bench-workers:", "bench-redis:"))]
        if len(native) != 7 or any("RUNNING" not in line for line in native):
            raise RuntimeError("Native Bench not healthy")
        pids = {line.split()[0]: re.search(r"pid (\d+)", line).group(1) for line in native}
        if self.state.get("native_pids") and self.state["native_pids"] != pids:
            raise RuntimeError("Native Bench process identity changed")
        self.state.setdefault("native_pids", pids)
        if subprocess.check_output(["iptables", "-S", "FORWARD"], text=True) != self.foundation["before"]["forward"]:
            raise RuntimeError("Host forwarding changed")
        info = json.loads(self.docker(["info", "--format", "{{json .}} "]))
        if "name=rootless" not in info.get("SecurityOptions", []) or info.get("DockerRootDir") != "/home/dt-containers/.local/share/docker":
            raise RuntimeError("Wrong daemon")
        cgroup = Path("/sys/fs/cgroup/user.slice/user-5001.slice")
        if (cgroup / "memory.max").read_text().strip() != "3221225472" or (cgroup / "cpu.max").read_text().strip() != "200000 100000":
            raise RuntimeError("Aggregate resource budget not enforced")
        self.save()

    def plugin(self):
        tool = self.request["tool"]
        if tool["bytes"] != 17800928 or tool["sha256"] != "642e9a8a0d115a8a57ad7045a4c7c2561522331197f0c087a762de1b72591672":
            raise RuntimeError("Unreviewed plugin artifact")
        package = WORK / "buildx.deb"
        if not package.exists():
            partial = WORK / "buildx.deb.download"
            if partial.exists():
                raise RuntimeError("Partial download requires review")
            self.guard(reserve=100 * 1024**2)
            url = tool["repository"] + "/" + tool["filename"]
            if not url.startswith("https://download.docker.com/linux/ubuntu/dists/noble/pool/stable/amd64/") or ".." in url:
                raise RuntimeError("Unsafe plugin URL")
            with urlopen(url, timeout=30) as response, open(partial, "xb") as output:
                while True:
                    remaining = min(DOWNLOAD_CAP - self.state["download_bytes"], tool["bytes"] - output.tell())
                    if remaining <= 0:
                        break
                    data = response.read(min(256 * 1024, remaining))
                    if not data:
                        break
                    self.state["download_bytes"] += len(data)
                    self.save()
                    output.write(data)
            if partial.stat().st_size != tool["bytes"] or digest(partial) != tool["sha256"]:
                raise RuntimeError("Plugin size/hash mismatch")
            partial.rename(package)
        if package.stat().st_size != tool["bytes"] or digest(package) != tool["sha256"]:
            raise RuntimeError("Cached plugin mismatch")
        extracted = WORK / "buildx-extracted"
        if not extracted.exists():
            self.run(["dpkg-deb", "--extract", package, extracted])
        source = extracted / "usr/libexec/docker/cli-plugins/docker-buildx"
        destination = Path("/opt/ndf-rootless/buildx/0.38.1/docker-buildx")
        for directory in (destination.parent.parent, destination.parent):
            directory.mkdir(mode=0o750, exist_ok=True)
            if directory.is_symlink() or directory.stat().st_uid != 0:
                raise RuntimeError("Unsafe plugin destination")
            os.chown(directory, 0, 5001)
            directory.chmod(0o750)
        if destination.exists():
            if destination.is_symlink() or digest(destination) != digest(source):
                raise RuntimeError("Unknown existing plugin binary")
        else:
            shutil.copyfile(source, destination)
            os.chown(destination, 0, 5001)
            destination.chmod(0o750)
        link = Path("/home/dt-containers/.docker/cli-plugins/docker-buildx")
        if link.exists() or link.is_symlink():
            if not link.is_symlink() or link.resolve() != destination:
                raise RuntimeError("Unknown existing plugin registration")
        else:
            link.symlink_to(destination)
            os.lchown(link, 5001, 5001)
        version = self.docker(["buildx", "version"])
        if "v0.38.1" not in version:
            raise RuntimeError("Unexpected Buildx version")
        inspection = self.docker(["buildx", "inspect", "default"])
        driver = re.search(r"^Driver:\s+(\S+)\s*$", inspection, re.M)
        if not driver or driver.group(1) != "docker":
            raise RuntimeError("Integrated Docker builder not available; no fallback download")
        self.docker(["image", "inspect", BASE])
        self.state["plugin"] = {"version": version.strip(), "driver": driver.group(1), "package_sha256": tool["sha256"]}
        self.save()

    def snapshot(self):
        destination = WORK / "source-snapshot"
        if destination.exists():
            raise RuntimeError("Existing/partial source snapshot requires review; no overwrite")
        apps = BENCH / "apps"
        before = manifest(apps)
        self.guard(reserve=sum(row.get("bytes", 0) for row in before.values()))
        self.check_pilot_revisions(apps)
        destination.mkdir(mode=0o700)
        for app in sorted(apps.iterdir()):
            if not app.is_dir() or app.is_symlink():
                raise RuntimeError("Unexpected app-tree root")
            self.guard(reserve=1024**3)
            def checked_copy(source, target):
                self.guard(reserve=Path(source).stat().st_size)
                return shutil.copy2(source, target)
            shutil.copytree(app, destination / app.name, symlinks=True, copy_function=checked_copy)
            self.health()
        if manifest(apps) != before or manifest(destination) != before:
            raise RuntimeError("Source changed during copy or snapshot differs; stop")
        self.check_pilot_revisions(apps)
        (WORK / "source-manifest.json").write_text(json.dumps(before, sort_keys=True))
        self.state["source_apps"] = sorted(path.name for path in apps.iterdir())
        self.save()

    def check_pilot_revisions(self, apps):
        for app in self.request["sources"]["apps"]:
            revision = self.run(["sudo", "-n", "-H", "-u", "frappe", "git", "-C", apps / app["app"], "rev-parse", "HEAD"])
            if revision.strip() != app["commit"]:
                raise RuntimeError("Pilot source revision drift")

    def copy_filtered(self, source, destination, excluded_rows, links):
        if source.is_symlink():
            links.append((source, destination))
            return
        info = source.lstat()
        if stat.S_ISDIR(info.st_mode):
            destination.mkdir(mode=0o755)
            destination.chmod(0o755)
            for child in sorted(source.iterdir()):
                if excluded(child.name):
                    excluded_rows.append(str(child))
                else:
                    self.copy_filtered(child, destination / child.name, excluded_rows, links)
        elif stat.S_ISREG(info.st_mode):
            self.guard(reserve=info.st_size)
            shutil.copyfile(source, destination)
            destination.chmod(0o755 if info.st_mode & 0o111 else 0o644)
        else:
            raise RuntimeError("Special file in filtered input")

    def context(self):
        if STAGE.exists():
            if self.request.get("review_existing_context") and self.request.get("prepare_only"):
                return self.review_context()
            raise RuntimeError("Existing/partial filtered stage requires review")
        expected = json.loads((WORK / "source-manifest.json").read_text())
        if manifest(WORK / "source-snapshot") != expected:
            raise RuntimeError("Private source snapshot changed since verification")
        self.health()
        support = WORK / "python-support"
        support.mkdir(mode=0o700, exist_ok=True)
        support_evidence = {}
        for name, original in (("sitecustomize.py", Path("/etc/python3.14/sitecustomize.py")),
                               ("libpython3.14.so.1.0", Path("/usr/lib/x86_64-linux-gnu/libpython3.14.so.1.0"))):
            if original.is_symlink() or not original.is_file() or original.stat().st_uid != 0:
                raise RuntimeError("Unexpected native Python support source")
            before = digest(original)
            copy = support / name
            if copy.exists():
                if copy.is_symlink() or digest(copy) != before:
                    raise RuntimeError("Existing Python support copy differs")
            else:
                self.guard(reserve=original.stat().st_size)
                shutil.copyfile(original, copy)
            if digest(original) != before or digest(copy) != before:
                raise RuntimeError("Python support source changed during copy")
            support_evidence[name] = {"sha256": before, "bytes": copy.stat().st_size}
        (WORK / "python-support.json").write_text(json.dumps(support_evidence, indent=2))
        STAGE.mkdir(mode=0o750)
        os.chown(STAGE, 0, 5001)
        STAGE.chmod(0o750)
        context = STAGE / "context"
        context.mkdir(mode=0o755)
        context.chmod(0o755)
        exclusions, links = [], []
        self.copy_filtered(WORK / "source-snapshot", context / "apps", exclusions, links)
        runtime = context / "runtime"
        runtime.mkdir(mode=0o755)
        runtime.chmod(0o755)
        for name in ("python3.14", "stdlib", "application-env", "bench-env", "node", "compatibility", "native-clients"):
            self.copy_filtered(PROBE / name, runtime / name, exclusions, links)
        self.copy_filtered(support / "sitecustomize.py", runtime / "sitecustomize.py", exclusions, links)
        self.copy_filtered(support / "libpython3.14.so.1.0", runtime / "compatibility/libpython3.14.so.1.0", exclusions, links)
        (runtime / "compatibility/libpython3.14.so.1").symlink_to("libpython3.14.so.1.0")
        # Review each symlink against original staged roots. Reconstruct only
        # known interpreter symlinks; never expose an arbitrary absolute target.
        roots = [(WORK / "source-snapshot", context / "apps")]
        roots += [(PROBE / name, runtime / name) for name in ("stdlib", "application-env", "bench-env", "node", "compatibility", "native-clients")]
        mappings = []
        for source, destination in links:
            target, reason = staged_link(source, destination, roots, support)
            destination.symlink_to(target)
            mappings.append({"path": str(destination.relative_to(context)), "target": target, "reason": reason})
        (WORK / "symlink-mappings-v2.json").write_text(json.dumps(mappings, indent=2))
        for name, encoded in self.request["recipe"].items():
            if name not in {"Dockerfile", "ndf-native-exec"}:
                raise RuntimeError("Unknown recipe input")
            path = context / name
            path.write_bytes(base64.b64decode(encoded, validate=True))
            path.chmod(0o755 if name == "ndf-native-exec" else 0o644)
        (WORK / "excluded-paths.json").write_text(json.dumps(exclusions, indent=2))
        findings = []
        for parent, dirs, files in os.walk(context, followlinks=False):
            for name in files:
                path = Path(parent) / name
                if path.is_symlink():
                    continue
                with open(path, "rb") as stream:
                    tail = b""
                    for block in iter(lambda: stream.read(1024 * 1024), b""):
                        data = tail + block
                        for label, pattern in SECRET_PATTERNS:
                            if pattern.search(data):
                                findings.append({"path": str(path.relative_to(context)), "indicator": label})
                        tail = data[-512:]
        (WORK / "credential-scan.json").write_text(json.dumps(findings, indent=2))
        if findings:
            raise RuntimeError("Possible embedded credentials in filtered context; private review required")
        (WORK / "filtered-manifest.json").write_text(json.dumps(manifest(context), sort_keys=True))
        self.state["excluded_entry_count"] = len(exclusions)
        self.state["context_path"] = str(context)
        self.save()

    def review_context(self):
        """Verify a retained context without editing, deleting or trusting it."""
        self.health()
        self.guard()
        if not (WORK / "hold-before-build").is_file():
            raise RuntimeError("Preparation review requires the operator build hold")
        decision = self.request["credential_review"]
        if decision["report"] != "credential-review-structure-v2.json":
            raise RuntimeError("Unexpected review report")
        report_path = WORK / decision["report"]
        if digest(report_path) != decision["report_sha256"]:
            raise RuntimeError("Review report differs from pinned human-reviewed evidence")
        report = json.loads(report_path.read_text())
        if sum(bool(row["record_match"]) for row in report) != decision["installed_record_matches"]:
            raise RuntimeError("Review provenance count mismatch")
        context = STAGE / "context"
        roots = [(WORK / "source-snapshot", context / "apps")]
        roots += [(PROBE / name, context / "runtime" / name) for name in
                  ("stdlib", "application-env", "bench-env", "node", "compatibility", "native-clients")]
        link_rows = json.loads((WORK / "symlink-mappings-v2.json").read_text())
        links = {row["path"]: row["target"] for row in link_rows}
        if len(links) != len(link_rows):
            raise RuntimeError("Duplicate link mapping")
        expected = {"runtime": {"type": "directory", "mode": 0o755}}

        def add_file(path, relative):
            if path.is_symlink() or not path.is_file():
                raise RuntimeError("Expected regular private-copy input")
            info = path.stat()
            expected[relative] = {"type": "file", "mode": 0o755 if info.st_mode & 0o111 else 0o644,
                                  "bytes": info.st_size, "sha256": digest(path)}

        def add_tree(source, prefix, recorded=None):
            expected[prefix] = {"type": "directory", "mode": 0o755}
            rows = recorded if recorded is not None else manifest(source)
            for relative, row in rows.items():
                if any(excluded(part) for part in Path(relative).parts):
                    continue
                key = prefix + "/" + relative
                if row["type"] == "symlink":
                    if key not in links:
                        raise RuntimeError("Missing reviewed link mapping")
                    target, reason = staged_link(source / relative, context / key, roots, WORK / "python-support")
                    if target != links[key]:
                        raise RuntimeError("Reviewed link mapping changed")
                    expected[key] = {"type": "symlink", "mode": 0o777, "target": target}
                elif row["type"] == "directory":
                    expected[key] = {"type": "directory", "mode": 0o755}
                else:
                    expected[key] = {**row, "mode": 0o755 if row["mode"] & 0o111 else 0o644}

        self.progress("Review: verifying context against recorded app snapshot and private runtimes")
        add_tree(WORK / "source-snapshot", "apps", json.loads((WORK / "source-manifest.json").read_text()))
        add_file(PROBE / "python3.14", "runtime/python3.14")
        for name in ("stdlib", "application-env", "bench-env", "node", "compatibility", "native-clients"):
            add_tree(PROBE / name, "runtime/" + name)
        support = WORK / "python-support"
        add_file(support / "sitecustomize.py", "runtime/sitecustomize.py")
        add_file(support / "libpython3.14.so.1.0", "runtime/compatibility/libpython3.14.so.1.0")
        expected["runtime/compatibility/libpython3.14.so.1"] = {"type": "symlink", "mode": 0o777, "target": "libpython3.14.so.1.0"}
        for name, encoded in self.request["recipe"].items():
            data = base64.b64decode(encoded, validate=True)
            expected[name] = {"type": "file", "mode": 0o755 if name == "ndf-native-exec" else 0o644,
                              "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        self.progress("Review: hashing and scanning retained context; no build scheduled")
        actual, findings = inspected_manifest(context)
        mismatches = sorted(key for key in set(expected) | set(actual) if expected.get(key) != actual.get(key))
        if mismatches:
            (WORK / "context-review-mismatches-v2.json").write_text(json.dumps(mismatches, indent=2))
            raise RuntimeError("Retained context differs from filtered source/runtime/recipe inputs")
        verify_review(findings, actual, report, decision["reviewed_file_count"])
        (WORK / "filtered-manifest.json").write_text(json.dumps(actual, sort_keys=True))
        acceptance = {"time_utc": datetime.now(timezone.utc).isoformat(), "report_sha256": decision["report_sha256"],
                      "context_matches_inputs": True, "reviewed_findings": len(findings), "unresolved_findings": 0,
                      "image_built": False, "operator_build_hold": True,
                      "limitations": decision["limitations"]}
        (WORK / "credential-review-acceptance-v2.json").write_text(json.dumps(acceptance, indent=2))
        self.state["credential_review"] = acceptance
        self.state["context_path"] = str(context)
        self.save()
        self.health()
        self.guard()

    def approve_build(self):
        if not self.request.get("approve_build") or self.request.get("prepare_only"):
            raise RuntimeError("Explicit operator build go-ahead required")
        if not self.state.get("prepared") or "context" not in self.state["steps"]:
            raise RuntimeError("Preparation must pass before build approval")
        acceptance = json.loads((WORK / "credential-review-acceptance-v2.json").read_text())
        if not acceptance.get("context_matches_inputs") or acceptance.get("unresolved_findings") != 0:
            raise RuntimeError("Context review has unresolved findings")
        approval = {"time_utc": datetime.now(timezone.utc).isoformat(),
                    "reason": "Operator explicitly requested: Build it and also inform me of the process",
                    "context_manifest_sha256": digest(WORK / "filtered-manifest.json"),
                    "script_sha256": self.request.get("script_sha256"), "customer_data_mount_approved": False}
        hold = WORK / "hold-before-build"
        if hold.is_symlink():
            raise RuntimeError("Unexpected hold symlink")
        if hold.exists():
            archive = WORK / ("hold-before-build.released-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
            if archive.exists():
                raise RuntimeError("Existing approval archive; no overwrite")
            hold.rename(archive)
            approval["preserved_hold_record"] = archive.name
        self.state["build_approval"] = approval
        self.save()
        self.progress("Build go-ahead recorded; preparation hold archived, not deleted")

    def build(self):
        if self.request.get("prepare_only") or (WORK / "hold-before-build").exists():
            raise RuntimeError("Operator hold: notify after preparation and wait for explicit build go-ahead")
        self.health()
        if self.state.get("build_approval", {}).get("context_manifest_sha256") != digest(WORK / "filtered-manifest.json"):
            raise RuntimeError("Build approval does not match prepared context manifest")
        if self.state.get("image"):
            # Image ID is journaled only after our build command exits zero.
            # Recover a lost final stdout/journal-step without rebuilding or
            # adopting an unrelated image/tag.
            image = json.loads(self.docker(["image", "inspect", TAG]))[0]
            if image["Id"] != self.state["image"] or image["Config"].get("Entrypoint") != ["/usr/local/bin/ndf-native-exec"]:
                raise RuntimeError("Recorded image no longer matches evaluation tag/config")
            self.guard()
            self.progress("Recovered previously completed image build by exact recorded ID; no rebuild")
            return
        self.guard(reserve=12 * 1024**3)
        self.progress("Pre-build check: rehashing approved context; no image build started yet")
        if manifest(STAGE / "context") != json.loads((WORK / "filtered-manifest.json").read_text()):
            raise RuntimeError("Prepared context changed before build")
        packages = package_inventory(PROBE / "application-env/lib/python3.14/site-packages")
        cli_packages = package_inventory(PROBE / "bench-env/lib/python3.14/site-packages")
        self.state["expected_native_packages"] = packages
        self.state["expected_bench_version"] = cli_packages["frappe-bench"]
        self.save()
        existing = self.docker(["image", "ls", "--format", "{{.Repository}}:{{.Tag}}"])
        if TAG in existing.splitlines():
            raise RuntimeError("Evaluation tag already exists; no overwrite")
        self.health()
        self.guard(reserve=12 * 1024**3)
        if (WORK / "hold-before-build").exists():
            raise RuntimeError("Operator reinstated build hold")
        self.progress("Build starting: cached base, integrated builder, network-none build steps, local tag only")
        self.docker(["buildx", "build", "--builder", "default", "--pull=false", "--network=none",
                     "--provenance=false", "--sbom=false", "--load", "--progress=plain", "--tag", TAG,
                     str(STAGE / "context")], timeout=3600, logfile=WORK / "build.log")
        self.state["image"] = json.loads(self.docker(["image", "inspect", TAG]))[0]["Id"]
        self.save()
        self.progress("Image build finished: " + self.state["image"])
        self.health()
        self.guard()

    def validate(self, target_image=None, receipt_name="validation.json"):
        self.progress("Validation starting: temporary read-only, network-none container; no host/data mounts")
        apps = [row["app"] for row in self.request["sources"]["apps"]]
        code = "APPS = " + repr(apps) + "\n" + r'''import ctypes,importlib,importlib.metadata,json,re,ssl,subprocess,sys
r={'python':sys.version.split()[0],'openssl':ssl.OPENSSL_VERSION,'imports':{},'commands':{},'pilot_apps':APPS}
library=ctypes.CDLL('/opt/ndf-native/lib/libpython3.14.so.1.0');library.Py_GetVersion.restype=ctypes.c_char_p
r['libpython']=library.Py_GetVersion().decode()
for name in ['node','yarn','mariadb','mariadb-dump','bench']:
 print('VALIDATING command: '+name,file=sys.stderr,flush=True)
 command='/home/frappe/.venv/bin/bench' if name=='bench' else name
 try:
  p=subprocess.run([command,'--version'],capture_output=True,text=True,timeout=30)
  r['commands'][name]={'code':p.returncode,'version':p.stdout.strip(),'error':p.stderr.strip()[:300] if p.returncode else None}
 except (OSError,subprocess.TimeoutExpired) as error:r['commands'][name]={'code':None,'version':None,'error':type(error).__name__}
for name in dict.fromkeys(APPS+['pymysql','MySQLdb','PIL.Image','cryptography','redis','rq','gunicorn','numpy','pandas','pyarrow']):
 print('VALIDATING import: '+name,file=sys.stderr,flush=True)
 try:
  m=importlib.import_module(name);r['imports'][name]={'ok':True,'file':getattr(m,'__file__',None),'version':getattr(m,'__version__',None)}
 except Exception as e:r['imports'][name]={'ok':False,'error':type(e).__name__,'message':str(e)[:300]}
r['packages']={re.sub(r'[-_.]+','-',d.metadata['Name']).lower():d.version for d in importlib.metadata.distributions(path=['/home/frappe/bench/env/lib/python3.14/site-packages'])}
print(json.dumps(r))'''
        output = self.docker(["run", "--rm", "--pull=never", "--network=none", "--read-only", "--cap-drop=ALL",
                              "--label=ndf.purpose=retained-image-validation",
                              "--security-opt=no-new-privileges", "--memory=768m", "--cpus=0.5", "--pids-limit=96",
                              "--env=OPENBLAS_NUM_THREADS=1", "--env=OMP_NUM_THREADS=1", "--env=NUMEXPR_NUM_THREADS=1",
                              target_image or self.state.get("retained_image_id") or self.state["image"],
                              "/home/frappe/bench/env/bin/python", "-c", code], timeout=300)
        result = json.loads(output)
        if receipt_name not in {"validation.json", "validation-yarn-v2.json"}:
            raise RuntimeError("Unexpected validation receipt name")
        (WORK / receipt_name).write_text(json.dumps(result, indent=2))
        assert_validation(result, self.request["sources"]["runtime"], self.state["expected_native_packages"], self.state["expected_bench_version"])
        self.state["validation"] = result
        self.save()
        self.progress("No-data image validation passed")

    def validate_retained_image(self):
        if self.request.get("prepare_only") or (WORK / "hold-before-build").exists():
            raise RuntimeError("Operator hold still active")
        if not self.state.get("prepared") or "context" not in self.state["steps"]:
            raise RuntimeError("Passed preparation required")
        expected = self.request["retained_image"]
        if expected["tag"] != TAG or expected["required_base"] != BASE:
            raise RuntimeError("Unreviewed retained-image scope")
        if self.state.get("build_approval", {}).get("context_manifest_sha256") != digest(WORK / "filtered-manifest.json"):
            raise RuntimeError("Retained export approval does not match prepared manifest")
        self.health()
        self.guard(reserve=256 * 1024**2)
        image = json.loads(self.docker(["image", "inspect", TAG]))[0]
        base = json.loads(self.docker(["image", "inspect", BASE]))[0]
        verify_retained_export(image, base, expected["exported_manifest"], (WORK / "build.log").read_text())
        self.state["retained_image_id"] = image["Id"]
        self.state["original_build_outcome"] = "canceled-after-export-by-disk-guard"
        self.state["original_build_command_succeeded"] = False
        if "retained_image_verified" not in self.state["steps"]:
            self.state["steps"].append("retained_image_verified")
        self.save()
        self.progress("Retained export verified by exact digest, log, base layers and startup config; no rebuild")
        self.validate()
        self.health()
        self.guard()
        if "retained_image_validated" not in self.state["steps"]:
            self.state["steps"].append("retained_image_validated")
        self.state["retained_image_validation_passed"] = True
        if self.state.get("last_error"):
            self.state.setdefault("historical_errors", []).append(self.state.pop("last_error"))
        self.save()
        receipt = {"retained_image_id": image["Id"], "no_data_validation_passed": True,
                   "original_build_command_succeeded": False, "rebuild_performed": False,
                   "customer_data_loaded": False, "cleanup_performed": False}
        (WORK / "retained-image-validation.json").write_text(json.dumps(receipt, indent=2))
        self.progress(json.dumps(receipt))

    def apply(self):
        if self.request.get("validate_retained_image"):
            try:
                self.validate_retained_image()
            except Exception as error:
                self.state["last_error"] = str(error)
                self.save()
                raise
            return
        if self.state.get("retained_image_id") and "build" not in self.state["steps"]:
            raise RuntimeError("Retained canceled-export image has a separate validation path; do not rebuild automatically")
        if self.state.get("complete"):
            self.progress(json.dumps({"evaluation_already_complete": True, "image": self.state["image"]}))
            return
        self.health()
        actions = [("plugin", self.plugin)]
        if not self.request.get("plugin_only"):
            actions += [("snapshot", self.snapshot), ("context", self.context)]
            if not self.request.get("prepare_only"):
                actions += [("build", self.build), ("validate", self.validate)]
        try:
            for name, action in actions:
                if name in self.state["steps"]:
                    continue
                self.progress("Evaluation step: " + name)
                if name == "build" and self.request.get("approve_build"):
                    self.approve_build()
                action()
                self.state["steps"].append(name)
                self.save()
                self.guard()
                self.health()
            self.state["prepared"] = "context" in self.state["steps"]
            self.state["complete"] = not self.request.get("plugin_only") and not self.request.get("prepare_only")
            if self.state.get("last_error"):
                self.state.setdefault("historical_errors", []).append(self.state.pop("last_error"))
            self.save()
        except Exception as error:
            self.state["last_error"] = str(error)
            self.save()
            raise
        self.progress(json.dumps({"completed_steps": self.state["steps"], "download_bytes": self.state["download_bytes"],
                          "image": self.state.get("image"), "customer_data_loaded": False,
                          "preparation_only": bool(self.request.get("prepare_only")),
                          "build_on_hold": (WORK / "hold-before-build").exists()}))


if __name__ == "__main__":
    if "REQUEST" not in globals():
        raise SystemExit("Use build_exact_runtime.py with explicit approval")
    EvaluationBuild(REQUEST).apply()
