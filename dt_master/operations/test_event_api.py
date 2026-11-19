# Copyright (c) 2026, Dhana Technologies and contributors
# For license information, please see license.txt

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import frappe

from dt_master.api import operations


class TestOperationEventHistoryAPI(unittest.TestCase):
    def setUp(self):
        self.operation = MagicMock()
        self.operation.initiated_by = "initiator@example.com"

    def test_initiator_can_read_paginated_event_history(self):
        rows = [
            SimpleNamespace(
                name="event-11",
                sequence=11,
                event_type="progress",
                schema_version="1.0.0",
                occurred_at="2026-11-19 12:00:00",
                phase="inspection",
                progress='{"completed":2,"total":4}',
                message="Two of four checks complete.",
                event_data='{"check":"compose"}',
            ),
            MagicMock(
                name="event-12",
                sequence=12,
                event_type="completed",
                schema_version="1.0.0",
                occurred_at="2026-11-19 12:01:00",
                phase="completed",
                progress=None,
                message="Operation completed.",
                event_data=None,
            ),
            SimpleNamespace(name="event-13"),
        ]
        with (
            patch.object(operations.frappe, "get_doc", return_value=self.operation),
            patch.dict(frappe.session, {"user": "initiator@example.com"}),
            patch.object(operations.frappe, "get_all", return_value=rows) as get_all,
        ):
            result = operations.get_operation_events("operation-test", after_sequence="10", limit="2")

        self.assertEqual(len(result["events"]), 2)
        self.assertEqual(result["events"][0]["event_id"], "event-11")
        self.assertEqual(result["events"][0]["progress"], {"completed": 2, "total": 4})
        self.assertEqual(result["events"][0]["execution_state"], "Running")
        self.assertNotIn("event_data", result["events"][0])
        self.assertEqual(result["next_after_sequence"], 12)
        self.assertTrue(result["has_more"])
        self.assertEqual(
            get_all.call_args.kwargs["filters"],
            {"operation": "operation-test", "sequence": [">", 10]},
        )
        self.assertEqual(get_all.call_args.kwargs["limit_page_length"], 3)

    def test_system_manager_can_read_another_users_event_history(self):
        with (
            patch.object(operations.frappe, "get_doc", return_value=self.operation),
            patch.dict(frappe.session, {"user": "admin@example.com"}),
            patch.object(operations.frappe, "get_roles", return_value=["System Manager"]),
            patch.object(operations.frappe, "get_all", return_value=[]),
        ):
            result = operations.get_operation_events("operation-test")
        self.assertEqual(result, {"events": [], "next_after_sequence": 0, "has_more": False})

    def test_other_user_is_denied_event_history(self):
        with (
            patch.object(operations.frappe, "get_doc", return_value=self.operation),
            patch.dict(frappe.session, {"user": "other@example.com"}),
            patch.object(operations.frappe, "get_roles", return_value=["Tenant User"]),
        ):
            with self.assertRaises(frappe.PermissionError):
                operations.get_operation_events("operation-test")

    def test_negative_cursor_is_rejected(self):
        with (
            patch.object(operations.frappe, "get_doc", return_value=self.operation),
            patch.dict(frappe.session, {"user": "initiator@example.com"}),
        ):
            with self.assertRaises(frappe.ValidationError):
                operations.get_operation_events("operation-test", after_sequence=-1)

    def test_page_size_is_bounded(self):
        with (
            patch.object(operations.frappe, "get_doc", return_value=self.operation),
            patch.dict(frappe.session, {"user": "initiator@example.com"}),
        ):
            with self.assertRaises(frappe.ValidationError):
                operations.get_operation_events("operation-test", limit=101)

    def test_non_integer_cursor_is_rejected(self):
        with (
            patch.object(operations.frappe, "get_doc", return_value=self.operation),
            patch.dict(frappe.session, {"user": "initiator@example.com"}),
        ):
            with self.assertRaises(frappe.ValidationError):
                operations.get_operation_events("operation-test", after_sequence="not-a-number")


if __name__ == "__main__":
    unittest.main()
