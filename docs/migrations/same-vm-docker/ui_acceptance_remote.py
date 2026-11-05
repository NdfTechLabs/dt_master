#!/usr/bin/env python3
"""New UI tool/probe only; uses existing rehearsal health/budget/identity guards."""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import platform
import shutil
import zipfile


def apply(request, helper_source, policy_source):
    namespace = {"__name__": "ndf_ui_rehearsal_support"}
    exec(compile(helper_source, "rehearsal_remote.py", "exec"), namespace)
    policy = {"__name__": "ndf_ui_policy_support"}
    exec(compile(policy_source, "ui_seccomp.py", "exec"), policy)
    rehearsal = namespace["Rehearsal"](request)
    root, deploy = namespace["WORK"], namespace["DEPLOY"]
    work = root / "ui-v1"
    if work.is_symlink():raise RuntimeError("Unsafe UI state path")
    work.mkdir(mode=0o700, exist_ok=True)
    state = rehearsal.state.setdefault("ui", {"initial_free_bytes": shutil.disk_usage("/").free,
        "steps": [], "download_bytes": 0, "approval": request["approval"]})
    rehearsal.save()
    def guard(reserve=0):
        rehearsal.guard(reserve=reserve)
        used = max(0, state["initial_free_bytes"] - shutil.disk_usage("/").free)
        if used + reserve > 1024**3:raise RuntimeError("UI 1-GiB tool subcap reached; no cleanup/increase")
    try:
        rehearsal.health()
        rehearsal.runtime_egress()
        guard(reserve=8 * 1024**2)
        if request["ui_phase"] == "csp":
            csp = {"__name__": "ndf_ui_csp_support"}
            exec(compile(request["csp_support"], "ui_csp.py", "exec"), csp)
            csp["apply"](rehearsal, namespace, state, work, deploy)
            guard()
            return
        if request["ui_phase"] == "tools":
            tools = {"__name__": "ndf_ui_tool_support"}
            exec(compile(request["tool_support"], "ui_tools.py", "exec"), tools)
            tools["prepare"](rehearsal, namespace, state, guard, request)
            rehearsal.health()
            return
        if request["ui_phase"] == "browser":
            if "tools" not in state["steps"] or not state.get("sandbox_prerequisites_passed"):
                raise RuntimeError("Verified UI tools/policy required before browser startup")
            profile = deploy / "ui-browser-seccomp.json"
            if namespace["digest"](profile) != state["profile_sha256"]:
                raise RuntimeError("Browser-only policy changed")
            prefix = deploy / "ui-tools"
            binary = prefix / state["browser_relative_path"]
            archive = work / "chrome-headless-shell.zip"
            if namespace["digest"](archive) != state["artifacts"][archive.name]["sha256"]:
                raise RuntimeError("Browser archive changed")
            expected = hashlib.sha256()
            with zipfile.ZipFile(archive) as source, source.open(str(Path(state["browser_relative_path"]).relative_to("browser"))) as incoming:
                for block in iter(lambda: incoming.read(1024 * 1024), b""):expected.update(block)
            if namespace["digest"](binary) != expected.hexdigest():
                raise RuntimeError("Extracted browser executable differs from verified archive")
            output = rehearsal.docker(["run", "--rm", "--pull=never", "--label", namespace["LABEL"], "--label=ndf.role=ui-browser-sandbox",
                "--network=none", "--read-only", "--user=1000:1000", "--cap-drop=ALL", "--security-opt=no-new-privileges",
                "--security-opt=seccomp=" + str(profile), "--memory=1g", "--cpus=0.5", "--pids-limit=256", "--shm-size=128m",
                "--tmpfs=/tmp:rw,nosuid,nodev,size=268435456,mode=1777", "--env=HOME=/tmp/ui-home",
                "--env=XDG_CONFIG_HOME=/tmp/ui-config", "--env=XDG_CACHE_HOME=/tmp/ui-cache", "--mount",
                "type=bind,src=" + str(prefix) + ",dst=/opt/ndf-ui,readonly", "--entrypoint=/opt/ndf-ui/chrome", namespace["APP_IMAGE"],
                "--headless", "--disable-gpu", "--no-first-run", "--no-default-browser-check", "--disable-background-networking",
                "--disable-component-update", "--disable-sync", "--disable-extensions", "--disable-default-apps", "--disable-breakpad",
                "--disable-crash-reporter", "--metrics-recording-only", "--mute-audio", "--disable-quic", "--dns-prefetch-disable",
                "--host-resolver-rules=MAP * ~NOTFOUND", "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                "--user-data-dir=/tmp/ui-profile", "--dump-dom", "data:text/html,<title>ndf-sandbox</title><p>ndf-synthetic-sandbox-ok</p>"], timeout=120)
            if "ndf-synthetic-sandbox-ok" not in output:
                raise RuntimeError("Normal Chrome sandbox synthetic render failed")
            state["normal_browser_sandbox_smoke_passed"] = True
            if "browser" not in state["steps"]:state["steps"].append("browser")
            state["browser_executable_sha256"] = expected.hexdigest()
            rehearsal.save();rehearsal.health();guard()
            rehearsal.progress(json.dumps({"normal_chrome_sandbox_synthetic_render_passed":True,
                "sandbox_disabled":False,"customer_pages_loaded":False,"source_change":False}))
            return
        if request["ui_phase"] in {"guard", "tests"}:
            if not state.get("normal_browser_sandbox_smoke_passed"):
                raise RuntimeError("Normal browser sandbox smoke required before guarded UI launch")
            profile = deploy / "ui-browser-seccomp.json"
            if namespace["digest"](profile) != state["profile_sha256"]:
                raise RuntimeError("Approved UI policy changed")
            mode = request["ui_phase"]
            if mode == "tests" and not state.get("request_guard_passed"):
                raise RuntimeError("No-data browser request/action guard must pass before credentials")
            proxy_hash = hashlib.sha256(request["proxy_source"].encode()).hexdigest()[:12]
            runner_hash = hashlib.sha256(request["runner_source"].encode()).hexdigest()[:12]
            if mode == "tests" and (state.get("guard_proxy_hash") != proxy_hash or state.get("guard_runner_hash") != runner_hash):
                raise RuntimeError("UI guard code changed since no-data proof; repeat guard before credentials")
            if mode == "tests":
                state["last_test_attempt"] = {"started_utc": datetime.now(timezone.utc).isoformat(),
                    "status": "running", "runner_hash": runner_hash, "proxy_hash": proxy_hash}
                rehearsal.save()
            proxy = rehearsal.write_private("ui-proxy-" + proxy_hash + ".cjs", request["proxy_source"], mode=0o444)
            runner = rehearsal.write_private("ui-runner-" + runner_hash + ".cjs", request["runner_source"], mode=0o444)
            flags = ["run", "--rm", "--pull=never", "--label", namespace["LABEL"], "--label=ndf.role=ui-" + mode,
                "--network", namespace["NETWORK"], "--dns=127.0.0.1", "--dns-option=timeout:1", "--dns-option=attempts:1",
                "--read-only", "--user=1000:1000", "--cap-drop=ALL", "--security-opt=no-new-privileges",
                "--security-opt=seccomp=" + str(profile), "--memory=1g", "--cpus=1", "--pids-limit=256", "--shm-size=128m",
                "--tmpfs=/tmp:rw,nosuid,nodev,size=268435456,mode=1777", "--env=HOME=/tmp/ui-home",
                "--env=XDG_CONFIG_HOME=/tmp/ui-config", "--env=XDG_CACHE_HOME=/tmp/ui-cache", "--mount",
                "type=bind,src=" + str(deploy / "ui-tools") + ",dst=/opt/ndf-ui,readonly", "--mount",
                "type=bind,src=" + str(proxy) + ",dst=/opt/ndf-ui-proxy.cjs,readonly", "--mount",
                "type=bind,src=" + str(runner) + ",dst=/opt/ndf-ui-runner.cjs,readonly"]
            if mode == "tests":
                credentials = json.loads((root / "clone-credentials.json").read_text())
                plan = rehearsal.state["application_plan"]
                access = {"users": ["Administrator"], "user": "Administrator", "password": credentials["operator_password"],
                          "fixture_name": plan["fixture_name"], "fixture_title": plan["fixture_title"]}
                private = rehearsal.write_private("ui-access.json", json.dumps(access), mode=0o444)
                flags += ["--mount", "type=bind,src=" + str(private) + ",dst=/run/ui-input.json,readonly"]
            output = rehearsal.docker(flags + [namespace["APP_IMAGE"], "node", "/opt/ndf-ui-runner.cjs", mode], timeout=900)
            result = json.loads(output)
            if mode == "guard":
                if not result.get("ui_request_action_guard_passed") or result.get("customer_pages_loaded"):
                    raise RuntimeError("Synthetic UI request/action guard proof incomplete")
                state["request_guard_passed"] = True
                state["guard_proxy_hash"] = proxy_hash
                state["guard_runner_hash"] = runner_hash
                if "guard" not in state["steps"]:state["steps"].append("guard")
            else:
                if state.get("guard_proxy_hash") != proxy_hash or state.get("guard_runner_hash") != runner_hash:
                    raise RuntimeError("UI guard code changed since no-data proof; repeat guard first")
                state["automated_ui_result"] = result
                state["last_test_attempt"]["status"] = "passed" if result.get("automated_ui_checks_passed") else "blocked"
                state["last_test_attempt"]["finished_utc"] = datetime.now(timezone.utc).isoformat()
                state["business_signoff_pending"] = True
                if not result.get("automated_ui_checks_passed"):
                    (work / "automated-ui-blocked.json").write_text(json.dumps(result, indent=2))
                    rehearsal.save()
                    raise RuntimeError("Automated UI has blocked actions/errors; do not declare acceptance")
                if "tests" not in state["steps"]:state["steps"].append("tests")
            (work / (mode + "-result.json")).write_text(json.dumps(result, indent=2))
            rehearsal.save();rehearsal.health();guard()
            rehearsal.progress(json.dumps(result))
            return
        if request["ui_phase"] != "sandbox":
            raise RuntimeError("UI phase implementation not yet reviewed")
        baseline = root / "ui-seccomp-default-v0.2.3.json"
        raw = baseline.read_bytes()
        profile, additions = policy["browser_profile"](raw, platform.machine())
        path = rehearsal.write_private("ui-browser-seccomp.json", json.dumps(profile, indent=2), mode=0o444)
        (work / "seccomp-added-rules.json").write_text(json.dumps(additions, indent=2))
        (work / "seccomp-baseline-sha256.txt").write_text(hashlib.sha256(raw).hexdigest() + "\n")
        checks = {}
        for name, flags, expected in (("inner_user", 0x10000000, 0), ("inner_user_pid_net", 0x70000000, 0),
                                      ("forbidden_mount_namespace", 0x10020000, -1),
                                      ("forbidden_uts_namespace", 0x14000000, -1),
                                      ("forbidden_ipc_namespace", 0x18000000, -1)):
            code = "FLAGS = " + repr(flags) + "\n" + r'''import ctypes,json,pathlib,os
outer=[line.split()[1] for line in pathlib.Path('/proc/self/status').read_text().splitlines() if line.startswith('CapEff:')][0]
if int(outer,16)!=0:raise RuntimeError('Outer capabilities must be zero')
libc=ctypes.CDLL(None,use_errno=True);result=libc.unshare(FLAGS);error=ctypes.get_errno()
print(json.dumps({'result':result,'errno':error,'outer_capabilities_zero':True}))'''
            output = rehearsal.docker(["run", "--rm", "--pull=never", "--label", namespace["LABEL"], "--label=ndf.role=ui-sandbox-probe",
                "--network=none", "--read-only", "--user=1000:1000", "--cap-drop=ALL", "--security-opt=no-new-privileges",
                "--security-opt=seccomp=" + str(path), "--memory=64m", "--cpus=0.25", "--pids-limit=32",
                namespace["APP_IMAGE"], "/home/frappe/bench/env/bin/python", "-c", code], timeout=60)
            result = json.loads(output)
            checks[name] = result
            if result["result"] != expected or (expected == -1 and result["errno"] != 1):
                (work / "sandbox-probes-partial.json").write_text(json.dumps(checks, indent=2))
                raise RuntimeError("Browser-only sandbox prerequisite/negative probe failed; stop before download")
        state["sandbox_prerequisites_passed"] = True
        state["sandbox_probes"] = checks
        state["profile_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        if "sandbox" not in state["steps"]:state["steps"].append("sandbox")
        (work / "sandbox-probes.json").write_text(json.dumps(checks, indent=2))
        rehearsal.save()
        rehearsal.health()
        guard()
        rehearsal.progress(json.dumps({"ui_sandbox_prerequisites_passed": True, "outer_capabilities_zero": True,
            "forbidden_namespace_probes_denied": True, "browser_binary_downloaded": False,
            "host_or_existing_container_policy_changed": False, "customer_pages_loaded": False}))
    except Exception as error:
        attempt = state.get("last_test_attempt", {})
        if request["ui_phase"] == "tests" and attempt.get("status") == "running":
            attempt["status"] = "failed_before_result"
            attempt["finished_utc"] = datetime.now(timezone.utc).isoformat()
        state["last_error"] = str(error)
        rehearsal.save()
        raise


if __name__ == "__main__":
    if not all(name in globals() for name in ("REQUEST", "HELPER", "POLICY")):
        raise SystemExit("Use ui_acceptance.py with explicit approval")
    apply(REQUEST, HELPER, POLICY)
