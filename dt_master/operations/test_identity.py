# Copyright (c) 2026, Dhana Technologies and contributors
# For license information, please see license.txt

import unittest
from unittest.mock import patch

import frappe

from dt_master.operations.identity import as_execution_identity, validate_execution_identity


class TestExecutionIdentity(unittest.TestCase):
    def test_missing_identity_is_rejected(self):
        with self.assertRaises(frappe.ValidationError):
            validate_execution_identity("")

    def test_administrator_and_guest_are_forbidden(self):
        for identity in ("Administrator", "Guest"):
            with self.subTest(identity=identity):
                with self.assertRaises(frappe.PermissionError):
                    validate_execution_identity(identity)

    def test_unknown_identity_is_rejected(self):
        with patch.object(frappe.db, "get_value", return_value=None):
            with self.assertRaises(frappe.ValidationError):
                validate_execution_identity("worker@example.com")

    def test_disabled_identity_is_rejected(self):
        with patch.object(frappe.db, "get_value", return_value=frappe._dict(enabled=0, user_type="System User")):
            with self.assertRaises(frappe.PermissionError):
                validate_execution_identity("worker@example.com")

    def test_website_user_is_rejected(self):
        with patch.object(frappe.db, "get_value", return_value=frappe._dict(enabled=1, user_type="Website User")):
            with self.assertRaises(frappe.PermissionError):
                validate_execution_identity("worker@example.com")

    def test_enabled_system_user_is_accepted(self):
        with patch.object(frappe.db, "get_value", return_value=frappe._dict(enabled=1, user_type="System User")):
            self.assertEqual(validate_execution_identity("worker@example.com"), "worker@example.com")

    def test_execution_context_switches_and_restores_worker_user(self):
        with (
            patch.object(frappe.db, "get_value", return_value=frappe._dict(enabled=1, user_type="System User")),
            patch.object(frappe, "set_user", side_effect=lambda user: setattr(frappe.session, "user", user)) as set_user,
            patch.dict(frappe.session, {"user": "Administrator"}),
        ):
            with as_execution_identity("worker@example.com"):
                self.assertEqual(frappe.session.user, "worker@example.com")
            self.assertEqual(frappe.session.user, "Administrator")
        self.assertEqual([call.args[0] for call in set_user.call_args_list],
                         ["worker@example.com", "Administrator"])

    def test_failed_identity_switch_restores_original_user(self):
        def switch(user):
            if user == "worker@example.com":
                raise RuntimeError("switch failed")
            frappe.session.user = user

        with (
            patch.object(frappe.db, "get_value", return_value=frappe._dict(enabled=1, user_type="System User")),
            patch.object(frappe, "set_user", side_effect=switch),
            patch.dict(frappe.session, {"user": "Administrator"}),
        ):
            with self.assertRaises(RuntimeError):
                with as_execution_identity("worker@example.com"):
                    self.fail("Handler must not run when identity switch fails")
            self.assertEqual(frappe.session.user, "Administrator")
