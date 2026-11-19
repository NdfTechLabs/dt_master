# Copyright (c) 2026, Dhana Technologies and contributors
# For license information, please see license.txt

import unittest
from unittest.mock import MagicMock, patch

import frappe

from dt_master.operations import reconciliation


class TestOperationReconciliation(unittest.TestCase):
    def _operation(self, *, attempt=1, maximum=3):
        return frappe._dict(
            name="OP-1", execution_state="Recovery Required",
            business_state="Accepted", attempt_count=attempt, max_attempts=maximum,
        )

    def _run(self, decision, *, target_state=None, leases=None, result=None):
        cursor = MagicMock(rowcount=1)
        with (
            patch.object(reconciliation.frappe.db, "get_value",
                         return_value=frappe._dict(attempt_count=1)),
            patch.object(reconciliation.frappe.db, "sql",
                         side_effect=[leases or [], [self._operation()]]),
            patch.object(reconciliation.frappe.db, "_cursor", cursor),
            patch.object(reconciliation.frappe.db, "set_value") as set_value,
            patch.object(reconciliation, "record_operation_event") as event,
            patch.object(reconciliation.frappe.db, "commit"),
            patch.object(reconciliation, "now_datetime", return_value="2026-11-19 12:00:00"),
            patch.dict(reconciliation.frappe.session, {"user": "operator@example.com"}),
        ):
            response = reconciliation.reconcile_operation(
                "OP-1", decision, "Inspected target", "Verified against provider state",
                target_state=target_state, result=result,
            )
        return response, set_value, event

    def test_completed_reconciliation_is_terminal_and_audited(self):
        response, set_value, event = self._run("completed", result={"status": "present"})
        values = set_value.call_args.args[2]
        self.assertEqual(response["execution_state"], "Succeeded")
        self.assertEqual(values["business_state"], "Completed")
        self.assertEqual(values["result_payload"], '{"status":"present"}')
        self.assertEqual(event.call_args.args[1], "reconciled")

    def test_failed_reconciliation_is_terminal_and_audited(self):
        response, set_value, event = self._run("failed")
        values = set_value.call_args.args[2]
        self.assertEqual(response["execution_state"], "Failed")
        self.assertEqual(values["error_code"], "OPERATOR_RECONCILED_FAILED")
        self.assertEqual(event.call_args.args[1], "reconciled")

    def test_retry_requires_target_confirmed_not_applied(self):
        with self.assertRaises(frappe.ValidationError):
            self._run("retry", target_state="unknown")

    def test_retry_resets_dispatch_without_incrementing_attempt(self):
        response, set_value, event = self._run("retry", target_state="not_applied")
        values = set_value.call_args.args[2]
        self.assertEqual(response["execution_state"], "Pending Dispatch")
        self.assertEqual(values["dispatch_state"], "Pending")
        self.assertEqual(values["attempt_count"] if "attempt_count" in values else 1, 1)
        self.assertEqual(event.call_args.kwargs["event_data"]["target_state"], "not_applied")

    def test_live_lease_blocks_reconciliation(self):
        lease = frappe._dict(expires_on="2026-11-19 12:01:00")
        with patch.object(reconciliation, "now_datetime", return_value="2026-11-19 12:00:00"):
            with patch.object(reconciliation.frappe.db, "get_value",
                              return_value=frappe._dict(attempt_count=1)):
                with patch.object(reconciliation.frappe.db, "sql",
                                  side_effect=[[lease], [self._operation()]]):
                    with self.assertRaises(frappe.ValidationError):
                        reconciliation.reconcile_operation(
                            "OP-1", "failed", "reason", "evidence"
                        )

    def test_missing_reason_or_evidence_is_rejected(self):
        with self.assertRaises(frappe.ValidationError):
            reconciliation._validate_text("", "Reason", 2000)


if __name__ == "__main__":
    unittest.main()
