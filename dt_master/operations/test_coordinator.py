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
        self.operation.attempt_count = 0
        self.operation.max_attempts = 1
        self.operation.attempt_count = 0
        self.operation.max_attempts = 1
        self.operation.attempt_count = 0
        self.operation.max_attempts = 1

    def _start(self, get_doc_side_effect=None):
        with (
            patch.dict(frappe.session, {"user": "operator@example.com"}),
            patch.object(frappe, "only_for"),
            patch.object(coordinator, "_existing_operation", return_value=None),
            patch.object(coordinator, "dispatch_operation"),
            patch.object(coordinator, "record_operation_event"),
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

    def test_acceptance_records_accepted_event_before_commit(self):
        calls = []
        with (
            patch.dict(frappe.session, {"user": "operator@example.com"}),
            patch.object(frappe, "only_for"),
            patch.object(coordinator, "_existing_operation", return_value=None),
            patch.object(coordinator, "dispatch_operation"),
            patch.object(coordinator, "record_operation_event", side_effect=lambda *a, **k: calls.append("event")),
            patch.object(coordinator.frappe.db, "commit", side_effect=lambda: calls.append("commit")),
            patch.object(coordinator.frappe, "get_doc", side_effect=[self.target, self.operation]),
        ):
            coordinator.start_operation("test.noop", "Infrastructure Node", "NODE-TEST", {}, "request-accepted")

        self.assertEqual(calls[:2], ["event", "commit"])

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
            patch.object(coordinator, "record_operation_event"),
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
            patch.object(coordinator.frappe.db, "_cursor", MagicMock(rowcount=1)),
            patch.object(coordinator, "record_operation_event") as record_event,
            patch.object(coordinator.frappe.db, "commit"),
            patch.object(coordinator.frappe, "enqueue", return_value=job) as enqueue,
            patch.object(coordinator, "now_datetime", return_value="2026-11-18 00:00:00"),
        ):
            coordinator.dispatch_operation("operation-test")
        record_event.assert_called_once_with(
            "operation-test",
            "queued",
            phase="queued",
            message="Operation queued for execution.",
            event_data={"queue": "short", "job_id": "rq-job-123"},
        )
        enqueue.assert_called_once_with(
            "dt_master.operations.worker.execute_operation",
            queue="short",
            timeout=60,
            job_name="dt-master-operation-operation-test",
            enqueue_after_commit=False,
            operation_id="operation-test",
        )

    def test_dispatch_does_not_record_queued_when_worker_wins_claim_race(self):
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
            patch.object(coordinator.frappe.db, "_cursor", MagicMock(rowcount=0)),
            patch.object(coordinator, "record_operation_event") as record_event,
            patch.object(coordinator.frappe.db, "commit"),
            patch.object(coordinator.frappe, "enqueue", return_value=job),
            patch.object(coordinator, "now_datetime", return_value="2026-11-18 00:00:00"),
        ):
            coordinator.dispatch_operation("operation-test")

        record_event.assert_not_called()


class TestOperationWorkerClaim(unittest.TestCase):
    def test_duplicate_delivery_does_not_invoke_handler(self):
        operation = MagicMock()
        operation.operation_id = "operation-test"
        operation.correlation_id = "correlation-test"
        operation.operation_type = "test.noop"
        operation.contract_version = "1.0.0"
        operation.execution_state = "Queued"
        operation.request_payload = "{}"
        operation.target_doctype = "Infrastructure Node"
        operation.target_name = "NODE-TEST"
        operation.initiated_by = "operator@example.com"
        operation.initiator_type = "Human"
        operation.execution_identity = "frappe-worker"
        operation.attempt_count = 0
        operation.max_attempts = 1
        target = MagicMock()
        definition = get_operation("test.noop")
        handler = MagicMock()
        definition = replace(definition, handler=handler)
        cursor = MagicMock()
        cursor.rowcount = 0
        lease = {
            "lock_key": "node:NODE-TEST",
            "operation_id": "operation-test",
            "attempt": 1,
            "worker_token": "worker-test",
            "fencing_token": 1,
            "expires_on": "2026-11-19 16:01:00",
        }
        with (
            patch.object(worker.frappe, "get_doc", side_effect=[operation, target]),
            patch.object(worker, "get_operation", return_value=definition),
            patch.object(worker, "as_execution_identity"),
            patch.object(worker.frappe, "get_roles", return_value=["System Manager"]),
            patch.object(worker, "acquire_resource_leases", return_value=[lease]),
            patch.object(worker, "_release_leases") as release,
            patch.object(worker.frappe.db, "sql"),
            patch.object(worker.frappe.db, "_cursor", cursor),
            patch.object(worker, "now_datetime", return_value="2026-11-18 00:00:00"),
            patch.object(worker.frappe.db, "commit"),
        ):
            worker.execute_operation("operation-test")
        handler.assert_not_called()
        release.assert_called_once_with([lease])

    def test_contract_mismatch_persists_failed_event_with_terminal_transition(self):
        operation = MagicMock()
        operation.operation_id = "operation-test"
        operation.operation_type = "test.noop"
        operation.contract_version = "0.9.0"
        operation.execution_state = "Queued"
        cursor = MagicMock()
        cursor.rowcount = 1
        with (
            patch.object(worker.frappe, "get_doc", return_value=operation),
            patch.object(worker, "get_operation", return_value=get_operation("test.noop")),
            patch.object(worker.frappe.db, "sql"),
            patch.object(worker.frappe.db, "_cursor", cursor),
            patch.object(worker, "record_operation_event") as record_event,
            patch.object(worker, "acquire_resource_leases", return_value=[{"lock_key":"node:NODE-TEST","operation_id":"operation-test","attempt":1,"worker_token":"w1","fencing_token":1,"expires_on":"2026-11-18 00:01:00"}]),
            patch.object(worker, "assert_resource_lease") as assert_lease,
            patch.object(worker, "renew_resource_lease", return_value=True),
            patch.object(worker, "release_resource_lease", return_value=True),
            patch.object(worker.frappe.db, "commit") as commit,
            patch.object(worker, "now_datetime", return_value="2026-11-19 12:00:00"),
        ):
            worker.execute_operation("operation-test")

        record_event.assert_called_once_with(
            "operation-test",
            "failed",
            phase="contract_validation",
            message="Operation failed because its registered contract version is no longer supported.",
            event_data={"error_code": "UNSUPPORTED_CONTRACT_VERSION"},
        )
        commit.assert_called_once()

    def test_terminal_operation_is_unchanged_by_stale_contract_delivery(self):
        operation = MagicMock()
        operation.operation_id = "operation-test"
        operation.operation_type = "test.noop"
        operation.contract_version = "0.9.0"
        operation.execution_state = "Succeeded"
        with (
            patch.object(worker.frappe, "get_doc", return_value=operation),
            patch.object(worker, "get_operation") as get_operation,
            patch.object(worker.frappe.db, "sql") as sql,
            patch.object(worker, "_set_operation_fields") as set_fields,
        ):
            worker.execute_operation("operation-test")

        get_operation.assert_not_called()
        sql.assert_not_called()
        set_fields.assert_not_called()


class TestOperationWorkerExecution(unittest.TestCase):
    def setUp(self):
        self.operation = MagicMock()
        self.operation.operation_id = "operation-test"
        self.operation.correlation_id = "correlation-test"
        self.operation.operation_type = "test.noop"
        self.operation.contract_version = "1.0.0"
        self.operation.execution_state = "Queued"
        self.operation.request_payload = "{}"
        self.operation.target_doctype = "Infrastructure Node"
        self.operation.target_name = "NODE-TEST"
        self.operation.initiated_by = "operator@example.com"
        self.operation.initiator_type = "Human"
        self.operation.execution_identity = "frappe-worker"

    def _run_worker(self, handler, authorize=None):
        definition = replace(get_operation("test.noop"), handler=handler, authorize=authorize or MagicMock())
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
            patch.object(worker, "record_operation_event") as record_event,
            patch.object(worker, "acquire_resource_leases", return_value=[{"lock_key":"node:NODE-TEST","operation_id":"operation-test","attempt":1,"worker_token":"w1","fencing_token":1,"expires_on":"2026-11-18 00:01:00"}]),
            patch.object(worker, "assert_resource_lease"),
            patch.object(worker, "renew_resource_lease", return_value=True),
            patch.object(worker, "release_resource_lease", return_value=True),
            patch.object(worker.frappe, "log_error"),
            patch.dict(worker.frappe.conf, {"dt_master_operation_execution_user": "worker@example.com"}),
            patch.object(worker.frappe.db, "get_value", return_value=frappe._dict(enabled=1, user_type="System User")),
            patch.object(worker.frappe, "set_user", side_effect=lambda user: setattr(worker.frappe.session, "user", user)),
            patch.dict(worker.frappe.session, {"user": "Administrator"}),
            patch.object(worker, "now_datetime", return_value="2026-11-18 00:00:00"),
        ):
            worker.execute_operation("operation-test")
        return set_value, rollback, definition, record_event

    def test_worker_success_persists_structured_result(self):
        observed = {}
        handler = MagicMock(
            side_effect=lambda context, payload: (
                observed.update(user=frappe.session.user) or {"status": "ok"}
            )
        )
        authorization = MagicMock(side_effect=lambda target: observed.update(actor=frappe.session.user))
        set_value, rollback, _, record_event = self._run_worker(handler, authorize=authorization)
        handler.assert_called_once()
        self.assertEqual(handler.call_args.args[0]["execution_identity"], "frappe-worker")
        self.assertIn("heartbeat", handler.call_args.args[0])
        self.assertIn("assert_leases", handler.call_args.args[0])
        self.assertEqual(observed, {"actor": "operator@example.com", "user": "worker@example.com"})
        rollback.assert_not_called()
        saved_values = set_value.call_args.args[2]
        self.assertEqual(saved_values["execution_state"], "Succeeded")
        self.assertEqual(saved_values["business_state"], "Completed")
        self.assertEqual(saved_values["result_payload"], '{"status":"ok"}')
        self.assertEqual([call.args[1] for call in record_event.call_args_list], ["started", "completed"])
        self.assertEqual(
            record_event.call_args_list[1].kwargs,
            {
                "phase": "completed",
                "message": "Operation completed successfully.",
                "event_data": {"result_keys": ["status"]},
            },
        )

    def test_worker_failure_persists_sanitized_failure(self):
        handler = MagicMock(side_effect=RuntimeError("sensitive internal detail"))
        set_value, rollback, _, record_event = self._run_worker(handler)
        handler.assert_called_once()
        rollback.assert_called_once()
        saved_values = set_value.call_args.args[2]
        self.assertEqual(saved_values["execution_state"], "Recovery Required")
        self.assertEqual(saved_values["business_state"], "Failed")
        self.assertEqual(saved_values["error_code"], "LEASE_OR_HANDLER_FAILED")
        self.assertNotIn("sensitive internal detail", saved_values["error_summary"])
        self.assertEqual([call.args[1] for call in record_event.call_args_list], ["started", "recovery_required"])
        self.assertEqual(
            record_event.call_args_list[1].kwargs,
            {
                "phase": "execution",
                "message": "Operation requires reconciliation after a lease or execution failure.",
                "event_data": {"error_code": "LEASE_OR_HANDLER_FAILED"},
            },
        )


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
        response = {}
        with (
            patch("dt_master.api.operations.start_operation", return_value=expected) as start_operation,
            patch.object(frappe.local, "response", response),
        ):
            from dt_master.api.operations import start

            result = start(
                operation_type="test.noop",
                target_doctype="Infrastructure Node",
                target_name="NODE-TEST",
                payload="{}",
                idempotency_key="api-request-123",
            )
            self.assertEqual(response["http_status_code"], 202)
        self.assertEqual(result, expected)
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
