# Copyright (c) 2026, Dhana Technologies and contributors
# For license information, please see license.txt

import unittest
from unittest.mock import MagicMock, patch

import frappe

from dt_master.operations import recovery


class TestOperationRecovery(unittest.TestCase):
    def test_rejects_unbounded_batch_size(self):
        with self.assertRaises(frappe.ValidationError):
            recovery._validate_limits(201, 90)

    def test_rejects_stale_threshold_shorter_than_lease_period(self):
        with self.assertRaises(frappe.ValidationError):
            recovery._validate_limits(50, 30)

    def test_rejects_boolean_limits(self):
        with self.assertRaises(frappe.ValidationError):
            recovery._validate_limits(True, 90)

    def test_active_current_attempt_lease_prevents_recovery(self):
        operation = frappe._dict(name="OP-1", attempt_count=2)
        lease = frappe._dict(expires_on="2026-11-19 13:02:00")
        with (
            patch.object(recovery, "now_datetime", return_value="2026-11-19 13:01:00"),
            patch.object(recovery.frappe.db, "sql") as sql,
        ):
            self.assertFalse(recovery._mark_recovery_required(operation, [lease]))
        sql.assert_not_called()

    def test_expired_lease_marks_recovery_and_persists_event(self):
        operation = frappe._dict(name="OP-1", operation_id="OP-1", attempt_count=2)
        lease = frappe._dict(expires_on="2026-11-19 13:00:00")
        cursor = MagicMock(rowcount=1)
        with (
            patch.object(recovery, "now_datetime", return_value="2026-11-19 13:01:00"),
            patch.object(recovery.frappe.db, "sql") as sql,
            patch.object(recovery.frappe.db, "_cursor", cursor),
            patch.object(recovery, "record_operation_event") as event,
        ):
            self.assertTrue(recovery._mark_recovery_required(operation, [lease]))
        self.assertIn("execution_state = 'Recovery Required'", sql.call_args.args[0])
        self.assertIn("attempt_count = %s", sql.call_args.args[0])
        event.assert_called_once()
        self.assertEqual(event.call_args.args[1], "recovery_required")

    def test_missing_lease_for_running_attempt_requires_recovery(self):
        operation = frappe._dict(name="OP-1", operation_id="OP-1", attempt_count=1)
        cursor = MagicMock(rowcount=1)
        with (
            patch.object(recovery, "now_datetime", return_value="2026-11-19 13:01:00"),
            patch.object(recovery.frappe.db, "sql") as sql,
            patch.object(recovery.frappe.db, "_cursor", cursor),
            patch.object(recovery, "record_operation_event"),
        ):
            self.assertTrue(recovery._mark_recovery_required(operation, []))
        self.assertIn("WORKER_HEARTBEAT_EXPIRED", sql.call_args.args[0])

    def test_supervisor_uses_bounded_candidate_query_and_rechecks_locked_row(self):
        candidate = frappe._dict(name="OP-1", attempt_count=1)
        operation = frappe._dict(
            name="OP-1", operation_id="OP-1", attempt_count=1,
            heartbeat_on="2026-11-19 12:00:00", started_on="2026-11-19 12:00:00",
        )
        with (
            patch.object(recovery, "now_datetime", return_value="2026-11-19 13:00:00"),
            patch.object(recovery, "add_to_date", return_value="2026-11-19 12:58:30"),
            patch.object(recovery.frappe.db, "sql", return_value=[candidate]) as sql,
            patch.object(recovery, "_lock_current_attempt_leases", return_value=[]) as lock_leases,
            patch.object(recovery, "_lock_running_operation", return_value=operation),
            patch.object(recovery, "_mark_recovery_required", return_value=True) as mark,
            patch.object(recovery.frappe.db, "commit") as commit,
        ):
            result = recovery.supervise_stale_operations(batch_size=10, stale_after_seconds=90)
        self.assertEqual(result, {"examined": 1, "recovery_required": 1})
        self.assertIn("LIMIT %s", sql.call_args.args[0])
        lock_leases.assert_called_once_with("OP-1", 1)
        mark.assert_called_once_with(operation, [])
        commit.assert_called_once()

    def test_supervisor_does_not_commit_when_no_operation_is_recovered(self):
        with (
            patch.object(recovery, "now_datetime", return_value="2026-11-19 13:00:00"),
            patch.object(recovery, "add_to_date", return_value="2026-11-19 12:58:30"),
            patch.object(recovery.frappe.db, "sql", return_value=[]),
            patch.object(recovery.frappe.db, "commit") as commit,
        ):
            result = recovery.supervise_stale_operations()
        self.assertEqual(result, {"examined": 0, "recovery_required": 0})
        commit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
