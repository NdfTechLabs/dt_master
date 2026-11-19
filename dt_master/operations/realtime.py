# Copyright (c) 2026, Dhana Technologies and contributors
# For license information, please see license.txt

import json

import frappe

EVENT_EXECUTION_STATES = {
    "accepted": "Pending Dispatch",
    "queued": "Queued",
    "started": "Running",
    "progress": "Running",
    "log": "Running",
    "heartbeat": "Running",
    "cancellation_requested": "Cancellation Requested",
    "completed": "Succeeded",
    "failed": "Failed",
    "cancelled": "Cancelled",
    "recovery_required": "Recovery Required",
}


def _authorized_recipients(operation):
    """Resolve recipients from persisted operation ownership and platform role assignments."""
    recipients = {operation.initiated_by} if operation.initiated_by else set()

    system_managers = frappe.get_all(
        "Has Role",
        filters={"parenttype": "User", "role": "System Manager"},
        pluck="parent",
    )
    if system_managers:
        enabled_managers = frappe.get_all(
            "User",
            filters={"name": ["in", sorted(set(system_managers))], "enabled": 1},
            pluck="name",
        )
        recipients.update(enabled_managers)

    return sorted(user for user in recipients if user and user != "Guest")


def _json_object(value):
    if not value:
        return None
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def publish_operation_event(event):
    """Schedule a sanitized event for authorized user rooms after transaction commit.

    Recipients are resolved exclusively from persisted operation ownership and
    System Manager role assignments. No client or worker-supplied room is trusted.
    Socket delivery is best-effort; the persisted event remains authoritative.
    """
    try:
        operation = frappe.get_doc("Infrastructure Operation", event["operation_id"])
        envelope = {
            "schema_version": event["schema_version"],
            "event_id": event["name"],
            "operation_id": event["operation_id"],
            "correlation_id": operation.correlation_id,
            "sequence": event["sequence"],
            "event_type": event["event_type"],
            "occurred_at": str(event["occurred_at"]),
            "execution_state": event.get("execution_state") or EVENT_EXECUTION_STATES.get(
                event["event_type"], operation.execution_state
            ),
            "phase": event.get("phase"),
            "progress": _json_object(event.get("progress")),
            "message": event.get("message"),
        }

        recipients = _authorized_recipients(operation)
        for user in recipients:
            frappe.publish_realtime(
                "operation_event",
                envelope,
                user=user,
                after_commit=True,
            )
    except Exception:
        # A realtime outage must never roll back or fail the durable operation.
        frappe.log_error(
            frappe.get_traceback(),
            f"Operation event publication scheduling failed: {event.get('operation_id', 'unknown')}",
        )
