#!/usr/bin/env python3
"""Root-only, pinned Dalali preparation checkpoint; invoked by collect_checkpoint.py.

Never changes services/configuration or uses a global database lock. The bounded
pause uses READ locks for base tables and exclusive locks for selected sequences.
"""

from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import shutil
import socket
import subprocess
import tempfile
import threading
import time


HOST = "mail.ndftechlabs.com"
BENCH = Path("/home/frappe/bench")
SITE = "dalali.ndftechlabs.com"
RECIPIENT = "E220A389F4E9105E6465C292F4708B832A07005F"
BASE = Path("/var/backups/ndf-migration")


def identifier(value):
    if not value or "\0" in value:
        raise ValueError("Invalid SQL identifier")
    return "`" + value.replace("`", "``") + "`"


def shutdown_connection(connection):
    # A watchdog must be able to interrupt an in-flight LOCK request without
    # sharing the SQL protocol or sending commands to another session.
    transport = getattr(connection, "_sock", None)
    if transport is not None:
        try:
            transport.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
    try:
        connection.close()
    except Exception:
        pass


class ScopedSchemaLock:
    def __init__(self, connect, database, tables, acquire_seconds=5, hold_seconds=55, sequences=()):
        if not tables or not 0 < acquire_seconds <= 5 or not 0 < hold_seconds <= 55:
            raise ValueError("Unsafe lock scope or deadline")
        self.connect = connect
        self.database = database
        self.tables = tuple(tables)
        self.sequences = tuple(sequences)
        if set(self.tables) & set(self.sequences):
            raise ValueError("Overlapping table/sequence scope")
        self.acquire_seconds = acquire_seconds
        self.hold_seconds = hold_seconds
        self.expired = threading.Event()
        self.connection = None
        self.timer = None
        self.on_timeout = lambda: None
        self.acquired_at = None
        self.held_seconds = None

    def expire(self):
        self.expired.set()
        if self.connection is not None:
            shutdown_connection(self.connection)
        self.on_timeout()

    def __enter__(self):
        self.connection = self.connect()
        try:
            with self.connection.cursor() as cursor:
                cursor.execute("SET SESSION lock_wait_timeout=5")
                cursor.execute("SET SESSION innodb_lock_wait_timeout=5")
                cursor.execute("SET SESSION wait_timeout=60")
                cursor.execute("SET autocommit=0")
                scope = ",".join(
                    [identifier(self.database) + "." + identifier(table) + " READ" for table in self.tables]
                    + [identifier(self.database) + "." + identifier(sequence) + " WRITE" for sequence in self.sequences]
                )
                self.timer = threading.Timer(self.acquire_seconds, self.expire)
                self.timer.daemon = True
                self.timer.start()
                cursor.execute("LOCK TABLES " + scope)
            self.timer.cancel()
            self.timer.join()
            if self.expired.is_set():
                raise TimeoutError("Pilot lock acquisition deadline exceeded")
            self.acquired_at = time.monotonic()
            self.timer = threading.Timer(self.hold_seconds, self.expire)
            self.timer.daemon = True
            self.timer.start()
            return self
        except Exception:
            if self.timer:
                self.timer.cancel()
                self.timer.join()
            shutdown_connection(self.connection)
            raise

    def sequence_sql(self):
        statements = ["-- Sequence definitions/cache boundaries captured by the lock-owning session\n"]
        with self.connection.cursor() as cursor:
            for name in self.sequences:
                qualified = identifier(self.database) + "." + identifier(name)
                cursor.execute("SHOW CREATE SEQUENCE " + qualified)
                definition = cursor.fetchone()[1].rstrip(";")
                cursor.execute("SELECT next_not_cached_value FROM " + qualified)
                boundary = int(cursor.fetchone()[0])
                statements.extend(["DROP SEQUENCE IF EXISTS " + identifier(name) + ";\n",
                                   definition + ";\n", "DO SETVAL(" + identifier(name) + ", " + str(boundary) + ", 0);\n"])
        return "".join(statements).encode("utf-8") if self.sequences else b""

    def __exit__(self, kind, value, traceback):
        try:
            if kind is None and not self.expired.is_set():
                with self.connection.cursor() as cursor:
                    cursor.execute("UNLOCK TABLES")
                self.connection.commit()
        finally:
            shutdown_connection(self.connection)
            self.timer.cancel()
            self.timer.join()
            self.held_seconds = time.monotonic() - self.acquired_at
        if self.expired.is_set() and kind is None:
            raise TimeoutError("Pilot schema-lock deadline exceeded; checkpoint is invalid")
        return False


def stop_processes(processes):
    for process in processes:
        if process.poll() is None:
            process.terminate()
    for process in processes:
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)


def locked_dump(connect, database, tables, args, env, output, log, acquire_seconds=5, hold_seconds=55, sequences=()):
    processes = []
    lock = ScopedSchemaLock(connect, database, tables, acquire_seconds, hold_seconds, sequences)
    try:
        with lock:
            lock.on_timeout = lambda: stop_processes(processes)
            with open(output, "xb") as target, open(log, "xb") as errors:
                prefix = lock.sequence_sql()
                if prefix:
                    # Concatenated gzip members are supported by gzip/Frappe;
                    # sequence DDL precedes any table defaults that reference it.
                    target.write(gzip.compress(prefix, compresslevel=1))
                    target.flush()
                dump_args = list(args) + ["--ignore-table=" + database + "." + name for name in sequences]
                dump = subprocess.Popen(dump_args, stdout=subprocess.PIPE, stderr=errors, env=env)
                processes.append(dump)
                compressor = subprocess.Popen(["gzip", "-1"], stdin=dump.stdout, stdout=target, stderr=errors)
                processes.append(compressor)
                resource.prlimit(compressor.pid, resource.RLIMIT_CORE, (0, 0))
                resource.prlimit(compressor.pid, resource.RLIMIT_FSIZE, (1024**3, 1024**3))
                dump.stdout.close()
                for process in processes:
                    remaining = hold_seconds - (time.monotonic() - lock.acquired_at) - 1
                    process.wait(timeout=max(0.05, remaining))
                if lock.expired.is_set() or any(process.returncode for process in processes):
                    raise RuntimeError("Database dump failed; private diagnostic retained")
                target.flush()
                os.fsync(target.fileno())
    finally:
        stop_processes(processes)
    # CRC/decompression verification happens after the lock is released.
    create_count = sequence_count = 0
    with gzip.open(output, "rb") as source:
        for line in source:
            create_count += line.startswith(b"CREATE TABLE `")
            sequence_count += line.startswith(b"CREATE SEQUENCE `")
    if create_count != len(tables):
        raise RuntimeError("Dump table-definition count does not match the locked scope")
    if sequence_count != len(sequences):
        raise RuntimeError("Dump sequence-definition count does not match the locked scope")
    return {"lock_held_seconds": round(lock.held_seconds, 3), "locked_table_count": len(tables),
            "locked_sequence_count": len(sequences), "dump_create_table_count": create_count,
            "dump_create_sequence_count": sequence_count, "database_gzip_crc_verified": True}


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def git_head(path):
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0", GIT_PAGER="cat")
    return subprocess.check_output(["git", "--no-optional-locks", "-c", f"safe.directory={path}",
                                    "-c", "core.fsmonitor=false", "-C", str(path), "rev-parse", "HEAD"],
                                   env=env, text=True, stderr=subprocess.DEVNULL).strip()


def git_status(path):
    return subprocess.check_output(["sudo", "-n", "-H", "-u", "frappe", "env", "GIT_OPTIONAL_LOCKS=0", "GIT_PAGER=cat",
                                    "git", "--no-optional-locks", "-c", "core.fsmonitor=false", "-C", str(path),
                                    "status", "--porcelain=v1", "--untracked-files=normal"],
                                   text=True, stderr=subprocess.DEVNULL).strip()


def main(request):
    import pymysql

    if os.geteuid() != 0 or subprocess.check_output(["hostname"], text=True).strip() != HOST:
        raise RuntimeError("Checkpoint must run as root on the pinned production host")
    if request.get("recipient") != RECIPIENT or request.get("site") != SITE:
        raise RuntimeError("Unexpected site/recipient")
    public = request.get("public_key", "")
    if not public.startswith("-----BEGIN PGP PUBLIC KEY BLOCK-----") or "PRIVATE KEY" in public:
        raise RuntimeError("Only the approved public recipient is accepted")
    site_dir = BENCH / "sites" / SITE
    if site_dir.is_symlink() or BENCH.is_symlink():
        raise RuntimeError("Unexpected source symlink")
    config_path = site_dir / "site_config.json"
    common_path = BENCH / "sites/common_site_config.json"
    config = json.loads(config_path.read_text())
    common = json.loads(common_path.read_text())
    if config.get("maintenance_mode") != 1:
        raise RuntimeError("Pilot maintenance baseline changed; refusing to proceed")
    config_hashes = {str(path): sha256(path) for path in (config_path, common_path)}
    if config_hashes[str(config_path)] != request["site_config_sha256"] or config_hashes[str(common_path)] != request["common_config_sha256"]:
        raise RuntimeError("Source configuration changed since approved inventory")
    heads = {app: git_head(BENCH / "apps" / app) for app in request["app_commits"]}
    if heads != request["app_commits"]:
        raise RuntimeError("Source revisions changed; refresh the source preservation plan")
    statuses = {app: git_status(BENCH / "apps" / app) for app in heads}
    if any(statuses[app] for app in request["clean_apps"]):
        raise RuntimeError("An originally clean source tree now has changes; refresh the preservation plan")
    for directory in (site_dir / "public/files", site_dir / "private/files"):
        if directory.is_symlink():
            raise RuntimeError("External file storage needs an explicit backup plan")
        for parent, dirs, files in os.walk(directory, followlinks=False):
            if any((Path(parent) / name).is_symlink() for name in dirs + files):
                raise RuntimeError("Site file symlinks need review before collection")
    if shutil.disk_usage("/var/backups").free < 40 * 1024**3:
        raise RuntimeError("Insufficient server disk safety margin")
    if BASE.is_symlink():
        raise RuntimeError("Unsafe checkpoint directory")
    os.umask(0o077)
    BASE.mkdir(mode=0o700, exist_ok=True)
    if BASE.stat().st_uid != 0 or BASE.stat().st_mode & 0o077:
        raise RuntimeError("Checkpoint directory must be root-owned/private")
    checkpoint = Path(tempfile.mkdtemp(prefix=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-dalali-"), dir=BASE))
    stage = checkpoint / "staging"
    stage.mkdir(mode=0o700)
    key_home = checkpoint / "public-keyring"
    key_home.mkdir(mode=0o700)
    recipient_file = checkpoint / "recipient-public.asc"
    recipient_file.write_text(public)
    gpg = ["gpg", "--no-options", "--homedir", str(key_home), "--batch", "--no-autostart"]
    with open(checkpoint / "gpg-import.log", "xb") as log:
        subprocess.run(gpg + ["--import", str(recipient_file)], stdout=log, stderr=log, check=True)
    keys = subprocess.check_output(gpg + ["--with-colons", "--fingerprint", "--list-keys", RECIPIENT], text=True)
    fingerprints = [line.split(":")[9] for line in keys.splitlines() if line.startswith("fpr:")]
    if not fingerprints or fingerprints[0] != RECIPIENT:
        raise RuntimeError("Public recipient fingerprint mismatch")

    database = config["db_name"]
    if not re.fullmatch(r"[A-Za-z0-9_]+", database):
        raise RuntimeError("Unexpected database identifier")
    host = config.get("db_host") or common.get("db_host") or "127.0.0.1"
    port = int(config.get("db_port") or common.get("db_port") or 3306)
    if host not in {"localhost", "127.0.0.1"}:
        raise RuntimeError("Unexpected production database host")
    user = config.get("db_user") or database

    def connect():
        return pymysql.connect(host="127.0.0.1", port=port, user=user, password=config["db_password"],
                               database=database, connect_timeout=3, read_timeout=6, write_timeout=2, autocommit=False)

    connection = connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT TABLE_NAME,ENGINE,TABLE_TYPE FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s ORDER BY TABLE_NAME", (database,))
            table_rows = cursor.fetchall()
        connection.rollback()
    finally:
        connection.close()
    observed_types = {kind: sum(row[2] == kind for row in table_rows) for kind in {row[2] for row in table_rows}}
    if observed_types != request["object_type_counts"] or any(row[2] not in {"BASE TABLE", "SEQUENCE"} for row in table_rows):
        raise RuntimeError("Table scope changed or includes unreviewed views")
    tables = [row[0] for row in table_rows if row[2] == "BASE TABLE"]
    sequences = [row[0] for row in table_rows if row[2] == "SEQUENCE"]
    dump = shutil.which("mariadb-dump") or shutil.which("mysqldump")
    env = dict(os.environ, MYSQL_PWD=config["db_password"])
    dump_args = [dump, "--host", "127.0.0.1", "--port", str(port), "--protocol=TCP", "--user", user,
                 "--skip-lock-tables", "--no-tablespaces", "--quick", "--hex-blob", "--routines", "--events", "--triggers", database]
    print("Collecting bounded pilot-only database checkpoint", file=__import__("sys").stderr, flush=True)
    dump_result = locked_dump(connect, database, tables, dump_args, env, stage / "database.sql.gz", stage / "dump.log", sequences=sequences)
    manifest = {"host": HOST, "site": SITE, "recipient": RECIPIENT, "preparation_only": True,
                "not_a_final_cutover_snapshot": True, "checkpoint_id": checkpoint.name,
                "created_at_utc": datetime.now(timezone.utc).isoformat(), "app_commits": heads,
                "checkpoint_script_sha256": request["checkpoint_script_sha256"],
                "collector_script_sha256": request["collector_script_sha256"],
                "source_status_sha256": {app: hashlib.sha256(status.encode()).hexdigest() for app, status in statuses.items()},
                "native_config_sha256": config_hashes, "database_tables": table_rows, **dump_result,
                "notes": ["Native services/maintenance unchanged", "Only pilot base tables/sequence objects were locked",
                          "Filesystem capture follows database dump; final quiescent resync is still required",
                          "Host mail/SSH/DKIM directories were not selected; source configuration remains encrypted"]}
    (stage / "manifest.json").write_text(json.dumps(manifest, indent=2))
    components = [BENCH / "apps", BENCH / "env", BENCH / "config", config_path, common_path,
                  BENCH / "sites/apps.txt", BENCH / "sites/apps.json", site_dir / "public/files", site_dir / "private/files",
                  Path("/home/frappe/.venv"), Path("/home/frank/scripts"), Path("/etc/nginx"), Path("/etc/supervisor"),
                  Path("/usr/local/share/fonts"), stage]
    components = [path for path in components if path.exists()]
    cipher = checkpoint / "checkpoint.tar.gz.gpg"
    processes = []
    print("Pilot lock released; encrypting source/files checkpoint", file=__import__("sys").stderr, flush=True)
    try:
        with open(checkpoint / "archive.log", "xb") as log:
            tar = subprocess.Popen(["tar", "--create", "--gzip", "--numeric-owner", "--acls", "--xattrs",
                                    "--exclude=*/__pycache__", "--exclude=*.pyc", "-C", "/",
                                    *[str(path).lstrip("/") for path in components]], stdout=subprocess.PIPE, stderr=log)
            processes.append(tar)
            encrypt = subprocess.Popen(gpg + ["--trust-model", "always", "--compress-algo", "none", "--recipient", RECIPIENT,
                                             "--output", str(cipher), "--encrypt"], stdin=tar.stdout, stdout=log, stderr=log)
            processes.append(encrypt)
            resource.prlimit(encrypt.pid, resource.RLIMIT_CORE, (0, 0))
            resource.prlimit(encrypt.pid, resource.RLIMIT_FSIZE, (8 * 1024**3, 8 * 1024**3))
            tar.stdout.close()
            deadline = time.monotonic() + 1800
            for process in processes:
                process.wait(timeout=max(1, deadline - time.monotonic()))
            if any(process.returncode for process in processes):
                raise RuntimeError("Archive/encryption failed; root-private diagnostics retained")
    finally:
        stop_processes(processes)
    if {str(path): sha256(path) for path in (config_path, common_path)} != config_hashes:
        raise RuntimeError("Native configuration changed during collection; checkpoint needs review")
    if {app: git_head(BENCH / "apps" / app) for app in heads} != heads:
        raise RuntimeError("Source revisions changed during collection; checkpoint needs review")
    if {app: git_status(BENCH / "apps" / app) for app in heads} != statuses:
        raise RuntimeError("Source worktree status changed during collection; checkpoint needs review")
    receipt = {"host": HOST, "site": SITE, "recipient": RECIPIENT, "checkpoint_id": checkpoint.name,
               "artifact": str(cipher), "bytes": cipher.stat().st_size, "sha256": sha256(cipher),
               "preparation_only": True, **dump_result}
    (checkpoint / "receipt.json").write_text(json.dumps(receipt, indent=2))
    # Only our newly created staging files: encrypted archive is finalized first.
    shutil.rmtree(stage)
    print(json.dumps(receipt))


if __name__ == "__main__":
    if "REQUEST" not in globals():
        raise SystemExit("Use collect_checkpoint.py; no standalone production defaults")
    main(REQUEST)
