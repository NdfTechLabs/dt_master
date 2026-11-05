#!/usr/bin/env python3
"""Private UI tools only; no host install, app image change or customer inputs."""

import base64
import hashlib
import json
from pathlib import Path
import re
import shutil
import stat
import struct
from urllib.request import Request, urlopen
import zipfile


def crc32c_table():
    table = []
    for value in range(256):
        for _ in range(8):
            value = (value >> 1) ^ (0x82F63B78 if value & 1 else 0)
        table.append(value)
    return table


CRC32C_TABLE = crc32c_table()


def crc32c_update(value, data):
    for byte in data:
        value = (value >> 8) ^ CRC32C_TABLE[(value ^ byte) & 255]
    return value


def safe_zip_member(name, mode):
    path = Path(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name or stat.S_ISLNK(mode):
        raise RuntimeError("Unsafe browser ZIP member")
    return path


def prepare(rehearsal, namespace, state, guard, request):
    root = namespace["WORK"] / "ui-v1"
    prefix = namespace["DEPLOY"] / "ui-tools"
    metadata = request["browser"]
    browser = metadata["browser"]
    if not state.get("sandbox_prerequisites_passed"):
        raise RuntimeError("Sandbox prerequisite proof required before tool download")
    policy = namespace["DEPLOY"] / "ui-browser-seccomp.json"
    if namespace["digest"](policy) != state["profile_sha256"]:
        raise RuntimeError("Reviewed browser policy changed")
    def fetch(url, target, size, checksum=None, generation=None, expected_crc=None):
        known = state.setdefault("artifacts", {}).get(target.name)
        if target.exists():
            if not known or namespace["digest"](target) != known["sha256"] or target.stat().st_size != size:
                raise RuntimeError("Existing UI artifact not verified; no overwrite")
            return
        partial = target.with_suffix(target.suffix + ".download")
        if partial.exists():raise RuntimeError("Partial UI download requires review")
        if state["download_bytes"] + size > 150_000_000:raise RuntimeError("UI transfer cap would be exceeded")
        guard(reserve=size)
        sha, crc = hashlib.sha256(), 0xFFFFFFFF
        with urlopen(Request(url, headers={"User-Agent": "ndf-private-ui-acceptance"}), timeout=30) as response, open(partial, "xb") as output:
            if generation and response.headers.get("x-goog-generation") != generation:
                raise RuntimeError("Browser object generation changed")
            declared = response.headers.get("Content-Length")
            if declared is not None and int(declared) != size:raise RuntimeError("UI artifact response size changed")
            while output.tell() < size:
                block = response.read(min(1024 * 1024, size - output.tell()))
                if not block:break
                state["download_bytes"] += len(block)
                rehearsal.save()
                guard(reserve=len(block))
                output.write(block);sha.update(block)
                if expected_crc:crc = crc32c_update(crc, block)
        if partial.stat().st_size != size:raise RuntimeError("UI artifact incomplete")
        if checksum and sha.hexdigest() != checksum:raise RuntimeError("UI package SHA-256 mismatch")
        crc_b64 = base64.b64encode(struct.pack(">I", crc ^ 0xFFFFFFFF)).decode() if expected_crc else None
        if expected_crc and crc_b64 != expected_crc:raise RuntimeError("Browser CRC32C mismatch")
        partial.rename(target)
        state["artifacts"][target.name] = {"bytes": size, "sha256": sha.hexdigest(), "crc32c": crc_b64}
        rehearsal.save()
    archive = root / "chrome-headless-shell.zip"
    rehearsal.progress("Downloading pinned browser/support tools directly on VM; no customer pages or credentials")
    fetch(browser["url"] + "?generation=" + browser["object_generation"], archive, browser["bytes"],
          generation=browser["object_generation"], expected_crc=browser["object_crc32c_base64"])
    for package in metadata["support_packages"]:
        fetch(package["repository"] + package["filename"], root / (package["name"] + ".deb"), package["bytes"], package["sha256"])
    if not state.get("tool_copy_complete"):
        if prefix.exists() or prefix.is_symlink():raise RuntimeError("Partial UI prefix requires review; no overwrite")
        prefix.mkdir(mode=0o755);prefix.chmod(0o755)
        for name in ("browser", "lib", "alsa"):
            path = prefix / name;path.mkdir(mode=0o755);path.chmod(0o755)
        with zipfile.ZipFile(archive) as source:
            if source.testzip() is not None:raise RuntimeError("Browser ZIP CRC verification failed")
            for member in source.infolist():
                mode = member.external_attr >> 16
                relative = safe_zip_member(member.filename, mode)
                target = prefix / "browser" / relative
                guard(reserve=member.file_size)
                if member.is_dir():target.mkdir(parents=True,exist_ok=True);target.chmod(0o755)
                else:
                    target.parent.mkdir(parents=True,exist_ok=True)
                    with source.open(member) as incoming, open(target,"xb") as output:shutil.copyfileobj(incoming,output,1024*1024)
                    target.chmod(0o755 if mode & 0o111 else 0o644)
        candidates = [path for path in (prefix / "browser/chrome-headless-shell-linux64/chrome-headless-shell",
                                       prefix / "browser/chrome-headless-shell-linux64/headless_shell") if path.is_file()]
        if len(candidates) != 1:raise RuntimeError("Ambiguous browser executable")
        binary = candidates[0];binary.chmod(0o755)
        for package in metadata["support_packages"]:
            extracted = root / (package["name"] + "-extracted")
            if not extracted.exists():rehearsal.run(["dpkg-deb", "--extract", root / (package["name"] + ".deb"), extracted])
        audio = root / "libasound2t64-extracted/usr/lib/x86_64-linux-gnu/libasound.so.2.0.0"
        if not audio.is_file():raise RuntimeError("Expected private audio library missing")
        alsa = root / "libasound2-data-extracted/usr/share/alsa"
        if not alsa.is_dir():raise RuntimeError("Expected private audio config missing")
        # Destination was created above only to keep root-owned parent policy;
        # copy individual public support entries without replacing any files.
        for child in alsa.iterdir():
            destination = prefix / "alsa" / child.name
            if child.is_symlink():raise RuntimeError("Unreviewed support config symlink")
            if child.is_dir():shutil.copytree(child,destination,symlinks=True)
            else:shutil.copyfile(child,destination)
        ldconfig = rehearsal.run(["ldconfig", "-p"])
        available = {}
        for line in ldconfig.splitlines():
            match = re.match(r"\s*(\S+) \([^)]*x86-64[^)]*\) => (\S+)", line)
            if match:available.setdefault(match.group(1),Path(match.group(2)).resolve())
        available["libasound.so.2"] = audio
        excluded = {"libc.so.6", "libm.so.6", "libpthread.so.0", "libdl.so.2", "librt.so.1", "ld-linux-x86-64.so.2"}
        pending = [binary] + [p for p in (prefix/"browser").rglob("*.so") if p.is_file()]
        visited, copied = set(), {}
        def copy_library(name, original):
            if original.stat().st_uid!=0 or original.stat().st_mode & 0o022:
                raise RuntimeError("Untrusted browser dependency ownership")
            if not original.is_relative_to(Path('/usr/lib')) and not original.is_relative_to(root):
                raise RuntimeError("Unreviewed browser dependency location")
            before=namespace["digest"](original);guard(reserve=original.stat().st_size)
            target=prefix/"lib"/name;shutil.copyfile(original,target);target.chmod(0o644)
            if namespace["digest"](original)!=before or namespace["digest"](target)!=before:raise RuntimeError("Browser library source changed")
            copied[name]={"source":str(original),"sha256":before};pending.append(target)
        # NSS loads these provider modules dynamically, so ELF NEEDED alone is
        # insufficient. Use existing root-owned system modules, never download.
        for name in ("libsoftokn3.so", "libfreeblpriv3.so", "libnssckbi.so"):
            original=available.get(name)
            if original is None:
                candidate=Path('/usr/lib/x86_64-linux-gnu')/name
                original=candidate.resolve() if candidate.is_file() else None
            if original is None:raise RuntimeError("Missing native NSS provider module; stop for review")
            copy_library(name,original)
        while pending:
            path = pending.pop()
            if path in visited:continue
            visited.add(path)
            # Static ELF inspection only. Never execute downloaded code on host.
            dynamic = rehearsal.run(["readelf", "-d", path])
            for name in re.findall(r"Shared library: \[([^\]]+)\]", dynamic):
                if name in excluded or name in copied:continue
                bundled = list((prefix/"browser").rglob(name))
                if bundled:
                    if len(bundled)!=1:raise RuntimeError("Ambiguous bundled browser dependency")
                    pending.append(bundled[0]);continue
                original = available.get(name)
                if original is None:raise RuntimeError("Missing browser library; stop before additional download")
                copy_library(name,original)
        for parent in prefix.rglob('*'):
            if parent.is_symlink():raise RuntimeError("Unexpected UI prefix symlink")
            if parent.is_dir():parent.chmod(0o755)
            elif parent.is_file() and not parent.stat().st_mode & 0o111:parent.chmod(0o644)
        relative=str(binary.relative_to(prefix))
        wrapper=prefix/'chrome'
        wrapper.write_text('#!/bin/sh\nset -eu\nexport LD_LIBRARY_PATH=/opt/ndf-ui/lib\nexport ALSA_CONFIG_PATH=/opt/ndf-ui/alsa/alsa.conf\nexec /opt/ndf-ui/'+relative+' "$@"\n')
        wrapper.chmod(0o755)
        (root/'browser-library-provenance.json').write_text(json.dumps(copied,indent=2))
        state['browser_relative_path']=relative;state['tool_copy_complete']=True
        rehearsal.save()
    output=rehearsal.docker(['run','--rm','--pull=never','--label',namespace['LABEL'],'--label=ndf.role=ui-tool-version',
        '--network=none','--read-only','--user=1000:1000','--cap-drop=ALL','--security-opt=no-new-privileges',
        '--security-opt=seccomp='+str(policy),'--memory=256m','--cpus=0.25','--pids-limit=64','--mount',
        'type=bind,src='+str(prefix)+',dst=/opt/ndf-ui,readonly','--entrypoint','/opt/ndf-ui/chrome',namespace['APP_IMAGE'],'--version'],timeout=60)
    if not re.search(r'\b'+re.escape(browser['version'])+r'\b',output):raise RuntimeError('Pinned browser version mismatch')
    state['browser_version_output']=output.strip()
    if 'tools' not in state['steps']:state['steps'].append('tools')
    rehearsal.save();guard()
    rehearsal.progress(json.dumps({'ui_tools_prepared':True,'download_bytes':state['download_bytes'],'browser_version':output.strip(),
        'host_packages_installed':False,'customer_pages_loaded':False,'normal_browser_sandbox_not_yet_proven':True}))
