# Copyright (c) 2026, Dhana Technologies and contributors
# For license information, please see license.txt

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import frappe

from dt_master.api import operations


def operation_row(operation_id="operation-1", state="Running"):
    return SimpleNamespace(
        operation_id=operation_id,
        correlation_id="correlation-1",
        operation_type="node.inspect",
        target_doctype="Infrastructure Node",
        target_name="node-1",
        initiated_by="operator@example.com",
        initiator_type="Human",
        business_state="Accepted",
        execution_state=state,
        attempt_count=1,
        started_on="2026-11-19 10:00:00",
        completed_on=None,
        creation="2026-11-19 09:59:00",
        modified="2026-11-19 10:00:00",
        error_code=None,
        error_summary=None,
        # These fields must never be selected or returned by the API.
        request_payload='{"private_key":"do-not-return"}',
        result_payload='{"credential":"do-not-return"}',
        execution_identity="root",
        lock_owner="worker-secret",
    )


class TestTaskManagerOperationListAPI(unittest.TestCase):
    def test_user_receives_only_their_active_and_recent_operations(self):
        active = [operation_row()]
        recent = [operation_row("operation-2", "Succeeded")]
        with (
            patch.dict(frappe.session, {"user": "operator@example.com"}),
            patch.object(operations.frappe, "get_roles", return_value=["System User"]),
            patch.object(operations.frappe, "get_all", side_effect=[active, recent]) as get_all,
        ):
            result = operations.get_operations()

        self.assertEqual([row["operation_id"] for row in result["active"]], ["operation-1"])
        self.assertEqual([row["operation_id"] for row in result["recent"]], ["operation-2"])
        self.assertEqual(
            get_all.call_args_list[0].kwargs["filters"]["initiated_by"],
            "operator@example.com",
        )
        self.assertEqual(
            get_all.call_args_list[1].kwargs["filters"]["initiated_by"],
            "operator@example.com",
        )
        self.assertNotIn("request_payload", result["active"][0])
        self.assertNotIn("result_payload", result["active"][0])
        self.assertNotIn("execution_identity", result["active"][0])
        self.assertNotIn("lock_owner", result["active"][0])

    def test_system_manager_receives_platform_wide_operations(self):
        with (
            patch.dict(frappe.session, {"user": "admin@example.com"}),
            patch.object(operations.frappe, "get_roles", return_value=["System Manager"]),
            patch.object(operations.frappe, "get_all", side_effect=[[], []]) as get_all,
        ):
            result = operations.get_operations(active_limit="25", recent_limit="10")

        self.assertEqual(result, {"active": [], "recent": []})
        self.assertNotIn("initiated_by", get_all.call_args_list[0].kwargs["filters"])
        self.assertNotIn("initiated_by", get_all.call_args_list[1].kwargs["filters"])

    def test_active_and_recent_queries_use_bounded_state_filters(self):
        with (
            patch.dict(frappe.session, {"user": "operator@example.com"}),
            patch.object(operations.frappe, "get_roles", return_value=[]),
            patch.object(operations.frappe, "get_all", side_effect=[[], []]) as get_all,
        ):
            operations.get_operations(active_limit=12, recent_limit=8)

        self.assertEqual(
            get_all.call_args_list[0].kwargs["filters"]["execution_state"],
            ["in", [
                "Pending Dispatch",
                "Queued",
                "Running",
                "Retry Waiting",
                "Cancellation Requested",
                "Recovery Required",
            ]],
        )
        self.assertEqual(
            get_all.call_args_list[1].kwargs["filters"]["execution_state"],
            ["in", ["Succeeded", "Failed", "Cancelled"]],
        )
        self.assertEqual(get_all.call_args_list[0].kwargs["limit_page_length"], 12)
        self.assertEqual(get_all.call_args_list[1].kwargs["limit_page_length"], 8)

    def test_active_operations_are_not_duplicated_in_recent(self):
        active = [operation_row()]
        recent = [operation_row(), operation_row("operation-2", "Failed")]
        with (
            patch.dict(frappe.session, {"user": "operator@example.com"}),
            patch.object(operations.frappe, "get_roles", return_value=[]),
            patch.object(operations.frappe, "get_all", side_effect=[active, recent]),
        ):
            result = operations.get_operations()

        self.assertEqual([row["operation_id"] for row in result["recent"]], ["operation-2"])

    def test_single_operation_response_includes_creation_metadata(self):
        operation = operation_row()
        with (
            patch.dict(frappe.session, {"user": "operator@example.com"}),
            patch.object(operations, "_get_authorized_operation", return_value=operation),
        ):
            result = operations.get_operation("operation-1")

        self.assertEqual(result["operation_id"], "operation-1")
        self.assertEqual(result["initiated_by"], "operator@example.com")
        self.assertEqual(result["initiator_type"], "Human")
        self.assertEqual(result["creation"], "2026-11-19 09:59:00")
        self.assertEqual(result["modified"], "2026-11-19 10:00:00")

    def test_guest_is_denied(self):
        with patch.dict(frappe.session, {"user": "Guest"}):
            with self.assertRaises(frappe.PermissionError):
                operations.get_operations()

    def test_limits_are_validated_and_bounded(self):
        with (
            patch.dict(frappe.session, {"user": "operator@example.com"}),
            patch.object(operations.frappe, "get_roles", return_value=[]),
        ):
            for kwargs in (
                {"active_limit": 0},
                {"active_limit": 101},
                {"recent_limit": -1},
                {"recent_limit": 51},
                {"active_limit": "invalid"},
            ):
                with self.subTest(kwargs=kwargs):
                    with self.assertRaises(frappe.ValidationError):
                        operations.get_operations(**kwargs)


if __name__ == "__main__":
    unittest.main()
