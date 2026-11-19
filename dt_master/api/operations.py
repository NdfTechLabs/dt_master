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
    operation = _get_authorized_operation(operation_id)

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
            "execution_state",
            "schema_version",
            "occurred_at",
            "phase",
            "progress",
            "message",
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
            "execution_state": row.execution_state or EVENT_EXECUTION_STATES.get(row.event_type, operation.execution_state),
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

def _operation_summary(row):
    """Return the minimal safe snapshot consumed by the global Task Manager."""
    return {
        "operation_id": row.operation_id,
        "correlation_id": row.correlation_id,
        "operation_type": row.operation_type,
        "target_doctype": row.target_doctype,
        "target_name": row.target_name,
        "initiated_by": row.initiated_by,
        "initiator_type": row.initiator_type,
        "business_state": row.business_state,
        "execution_state": row.execution_state,
        "attempt_count": row.attempt_count,
        "started_on": str(row.started_on) if row.started_on else None,
        "completed_on": str(row.completed_on) if row.completed_on else None,
        "creation": str(row.creation) if row.creation else None,
        "modified": str(row.modified) if row.modified else None,
        "error_code": row.error_code,
        "error_summary": row.error_summary,
    }


@frappe.whitelist()
def get_operations(active_limit=50, recent_limit=20):
    """List authorized active and recently completed operations for the Desk Task Manager.

    Human users see their own operations. System Managers may see platform-wide
    operations, matching the single-operation read authorization policy.
    Request/result payloads, execution identity, lock metadata and internal
    diagnostics are deliberately excluded.
    """
    if frappe.session.user == "Guest":
        frappe.throw(_("Authentication is required to view operations."), frappe.PermissionError)

    try:
        active_limit = int(active_limit)
        recent_limit = int(recent_limit)
    except (TypeError, ValueError):
        frappe.throw(_("Operation limits must be integers."), frappe.ValidationError)

    if not 1 <= active_limit <= 100 or not 0 <= recent_limit <= 50:
        frappe.throw(_("Active limit must be 1–100 and recent limit must be 0–50."), frappe.ValidationError)

    filters = {}
    if "System Manager" not in frappe.get_roles():
        filters["initiated_by"] = frappe.session.user

    active_states = [
        "Pending Dispatch",
        "Queued",
        "Running",
        "Retry Waiting",
        "Cancellation Requested",
        "Recovery Required",
    ]
    fields = [
        "operation_id",
        "correlation_id",
        "operation_type",
        "target_doctype",
        "target_name",
        "initiated_by",
        "initiator_type",
        "business_state",
        "execution_state",
        "attempt_count",
        "started_on",
        "completed_on",
        "creation",
        "modified",
        "error_code",
        "error_summary",
    ]

    active_rows = frappe.get_all(
        "Infrastructure Operation",
        filters={**filters, "execution_state": ["in", active_states]},
        fields=fields,
        order_by="modified desc",
        limit_page_length=active_limit,
    )
    recent_rows = []
    if recent_limit:
        terminal_rows = frappe.get_all(
            "Infrastructure Operation",
            filters={
                **filters,
                "execution_state": ["in", ["Succeeded", "Failed", "Cancelled"]],
            },
            fields=fields,
            order_by="modified desc",
            limit_page_length=recent_limit,
        )
        active_ids = {row.operation_id for row in active_rows}
        recent_rows = [row for row in terminal_rows if row.operation_id not in active_ids]

    return {
        "active": [_operation_summary(row) for row in active_rows],
        "recent": [_operation_summary(row) for row in recent_rows],
    }



@frappe.whitelist(methods=["POST"])
def reconcile(operation_id, decision, reason, evidence, target_state=None, result=None):
    """Reconcile an ambiguous operation as a platform System Manager."""
    if frappe.session.user == "Guest" or "System Manager" not in frappe.get_roles():
        frappe.throw(_("Only platform System Managers may reconcile operations."), frappe.PermissionError)
    if isinstance(result, str):
        try:
            result = frappe.parse_json(result)
        except Exception:
            frappe.throw(_("Result must be valid JSON."), frappe.ValidationError)
    from dt_master.operations.reconciliation import reconcile_operation
    return reconcile_operation(
        operation_id=operation_id,
        decision=decision,
        reason=reason,
        evidence=evidence,
        target_state=target_state,
        result=result,
    )
