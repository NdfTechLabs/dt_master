"""Run in the existing local Docker backend, never on the production VM."""

import importlib.util
import gzip
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import time
from uuid import uuid4

import pymysql


def main():
    if os.environ.get("DT_DOCKER_SANDBOX") != "1" or not Path("/home/frappe/bench/sites/master.localhost/site_config.json").is_file():
        raise RuntimeError("Local Docker sandbox required")
    spec = importlib.util.spec_from_file_location("checkpoint", "/tmp/ndf-checkpoint.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    suffix = uuid4().hex[:10]
    pilot, other, restored = "migration_lock_" + suffix, "migration_other_" + suffix, "migration_restore_" + suffix
    username, password = "lock_probe_" + suffix, uuid4().hex
    def admin_connect():
        return pymysql.connect(host="db", user="root", password=os.environ["DB_ROOT_PASSWORD"],
                               connect_timeout=3, read_timeout=5, write_timeout=2, autocommit=True)
    def connect():
        return pymysql.connect(host="db", user=username, password=password,
                               connect_timeout=3, read_timeout=5, write_timeout=2, autocommit=True)
    connection = admin_connect()
    try:
        with connection.cursor() as cursor:
            for name in (pilot, other, restored):
                cursor.execute(f"CREATE DATABASE `{name}`")
            cursor.execute("CREATE USER %s@'%%' IDENTIFIED BY %s", (username, password))
            for name in (pilot, other, restored):
                cursor.execute(f"GRANT ALL ON `{name}`.* TO %s@'%%'", (username,))
            cursor.execute(f"CREATE TABLE `{pilot}`.business (id INT PRIMARY KEY,n INT) ENGINE=InnoDB")
            cursor.execute(f"CREATE TABLE `{pilot}`.logs (id INT PRIMARY KEY,n INT) ENGINE=MyISAM")
            cursor.execute(f"CREATE TABLE `{other}`.business (id INT PRIMARY KEY,n INT) ENGINE=InnoDB")
            cursor.execute(f"CREATE SEQUENCE `{pilot}`.ids START WITH 42 CACHE 10")
            cursor.execute(f"SELECT NEXT VALUE FOR `{pilot}`.ids")
            for table in (f"`{pilot}`.business", f"`{pilot}`.logs", f"`{other}`.business"):
                cursor.execute(f"INSERT INTO {table} VALUES (1,0)")
        errors = []
        def writer(table, started, done):
            client = connect()
            try:
                started.set()
                with client.cursor() as cursor:
                    cursor.execute(f"UPDATE {table} SET n=n+1 WHERE id=1")
                done.set()
            except Exception as exc:
                errors.append(type(exc).__name__)
            finally:
                client.close()
        sequence_started, sequence_done = threading.Event(), threading.Event()
        def allocate_id():
            client = connect()
            try:
                sequence_started.set()
                with client.cursor() as cursor:
                    cursor.execute(f"SELECT NEXT VALUE FOR `{pilot}`.ids")
                sequence_done.set()
            except Exception as exc:
                errors.append(type(exc).__name__)
            finally:
                client.close()
        started, blocked_done, other_started, other_done, innodb_started, innodb_done = [threading.Event() for _ in range(6)]
        with module.ScopedSchemaLock(connect, pilot, ["business", "logs"], acquire_seconds=2, hold_seconds=5, sequences=["ids"]):
            blocked = threading.Thread(target=writer, args=(f"`{pilot}`.logs", started, blocked_done))
            unrelated = threading.Thread(target=writer, args=(f"`{other}`.business", other_started, other_done))
            innodb = threading.Thread(target=writer, args=(f"`{pilot}`.business", innodb_started, innodb_done))
            allocator = threading.Thread(target=allocate_id)
            blocked.start(); unrelated.start(); innodb.start(); allocator.start()
            assert started.wait(1)
            assert other_done.wait(2), "Unrelated database write was blocked"
            assert not blocked_done.wait(0.2), "Pilot writer escaped its read lock"
            assert innodb_started.wait(1)
            assert not innodb_done.wait(0.2), "InnoDB writer escaped its read lock"
            assert sequence_started.wait(1)
            assert not sequence_done.wait(0.2), "Sequence allocation escaped its exclusive lock"
        assert blocked_done.wait(2), "Pilot lock was not released"
        assert innodb_done.wait(2)
        assert sequence_done.wait(2)
        blocked.join(); unrelated.join(); innodb.join(); allocator.join()
        assert not errors, errors
        expired = False
        try:
            with module.ScopedSchemaLock(connect, pilot, ["business", "logs"], acquire_seconds=2, hold_seconds=0.3, sequences=["ids"]):
                threading.Event().wait(0.6)
        except TimeoutError:
            expired = True
        assert expired, "Watchdog failed to reject an expired lock"
        with connection.cursor() as cursor:
            cursor.execute(f"UPDATE `{pilot}`.logs SET n=n+1 WHERE id=1")
        with tempfile.TemporaryDirectory(prefix="lock-rehearsal-") as directory:
            env = dict(os.environ, MYSQL_PWD=password)
            args = [shutil.which("mariadb-dump") or shutil.which("mysqldump"), "-h", "db", "-u", username,
                    "--skip-lock-tables", "--no-tablespaces", "--quick", "--hex-blob", "--routines", "--events", "--triggers", pilot]
            dump = module.locked_dump(connect, pilot, ["business", "logs"], args, env,
                                      Path(directory)/"db.sql.gz", Path(directory)/"dump.log", hold_seconds=5, sequences=["ids"])
            assert dump["locked_table_count"] == 2
            assert dump["dump_create_table_count"] == 2
            assert dump["dump_create_sequence_count"] == 1
            with gzip.open(Path(directory)/"db.sql.gz", "rb") as source:
                sql = source.read()
            restored_result = __import__('subprocess').run([
                "mariadb", "-h", "db", "-u", username, "--database", restored
            ], input=sql, env=env, capture_output=True)
            assert restored_result.returncode == 0, "Synthetic restore failed"
            with connection.cursor() as cursor:
                cursor.execute(f"SELECT n FROM `{restored}`.business WHERE id=1")
                assert cursor.fetchone()[0] == 1
                cursor.execute(f"SELECT NEXT VALUE FOR `{restored}`.ids")
                assert cursor.fetchone()[0] >= 44, "Restored sequence regressed allocated IDs"
            failed = False
            try:
                module.locked_dump(connect, pilot, ["business", "logs"], ["/bin/false"], env,
                                   Path(directory)/"failed.sql.gz", Path(directory)/"failed.log", hold_seconds=5, sequences=["ids"])
            except RuntimeError:
                failed = True
            assert failed
            with connection.cursor() as cursor:
                cursor.execute(f"UPDATE `{pilot}`.logs SET n=n+1 WHERE id=1")
        print(json.dumps({"pilot_myisam_writer_blocked": True, "pilot_innodb_writer_blocked": True, "other_database_writable": True,
                          "sequence_allocation_fenced": True, "sequence_restore_verified": True,
                          "normal_release_verified": True, "watchdog_release_verified": True,
                          "failure_release_verified": True, "real_dump_verified": True}))
    finally:
        with connection.cursor() as cursor:
            for name in (pilot, other, restored):
                assert name.startswith(("migration_lock_", "migration_other_", "migration_restore_")) and suffix in name
                cursor.execute(f"DROP DATABASE IF EXISTS `{name}`")
            cursor.execute("DROP USER IF EXISTS %s@'%%'", (username,))
        connection.close()


if __name__ == "__main__":
    main()
