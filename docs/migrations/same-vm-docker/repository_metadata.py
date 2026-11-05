#!/usr/bin/env python3
"""Verify small public vendor metadata; download no Docker binaries/images."""

import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
from urllib.request import urlopen


BASE = "https://download.docker.com/linux/ubuntu"
SIGNER = "9DC858229FC7DD38854AE2D88D81803C0EBFCD88"


def download(url):
    with urlopen(url, timeout=30) as response:
        data = response.read(2 * 1024 * 1024 + 1)
        if len(data) > 2 * 1024 * 1024:
            raise RuntimeError("Unexpectedly large repository metadata")
        return data


def main():
    key, release = download(BASE + "/gpg"), download(BASE + "/dists/noble/InRelease")
    with tempfile.TemporaryDirectory(prefix="ndf-public-metadata-") as temporary:
        root = Path(temporary)
        (root / "key.asc").write_bytes(key)
        (root / "InRelease").write_bytes(release)
        result = subprocess.run(["gpg", "--no-options", "--homedir", temporary, "--batch", "--with-colons",
                                 "--show-keys", str(root / "key.asc")], capture_output=True, text=True, check=True)
        fingerprint = next(line.split(":")[9] for line in result.stdout.splitlines() if line.startswith("fpr:"))
        if fingerprint != SIGNER:
            raise RuntimeError("Unexpected Docker repository signing key")
        subprocess.run(["gpg", "--no-options", "--homedir", temporary, "--batch", "--dearmor", "--output",
                        str(root / "keyring.gpg"), str(root / "key.asc")], capture_output=True, check=True)
        subprocess.run(["gpgv", "--keyring", str(root / "keyring.gpg"), "--output", str(root / "Release"),
                        str(root / "InRelease")], capture_output=True, check=True)
        text = (root / "Release").read_text()
        target = "stable/binary-amd64/Packages.gz"
        section = text.split("SHA256:\n", 1)[1].split("\n\n", 1)[0]
        entry = next(line.split() for line in section.splitlines() if line.strip().endswith(" " + target))
        data = download(BASE + "/dists/noble/" + target)
        if hashlib.sha256(data).hexdigest() != entry[0]:
            raise RuntimeError("Repository package index checksum mismatch")
        blocks = []
        for paragraph in gzip.decompress(data).decode().split("\n\n"):
            values, last = {}, None
            for line in paragraph.splitlines():
                if line.startswith(" ") and last:
                    values[last] += " " + line.strip()
                elif ": " in line:
                    last, value = line.split(": ", 1)
                    values[last] = value
            if values:
                blocks.append(values)
        wanted = {"docker-ce": "5:29.6.1-", "docker-ce-cli": "5:29.6.1-",
                  "docker-ce-rootless-extras": "5:29.6.1-", "docker-compose-plugin": "5.3.1-"}
        selected = {}
        fields = ("Version", "Size", "SHA256", "Filename", "Depends")
        for name, prefix in wanted.items():
            candidates = [block for block in blocks if block.get("Package") == name and block.get("Version", "").startswith(prefix)]
            if candidates:
                selected[name] = {field: candidates[-1].get(field) for field in fields}
            else:
                selected[name] = {"requested_version_available": False,
                                  "recent_versions": [block["Version"] for block in blocks if block.get("Package") == name][-3:]}
        candidates = [block for block in blocks if block.get("Package") == "containerd.io"]
        if candidates:
            candidate = candidates[0]
            for block in candidates[1:]:
                if subprocess.run(["dpkg", "--compare-versions", block["Version"], "gt", candidate["Version"]]).returncode == 0:
                    candidate = block
            selected["containerd.io"] = {field: candidate.get(field) for field in fields}
        buildx = [block for block in blocks if block.get("Package") == "docker-buildx-plugin"]
        build_tool = None
        if buildx:
            candidate = buildx[0]
            for block in buildx[1:]:
                if subprocess.run(["dpkg", "--compare-versions", block["Version"], "gt", candidate["Version"]]).returncode == 0:
                    candidate = block
            build_tool = {field: candidate.get(field) for field in fields}
        print(json.dumps({"repository_signature_verified": True, "signing_fingerprint": fingerprint,
                          "package_index_sha256": entry[0], "selected_metadata": selected,
                          "additional_build_tool_metadata_only": build_tool,
                          "selected_package_download_bytes": sum(int(package.get("Size", 0)) for package in selected.values()),
                          "binary_packages_downloaded": False}, indent=2))


if __name__ == "__main__":
    main()
