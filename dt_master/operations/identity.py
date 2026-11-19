# Copyright (c) 2026, Dhana Technologies and contributors
# For license information, please see license.txt

from contextlib import contextmanager

import frappe
from frappe import _

FORBIDDEN_EXECUTION_USERS = {"Administrator", "Guest"}


def validate_execution_identity(identity):
    """Resolve a registered execution principal to an enabled Frappe System User."""
    if not isinstance(identity, str) or not identity.strip():
        frappe.throw(_("An execution identity is required."), frappe.ValidationError)

    identity = identity.strip()
    if identity in FORBIDDEN_EXECUTION_USERS:
        frappe.throw(_("Administrator and Guest cannot be operation execution identities."), frappe.PermissionError)

    user = frappe.db.get_value(
        "User",
        identity,
        ["enabled", "user_type"],
        as_dict=True,
    )
    if not user:
        frappe.throw(_("The configured operation execution identity does not exist."), frappe.ValidationError)
    if not user.enabled:
        frappe.throw(_("The configured operation execution identity is disabled."), frappe.PermissionError)
    if user.user_type != "System User":
        frappe.throw(_("Operation execution identities must be Frappe System Users."), frappe.PermissionError)
    return identity


@contextmanager
def as_execution_identity(identity):
    """Run handler code as its configured principal and always restore the worker user."""
    identity = validate_execution_identity(identity)
    previous_user = frappe.session.user
    try:
        frappe.set_user(identity)
        if frappe.session.user != identity:
            frappe.throw(_("Frappe did not activate the configured execution identity."), frappe.PermissionError)
        yield identity
    finally:
        frappe.set_user(previous_user)
