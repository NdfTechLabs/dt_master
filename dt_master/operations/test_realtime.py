# Copyright (c) 2026, Dhana Technologies and contributors
# For license information, please see license.txt

import unittest
from unittest.mock import MagicMock, patch

import frappe

from dt_master.operations import realtime


class TestOperationRealtimeDelivery(unittest.TestCase):
    def setUp(self):
        self.operation = MagicMock()
        self.operation.initiated_by = "initiator@example.com"
        self.operation.correlation_id = "correlation-test"
        self.operation.execution_state = "Running"

    def test_recipients_are_initiator_and_enabled_system_managers(self):
        with (
            patch.object(
                realtime.frappe,
                "get_all",
                side_effect=[
                    ["admin@example.com", "initiator@example.com", "disabled@example.com"],
                    ["admin@example.com"],
                ],
            ) as get_all,
        ):
            recipients = realtime._authorized_recipients(self.operation)

        self.assertEqual(recipients, ["admin@example.com", "initiator@example.com"])
        self.assertEqual(get_all.call_count, 2)
        self.assertEqual(
            get_all.call_args_list[0].kwargs["filters"],
            {"parenttype": "User", "role": "System Manager"},
        )
        self.assertEqual(
            get_all.call_args_list[1].kwargs["filters"],
            {"name": ["in", ["admin@example.com", "disabled@example.com", "initiator@example.com"]], "enabled": 1},
        )

    def test_recipients_exclude_guest_and_deduplicate_initiator(self):
        self.operation.initiated_by = "Guest"
        with patch.object(realtime.frappe, "get_all", return_value=[]):
            self.assertEqual(realtime._authorized_recipients(self.operation), [])

    def test_publication_uses_user_rooms_and_after_commit(self):
        event = {
            "name": "event-test",
            "operation_id": "operation-test",
            "sequence": 4,
            "event_type": "queued",
            "execution_state": "Queued",
            "schema_version": "1.1.0",
            "occurred_at": "2026-11-19 12:00:00",
            "phase": "queued",
            "progress": '{"completed":1,"total":2}',
            "message": "Operation queued.",
            "event_data": '{"job_id":"private-job"}',
        }
        with (
            patch.object(realtime.frappe, "get_doc", return_value=self.operation),
            patch.object(realtime, "_authorized_recipients", return_value=["admin@example.com", "initiator@example.com"]),
            patch.object(realtime.frappe, "publish_realtime") as publish,
        ):
            realtime.publish_operation_event(event)

        expected = {
            "schema_version": "1.1.0",
            "event_id": "event-test",
            "operation_id": "operation-test",
            "correlation_id": "correlation-test",
            "sequence": 4,
            "event_type": "queued",
            "occurred_at": "2026-11-19 12:00:00",
            "execution_state": "Queued",
            "phase": "queued",
            "progress": {"completed": 1, "total": 2},
            "message": "Operation queued.",
        }
        self.assertEqual(publish.call_count, 2)
        for call in publish.call_args_list:
            self.assertEqual(call.args, ("operation_event", expected))
            self.assertTrue(call.kwargs["after_commit"])
        self.assertEqual(
            {call.kwargs["user"] for call in publish.call_args_list},
            {"admin@example.com", "initiator@example.com"},
        )
        self.assertNotIn("event_data", publish.call_args.args[1])

    def test_event_state_is_stable_even_if_operation_has_advanced(self):
        event = {
            "name": "event-queued",
            "operation_id": "operation-test",
            "sequence": 2,
            "event_type": "queued",
            "schema_version": "1.0.0",
            "occurred_at": "2026-11-19 12:00:00",
        }
        self.operation.execution_state = "Running"
        with (
            patch.object(realtime.frappe, "get_doc", return_value=self.operation),
            patch.object(realtime, "_authorized_recipients", return_value=["initiator@example.com"]),
            patch.object(realtime.frappe, "publish_realtime") as publish,
        ):
            realtime.publish_operation_event(event)

        self.assertEqual(publish.call_args.args[1]["execution_state"], "Pending Dispatch")

    def test_operation_lookup_failure_is_best_effort(self):
        event = {
            "name": "event-test",
            "operation_id": "operation-test",
            "sequence": 1,
            "event_type": "accepted",
            "schema_version": "1.0.0",
            "occurred_at": "2026-11-19 12:00:00",
        }
        with (
            patch.object(realtime.frappe, "get_doc", side_effect=RuntimeError("database unavailable")),
            patch.object(realtime.frappe, "log_error") as log_error,
        ):
            realtime.publish_operation_event(event)

        log_error.assert_called_once()

    def test_publication_failure_is_logged_and_does_not_escape(self):
        event = {
            "name": "event-test",
            "operation_id": "operation-test",
            "sequence": 1,
            "event_type": "accepted",
            "schema_version": "1.0.0",
            "occurred_at": "2026-11-19 12:00:00",
        }
        with (
            patch.object(realtime.frappe, "get_doc", return_value=self.operation),
            patch.object(realtime, "_authorized_recipients", return_value=["initiator@example.com"]),
            patch.object(realtime.frappe, "publish_realtime", side_effect=RuntimeError("redis unavailable")),
            patch.object(realtime.frappe, "log_error") as log_error,
        ):
            realtime.publish_operation_event(event)

        log_error.assert_called_once()


if __name__ == "__main__":
    unittest.main()
