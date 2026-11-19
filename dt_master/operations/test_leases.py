# Copyright (c) 2026, Dhana Technologies and contributors
# For license information, please see license.txt

import unittest
from unittest.mock import MagicMock, patch

import frappe

from dt_master.operations import leases


class TestResourceLeases(unittest.TestCase):
    def test_first_acquisition_creates_fencing_token_one(self):
        cursor = MagicMock(rowcount=1)
        with (
            patch.object(leases.frappe.db, "exists", return_value=True),
            patch.object(leases.frappe.db, "sql") as sql,
            patch.object(leases.frappe.db, "_cursor", cursor),
            patch.object(leases, "now_datetime", return_value="2026-11-19 10:00:00"),
            patch.object(leases, "add_to_date", return_value="2026-11-19 10:01:00"),
        ):
            result = leases.acquire_resource_lease("node:NODE-1", "OP-1", 1, worker_token="worker-1")
        self.assertEqual(result["fencing_token"], 1)
        self.assertEqual(result["worker_token"], "worker-1")
        self.assertIn("INSERT IGNORE", sql.call_args.args[0])

    def test_missing_operation_is_rejected(self):
        with patch.object(leases.frappe.db, "exists", return_value=False):
            with self.assertRaises(frappe.ValidationError):
                leases.acquire_resource_lease("node:A", "MISSING", 1)

    def test_busy_unexpired_lease_is_not_stolen(self):
        cursor = MagicMock(rowcount=0)
        with (
            patch.object(leases.frappe.db, "exists", return_value=True),
            patch.object(leases.frappe.db, "sql"),
            patch.object(leases.frappe.db, "_cursor", cursor),
            patch.object(leases, "now_datetime", return_value="2026-11-19 10:00:00"),
            patch.object(leases, "add_to_date", return_value="2026-11-19 10:01:00"),
        ):
            result = leases.acquire_resource_lease("node:NODE-1", "OP-2", 1)
        self.assertIsNone(result)

    def test_expired_lease_takeover_increments_fencing_token(self):
        cursor = MagicMock(rowcount=0)
        with (
            patch.object(leases.frappe.db, "exists", return_value=True),
            patch.object(leases.frappe.db, "sql"),
            patch.object(leases.frappe.db, "_cursor", cursor),
            patch.object(leases, "_rowcount", side_effect=[0, 1]),
            patch.object(leases.frappe.db, "get_value", return_value=8),
            patch.object(leases, "now_datetime", return_value="2026-11-19 10:00:00"),
            patch.object(leases, "add_to_date", return_value="2026-11-19 10:01:00"),
        ):
            result = leases.acquire_resource_lease("node:NODE-1", "OP-2", 2)
        self.assertEqual(result["fencing_token"], 8)

    def test_expired_takeover_uses_conditional_update(self):
        cursor = MagicMock(rowcount=0)
        with (
            patch.object(leases.frappe.db, "exists", return_value=True),
            patch.object(leases.frappe.db, "sql") as sql,
            patch.object(leases.frappe.db, "_cursor", cursor),
            patch.object(leases.frappe.db, "get_value", return_value=2),
            patch.object(leases, "now_datetime", return_value="2026-11-19 10:00:00"),
            patch.object(leases, "add_to_date", return_value="2026-11-19 10:01:00"),
        ):
            leases.acquire_resource_lease("node:NODE-1", "OP-2", 2)
        self.assertIn("expires_on <= %s", sql.call_args.args[0])

    def test_renewal_requires_exact_unexpired_fencing_identity(self):
        cursor = MagicMock(rowcount=1)
        lease = {"lock_key":"node:NODE-1","operation_id":"OP-1","attempt":1,
                 "worker_token":"worker-1","fencing_token":3,"expires_on":"old"}
        with (
            patch.object(leases.frappe.db, "sql") as sql,
            patch.object(leases.frappe.db, "_cursor", cursor),
            patch.object(leases, "now_datetime", return_value="2026-11-19 10:00:00"),
            patch.object(leases, "add_to_date", return_value="2026-11-19 10:01:00"),
        ):
            self.assertTrue(leases.renew_resource_lease(lease))
        self.assertEqual(lease["expires_on"], "2026-11-19 10:01:00")
        self.assertIn("fencing_token = %s", sql.call_args.args[0])
        self.assertIn("expires_on > %s", sql.call_args.args[0])

    def test_stale_holder_cannot_renew(self):
        cursor = MagicMock(rowcount=0)
        lease = {"lock_key":"node:NODE-1","operation_id":"OP-1","attempt":1,
                 "worker_token":"worker-1","fencing_token":3}
        with (
            patch.object(leases.frappe.db, "sql"),
            patch.object(leases.frappe.db, "_cursor", cursor),
            patch.object(leases, "now_datetime", return_value="2026-11-19 10:00:00"),
            patch.object(leases, "add_to_date", return_value="2026-11-19 10:01:00"),
        ):
            self.assertFalse(leases.renew_resource_lease(lease))

    def test_assert_requires_matching_current_lease(self):
        lease = {"lock_key":"node:NODE-1","operation_id":"OP-1","attempt":1,
                 "worker_token":"worker-1","fencing_token":3}
        with (
            patch.object(leases.frappe.db, "exists", return_value=True) as exists,
            patch.object(leases, "now_datetime", return_value="2026-11-19 10:00:00"),
        ):
            self.assertTrue(leases.assert_resource_lease(lease))
        self.assertEqual(exists.call_args.args[1]["fencing_token"], 3)

    def test_assert_rejects_lost_lease(self):
        lease = {"lock_key":"node:NODE-1","operation_id":"OP-1","attempt":1,
                 "worker_token":"worker-1","fencing_token":2}
        with (
            patch.object(leases.frappe.db, "exists", return_value=False),
            patch.object(leases, "now_datetime", return_value="2026-11-19 10:00:00"),
        ):
            with self.assertRaises(frappe.ValidationError):
                leases.assert_resource_lease(lease)

    def test_release_is_fenced_and_retains_record(self):
        cursor = MagicMock(rowcount=1)
        lease = {"lock_key":"node:NODE-1","operation_id":"OP-1","attempt":1,
                 "worker_token":"worker-1","fencing_token":3}
        with (
            patch.object(leases.frappe.db, "sql") as sql,
            patch.object(leases.frappe.db, "_cursor", cursor),
            patch.object(leases, "now_datetime", return_value="2026-11-19 10:00:00"),
        ):
            self.assertTrue(leases.release_resource_lease(lease))
        self.assertIn("fencing_token = %s", sql.call_args.args[0])
        self.assertNotIn("DELETE", sql.call_args.args[0].upper())

    def test_multiple_resource_locks_are_sorted_and_released_on_conflict(self):
        first = {"lock_key":"node:A","operation_id":"OP-1","attempt":1,
                 "worker_token":"w1","fencing_token":1}
        with (
            patch.object(leases, "acquire_resource_lease", side_effect=[first, None]) as acquire,
            patch.object(leases, "release_resource_lease", return_value=True) as release,
        ):
            result = leases.acquire_resource_leases(["node:B", "node:A"], "OP-1", 1)
        self.assertIsNone(result)
        self.assertEqual([call.args[0] for call in acquire.call_args_list], ["node:A", "node:B"])
        release.assert_called_once_with(first)

    def test_multiple_resource_locks_return_all_fencing_identities(self):
        first = {"lock_key":"node:A","operation_id":"OP-1","attempt":1,
                 "worker_token":"w1","fencing_token":1}
        second = {"lock_key":"node:B","operation_id":"OP-1","attempt":1,
                  "worker_token":"w2","fencing_token":1}
        with patch.object(leases, "acquire_resource_lease", side_effect=[first, second]) as acquire:
            result = leases.acquire_resource_leases(["node:B", "node:A"], "OP-1", 1)
        self.assertEqual(result, [first, second])
        self.assertEqual([call.args[0] for call in acquire.call_args_list], ["node:A", "node:B"])

    def test_duplicate_lock_keys_are_rejected(self):
        with self.assertRaises(frappe.ValidationError):
            leases.acquire_resource_leases(["node:A", "node:A"], "OP-1", 1)

    def test_invalid_lease_duration_is_rejected(self):
        with self.assertRaises(frappe.ValidationError):
            leases.acquire_resource_lease("node:A", "OP-1", 1, lease_seconds=0)

    def test_invalid_lock_key_is_rejected(self):
        with self.assertRaises(frappe.ValidationError):
            leases.acquire_resource_lease(" node:A", "OP-1", 1)

    def test_transaction_is_not_committed_by_lease_service(self):
        cursor = MagicMock(rowcount=1)
        with (
            patch.object(leases.frappe.db, "exists", return_value=True),
            patch.object(leases.frappe.db, "sql"),
            patch.object(leases.frappe.db, "_cursor", cursor),
            patch.object(leases.frappe.db, "commit") as commit,
            patch.object(leases, "now_datetime", return_value="2026-11-19 10:00:00"),
            patch.object(leases, "add_to_date", return_value="2026-11-19 10:01:00"),
        ):
            leases.acquire_resource_lease("node:A", "OP-1", 1)
        commit.assert_not_called()
