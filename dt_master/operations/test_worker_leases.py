# Copyright (c) 2026, Dhana Technologies and contributors
# For license information, please see license.txt

import unittest
from dataclasses import replace
from unittest.mock import MagicMock, patch

import frappe

from dt_master.operations import worker
from dt_master.operations.registry import get_operation


class TestWorkerLeaseIntegration(unittest.TestCase):
    def _operation(self):
        operation = MagicMock()
        operation.name = "operation-test"
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
        return operation

    def test_resource_contention_returns_operation_to_pending_dispatch(self):
        operation = self._operation()
        target = MagicMock()
        target.name = "NODE-TEST"
        definition = get_operation("test.noop")
        cursor = MagicMock(rowcount=1)
        with (
            patch.object(worker.frappe, "get_doc", side_effect=[operation, target]),
            patch.object(worker, "get_operation", return_value=definition),
            patch.object(worker, "acquire_resource_leases", return_value=None) as acquire,
            patch.object(worker.frappe.db, "sql") as sql,
            patch.object(worker.frappe.db, "_cursor", cursor),
            patch.object(worker.frappe.db, "commit") as commit,
            patch.object(worker.frappe, "set_user", side_effect=lambda user: setattr(worker.frappe.session, "user", user)),
            patch.object(worker.frappe, "get_roles", return_value=["System Manager"]),
            patch.object(worker.frappe.db, "get_value", return_value=frappe._dict(enabled=1, user_type="System User")),
            patch.dict(worker.frappe.conf, {"dt_master_operation_execution_user": "worker@example.com"}),
            patch.dict(worker.frappe.session, {"user": "Administrator"}),
        ):
            worker.execute_operation("operation-test")

        acquire.assert_called_once_with(["node:NODE-TEST"], "operation-test", 1)
        self.assertIn("SET execution_state = 'Pending Dispatch'", sql.call_args.args[0])
        self.assertIn("dispatch_state = 'Pending'", sql.call_args.args[0])
        commit.assert_called_once()

    def test_registered_lock_resolver_is_used(self):
        operation = self._operation()
        target = MagicMock()
        resolver = MagicMock(return_value=["node:NODE-TEST", "bench:BENCH-1"])
        definition = replace(get_operation("test.noop"), resource_lock_keys=resolver)
        self.assertEqual(worker._resource_lock_keys(definition, target, operation),
                         ["node:NODE-TEST", "bench:BENCH-1"])
        resolver.assert_called_once_with(target, operation)

    def test_default_lock_resolver_serializes_node_operations(self):
        operation = self._operation()
        target = MagicMock()
        target.name = "NODE-TEST"
        self.assertEqual(worker._resource_lock_keys(get_operation("test.noop"), target, operation),
                         ["node:NODE-TEST"])

    def test_invalid_empty_lock_set_is_rejected(self):
        operation = self._operation()
        definition = replace(get_operation("test.noop"), resource_lock_keys=lambda target, operation: [])
        with self.assertRaises(frappe.ValidationError):
            worker._resource_lock_keys(definition, MagicMock(), operation)

    def test_completion_lease_check_uses_frappe_query_results(self):
        lease = {
            "lock_key": "node:NODE-TEST", "worker_token": "worker-1",
            "fencing_token": 4,
        }
        cursor = MagicMock()
        with (
            patch.object(worker.frappe.db, "sql", return_value=[{"name": "lease-row"}]) as sql,
            patch.object(worker.frappe.db, "_cursor", cursor),
            patch.object(worker, "now_datetime", return_value="2026-11-19 16:00:00"),
        ):
            self.assertTrue(worker._lock_and_verify_lease_row(lease, "operation-test", 1))

        self.assertTrue(sql.call_args.kwargs["as_dict"])
        self.assertIn("FOR UPDATE", sql.call_args.args[0])
        cursor.fetchone.assert_not_called()

    def test_completion_lease_check_rejects_missing_locked_row(self):
        lease = {
            "lock_key": "node:NODE-TEST", "worker_token": "worker-1",
            "fencing_token": 4,
        }
        with (
            patch.object(worker.frappe.db, "sql", return_value=[]) as sql,
            patch.object(worker, "now_datetime", return_value="2026-11-19 16:00:00"),
        ):
            self.assertFalse(worker._lock_and_verify_lease_row(lease, "operation-test", 1))

        self.assertTrue(sql.call_args.kwargs["as_dict"])

    def test_heartbeat_is_fenced_to_running_attempt(self):
        lease = {
            "lock_key": "node:NODE-TEST", "operation_id": "operation-test",
            "attempt": 1, "worker_token": "worker-1", "fencing_token": 4,
            "expires_on": "2026-11-19 16:01:00",
        }
        cursor = MagicMock(rowcount=1)
        with (
            patch.object(worker, "renew_resource_lease", return_value=True) as renew,
            patch.object(worker, "assert_resource_lease") as assert_lease,
            patch.object(worker.frappe.db, "sql") as sql,
            patch.object(worker.frappe.db, "_cursor", cursor),
            patch.object(worker.frappe.db, "commit") as commit,
            patch.object(worker, "now_datetime", return_value="2026-11-19 16:00:00"),
        ):
            self.assertTrue(worker._lease_checkpoint("operation-test", 1, [lease]))
        renew.assert_called_once_with(lease)
        assert_lease.assert_called_once_with(lease)
        self.assertIn("attempt_count = %s", sql.call_args.args[0])
        self.assertIn("execution_state = 'Running'", sql.call_args.args[0])
        commit.assert_called_once()

    def test_heartbeat_stops_when_lease_renewal_fails(self):
        lease = {
            "lock_key": "node:NODE-TEST", "operation_id": "operation-test",
            "attempt": 1, "worker_token": "worker-1", "fencing_token": 4,
        }
        with patch.object(worker, "renew_resource_lease", return_value=False):
            with self.assertRaises(frappe.ValidationError):
                worker._lease_checkpoint("operation-test", 1, [lease])


if __name__ == "__main__":
    unittest.main()
