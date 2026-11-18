# Copyright (c) 2026, Dhana Technologies and contributors
# For license information, please see license.txt

import frappe
from frappe import _

from dt_master.operations.coordinator import start_operation


@frappe.whitelist(methods=["POST"])
def start(operation_type, target_doctype, target_name, payload, idempotency_key, correlation_id=None):
    """Accept and dispatch a registered asynchronous operation."""
    if isinstance(payload, str):
        try:
            payload = frappe.parse_json(payload)
        except Exception:
            frappe.throw(_("Payload must be valid JSON."), frappe.ValidationError)
    result = start_operation(
        operation_type=operation_type,
        target_doctype=target_doctype,
        target_name=target_name,
        payload=payload,
        idempotency_key=idempotency_key,
        correlation_id=correlation_id,
    )
    frappe.local.response["http_status_code"] = 202
    return result


@frappe.whitelist()
def get_operation(operation_id):
    """Return an operation only to its initiator or a platform System Manager."""
    operation = frappe.get_doc("Infrastructure Operation", operation_id)
    if operation.initiated_by != frappe.session.user and "System Manager" not in frappe.get_roles():
        frappe.throw(_("Not permitted to view this operation."), frappe.PermissionError)
    return {
        "operation_id": operation.operation_id,
        "correlation_id": operation.correlation_id,
        "operation_type": operation.operation_type,
        "target_doctype": operation.target_doctype,
        "target_name": operation.target_name,
        "business_state": operation.business_state,
        "execution_state": operation.execution_state,
        "attempt_count": operation.attempt_count,
        "started_on": operation.started_on,
        "completed_on": operation.completed_on,
        "result": frappe.parse_json(operation.result_payload) if operation.result_payload else None,
        "error_code": operation.error_code,
        "error_summary": operation.error_summary,
    }
