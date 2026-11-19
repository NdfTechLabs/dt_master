# Copyright (c) 2026, Dhana Technologies and contributors
# For license information, please see license.txt

import unittest
from unittest.mock import MagicMock, patch

import frappe

from dt_master.operations import events


class TestOperationEvents(unittest.TestCase):
    def setUp(self):
        self.operation_id = "operation-test"
        self.lock_result = [{"name": self.operation_id}]
        self.event_doc = MagicMock()
        self.event_doc.name = "event-test"

    def _record(self, event_type="progress", **kwargs):
        with (
            patch.object(events.frappe.db, "sql", side_effect=[self.lock_result, [{"last_sequence": 0}]]),
            patch.object(events.frappe, "get_doc", return_value=self.event_doc),
            patch.object(events, "now_datetime", return_value="2026-11-19 10:00:00"),
        ):
            return events.record_operation_event(self.operation_id, event_type, **kwargs)

    def test_persists_event_with_first_sequence_and_schema_version(self):
        result = self._record("accepted", message="Operation accepted")
        self.assertEqual(result["sequence"], 1)
        self.assertEqual(result["event_type"], "accepted")
        self.assertEqual(result["schema_version"], "1.0.0")
        self.event_doc.insert.assert_called_once_with(ignore_permissions=True)

    def test_next_event_sequence_increments_from_existing_history(self):
        with (
            patch.object(
                events.frappe.db,
                "sql",
                side_effect=[self.lock_result, [{"last_sequence": 7}]],
            ),
            patch.object(events.frappe, "get_doc", return_value=self.event_doc),
            patch.object(events, "now_datetime", return_value="2026-11-19 10:00:00"),
        ):
            result = events.record_operation_event(self.operation_id, "progress")
        self.assertEqual(result["sequence"], 8)

    def test_sequence_is_scoped_to_each_operation(self):
        with (
            patch.object(
                events.frappe.db,
                "sql",
                side_effect=[
                    [{"name": "operation-a"}],
                    [{"last_sequence": 3}],
                    [{"name": "operation-b"}],
                    [{"last_sequence": 0}],
                ],
            ),
            patch.object(events.frappe, "get_doc", return_value=self.event_doc),
            patch.object(events, "now_datetime", return_value="2026-11-19 10:00:00"),
        ):
            first = events.record_operation_event("operation-a", "started")
            second = events.record_operation_event("operation-b", "started")
        self.assertEqual(first["sequence"], 4)
        self.assertEqual(second["sequence"], 1)

    def test_parent_operation_is_locked_before_sequence_is_read(self):
        with (
            patch.object(
                events.frappe.db,
                "sql",
                side_effect=[self.lock_result, [{"last_sequence": 0}]],
            ) as sql,
            patch.object(events.frappe, "get_doc", return_value=self.event_doc),
            patch.object(events, "now_datetime", return_value="2026-11-19 10:00:00"),
        ):
            events.record_operation_event(self.operation_id, "started")
        self.assertIn("FOR UPDATE", sql.call_args_list[0].args[0])
        self.assertIn("MAX(sequence)", sql.call_args_list[1].args[0])

    def test_missing_parent_operation_is_rejected(self):
        with patch.object(events.frappe.db, "sql", return_value=[]):
            with self.assertRaises(frappe.DoesNotExistError):
                events.record_operation_event(self.operation_id, "started")

    def test_unsupported_event_type_is_rejected(self):
        with self.assertRaises(frappe.ValidationError):
            events.record_operation_event(self.operation_id, "arbitrary")

    def test_non_object_progress_is_rejected(self):
        with self.assertRaises(frappe.ValidationError):
            events.record_operation_event(self.operation_id, "progress", progress=50)

    def test_non_object_event_data_is_rejected(self):
        with self.assertRaises(frappe.ValidationError):
            events.record_operation_event(self.operation_id, "progress", event_data=["bad"])

    def test_secret_material_is_rejected_recursively(self):
        with self.assertRaises(frappe.ValidationError):
            events.record_operation_event(
                self.operation_id,
                "progress",
                event_data={"details": [{"api_token": "do-not-store"}]},
            )

    def test_secret_reference_is_allowed(self):
        result = self._record("progress", event_data={"database_secret_ref": "vault://db"})
        self.assertIn("database_secret_ref", result["event_data"])

    def test_event_payload_size_is_bounded(self):
        with self.assertRaises(frappe.ValidationError):
            events.record_operation_event(
                self.operation_id,
                "progress",
                event_data={"details": "x" * events.MAX_EVENT_PAYLOAD_BYTES},
            )

    def test_message_and_phase_are_bounded(self):
        with self.assertRaises(frappe.ValidationError):
            events.record_operation_event(self.operation_id, "progress", message="x" * 2001)
        with self.assertRaises(frappe.ValidationError):
            events.record_operation_event(self.operation_id, "progress", phase="x" * 141)

    def test_service_does_not_commit_or_publish(self):
        with (
            patch.object(
                events.frappe.db,
                "sql",
                side_effect=[self.lock_result, [{"last_sequence": 0}]],
            ),
            patch.object(events.frappe, "get_doc", return_value=self.event_doc),
            patch.object(events.frappe.db, "commit") as commit,
            patch.object(events.frappe, "publish_realtime") as publish,
            patch.object(events, "now_datetime", return_value="2026-11-19 10:00:00"),
        ):
            events.record_operation_event(self.operation_id, "started")
        commit.assert_not_called()
        publish.assert_not_called()

    def test_event_creation_flag_is_cleared_after_insert_failure(self):
        self.event_doc.insert.side_effect = RuntimeError("insert failed")
        with (
            patch.object(
                events.frappe.db,
                "sql",
                side_effect=[self.lock_result, [{"last_sequence": 0}]],
            ),
            patch.object(events.frappe, "get_doc", return_value=self.event_doc),
            patch.object(events, "now_datetime", return_value="2026-11-19 10:00:00"),
        ):
            with self.assertRaises(RuntimeError):
                events.record_operation_event(self.operation_id, "started")
        self.assertFalse(frappe.flags.infrastructure_operation_event_create)


if __name__ == "__main__":
    unittest.main()
