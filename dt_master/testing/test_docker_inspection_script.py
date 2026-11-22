"""Acceptance tests for the fixed, read-only Docker inspection script.

These tests execute the real shell script with a controlled PATH and a fake
Docker CLI. They do not require or contact a live Docker daemon.
"""
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "bin" / "docker" / "inspect_docker.sh"

TOOLS = ("bash", "date", "getconf", "awk", "id", "tail", "grep", "dirname")


class DockerInspectionScriptAcceptance(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.bin_dir = Path(self.temp.name)
        for name in TOOLS:
            source = shutil.which(name)
            if source:
                (self.bin_dir / name).symlink_to(source)
        # Use the exact interpreter running the test suite (Bench's venv
        # when invoked through bench), not whichever python3 pyenv exposes.
        python_wrapper = self.bin_dir / "python3"
        python_wrapper.write_text(
            f"#!/bin/sh\nexec {shlex.quote(sys.executable)} \"$@\"\n"
        )
        python_wrapper.chmod(0o755)
        self.log = self.bin_dir / "docker-commands.log"
        self.docker = self.bin_dir / "docker"
        self.docker.write_text(
            """#!/bin/sh
printf '%s\\n' "$*" >> "$DOCKER_TEST_LOG"
case "$1 $2" in
  "version --format")
    printf '%s\\n' "29.9.3"
    ;;
  "compose version")
    if [ "$DOCKER_TEST_COMPOSE" = "missing" ]; then
      echo "compose plugin unavailable" >&2
      exit 1
    fi
    printf '%s\\n' "2.30.0"
    ;;
  "ps --all")
    printf '%s\\n' "container-a" "container-b"
    ;;
  "ps --quiet")
    printf '%s\\n' "container-a"
    ;;
  "info --format")
    if [ "$DOCKER_TEST_MODE" = "denied" ]; then
      echo "permission denied while trying to connect to the Docker daemon socket" >&2
      exit 1
    fi
    if [ "$DOCKER_TEST_MODE" = "unavailable" ]; then
      echo "cannot connect to the Docker daemon" >&2
      exit 1
    fi
    printf '%s\\n' '{"ServerVersion":"29.9.3","ServerAPIVersion":"1.52","MinAPIVersion":"1.44","Driver":"overlay2","DockerRootDir":"/var/lib/docker"}'
    ;;
  *)
    echo "unexpected docker invocation: $*" >&2
    exit 90
    ;;
esac
"""
        )
        self.docker.chmod(0o755)
        fake_df = self.bin_dir / "df"
        fake_df.write_text(
            """#!/bin/sh
printf '%s\\n' 'Filesystem 1B-blocks Used Available Mounted on' '/dev/test 100000000000 50000000000 50000000000 /'
"""
        )
        fake_df.chmod(0o755)

    def tearDown(self):
        self.temp.cleanup()

    def run_inspection(self, *, mode="healthy", compose="present", docker=True):
        env = os.environ.copy()
        env.update(
            {
                "PATH": str(self.bin_dir),
                "DOCKER_TEST_LOG": str(self.log),
                "DOCKER_TEST_MODE": mode,
                "DOCKER_TEST_COMPOSE": compose,
                "DOCKER_CONTEXT": "must-be-ignored",
                "DOCKER_HOST": "tcp://must-be-ignored:2375",
            }
        )
        if not docker:
            self.docker.unlink()
        completed = subprocess.run(
            ["/usr/bin/env", "bash", str(SCRIPT)],
            env=env,
            text=True,
            capture_output=True,
            timeout=15,
            check=False,
        )
        if completed.returncode != 0:
            traced = subprocess.run(
                ["/usr/bin/env", "bash", "-x", str(SCRIPT)],
                env=env,
                text=True,
                capture_output=True,
                timeout=15,
                check=False,
            )
            self.fail(
                "Inspection script exited with "
                f"{completed.returncode}. Shell trace:\\n{traced.stderr}"
            )
        return json.loads(completed.stdout)

    def test_healthy_engine_reports_versions_and_data_root_filesystem(self):
        result = self.run_inspection()
        self.assertEqual(result["inspection_status"], "Successful")
        self.assertEqual(result["engine"]["availability"], "Available")
        self.assertEqual(result["engine"]["server_version"], "29.9.3")
        self.assertEqual(result["engine"]["api_version"], "1.52")
        self.assertEqual(result["storage"]["driver"], "overlay2")
        self.assertEqual(result["storage"]["data_root"], "/var/lib/docker")
        self.assertEqual(result["host_inventory"]["schema_version"], "1.0.0")
        self.assertEqual(result["host_inventory"]["workloads"]["running_containers"], 1)
        self.assertEqual(result["host_inventory"]["workloads"]["total_containers"], 2)
        self.assertIsNotNone(result["storage"]["filesystem"]["mount_point"])

    def test_missing_docker_cli_is_reported_without_failing_inspection(self):
        result = self.run_inspection(docker=False)
        self.assertFalse(result["docker_cli"]["installed"])
        self.assertEqual(result["engine"]["availability"], "Not Installed")
        self.assertIn("DOCKER_CLI_MISSING", {item["code"] for item in result["diagnostics"]})
        self.assertEqual(result["storage"]["filesystem"]["mount_point"], "/")
        self.assertEqual(result["storage"]["filesystem"]["available_bytes"], 50_000_000_000)

    def test_unavailable_daemon_is_distinguished_from_missing_cli(self):
        result = self.run_inspection(mode="unavailable")
        self.assertTrue(result["docker_cli"]["installed"])
        self.assertEqual(result["engine"]["availability"], "Unavailable")
        self.assertIn("DOCKER_ENGINE_UNAVAILABLE", {item["code"] for item in result["diagnostics"]})

    def test_permission_denied_is_reported_without_privilege_escalation(self):
        result = self.run_inspection(mode="denied")
        self.assertEqual(result["permissions"]["inspection_access"], "Denied")
        self.assertIn("DOCKER_ACCESS_DENIED", {item["code"] for item in result["diagnostics"]})

    def test_compose_availability_is_probed_when_daemon_is_unavailable(self):
        result = self.run_inspection(mode="unavailable", compose="missing")
        self.assertEqual(result["engine"]["availability"], "Unavailable")
        self.assertFalse(result["compose"]["installed"])
        self.assertEqual(result["compose"]["compatibility"], "Unavailable")

    def test_only_read_only_docker_queries_are_invoked(self):
        self.run_inspection()
        commands = self.log.read_text().splitlines()
        self.assertEqual(
            commands,
            [
                "version --format {{.Client.Version}}",
                "compose version --short",
                "info --format {{json .}}",
                "ps --all --quiet",
                "ps --quiet",
            ],
        )
        forbidden = {"run", "create", "start", "stop", "rm", "rmi", "pull", "build", "volume", "network"}
        self.assertFalse(any(line.split()[0] in forbidden for line in commands))

    def test_inspection_ignores_caller_selected_docker_context_and_host(self):
        self.run_inspection()
        commands = self.log.read_text().splitlines()
        self.assertTrue(commands)
        # The script pins the system socket before making any Docker request.
        source = SCRIPT.read_text()
        self.assertIn('unset DOCKER_CONTEXT DOCKER_TLS_VERIFY DOCKER_CERT_PATH', source)
        self.assertIn('export DOCKER_HOST=unix:///var/run/docker.sock', source)


if __name__ == "__main__":
    unittest.main()
