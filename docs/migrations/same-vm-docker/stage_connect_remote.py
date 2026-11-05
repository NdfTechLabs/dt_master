#!/usr/bin/env python3
"""Start/audit only the approved connector; stop it on failure, never touch shared."""

from datetime import datetime, timezone
import http.client
import json
from pathlib import Path
import shutil
import stat
import time
import urllib.error
import urllib.parse
import urllib.request


UNIT = "ndf-dalali-stage-tunnel.service"
CODE = Path("/opt/ndf-rootless/dalali-stage-v1")
PRIVATE = Path("/etc/ndf-dalali-stage")


def metrics(path):
    connection = http.client.HTTPConnection("127.0.0.1", 18090, timeout=3)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        body = response.read(65537)
        if len(body) > 65536:raise RuntimeError("Unexpected staging connector metadata size")
        return response.status, body
    finally:
        connection.close()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def connect(request, helper):
    ns = {"__name__": "stage_connect_support"}
    exec(compile(helper, "rehearsal_remote.py", "exec"), ns)
    r = ns["Rehearsal"](request)
    r.health();r.runtime_egress()
    if request.get("route_confirmed") is not True or request["tunnel_id"] != "9388ab42-7fa1-429c-a21b-bca54d301486":
        raise RuntimeError("Exact operator route/tunnel confirmation required")
    root = ns["WORK"] / "stage-v1"
    stage = r.state["staging"]
    if request.get("readiness_rollback") and not stage.get("guest_rollback_verified"):
        raise RuntimeError("Retained readiness version allowed only after verified Guest activation rollback")
    def guard():
        r.guard(reserve=1024**2)
        free = shutil.disk_usage("/").free
        if max(0, stage["initial_free_bytes"] - free) + 1024**2 > 64 * 1024**2:
            raise RuntimeError("Staging 64-MiB subcap reached")
        if max(0, r.state["ui"]["initial_free_bytes"] - free) + 1024**2 > 1024**3:
            raise RuntimeError("Staging within UI 1-GiB subcap required")
    guard()
    if PRIVATE.is_symlink() or PRIVATE.stat().st_uid != 0 or PRIVATE.stat().st_mode & 0o077:
        raise RuntimeError("Unsafe private staging credential directory")
    info = (PRIVATE / "tunnel-token").lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1 or not 1 <= info.st_size <= 16384:
        raise RuntimeError("Unexpected staging credential metadata; content not read")
    receipt = PRIVATE / "tunnel-credential-receipt.json"
    if receipt.is_symlink() or receipt.stat().st_uid != 0 or receipt.stat().st_mode & 0o077:
        raise RuntimeError("Unsafe credential metadata receipt")
    if json.loads(receipt.read_text()) != {"tunnel_id": request["tunnel_id"], "credential_stored": True, "services_started": False}:
        raise RuntimeError("Staging credential receipt differs")
    if request["tunnel_id"] in Path("/etc/cloudflared/config.yml").read_text().lower():
        raise RuntimeError("Refuse protected shared-tunnel credential")
    for name, expected in request["source_hashes"].items():
        path = CODE / name
        if path.is_symlink() or path.stat().st_uid != 0 or path.stat().st_mode & 0o022 or ns["digest"](path) != expected:
            raise RuntimeError("Staging source drift; do not start connector")
    units = {"__name__": "stage_unit_support"}
    exec(compile(request["unit_support"], "stage_prepare_remote.py", "exec"), units)
    for name, expected in units["units"]().items():
        if Path("/etc/systemd/system", name).read_text() != expected:
            raise RuntimeError("Staging unit differs from approved service")
    if (CODE / "cloudflared.yml").read_text() != "protocol: http2\nmetrics: 127.0.0.1:18090\nloglevel: error\n":
        raise RuntimeError("Staging connector local config differs")
    access = json.loads((CODE / "runtime/access.json").read_text())
    if access.get("origin_enabled") is not False or access.get("issuer") != "https://ndftech.cloudflareaccess.com":
        raise RuntimeError("Readiness-only Access config required")
    gateway = r.owned("container", "ndf-dalali-stage-gateway-v1")
    if not gateway["State"].get("Running") or gateway["Image"] != ns["APP_IMAGE"]:
        raise RuntimeError("Owned readiness gateway unavailable")
    status = r.run(["curl", "--silent", "--show-error", "--max-time", "10", "--header", "Host: d-stage.ndftechlabs.com",
        "--output", "/dev/null", "--write-out", "%{http_code}", "http://127.0.0.1:18089/"]).strip()
    if status != "403":raise RuntimeError("Anonymous readiness denial failed")
    snapshot = json.loads(Path("/var/lib/ndf-dalali-stage-keys/jwks.json").read_text())
    if not 0 <= int(time.time()) - snapshot["fetched_at"] <= 3600:raise RuntimeError("Stale Access signing keys")
    shared_pid = r.run(["systemctl", "show", "cloudflared.service", "--property=MainPID", "--value"]).strip()
    state = r.run(["systemctl", "show", UNIT, "--property=ActiveState", "--value"]).strip()
    if state not in ("inactive", "failed"):
        raise RuntimeError("New connector already active/transitioning; inspect instead of replaying startup")
    if r.run(["ss", "-H", "-lnt", "sport = :18090"]).strip():raise RuntimeError("Staging metrics port already occupied")
    cg = Path("/sys/fs/cgroup/user.slice/user-5001.slice")
    if int((cg / "memory.max").read_text()) - int((cg / "memory.current").read_text()) < 160 * 1024**2:
        raise RuntimeError("Insufficient aggregate memory headroom for connector")
    policy = {"__name__": "stage_route_policy"}
    exec(compile(request["route_policy"], "stage_route_policy.py", "exec"), policy)
    marker = PRIVATE / "connector-approved"
    marker_text = json.dumps({"tunnel_id": request["tunnel_id"], "scope": "readiness-only",
        "origin": "http://127.0.0.1:18089", "required_access_team": "ndftech", "audience": access["audience"]}, sort_keys=True)
    if marker.is_symlink():raise RuntimeError("Unsafe staging approval marker")
    if marker.exists():
        if marker.stat().st_uid != 0 or marker.stat().st_mode & 0o077 or marker.read_text() != marker_text:
            raise RuntimeError("Unexpected staging approval marker; no overwrite")
    else:
        with open(marker, "x") as output:output.write(marker_text)
        marker.chmod(0o600)
    attempted = False
    try:
        attempted = True
        r.run(["systemctl", "start", UNIT], timeout=45)
        accepted = None
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            active = r.run(["systemctl", "show", UNIT, "--property=ActiveState", "--value"]).strip()
            if active in ("failed", "inactive"):raise RuntimeError("New staging connector exited; inspect safe service status")
            try:
                code, raw = metrics("/config")
            except (OSError, http.client.HTTPException):
                time.sleep(1);continue
            if code != 200:
                time.sleep(1);continue
            try:document = json.loads(raw)
            except ValueError:raise RuntimeError("Malformed staging connector metadata") from None
            if document.get("version") == -1:
                time.sleep(1);continue
            accepted = policy["validate_config"](document, access["audience"])
            ready, _ = metrics("/ready")
            if ready == 200:break
            time.sleep(1)
        else:raise RuntimeError("Staging connector registration/route validation deadline exceeded")
        properties = dict(line.split("=", 1) for line in r.run(["systemctl", "show", UNIT, "--property=MainPID",
            "--property=ControlGroup", "--property=User", "--property=Slice", "--property=MemoryMax", "--property=TasksMax"]).splitlines() if "=" in line)
        if properties.get("User") != "dt-containers" or properties.get("Slice") != "user-5001.slice" or properties.get("MemoryMax") != "134217728" or properties.get("TasksMax") != "128":
            raise RuntimeError("Staging connector resource/identity drift")
        control_group = properties.get("ControlGroup", "")
        if not control_group.startswith("/user.slice/user-5001.slice/") or not control_group.endswith("/" + UNIT):
            raise RuntimeError("Staging connector outside approved aggregate cgroup")
        process = Path("/proc") / properties["MainPID"]
        status_fields = dict(line.split(":", 1) for line in (process / "status").read_text().splitlines() if ":" in line)
        if int(status_fields["Uid"].split()[0]) != 5001 or int(status_fields["CapEff"].strip(), 16) != 0:
            raise RuntimeError("Staging connector has unexpected outer privilege")
        actual_group = Path("/sys/fs/cgroup" + control_group)
        quota, period = (actual_group / "cpu.max").read_text().split()
        if (actual_group / "memory.max").read_text().strip() != "134217728" or (actual_group / "memory.swap.max").read_text().strip() != "0" or quota == "max" or int(quota) / int(period) > 0.25:
            raise RuntimeError("Staging connector resource enforcement differs")
        listeners = [line.split()[3] for line in r.run(["ss", "-H", "-lnt", "sport = :18090"]).splitlines() if len(line.split()) >= 4]
        if listeners != ["127.0.0.1:18090"]:raise RuntimeError("Staging metrics not exclusively loopback")
        try:response = urllib.request.build_opener(NoRedirect).open(urllib.request.Request("https://d-stage.ndftechlabs.com/", method="HEAD"), timeout=20)
        except urllib.error.HTTPError as error:response = error
        with response:
            anonymous_status = response.status
            destination = urllib.parse.urlsplit(response.headers.get("Location", ""))
        expected_redirect = destination.scheme == "https" and destination.hostname == "ndftech.cloudflareaccess.com"
        if anonymous_status not in (302, 303, 401, 403) or anonymous_status in (302, 303) and not expected_redirect:
            raise RuntimeError("Anonymous public readiness request did not receive expected Access denial")
        r.health();r.runtime_egress();guard()
        if r.run(["systemctl", "show", "cloudflared.service", "--property=MainPID", "--value"]).strip() != shared_pid:
            raise RuntimeError("Protected shared connector process changed")
        r.run(["systemctl", "enable", UNIT])
        stage.update({"connector_started": True, "connector_enabled": True, "public_route_enabled": True,
            "origin_enabled": False, "operator_readiness_login_pending": True, "received_route": accepted})
        r.save()
        result = {"checked_utc": datetime.now(timezone.utc).isoformat(), "managed_connector_started": True,
            "tunnel_id": request["tunnel_id"], "connector_ready": True, "received_route": accepted,
            "anonymous_public_status": anonymous_status, "anonymous_expected_team_redirect": expected_redirect,
            "shared_connector_pid_unchanged": True, "aggregate_cgroup_verified": True,
            "token_content_or_arguments_exported": False, "clone_origin_enabled": False, "operator_readiness_login_pending": True}
        (root / "connector-result.json").write_text(json.dumps(result, indent=2))
        r.progress(json.dumps(result))
    except Exception:
        if attempted:
            r.run(["systemctl", "stop", UNIT], timeout=30)
            stage["connector_started"] = False
            stage["connector_last_attempt_failed"] = True
            r.save()
        raise


if __name__ == "__main__":
    if not all(name in globals() for name in ("REQUEST", "HELPER")):raise SystemExit("Use stage_connect.py after operator route confirmation")
    connect(REQUEST, HELPER)
