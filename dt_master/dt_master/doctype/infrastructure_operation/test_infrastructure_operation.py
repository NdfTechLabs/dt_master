# Copyright (c) 2026, Dhana Technologies and contributors
# For license information, please see license.txt

import json
import uuid

import frappe
from frappe.tests import IntegrationTestCase


class IntegrationTestInfrastructureOperation(IntegrationTestCase):
    def _new_operation(self, **overrides):
        values = {
            "operation_id": str(uuid.uuid4()),
            "idempotency_key": f"test-{uuid.uuid4()}",
            "request_fingerprint": "a" * 64,
            "contract_version": "1.0.0",
            "operation_type": "test.noop",
            "target_doctype": "Infrastructure Node",
            "target_name": "test-node",
            "initiator_type": "Human",
            "initiated_by": frappe.session.user,
            "business_state": "Accepted",
            "execution_state": "Pending Dispatch",
            "dispatch_state": "Pending",
            "attempt_count": 0,
            "max_attempts": 1,
            "priority": "Normal",
            "timeout_seconds": 60,
            "request_payload": json.dumps({"safe": True}),
        }
        values.update(overrides)
        return frappe.get_doc({"doctype": "Infrastructure Operation", **values})

    def _insert_operation(self, **overrides):
        doc = self._new_operation(**overrides)
        frappe.flags.infrastructure_operation_create = True
        try:
            doc.insert(ignore_permissions=True)
        finally:
            frappe.flags.infrastructure_operation_create = False
        return doc

    def test_operation_requires_coordinator_creation_context(self):
        with self.assertRaises(frappe.ValidationError):
            self._new_operation().insert(ignore_permissions=True)

    def test_operation_is_created_with_server_identity(self):
        operation = self._insert_operation()
        self.assertEqual(operation.name, operation.operation_id)
        self.assertEqual(operation.execution_state, "Pending Dispatch")
        self.assertEqual(operation.business_state, "Accepted")

    def test_operation_rejects_invalid_request_json(self):
        operation = self._new_operation(request_payload="{")
        frappe.flags.infrastructure_operation_create = True
        try:
            with self.assertRaises(frappe.ValidationError):
                operation.insert(ignore_permissions=True)
        finally:
            frappe.flags.infrastructure_operation_create = False

    def test_operation_rejects_non_object_request_json(self):
        operation = self._new_operation(request_payload="[]")
        frappe.flags.infrastructure_operation_create = True
        try:
            with self.assertRaises(frappe.ValidationError):
                operation.insert(ignore_permissions=True)
        finally:
            frappe.flags.infrastructure_operation_create = False

    def test_operation_rejects_non_positive_timeout(self):
        operation = self._new_operation(timeout_seconds=0)
        frappe.flags.infrastructure_operation_create = True
        try:
            with self.assertRaises(frappe.ValidationError):
                operation.insert(ignore_permissions=True)
        finally:
            frappe.flags.infrastructure_operation_create = False

    def test_operation_rejects_client_lifecycle_mutation(self):
        operation = self._insert_operation()
        operation.execution_state = "Succeeded"
        with self.assertRaises(frappe.ValidationError):
            operation.save(ignore_permissions=True)

    def test_operation_rejects_deletion(self):
        operation = self._insert_operation()
        with self.assertRaises(frappe.ValidationError):
            operation.delete(ignore_permissions=True)

    def test_same_idempotency_scope_is_unique(self):
        key = f"duplicate-{uuid.uuid4()}"
        self._insert_operation(idempotency_key=key)
        duplicate = self._new_operation(idempotency_key=key)
        frappe.flags.infrastructure_operation_create = True
        try:
            with self.assertRaises(frappe.DuplicateEntryError):
                duplicate.insert(ignore_permissions=True)
        finally:
            frappe.flags.infrastructure_operation_create = False
