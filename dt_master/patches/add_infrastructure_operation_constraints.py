# Copyright (c) 2026, Dhana Technologies and contributors
# For license information, please see license.txt

import frappe


def execute():
    """Enforce operation idempotency and per-operation event ordering in MariaDB."""
    frappe.db.add_unique(
        "Infrastructure Operation",
        ["initiated_by", "operation_type", "target_doctype", "target_name", "idempotency_key"],
        "uniq_infrastructure_operation_idempotency",
    )
    frappe.db.add_unique(
        "Infrastructure Operation Event",
        ["operation", "sequence"],
        "uniq_infrastructure_operation_event_sequence",
    )
