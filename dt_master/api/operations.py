# Copyright (c) 2026, Dhana Technologies and contributors
# For license information, please see license.txt

import frappe
from frappe import _

from dt_master.operations.coordinator import start_operation
from dt_master.operations.realtime import EVENT_EXECUTION_STATES


def _get_authorized_operation(operation_id):
    operation = frappe.get_doc("Infrastructure Operation", operation_id)
    if operation.initiated_by != frappe.session.user and "System Manager" not in frappe.get_roles():
        frappe.throw(_("Not permitted to view this operation."), frappe.PermissionError)
    return operation


def _parse_event_json(value):
    if not value:
        return None
    try:
        return frappe.parse_json(value) if isinstance(value, str) else value
    except (TypeError, ValueError):
        return None


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
    operation = _get_authorized_operation(operation_id)
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



@frappe.whitelist()
def get_operation_events(operation_id, after_sequence=0, limit=50):
    """Return an authorized, sequence-ordered page of durable operation events."""
    _get_authorized_operation(operation_id)

    try:
        after_sequence = int(after_sequence)
        limit = int(limit)
    except (TypeError, ValueError):
        frappe.throw(_("Event cursor and page size must be integers."), frappe.ValidationError)

    if after_sequence < 0:
        frappe.throw(_("Event cursor cannot be negative."), frappe.ValidationError)
    if not 1 <= limit <= 100:
        frappe.throw(_("Event page size must be between 1 and 100."), frappe.ValidationError)

    rows = frappe.get_all(
        "Infrastructure Operation Event",
        filters={"operation": operation_id, "sequence": [">", after_sequence]},
        fields=[
            "name",
            "sequence",
            "event_type",
            "schema_version",
            "occurred_at",
            "phase",
            "progress",
            "message",
            "event_data",
        ],
        order_by="sequence asc",
        limit_page_length=limit + 1,
    )
    has_more = len(rows) > limit
    page = rows[:limit]
    events = [
        {
            "event_id": row.name,
            "operation_id": operation_id,
            "sequence": row.sequence,
            "event_type": row.event_type,
            "execution_state": EVENT_EXECUTION_STATES.get(row.event_type),
            "schema_version": row.schema_version,
            "occurred_at": str(row.occurred_at),
            "phase": row.phase,
            "progress": _parse_event_json(row.progress),
            "message": row.message,
        }
        for row in page
    ]

    return {
        "events": events,
        "next_after_sequence": page[-1].sequence if page else after_sequence,
        "has_more": has_more,
    }
