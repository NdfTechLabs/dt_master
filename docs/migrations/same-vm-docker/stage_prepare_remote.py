#!/usr/bin/env python3
"""Approved readiness-only staging services; no tunnel credential or clone data."""

import hashlib
import ipaddress
import json
import os
from pathlib import Path
import shutil


CODE = Path("/opt/ndf-rootless/dalali-stage-v1")
KEYS = Path("/var/lib/ndf-dalali-stage-keys")
CREDENTIALS = Path("/etc/ndf-dalali-stage")
GATEWAY = "ndf-dalali-stage-gateway-v1"


def units():
    common = '''User=dt-containers
Group=dt-containers
Slice=user-5001.slice
NoNewPrivileges=yes
CapabilityBoundingSet=
AmbientCapabilities=
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictSUIDSGID=yes
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
InaccessiblePaths=-/run/user/5001/docker.sock -/run/user/5001/bus
MemorySwapMax=0
StandardOutput=null
StandardError=null
'''
    return {
        "ndf-dalali-stage-keys.service": '''[Unit]
Description=Bounded public keys for guarded Dalali staging
After=network-online.target
[Service]
Type=oneshot
''' + common + '''MemoryMax=32M
CPUQuota=10%
TasksMax=16
TimeoutStartSec=30
ReadWritePaths=/var/lib/ndf-dalali-stage-keys
ExecStart=/usr/bin/python3 /opt/ndf-rootless/dalali-stage-v1/stage_jwks.py
''',
        "ndf-dalali-stage-keys.timer": '''[Unit]
Description=Refresh Dalali staging public signing keys
[Timer]
OnBootSec=30s
OnUnitActiveSec=15min
AccuracySec=30s
Unit=ndf-dalali-stage-keys.service
[Install]
WantedBy=timers.target
''',
        "ndf-dalali-stage-tunnel.service": '''[Unit]
Description=Separate guarded Dalali staging connector
After=network-online.target
Wants=network-online.target
ConditionPathExists=/etc/ndf-dalali-stage/tunnel-token
ConditionPathExists=/etc/ndf-dalali-stage/connector-approved
StartLimitIntervalSec=120
StartLimitBurst=3
[Service]
Type=simple
''' + common + '''MemoryMax=128M
CPUQuota=25%
TasksMax=128
LoadCredential=tunnel-token:/etc/ndf-dalali-stage/tunnel-token
ExecStart=/usr/local/bin/cloudflared --no-autoupdate --config /opt/ndf-rootless/dalali-stage-v1/cloudflared.yml tunnel run --token-file %d/tunnel-token
Restart=on-failure
RestartSec=10
[Install]
WantedBy=multi-user.target
''',
    }


def prepare(request, helper):
    support = {"__name__": "stage_rehearsal_support"}
    exec(compile(helper, "rehearsal_remote.py", "exec"), support)
    r = support["Rehearsal"](request)
    r.health();r.runtime_egress()
    root = support["WORK"] / "stage-v1"
    if root.is_symlink() or not root.is_dir():raise RuntimeError("Private staging request required")
    access = json.loads((root / "access-config.json").read_text())
    if access.get("origin_enabled") is not False or access.get("hostname") != "d-stage.ndftechlabs.com":
        raise RuntimeError("Readiness-only stage request required")
    stage = r.state.setdefault("staging", {"initial_free_bytes": shutil.disk_usage("/").free,
        "approval": request["approval"], "origin_enabled": False})
    r.save()
    def guard(reserve=0):
        r.guard(reserve=reserve)
        free = shutil.disk_usage("/").free
        if max(0, stage["initial_free_bytes"] - free) + reserve > 64 * 1024**2:
            raise RuntimeError("Staging 64-MiB disk cap reached")
        if max(0, r.state["ui"]["initial_free_bytes"] - free) + reserve > 1024**3:
            raise RuntimeError("Staging within UI 1-GiB subcap required")
    def directory(path, uid=0, gid=0, mode=0o755):
        if path.is_symlink():raise RuntimeError("Unsafe stage directory")
        if path.exists():
            info = path.stat()
            if not path.is_dir() or (info.st_uid, info.st_gid, info.st_mode & 0o777) != (uid, gid, mode):
                raise RuntimeError("Unexpected existing stage directory; no overwrite")
        else:path.mkdir(mode=mode);os.chown(path, uid, gid);path.chmod(mode)
    def write(path, text, mode=0o444):
        if path.is_symlink():raise RuntimeError("Unsafe stage file")
        if path.exists():
            if path.read_text() != text or path.stat().st_uid != 0 or path.stat().st_mode & 0o022:
                raise RuntimeError("Existing staging file differs; inspect before changing")
        else:
            with open(path, "x") as output:output.write(text)
            path.chmod(mode)
        return path
    guard(reserve=4 * 1024**2)
    directory(CODE);directory(CODE / "runtime", gid=5001, mode=0o750)
    directory(KEYS, uid=5001, gid=5001);directory(CREDENTIALS, mode=0o700)
    for name, value in request["files"].items():
        if Path(name).name != name:raise RuntimeError("Unsafe stage source name")
        write(CODE / name, value)
    write(CODE / "runtime/access.json", json.dumps(access, sort_keys=True))
    budget = {"stage_initial_free_bytes": stage["initial_free_bytes"],
        "ui_initial_free_bytes": r.state["ui"]["initial_free_bytes"],
        "rehearsal_initial_free_bytes": r.state["initial_free_bytes"]}
    write(CODE / "budget.json", json.dumps(budget, sort_keys=True))
    write(CODE / "cloudflared.yml", "protocol: http2\nmetrics: 127.0.0.1:18090\nloglevel: error\n")
    proof_hash = hashlib.sha256(json.dumps(request["files"], sort_keys=True).encode()).hexdigest()
    # New no-data, no-network container: no clone mounts, credentials or data.
    proof_mounts = []
    for name in request["files"]:
        proof_mounts += ["--mount", "type=bind,src=" + str(CODE / name) + ",dst=/opt/ndf-stage/" + name + ",readonly"]
    output = r.docker(["run", "--rm", "--pull=never", "--label", support["LABEL"], "--label=ndf.role=stage-synthetic-proof",
        "--network=none", "--read-only", "--user=1000:1000", "--cap-drop=ALL", "--security-opt=no-new-privileges",
        "--memory=96m", "--memory-swap=96m", "--cpus=0.25", "--pids-limit=64", "--tmpfs=/tmp:rw,nosuid,nodev,size=16777216,mode=1777",
        "--env=NDF_STAGE_SOURCE_DIR=/opt/ndf-stage", *proof_mounts,
        support["APP_IMAGE"], "node", "--test", "--test-concurrency=1", "/opt/ndf-stage/stage_access.test.cjs", "/opt/ndf-stage/stage_gateway.test.cjs"], timeout=180)
    if "# fail 0" not in output and "fail 0" not in output:raise RuntimeError("Synthetic stage proof incomplete")
    stage["readiness_proof_sha256"] = proof_hash;r.save()
    unit_files = units()
    for name, text in unit_files.items():write(Path("/etc/systemd/system") / name, text, mode=0o644)
    r.run(["systemd-analyze", "verify", *[str(Path("/etc/systemd/system") / name) for name in unit_files]], timeout=60)
    r.run(["systemctl", "daemon-reload"])
    for name in ("ndf-dalali-stage-keys.service", "ndf-dalali-stage-tunnel.service"):
        properties = r.run(["systemctl", "show", name, "--property=User", "--property=Slice", "--property=MemoryMax", "--property=TasksMax"])
        if "User=dt-containers" not in properties or "Slice=user-5001.slice" not in properties:
            raise RuntimeError("Stage service identity/slice differs")
    if r.run(["systemctl", "show", "ndf-dalali-stage-tunnel.service", "--property=ActiveState", "--value"]).strip() == "active":
        raise RuntimeError("Tunnel unexpectedly active before approval/credential checks")
    r.run(["systemctl", "start", "ndf-dalali-stage-keys.service"], timeout=60)
    snapshot = json.loads((KEYS / "jwks.json").read_text())
    if not snapshot.get("keys"):raise RuntimeError("Fresh signing-key snapshot unavailable")
    existing = r.inspect_optional("container", GATEWAY)
    if existing is None:
        if GATEWAY in r.state["resources"]["containers"]:raise RuntimeError("Recorded stage gateway vanished; no recreation")
        if r.run(["ss", "-H", "-lnt", "sport = :18089"]).strip():raise RuntimeError("Stage loopback port occupied")
        r.owned("network", support["NETWORK"])
        r.docker(["run", "--detach", "--name", GATEWAY, "--label", support["LABEL"], "--label=ndf.role=stage-readiness",
            "--pull=never", "--network", support["NETWORK"], "--dns=127.0.0.1", "--read-only", "--user=1000:1000",
            "--cap-drop=ALL", "--security-opt=no-new-privileges", "--memory=96m", "--memory-swap=96m", "--cpus=0.25", "--pids-limit=64",
            "--log-driver=json-file", "--log-opt=max-size=1m", "--log-opt=max-file=2",
            "--tmpfs=/tmp:rw,nosuid,nodev,size=16777216,mode=1777", "--mount", "type=bind,src=" + str(CODE) + ",dst=/opt/ndf-stage,readonly",
            "--mount", "type=bind,src=" + str(CODE / "runtime/access.json") + ",dst=/run/ndf-stage-access.json,readonly",
            "--mount", "type=bind,src=" + str(KEYS) + ",dst=/run/ndf-stage-keys,readonly",
            support["APP_IMAGE"], "node", "/opt/ndf-stage/stage_gateway.cjs", "/run/ndf-stage-access.json", "/run/ndf-stage-keys/jwks.json"])
        r.record_resource("container", GATEWAY)
    container = r.owned("container", GATEWAY)
    if not container["State"].get("Running") or container["Image"] != support["APP_IMAGE"]:
        raise RuntimeError("Stage readiness container failed or changed")
    network = r.owned("network", support["NETWORK"])
    if not network.get("Internal") or set(container["NetworkSettings"]["Networks"]) != {support["NETWORK"]}:
        raise RuntimeError("Stage gateway network is not isolated")
    child = container["NetworkSettings"]["Networks"][support["NETWORK"]]["IPAddress"]
    if not any(ipaddress.ip_address(child) in ipaddress.ip_network(row["Subnet"]) for row in network["IPAM"]["Config"] if row.get("Subnet")):
        raise RuntimeError("Stage gateway IP outside owned subnet")
    spec = {"proto": "tcp", "parentIP": "127.0.0.1", "parentPort": 18089, "childIP": child, "childPort": 18089}
    ports = r.rootless_port_api("GET", "/v1/ports") or []
    recorded = stage.get("loopback_mapping")
    if recorded:
        if len([p for p in ports if p["id"] == recorded["id"] and p.get("spec") == spec]) != 1:
            raise RuntimeError("Stage mapping changed; no replacement")
    else:
        if any(p.get("spec", {}).get("parentPort") == 18089 for p in ports):raise RuntimeError("Unknown stage loopback mapping")
        created = r.rootless_port_api("POST", "/v1/ports", spec)
        if not created or created.get("spec") != spec:raise RuntimeError("Stage mapping result differs")
        stage["loopback_mapping"] = {"id": created["id"], "spec": spec, "gateway_id": container["Id"]};r.save()
    listeners = [line.split()[3] for line in r.run(["ss", "-H", "-lnt", "sport = :18089"]).splitlines() if len(line.split()) >= 4]
    if listeners != ["127.0.0.1:18089"]:raise RuntimeError("Stage gateway listener not exclusively loopback")
    status = r.run(["curl", "--silent", "--show-error", "--retry", "5", "--retry-connrefused", "--retry-delay", "1", "--max-time", "10",
        "--header", "Host: d-stage.ndftechlabs.com", "--output", "/dev/null", "--write-out", "%{http_code}", "http://127.0.0.1:18089/"])
    if status.strip() != "403":raise RuntimeError("Anonymous stage denial failed")
    r.run(["systemctl", "enable", "--now", "ndf-dalali-stage-keys.timer"])
    stage.update({"readiness_gateway_running": True, "keys_timer_enabled": True,
        "connector_started": False, "origin_enabled": False, "public_route_enabled": False})
    r.save();r.health();r.runtime_egress();guard()
    receipt = {"stage_readiness_prepared": True, "vm_synthetic_auth_http_proof_passed": True,
        "anonymous_loopback_status": 403, "loopback_port": 18089, "keys_timer_enabled": True,
        "connector_started": False, "public_route_enabled": False, "clone_origin_enabled": False,
        "native_maintenance_unchanged": True, "code_sha256": proof_hash}
    (root / "readiness-result.json").write_text(json.dumps(receipt, indent=2))
    r.progress(json.dumps(receipt))


if __name__ == "__main__":
    if not all(name in globals() for name in ("REQUEST", "HELPER")):raise SystemExit("Use stage_review.py --execute after exact approval")
    prepare(REQUEST, HELPER)
