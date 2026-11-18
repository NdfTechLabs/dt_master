# Copyright (c) 2026, Dhana Technologies and contributors
# For license information, please see license.txt

import unittest
from dataclasses import replace
from unittest.mock import MagicMock, patch

import frappe

from dt_master.operations import coordinator, worker
from dt_master.operations.registry import get_operation


class TestOperationCoordinator(unittest.TestCase):
    def setUp(self):
        self.target = MagicMock()
        self.target.doctype = "Infrastructure Node"
        self.target.name = "NODE-TEST"
        self.operation = MagicMock()
        self.operation.name = "operation-test"
        self.operation.operation_id = "operation-test"
        self.operation.correlation_id = "correlation-test"
        self.operation.business_state = "Accepted"
        self.operation.execution_state = "Pending Dispatch"
        self.operation.initiated_by = "operator@example.com"
        self.operation.contract_version = "1.0.0"
        self.operation.operation_type = "test.noop"
        self.operation.target_doctype = "Infrastructure Node"
        self.operation.target_name = "NODE-TEST"
        self.operation.request_payload = "{}"
        self.operation.initiator_type = "Human"
        self.operation.execution_identity = "frappe-worker"

    def _start(self, get_doc_side_effect=None):
        with (
            patch.dict(frappe.session, {"user": "operator@example.com"}),
            patch.object(frappe, "only_for"),
            patch.object(coordinator, "_existing_operation", return_value=None),
            patch.object(coordinator, "dispatch_operation"),
            patch.object(coordinator.frappe.db, "commit"),
            patch.object(coordinator.frappe, "get_doc", side_effect=get_doc_side_effect or [self.target, self.operation]),
        ):
            return coordinator.start_operation(
                operation_type="test.noop",
                target_doctype="Infrastructure Node",
                target_name="NODE-TEST",
                payload={},
                idempotency_key="request-123",
            )

    def test_acceptance_persists_before_dispatch_and_returns_operation_id(self):
        result = self._start()
        self.operation.insert.assert_called_once_with(ignore_permissions=True)
        self.operation.reload.assert_called_once()
        self.assertEqual(result["operation_id"], "operation-test")
        self.assertEqual(result["execution_state"], "Pending Dispatch")

    def test_same_idempotency_request_reuses_existing_operation(self):
        definition = get_operation("test.noop")
        _, _, fingerprint = coordinator._canonical_payload(definition, {})
        existing = MagicMock()
        existing.request_fingerprint = fingerprint
        existing.operation_id = "existing-operation"
        existing.correlation_id = "existing-correlation"
        existing.business_state = "Accepted"
        existing.execution_state = "Queued"
        with (
            patch.dict(frappe.session, {"user": "operator@example.com"}),
            patch.object(frappe, "only_for"),
            patch.object(coordinator, "_existing_operation", return_value=existing),
            patch.object(coordinator.frappe, "get_doc", return_value=self.target),
            patch.object(coordinator, "dispatch_operation") as dispatch,
        ):
            result = coordinator.start_operation(
                "test.noop", "Infrastructure Node", "NODE-TEST", {}, "request-123"
            )
        dispatch.assert_not_called()
        self.assertTrue(result["reused"])
        self.assertEqual(result["operation_id"], "existing-operation")

    def test_same_idempotency_key_with_different_payload_is_rejected(self):
        existing = MagicMock()
        existing.request_fingerprint = "different-fingerprint"
        with (
            patch.dict(frappe.session, {"user": "operator@example.com"}),
            patch.object(frappe, "only_for"),
            patch.object(coordinator, "_existing_operation", return_value=existing),
            patch.object(coordinator.frappe, "get_doc", return_value=self.target),
        ):
            with self.assertRaises(frappe.ValidationError):
                coordinator.start_operation(
                    "test.noop", "Infrastructure Node", "NODE-TEST", {}, "request-123"
                )

    def test_queue_failure_keeps_durable_pending_operation(self):
        with (
            patch.dict(frappe.session, {"user": "operator@example.com"}),
            patch.object(frappe, "only_for"),
            patch.object(coordinator, "_existing_operation", return_value=None),
            patch.object(coordinator, "dispatch_operation", side_effect=RuntimeError("queue offline")),
            patch.object(coordinator.frappe.db, "commit") as commit,
            patch.object(coordinator.frappe, "log_error"),
            patch.object(coordinator.frappe, "get_doc", side_effect=[self.target, self.operation]),
        ):
            result = coordinator.start_operation(
                "test.noop", "Infrastructure Node", "NODE-TEST", {}, "request-123"
            )
        self.operation.insert.assert_called_once_with(ignore_permissions=True)
        self.operation.reload.assert_called_once()
        self.assertGreaterEqual(commit.call_count, 1)
        self.assertEqual(result["execution_state"], "Pending Dispatch")


class TestOperationWorkerClaim(unittest.TestCase):
    def test_duplicate_delivery_does_not_invoke_handler(self):
        operation = MagicMock()
        operation.operation_id = "operation-test"
        operation.operation_type = "test.noop"
        operation.contract_version = "1.0.0"
        definition = get_operation("test.noop")
        handler = MagicMock()
        definition = replace(definition, handler=handler)
        with (
            patch.object(worker.frappe, "get_doc", return_value=operation),
            patch.object(worker, "get_operation", return_value=definition),
            patch.object(worker.frappe.db, "sql"),
            patch.object(worker.frappe.db, "affected_rows", return_value=0),
            patch.object(worker.frappe.db, "commit"),
        ):
            worker.execute_operation("operation-test")
        handler.assert_not_called()
