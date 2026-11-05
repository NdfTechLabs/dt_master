#!/usr/bin/env python3
"""Small additive image work; original snapshots/tag remain untouched."""

import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time


NEW_TAG = "ndf-local/exact-runtime:dalali-v2"
STAGE = Path("/opt/ndf-rootless/yarn-layer-v1")


def verify_native_files(rows, expected):
    if any(row["type"] not in {"file", "directory"} for row in rows.values()):
        raise RuntimeError("Native Yarn contains symlink/special input")
    files = {path: row["sha256"] for path, row in rows.items() if row["type"] == "file"}
    if files != expected:
        raise RuntimeError("Native Yarn differs from exact reviewed file hashes")


def apply(request, helper_source):
    namespace = {"__name__": "ndf_runtime_validation"}
    exec(compile(helper_source, "exact_runtime_remote.py", "exec"), namespace)
    build = namespace["EvaluationBuild"](request)
    work = namespace["WORK"] / "yarn-layer-v1"
    if work.is_symlink() or STAGE.is_symlink():
        raise RuntimeError("Unsafe Yarn stage path")
    work.mkdir(mode=0o700, exist_ok=True)
    state = build.state.setdefault("yarn_fix", {"started_at_utc": datetime.now(timezone.utc).isoformat(),
        "initial_free_bytes": shutil.disk_usage("/").free, "steps": [], "new_tag": NEW_TAG})
    build.save()
    if state.get("validation_passed"):
        build.progress(json.dumps({"yarn_layer_already_validated": True, "image": state["image"]}))
        return

    def guard(reserve=0):
        build.guard(reserve=reserve)
        used = max(0, state["initial_free_bytes"] - shutil.disk_usage("/").free)
        if used + reserve > request["native_yarn"]["added_disk_cap_bytes"]:
            raise RuntimeError("100-MB corrective-layer disk guard; no automatic cleanup")

    try:
        build.health()
        guard(reserve=32_000_000)
        parent = request["retained_image"]["exported_manifest"]
        image = json.loads(build.docker(["image", "inspect", namespace["TAG"]]))[0]
        base = json.loads(build.docker(["image", "inspect", namespace["BASE"]]))[0]
        namespace["verify_retained_export"](image, base, parent, (namespace["WORK"] / "build.log").read_text())
        parent_reference = "ndf-local/exact-runtime@" + parent
        if parent_reference not in image.get("RepoDigests", []):
            raise RuntimeError("Immutable corrective parent not locally available")
        if "context" not in state["steps"]:
            if STAGE.exists() or (work / "native-yarn").exists():
                raise RuntimeError("Partial corrective context requires review; no overwrite")
            source = Path(request["native_yarn"]["source"])
            if source != Path("/usr/lib/node_modules/yarn") or source.is_symlink() or source.stat().st_uid != 0:
                raise RuntimeError("Unexpected native Yarn package source")
            before = namespace["manifest"](source)
            verify_native_files(before, request["native_yarn"]["files"])
            package = json.loads((source / "package.json").read_text())
            if package.get("name") != "yarn" or package.get("version") != "1.22.22":
                raise RuntimeError("Native Yarn version mismatch")
            shutil.copytree(source, work / "native-yarn", symlinks=True)
            if namespace["manifest"](source) != before or namespace["manifest"](work / "native-yarn") != before:
                raise RuntimeError("Native Yarn changed during private copy")
            STAGE.mkdir(mode=0o750)
            os.chown(STAGE, 0, 5001)
            STAGE.chmod(0o750)
            exclusions, links = [], []
            build.copy_filtered(work / "native-yarn", STAGE / "yarn", exclusions, links)
            if exclusions or links:
                raise RuntimeError("Unexpected filtered Yarn inputs")
            for name, encoded in request["recipe"].items():
                path = STAGE / name
                path.write_bytes(base64.b64decode(encoded, validate=True))
                path.chmod(0o755 if name == "ndf-native-exec" else 0o644)
            manifest, findings = namespace["inspected_manifest"](STAGE)
            if findings:
                (work / "credential-scan.json").write_text(json.dumps(findings, indent=2))
                raise RuntimeError("Corrective context has unreviewed credential indicators")
            (work / "context-manifest.json").write_text(json.dumps(manifest, sort_keys=True))
            state["steps"].append("context")
            build.save()
            guard(reserve=32_000_000)
            build.progress("Native Yarn 1.22.22 private copy/context hashes and credential scan passed; no download")
        if "build" not in state["steps"]:
            active_manifest = work / state.get("active_manifest", "context-manifest.json")
            if namespace["manifest"](STAGE) != json.loads(active_manifest.read_text()):
                raise RuntimeError("Corrective context changed")
            local_recipe = STAGE / "Dockerfile.local-parent"
            recipe_bytes = base64.b64decode(request["recipe"]["Dockerfile.local-parent"], validate=True)
            if local_recipe.exists():
                if local_recipe.is_symlink() or local_recipe.read_bytes() != recipe_bytes:
                    raise RuntimeError("Unknown local-parent recipe; no overwrite")
            else:
                # Add a separately named recipe only after verifying the exact
                # retained context; do not replace the failed digest recipe.
                local_recipe.write_bytes(recipe_bytes)
                local_recipe.chmod(0o644)
                corrected_manifest, findings = namespace["inspected_manifest"](STAGE)
                if findings:
                    raise RuntimeError("Corrective context credential indicators changed")
                new_manifest = work / "context-manifest-local-parent.json"
                if new_manifest.exists():
                    raise RuntimeError("Existing alternate context receipt; no overwrite")
                new_manifest.write_text(json.dumps(corrected_manifest, sort_keys=True))
                state["active_manifest"] = new_manifest.name
                build.save()
            existing = build.docker(["image", "ls", "--format", "{{.Repository}}:{{.Tag}}"])
            if NEW_TAG in existing.splitlines():
                raise RuntimeError("Corrective tag already exists; inspect before resuming, no overwrite")
            args = ["buildx", "build", "--builder", "default", "--pull=false", "--network=none",
                    "--provenance=false", "--sbom=false", "--load", "--progress=plain", "--tag", NEW_TAG,
                    "--file", str(local_recipe), str(STAGE)]
            prefix = ["sudo", "-n", "-H", "-u", "dt-containers", "env", "XDG_RUNTIME_DIR=/run/user/5001",
                      "DOCKER_HOST=unix:///run/user/5001/docker.sock",
                      "PATH=/opt/ndf-rootless/29.6.1/bin:/usr/bin:/bin:/usr/sbin:/sbin", str(namespace["BIN"])]
            guard(reserve=32_000_000)
            build.progress("Building small additive Yarn layer only; cached parent and original tag preserved")
            started = time.monotonic()
            with open(work / "build.log", "a") as log:
                process = subprocess.Popen(prefix + args, stdout=log, stderr=log, start_new_session=True)
                try:
                    while True:
                        try:
                            code = process.wait(timeout=2)
                            break
                        except subprocess.TimeoutExpired:
                            guard(reserve=16_000_000)
                            if time.monotonic() - started > 600:
                                raise RuntimeError("Corrective build time limit reached")
                    if code:
                        raise RuntimeError("Corrective build failed; private build log retained")
                except BaseException:
                    if process.poll() is None:
                        os.killpg(process.pid, signal.SIGTERM)
                        try:
                            process.wait(timeout=20)
                        except subprocess.TimeoutExpired:
                            os.killpg(process.pid, signal.SIGKILL)
                            process.wait()
                    raise
            corrected = json.loads(build.docker(["image", "inspect", NEW_TAG]))[0]
            parent_after = json.loads(build.docker(["image", "inspect", namespace["TAG"]]))[0]
            if parent_after["Id"] != parent:
                raise RuntimeError("Verified local parent tag changed during corrective build")
            layers, parent_layers = corrected["RootFS"]["Layers"], image["RootFS"]["Layers"]
            if len(layers) <= len(parent_layers) or layers[:len(parent_layers)] != parent_layers:
                raise RuntimeError("Corrective image parent layer mismatch")
            state["image"] = corrected["Id"]
            state["parent_image"] = image["Id"]
            state["steps"].append("build")
            build.save()
            build.progress("Corrective image created: " + corrected["Id"])
        guard(reserve=8_000_000)
        build.health()
        build.validate(target_image=state["image"], receipt_name="validation-yarn-v2.json")
        guard()
        build.health()
        state["validation_passed"] = True
        state["steps"].append("validation")
        state["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        state["added_disk_bytes"] = max(0, state["initial_free_bytes"] - shutil.disk_usage("/").free)
        if state.get("last_error"):
            state.setdefault("historical_errors", []).append(state.pop("last_error"))
        build.state["validated_image_id"] = state["image"]
        if build.state.get("last_error"):
            build.state.setdefault("historical_errors", []).append(build.state.pop("last_error"))
        build.save()
        receipt = {"image": state["image"], "tag": NEW_TAG, "no_data_validation_passed": True,
                   "added_disk_bytes": state["added_disk_bytes"], "new_downloads": False,
                   "customer_data_loaded": False, "cleanup_performed": False,
                   "original_build_command_succeeded": False}
        (work / "result.json").write_text(json.dumps(receipt, indent=2))
        build.progress(json.dumps(receipt))
    except Exception as error:
        state["last_error"] = str(error)
        build.save()
        raise


if __name__ == "__main__":
    if "REQUEST" not in globals() or "HELPER" not in globals():
        raise SystemExit("Use add_native_yarn.py with explicit approval")
    apply(REQUEST, HELPER)
