"""Tests for the pure NDF descriptor validation boundary."""

import hashlib
import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator

from dt_master.deployment_descriptors.ndf import (
    DeploymentDescriptorError,
    canonical_pdd_bytes,
    parse_descriptor_json,
    validate_product_deployment_descriptor,
    validate_service_descriptor,
)


class TestNdfDescriptorValidation(unittest.TestCase):
    def test_pinned_ndf_schemas_are_valid_draft_2020_12_schemas(self):
        fixture_dir = Path(__file__).parent / "tests" / "fixtures"
        for filename in (
            "product-deployment-descriptor-v1.schema.json",
            "service-descriptor-v1.schema.json",
        ):
            with self.subTest(schema=filename):
                schema = json.loads((fixture_dir / filename).read_text(encoding="utf-8"))
                Draft202012Validator.check_schema(schema)

    def test_actual_ndf_service_schema_rejects_mutable_container_image(self):
        fixture = Path(__file__).parent / "tests" / "fixtures" / "service-descriptor-v1.schema.json"
        schema = json.loads(fixture.read_text(encoding="utf-8"))
        descriptor = {
            "descriptor_version": "1.0.0",
            "service_id": "redis",
            "name": "Redis",
            "service_kind": "container",
            "artifact": {"image": "redis:latest"},
        }
        with self.assertRaisesRegex(DeploymentDescriptorError, "Schema validation failed"):
            from dt_master.deployment_descriptors.ndf import _schema_validate
            _schema_validate(descriptor, schema)

    def test_parser_rejects_duplicate_json_keys(self):
        with self.assertRaisesRegex(DeploymentDescriptorError, "Duplicate JSON object key"):
            parse_descriptor_json('{"service_id":"redis","service_id":"mysql"}')

    def test_parser_rejects_non_finite_numbers(self):
        with self.assertRaisesRegex(DeploymentDescriptorError, "Non-finite"):
            parse_descriptor_json('{"value":NaN}')

    def test_canonical_profile_rejects_floats(self):
        with self.assertRaisesRegex(DeploymentDescriptorError, "floating-point"):
            canonical_pdd_bytes({"resource": 1.5})

    def test_canonical_profile_preserves_unicode_and_sorts_keys(self):
        self.assertEqual(
            canonical_pdd_bytes({"z": 1, "name": "Dhana"}),
            b'{"name":"Dhana","z":1}',
        )

    def test_pdd_requires_a_schema_object(self):
        with self.assertRaisesRegex(DeploymentDescriptorError, "trusted NDF JSON Schema"):
            validate_product_deployment_descriptor({}, schema=None)

    def test_pdd_schema_rejects_unknown_top_level_fields(self):
        with self.assertRaisesRegex(DeploymentDescriptorError, "Schema validation failed"):
            validate_product_deployment_descriptor(
                {"unexpected": True},
                schema={"type": "object", "additionalProperties": False},
            )

    def test_service_descriptor_digest_must_match_pdd_binding(self):
        raw = json.dumps({
            "service_id": "redis",
            "service_kind": "container",
            "artifact": {"image": "ghcr.io/example/redis@sha256:" + "a" * 64},
            "depends_on": [],
        }, sort_keys=True, separators=(",", ":")).encode()
        descriptor = json.loads(raw)
        schema = {
            "type": "object",
            "required": ["service_id", "service_kind", "artifact", "depends_on"],
            "properties": {
                "service_id": {"const": "redis"},
                "service_kind": {"const": "container"},
                "artifact": {
                    "type": "object",
                    "required": ["image"],
                    "properties": {
                        "image": {"type": "string", "pattern": "^[^@\\s]+@sha256:[a-f0-9]{64}$"}
                    },
                    "additionalProperties": False,
                },
                "depends_on": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
            },
            "additionalProperties": False,
        }
        digest = hashlib.sha256(raw).hexdigest()
        result = validate_service_descriptor(
            descriptor,
            schema=schema,
            raw_bytes=raw,
            expected_service_id="redis",
            expected_descriptor_sha256=digest,
            expected_image_reference=descriptor["artifact"]["image"],
        )
        self.assertEqual(result["status"], "validated")
        self.assertEqual(result["descriptor_sha256"], digest)

    def test_service_descriptor_rejects_modified_bytes(self):
        raw = b'{"service_id":"redis"}'
        with self.assertRaisesRegex(DeploymentDescriptorError, "do not match"):
            validate_service_descriptor(
                {"service_id": "redis"},
                schema={"type": "object"},
                raw_bytes=raw,
                expected_service_id="redis",
                expected_descriptor_sha256="0" * 64,
            )

    def test_service_descriptor_rejects_identity_mismatch(self):
        raw = json.dumps({
            "service_id": "mysql",
            "service_kind": "external",
            "provider": {"name": "provider", "requirement": "managed database"},
            "depends_on": [],
        }, sort_keys=True, separators=(",", ":")).encode()
        with self.assertRaisesRegex(DeploymentDescriptorError, "identity differs"):
            validate_service_descriptor(
                json.loads(raw),
                schema={"type": "object"},
                raw_bytes=raw,
                expected_service_id="redis",
                expected_descriptor_sha256=hashlib.sha256(raw).hexdigest(),
            )

    def test_service_descriptor_rejects_mutable_image_reference(self):
        raw = json.dumps({
            "service_id": "redis",
            "service_kind": "container",
            "artifact": {"image": "redis:latest"},
            "depends_on": [],
        }, sort_keys=True, separators=(",", ":")).encode()
        schema = {
            "type": "object",
            "required": ["service_id", "service_kind", "artifact", "depends_on"],
            "properties": {
                "service_id": {"type": "string"},
                "service_kind": {"const": "container"},
                "artifact": {
                    "type": "object",
                    "properties": {"image": {"type": "string"}},
                    "required": ["image"],
                },
                "depends_on": {"type": "array"},
            },
        }
        with self.assertRaisesRegex(DeploymentDescriptorError, "digest-pinned"):
            validate_service_descriptor(
                json.loads(raw),
                schema=schema,
                raw_bytes=raw,
                expected_service_id="redis",
                expected_descriptor_sha256=hashlib.sha256(raw).hexdigest(),
            )


if __name__ == "__main__":
    unittest.main()
