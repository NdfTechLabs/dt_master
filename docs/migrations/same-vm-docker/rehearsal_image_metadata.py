#!/usr/bin/env python3
"""Public Docker registry metadata only; never pull image layers or site data."""

import hashlib
import json
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ACCEPT = ",".join(("application/vnd.oci.image.index.v1+json", "application/vnd.docker.distribution.manifest.list.v2+json",
                   "application/vnd.oci.image.manifest.v1+json", "application/vnd.docker.distribution.manifest.v2+json"))


def fetch_json(url, token=None, limit=2 * 1024 * 1024):
    headers = {"Accept": ACCEPT, "User-Agent": "ndf-rehearsal-metadata"}
    if token:
        headers["Authorization"] = "Bearer " + token
    with urlopen(Request(url, headers=headers), timeout=30) as response:
        raw = response.read(limit + 1)
    if len(raw) > limit:
        raise RuntimeError("Unexpectedly large registry metadata")
    return json.loads(raw), raw


def inspect(repository, tag):
    authentication, _ = fetch_json("https://auth.docker.io/token?" + urlencode({
        "service": "registry.docker.io", "scope": "repository:" + repository + ":pull"}), limit=64 * 1024)
    token = authentication.get("token") or authentication["access_token"]
    root = "https://registry-1.docker.io/v2/" + repository
    index, raw = fetch_json(root + "/manifests/" + tag, token)
    index_digest = "sha256:" + hashlib.sha256(raw).hexdigest()
    if "manifests" in index:
        candidates = [row for row in index["manifests"] if row.get("platform", {}).get("os") == "linux"
                      and row.get("platform", {}).get("architecture") == "amd64"]
        if len(candidates) != 1:
            raise RuntimeError("Ambiguous linux/amd64 image manifest")
        digest = candidates[0]["digest"]
        manifest, raw = fetch_json(root + "/manifests/" + digest, token)
        if "sha256:" + hashlib.sha256(raw).hexdigest() != digest:
            raise RuntimeError("Registry image manifest checksum mismatch")
    else:
        digest, manifest = index_digest, index
    descriptor = manifest["config"]
    config, raw = fetch_json(root + "/blobs/" + descriptor["digest"], token, limit=256 * 1024)
    if "sha256:" + hashlib.sha256(raw).hexdigest() != descriptor["digest"]:
        raise RuntimeError("Registry image config checksum mismatch")
    if config.get("os") != "linux" or config.get("architecture") != "amd64":
        raise RuntimeError("Unexpected image platform")
    versions = {entry.split("=", 1)[0]: entry.split("=", 1)[1] for entry in config.get("config", {}).get("Env", [])
                if entry.startswith(("MARIADB_VERSION=", "REDIS_VERSION="))}
    return {"repository": repository, "tag": tag, "index_digest": index_digest, "amd64_digest": digest,
            "compressed_layer_bytes": sum(row["size"] for row in manifest["layers"]),
            "config_bytes": descriptor["size"], "version_environment": versions,
            "layers": [{"digest": row["digest"], "bytes": row["size"]} for row in manifest["layers"]]}


def main():
    images = [inspect("library/mariadb", "10.11.14"), inspect("library/redis", "8.8.0")]
    print(json.dumps({"metadata_only": True, "image_layers_downloaded": False, "images": images,
                      "total_compressed_layer_bytes": sum(row["compressed_layer_bytes"] for row in images)}, indent=2))


if __name__ == "__main__":
    main()
