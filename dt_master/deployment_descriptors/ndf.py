"""Pure validation for NDF Product Deployment Descriptors.

The authoritative JSON Schemas are supplied by the trusted NDF contract
bundle. This module does not fetch schemas, verify signatures, resolve
artifacts, or execute Docker operations.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError


class DeploymentDescriptorError(ValueError):
    """Raised when an NDF descriptor fails structural or semantic validation."""


EXPECTED_PDD_EVIDENCE = frozenset({
    "lock_v2",
    "apps_json",
    "build_metadata",
    "buildx_metadata",
    "build_inventory",
    "build_evidence",
    "source_provenance",
    "source_provenance_verification",
    "source_tree_post_cleanup",
    "source_tree_runtime",
    "image_composition_verification",
})
_SHA256 = re.compile(r"^[a-f0-9]{64}$")


def _reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise DeploymentDescriptorError(f"Duplicate JSON object key: {key}")
        result[key] = value
    return result


def _reject_constant(value):
    raise DeploymentDescriptorError(f"Non-finite JSON number is prohibited: {value}")


def parse_descriptor_json(raw: bytes | str) -> dict:
    """Parse strict UTF-8 JSON, rejecting duplicate keys and non-finite numbers."""
    try:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        if not isinstance(raw, str):
            raise DeploymentDescriptorError("Descriptor input must be UTF-8 JSON bytes or text.")
        value = json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DeploymentDescriptorError(f"Descriptor is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise DeploymentDescriptorError("Descriptor root must be a JSON object.")
    return value


def canonical_pdd_bytes(value: Any) -> bytes:
    """Implement NDF-JSON-CANONICAL-V1 (not RFC 8785)."""
    def check(item):
        if isinstance(item, float):
            raise DeploymentDescriptorError("NDF canonical JSON prohibits floating-point values.")
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                raise DeploymentDescriptorError("JSON object keys must be strings.")
            for child in item.values():
                check(child)
        elif isinstance(item, list):
            for child in item:
                check(child)

    check(value)
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise DeploymentDescriptorError(f"Unable to canonicalize descriptor: {exc}") from exc


def _schema_validate(value: dict, schema: dict) -> None:
    if not isinstance(schema, dict):
        raise DeploymentDescriptorError("A trusted NDF JSON Schema object is required.")
    try:
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(
            schema, format_checker=Draft202012Validator.FORMAT_CHECKER
        )
        errors = sorted(
            validator.iter_errors(value),
            key=lambda error: (list(map(str, error.absolute_path)), error.message),
        )
    except SchemaError as exc:
        raise DeploymentDescriptorError(f"Supplied NDF schema is invalid: {exc.message}") from exc
    if errors:
        error = errors[0]
        location = ".".join(str(part) for part in error.absolute_path) or "<root>"
        raise DeploymentDescriptorError(f"Schema validation failed at {location}: {error.message}")


def _validate_integrity(descriptor: dict) -> None:
    integrity = descriptor["integrity"]
    payload = {
        **descriptor,
        "integrity": {key: value for key, value in integrity.items() if key != "content_sha256"},
    }
    actual = hashlib.sha256(canonical_pdd_bytes(payload)).hexdigest()
    if actual != integrity["content_sha256"]:
        raise DeploymentDescriptorError("PDD canonical content SHA-256 does not match integrity.content_sha256.")


def _validate_identity(descriptor: dict) -> None:
    product = descriptor["product"]
    artifact = descriptor["artifact"]
    digest = artifact["digest"]
    platform = artifact["platform"].replace("/", "-")
    expected = f"pdd:{product['id']}:{platform}:sha256:{digest.removeprefix('sha256:')}"
    if descriptor["descriptor_id"] != expected:
        raise DeploymentDescriptorError("PDD descriptor_id does not match product/platform/image identity.")
    immutable_reference = artifact.get("immutable_reference")
    if immutable_reference is not None and immutable_reference != f"{artifact['repository']}@{digest}":
        raise DeploymentDescriptorError("PDD immutable_reference does not match repository and digest.")


def _safe_evidence_path(path: str) -> bool:
    if not path or "\\" in path or path.startswith("/") or ":" in path:
        return False
    if any(ord(character) < 32 for character in path):
        return False
    return all(part not in ("", ".", "..") for part in path.split("/"))


def _validate_evidence(descriptor: dict) -> dict[str, dict]:
    records = descriptor["evidence"]
    by_name = {}
    paths = set()
    for record in records:
        name, path = record["name"], record["path"]
        if name in by_name:
            raise DeploymentDescriptorError(f"Duplicate evidence name: {name}.")
        if not _safe_evidence_path(path):
            raise DeploymentDescriptorError(f"Unsafe evidence path for {name}.")
        if path in paths:
            raise DeploymentDescriptorError(f"Duplicate evidence path: {path}.")
        by_name[name] = record
        paths.add(path)

    missing = EXPECTED_PDD_EVIDENCE - by_name.keys()
    extra = by_name.keys() - EXPECTED_PDD_EVIDENCE
    if missing or extra:
        raise DeploymentDescriptorError(
            f"PDD evidence inventory mismatch; missing={sorted(missing)}, extra={sorted(extra)}."
        )

    if descriptor["build"].get("build_metadata_sha256") != by_name["build_metadata"]["sha256"]:
        raise DeploymentDescriptorError("Build metadata digest does not match its evidence record.")
    if descriptor["composition"]["verification"]["evidence_sha256"] != by_name["image_composition_verification"]["sha256"]:
        raise DeploymentDescriptorError("Composition verification digest does not match its evidence record.")
    if descriptor["composition"]["lock"]["sha256"] != by_name["lock_v2"]["sha256"]:
        raise DeploymentDescriptorError("Composition lock digest does not match lock-v2 evidence.")
    if descriptor["runtime_inventory"]["source_evidence"] != "build_inventory":
        raise DeploymentDescriptorError("Runtime inventory source must be build_inventory.")
    for domain in descriptor["requirements"].values():
        for verification in domain["verification"]:
            evidence = next((item for item in records if item["path"] == verification["evidence_reference"]), None)
            if evidence and verification["evidence_sha256"] != evidence["sha256"]:
                raise DeploymentDescriptorError(
                    f"Requirement evidence digest mismatch for {verification['evidence_reference']}."
                )
    return by_name


def _validate_composition(descriptor: dict) -> None:
    composition = descriptor["composition"]
    applications = {}
    for app in composition["applications"]:
        name = app["app_name"]
        if name in applications:
            raise DeploymentDescriptorError(f"Duplicate Bench application identity: {name}.")
        applications[name] = app
        if ("descriptor_path" in app) != ("descriptor_sha256" in app):
            raise DeploymentDescriptorError(
                f"Application {name} must provide descriptor_path and descriptor_sha256 together."
            )

    if descriptor["artifact"]["platform"] != descriptor["runtime_inventory"]["platform"]["oci"]:
        raise DeploymentDescriptorError("Artifact platform differs from measured runtime inventory platform.")
    framework = descriptor["runtime_inventory"]["framework"]
    if framework["name"] != "frappe":
        raise DeploymentDescriptorError("Runtime framework identity must be frappe.")
    framework_app = applications.get("frappe")
    if framework_app is None:
        raise DeploymentDescriptorError("Frappe framework is absent from the Bench application inventory.")
    for field in ("repository", "requested_ref", "actual_commit"):
        if framework_app[field] != framework[field]:
            raise DeploymentDescriptorError(f"Runtime framework {field} differs from Bench application inventory.")

    item_ids = set(applications)
    graph = {}
    expected_service_claims = set()
    for service in composition["services"]:
        service_id = service["service_id"]
        if service_id in item_ids:
            raise DeploymentDescriptorError(f"Service identity collides with an application: {service_id}.")
        item_ids.add(service_id)
        graph[service_id] = service["depends_on"]
        expected_service_claims.add((
            service["descriptor_path"], service["descriptor_sha256"], service_id, "container"
        ))
    for requirement in composition["external_requirements"]:
        requirement_id = requirement["requirement_id"]
        if requirement_id in item_ids:
            raise DeploymentDescriptorError(f"External requirement identity collision: {requirement_id}.")
        item_ids.add(requirement_id)
        graph[requirement_id] = requirement["depends_on"]
        expected_service_claims.add((
            requirement["descriptor_path"], requirement["descriptor_sha256"], requirement_id, "external"
        ))

    service_item_ids = set(graph)
    for owner, dependencies in graph.items():
        for dependency in dependencies:
            if dependency not in service_item_ids:
                raise DeploymentDescriptorError(f"Undeclared dependency {dependency!r} referenced by {owner}.")
            if dependency == owner:
                raise DeploymentDescriptorError(f"Composition item {owner} cannot depend on itself.")

    visiting, visited = set(), set()

    def visit(node):
        if node in visiting:
            raise DeploymentDescriptorError(f"Composition dependency cycle includes {node}.")
        if node in visited:
            return
        visiting.add(node)
        for dependency in graph[node]:
            if dependency in graph:
                visit(dependency)
        visiting.remove(node)
        visited.add(node)

    for node in graph:
        visit(node)

    declarations = descriptor["requirements"]["services"]["declarations"]
    actual = []
    for declaration in declarations:
        if declaration["authority"] == "service_descriptor":
            for claim in declaration["claims"]:
                actual.append((
                    declaration["reference"], declaration["sha256"],
                    claim.get("service_id", ""), claim.get("service_kind", ""),
                ))
    if len(actual) != len(set(actual)) or set(actual) != expected_service_claims:
        raise DeploymentDescriptorError(
            "Services requirement declarations do not match composition service/external identities."
        )
    service_state = descriptor["requirements"]["services"]["state"]
    if expected_service_claims and service_state not in {"declared", "verified"}:
        raise DeploymentDescriptorError("Services requirement state must be declared or verified.")
    if not expected_service_claims and service_state != "not_declared":
        raise DeploymentDescriptorError("Services requirement state must be not_declared without composition services.")


def validate_product_deployment_descriptor(
    descriptor: dict, *, schema: dict, expected_image_reference: str | None = None
) -> dict:
    """Validate PDD schema, canonical integrity, identity, evidence and composition.

    This validates descriptor integrity only. It does not verify NDF signatures,
    attestations, producer identity, or deployment authorization.
    """
    if not isinstance(descriptor, dict):
        raise DeploymentDescriptorError("PDD must be a JSON object.")
    _schema_validate(descriptor, schema)
    _validate_integrity(descriptor)
    _validate_identity(descriptor)
    evidence = _validate_evidence(descriptor)
    _validate_composition(descriptor)
    artifact = descriptor["artifact"]
    immutable_reference = f"{artifact['repository']}@{artifact['digest']}"
    if expected_image_reference and expected_image_reference != immutable_reference:
        raise DeploymentDescriptorError("PDD image identity does not match the expected immutable image reference.")
    return {
        "status": "validated",
        "schema_version": descriptor["schema_version"],
        "descriptor_id": descriptor["descriptor_id"],
        "image_reference": immutable_reference,
        "image_digest": artifact["digest"],
        "content_sha256": descriptor["integrity"]["content_sha256"],
        "evidence_count": len(evidence),
        "trust_status": "not_verified",
        "deployment_authorized": False,
    }


def validate_service_descriptor(
    descriptor: dict, *, schema: dict, raw_bytes: bytes, expected_service_id: str,
    expected_descriptor_sha256: str, expected_image_reference: str | None = None
) -> dict:
    """Validate one NDF service descriptor against its PDD-bound bytes and identity."""
    if not isinstance(raw_bytes, bytes):
        raise DeploymentDescriptorError("Service descriptor raw bytes are required for digest binding.")
    actual_sha = hashlib.sha256(raw_bytes).hexdigest()
    if not _SHA256.fullmatch(expected_descriptor_sha256) or actual_sha != expected_descriptor_sha256:
        raise DeploymentDescriptorError("Service descriptor bytes do not match the PDD-bound SHA-256.")
    parsed = parse_descriptor_json(raw_bytes)
    if parsed != descriptor:
        raise DeploymentDescriptorError("Parsed service descriptor does not match supplied descriptor object.")
    _schema_validate(descriptor, schema)
    if descriptor["service_id"] != expected_service_id:
        raise DeploymentDescriptorError("Service descriptor identity differs from PDD composition identity.")
    image = descriptor.get("artifact", {}).get("image")
    if expected_image_reference and image != expected_image_reference:
        raise DeploymentDescriptorError("Service image does not match the expected immutable image reference.")
    if descriptor["service_kind"] == "container" and (
        not isinstance(image, str) or not re.fullmatch(r"[^@\\s]+@sha256:[a-f0-9]{64}", image)
    ):
        raise DeploymentDescriptorError("Container service image must be digest-pinned.")
    return {
        "status": "validated",
        "service_id": expected_service_id,
        "descriptor_sha256": actual_sha,
        "image_reference": image,
    }
