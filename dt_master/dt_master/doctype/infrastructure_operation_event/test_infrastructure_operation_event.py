# Copyright (c) 2026, Dhana Technologies and contributors
# For license information, please see license.txt

import json
import uuid

import frappe
from frappe.tests import IntegrationTestCase


class IntegrationTestInfrastructureOperationEvent(IntegrationTestCase):
    def _insert_operation(self):
        operation_id = str(uuid.uuid4())
        operation = frappe.get_doc(
            {
                "doctype": "Infrastructure Operation",
                "operation_id": operation_id,
                "idempotency_key": f"event-test-{uuid.uuid4()}",
                "request_fingerprint": "b" * 64,
                "contract_version": "1.0.0",
                "operation_type": "test.event",
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
        )
        frappe.flags.infrastructure_operation_create = True
        try:
            operation.insert(ignore_permissions=True)
        finally:
            frappe.flags.infrastructure_operation_create = False
        return operation

    def _insert_event(self, operation, sequence=1):
        event = frappe.get_doc(
            {
                "doctype": "Infrastructure Operation Event",
                "operation": operation.name,
                "sequence": sequence,
                "event_type": "queued",
                "schema_version": "1.0.0",
                "occurred_at": frappe.utils.now_datetime(),
                "event_data": json.dumps({"safe": True}),
            }
        )
        frappe.flags.infrastructure_operation_event_create = True
        try:
            event.insert(ignore_permissions=True)
        finally:
            frappe.flags.infrastructure_operation_event_create = False
        return event

    def test_event_requires_coordinator_creation_context(self):
        operation = self._insert_operation()
        event = frappe.get_doc(
            {
                "doctype": "Infrastructure Operation Event",
                "operation": operation.name,
                "sequence": 1,
                "event_type": "queued",
                "schema_version": "1.0.0",
                "occurred_at": frappe.utils.now_datetime(),
            }
        )
        with self.assertRaises(frappe.ValidationError):
            event.insert(ignore_permissions=True)

    def test_event_is_appended_to_operation(self):
        operation = self._insert_operation()
        event = self._insert_event(operation)
        self.assertEqual(event.operation, operation.name)
        self.assertEqual(event.sequence, 1)

    def test_event_rejects_duplicate_sequence(self):
        operation = self._insert_operation()
        self._insert_event(operation)
        with self.assertRaises(frappe.ValidationError):
            self._insert_event(operation)

    def test_event_rejects_non_object_json(self):
        operation = self._insert_operation()
        event = frappe.get_doc(
            {
                "doctype": "Infrastructure Operation Event",
                "operation": operation.name,
                "sequence": 2,
                "event_type": "progress",
                "schema_version": "1.0.0",
                "occurred_at": frappe.utils.now_datetime(),
                "progress": "[]",
            }
        )
        frappe.flags.infrastructure_operation_event_create = True
        try:
            with self.assertRaises(frappe.ValidationError):
                event.insert(ignore_permissions=True)
        finally:
            frappe.flags.infrastructure_operation_event_create = False

    def test_event_cannot_be_updated_or_deleted(self):
        operation = self._insert_operation()
        event = self._insert_event(operation)
        event.message = "tampered"
        with self.assertRaises(frappe.ValidationError):
            event.save(ignore_permissions=True)
        with self.assertRaises(frappe.ValidationError):
            event.delete(ignore_permissions=True)
