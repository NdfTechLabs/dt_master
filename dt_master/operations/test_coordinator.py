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


    def test_guest_cannot_start_operation(self):
        with patch.dict(frappe.session, {"user": "Guest"}):
            with self.assertRaises(frappe.PermissionError):
                coordinator.start_operation(
                    "test.noop", "Infrastructure Node", "NODE-TEST", {}, "request-guest"
                )

    def test_unsupported_target_doctype_is_rejected(self):
        with (
            patch.dict(frappe.session, {"user": "operator@example.com"}),
            patch.object(frappe, "only_for"),
        ):
            with self.assertRaises(frappe.ValidationError):
                coordinator.start_operation("test.noop", "User", "operator@example.com", {}, "request-user")

    def test_registered_payload_validator_rejects_malformed_payload(self):
        with (
            patch.dict(frappe.session, {"user": "operator@example.com"}),
            patch.object(frappe, "only_for"),
            patch.object(coordinator.frappe, "get_doc", return_value=self.target),
        ):
            with self.assertRaises(frappe.ValidationError):
                coordinator.start_operation(
                    "test.noop",
                    "Infrastructure Node",
                    "NODE-TEST",
                    {"unexpected": "value"},
                    "request-invalid-payload",
                )

    def test_dispatch_uses_registry_queue_and_worker_entrypoint(self):
        queued_operation = MagicMock()
        queued_operation.name = "operation-test"
        queued_operation.operation_id = "operation-test"
        queued_operation.execution_state = "Pending Dispatch"
        queued_operation.operation_type = "test.noop"
        queued_operation.contract_version = "1.0.0"
        queued_operation.business_state = "Accepted"
        job = MagicMock()
        job.id = "rq-job-123"
        with (
            patch.object(coordinator.frappe, "get_doc", side_effect=[queued_operation, queued_operation]),
            patch.object(coordinator.frappe.db, "set_value"),
            patch.object(coordinator.frappe.db, "sql"),
            patch.object(coordinator.frappe.db, "commit"),
            patch.object(coordinator.frappe, "enqueue", return_value=job) as enqueue,
            patch.object(coordinator, "now_datetime", return_value="2026-11-18 00:00:00"),
        ):
            coordinator.dispatch_operation("operation-test")
        enqueue.assert_called_once_with(
            "dt_master.operations.worker.execute_operation",
            queue="short",
            timeout=60,
            job_name="dt-master-operation-operation-test",
            enqueue_after_commit=False,
            operation_id="operation-test",
        )

class TestOperationWorkerClaim(unittest.TestCase):
    def test_duplicate_delivery_does_not_invoke_handler(self):
        operation = MagicMock()
        operation.operation_id = "operation-test"
        operation.operation_type = "test.noop"
        operation.contract_version = "1.0.0"
        definition = get_operation("test.noop")
        handler = MagicMock()
        definition = replace(definition, handler=handler)
        cursor = MagicMock()
        cursor.rowcount = 0
        with (
            patch.object(worker.frappe, "get_doc", return_value=operation),
            patch.object(worker, "get_operation", return_value=definition),
            patch.object(worker.frappe.db, "sql"),
            patch.object(worker.frappe.db, "_cursor", cursor),
            patch.object(worker, "now_datetime", return_value="2026-11-18 00:00:00"),
            patch.object(worker.frappe.db, "commit"),
        ):
            worker.execute_operation("operation-test")
        handler.assert_not_called()



class TestOperationWorkerExecution(unittest.TestCase):
    def setUp(self):
        self.operation = MagicMock()
        self.operation.operation_id = "operation-test"
        self.operation.correlation_id = "correlation-test"
        self.operation.operation_type = "test.noop"
        self.operation.contract_version = "1.0.0"
        self.operation.request_payload = "{}"
        self.operation.target_doctype = "Infrastructure Node"
        self.operation.target_name = "NODE-TEST"
        self.operation.initiated_by = "operator@example.com"
        self.operation.initiator_type = "Human"
        self.operation.execution_identity = "frappe-worker"

    def _run_worker(self, handler):
        definition = replace(get_operation("test.noop"), handler=handler, authorize=MagicMock())
        cursor = MagicMock()
        cursor.rowcount = 1
        target = MagicMock()
        with (
            patch.object(worker.frappe, "get_doc", side_effect=[self.operation, target]),
            patch.object(worker, "get_operation", return_value=definition),
            patch.object(worker.frappe.db, "sql"),
            patch.object(worker.frappe.db, "_cursor", cursor),
            patch.object(worker.frappe.db, "commit"),
            patch.object(worker.frappe.db, "rollback") as rollback,
            patch.object(worker.frappe.db, "set_value") as set_value,
            patch.object(worker.frappe, "log_error"),
            patch.object(worker.frappe, "set_user"),
            patch.dict(worker.frappe.session, {"user": "Administrator"}),
            patch.object(worker, "now_datetime", return_value="2026-11-18 00:00:00"),
        ):
            worker.execute_operation("operation-test")
        return set_value, rollback, definition

    def test_worker_success_persists_structured_result(self):
        handler = MagicMock(return_value={"status": "ok"})
        set_value, rollback, _ = self._run_worker(handler)
        handler.assert_called_once()
        rollback.assert_not_called()
        saved_values = set_value.call_args.args[2]
        self.assertEqual(saved_values["execution_state"], "Succeeded")
        self.assertEqual(saved_values["business_state"], "Completed")
        self.assertEqual(saved_values["result_payload"], '{"status":"ok"}')

    def test_worker_failure_persists_sanitized_failure(self):
        handler = MagicMock(side_effect=RuntimeError("sensitive internal detail"))
        set_value, rollback, _ = self._run_worker(handler)
        handler.assert_called_once()
        rollback.assert_called_once()
        saved_values = set_value.call_args.args[2]
        self.assertEqual(saved_values["execution_state"], "Failed")
        self.assertEqual(saved_values["business_state"], "Failed")
        self.assertEqual(saved_values["error_code"], "HANDLER_FAILED")
        self.assertNotIn("sensitive internal detail", saved_values["error_summary"])


class TestPendingOperationDispatch(unittest.TestCase):
    def test_scheduler_dispatches_only_bounded_pending_records(self):
        pending = [MagicMock(name="OP-1"), MagicMock(name="OP-2")]
        pending[0].name = "OP-1"
        pending[1].name = "OP-2"
        with (
            patch.object(coordinator.frappe, "get_all", return_value=pending) as get_all,
            patch.object(coordinator, "dispatch_operation") as dispatch,
        ):
            result = coordinator.dispatch_pending_operations(batch_size=2)
        self.assertEqual(result, {"examined": 2, "submitted": 2})
        self.assertEqual(dispatch.call_count, 2)
        get_all.assert_called_once_with(
            "Infrastructure Operation",
            filters={"execution_state": "Pending Dispatch", "dispatch_state": "Pending"},
            fields=["name"],
            order_by="creation asc",
            limit_page_length=2,
        )

    def test_scheduler_continues_after_one_dispatch_failure(self):
        pending = [MagicMock(), MagicMock()]
        pending[0].name = "OP-FAIL"
        pending[1].name = "OP-OK"
        with (
            patch.object(coordinator.frappe, "get_all", return_value=pending),
            patch.object(coordinator, "dispatch_operation", side_effect=[RuntimeError("queue down"), None]) as dispatch,
            patch.object(coordinator.frappe, "log_error") as log_error,
        ):
            result = coordinator.dispatch_pending_operations(batch_size=2)
        self.assertEqual(result, {"examined": 2, "submitted": 1})
        self.assertEqual(dispatch.call_count, 2)
        log_error.assert_called_once()


class TestOperationAPI(unittest.TestCase):
    def test_start_api_returns_accepted_operation_and_http_202(self):
        expected = {
            "operation_id": "operation-test",
            "correlation_id": "correlation-test",
            "business_state": "Accepted",
            "execution_state": "Pending Dispatch",
            "reused": False,
            "status_url": "/api/method/dt_master.api.operations.get_operation",
        }
        with (
            patch("dt_master.api.operations.start_operation", return_value=expected) as start_operation,
            patch.object(frappe.local, "response", {}),
        ):
            from dt_master.api.operations import start

            result = start(
                operation_type="test.noop",
                target_doctype="Infrastructure Node",
                target_name="NODE-TEST",
                payload="{}",
                idempotency_key="api-request-123",
            )
        self.assertEqual(result, expected)
        self.assertEqual(frappe.local.response["http_status_code"], 202)
        start_operation.assert_called_once()

    def test_status_api_rejects_another_users_operation(self):
        from dt_master.api.operations import get_operation

        operation = MagicMock()
        operation.initiated_by = "another@example.com"
        with (
            patch.object(frappe, "get_doc", return_value=operation),
            patch.dict(frappe.session, {"user": "operator@example.com"}),
            patch.object(frappe, "get_roles", return_value=["Tenant User"]),
        ):
            with self.assertRaises(frappe.PermissionError):
                get_operation("operation-private")
