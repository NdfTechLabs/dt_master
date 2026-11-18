# Copyright (c) 2026, Dhana Technologies and contributors
# For license information, please see license.txt

import unittest

import frappe

from dt_master.operations.coordinator import _canonical_payload, _reject_secret_values
from dt_master.operations.registry import (
    get_operation,
    registered_operation_types,
)


class TestOperationRegistryAndContracts(unittest.TestCase):
    def test_noop_operation_is_registered(self):
        definition = get_operation("test.noop")
        self.assertEqual(definition.operation_type, "test.noop")
        self.assertEqual(definition.queue, "short")
        self.assertEqual(definition.max_attempts, 1)

    def test_registered_types_are_stable_and_sorted(self):
        types = registered_operation_types()
        self.assertEqual(types, tuple(sorted(types)))
        self.assertIn("test.noop", types)

    def test_unknown_operation_type_is_rejected(self):
        with self.assertRaises(frappe.ValidationError):
            get_operation("arbitrary.python.path")

    def test_noop_rejects_unexpected_payload_fields(self):
        definition = get_operation("test.noop")
        with self.assertRaises(frappe.ValidationError):
            definition.validate_payload({"command": "anything"})

    def test_canonical_payload_fingerprint_is_order_independent(self):
        definition = get_operation("test.noop")
        first = {"nested": {"b": 2, "a": 1}}
        second = {"nested": {"a": 1, "b": 2}}
        _, first_json, first_fingerprint = _canonical_payload(
            definition_for_test_payload(first), first
        )
        _, second_json, second_fingerprint = _canonical_payload(
            definition_for_test_payload(second), second
        )
        self.assertEqual(first_json, second_json)
        self.assertEqual(first_fingerprint, second_fingerprint)

    def test_secret_values_are_rejected(self):
        with self.assertRaises(frappe.ValidationError):
            _reject_secret_values({"database_password": "do-not-store"})

    def test_secret_references_are_allowed(self):
        _reject_secret_values({"database_secret_ref": "vault://platform/db"})


def definition_for_test_payload(payload):
    """Return a copy of the no-op definition with a general object validator."""
    from dataclasses import replace

    from dt_master.operations.registry import validate_object_payload

    return replace(get_operation("test.noop"), validate_payload=validate_object_payload)
