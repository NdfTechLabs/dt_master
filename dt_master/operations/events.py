# Copyright (c) 2026, Dhana Technologies and contributors
# For license information, please see license.txt

import json

import frappe
from frappe import _
from frappe.utils import now_datetime

from dt_master.operations.realtime import publish_operation_event

EVENT_SCHEMA_VERSION = "1.1.0"
MAX_EVENT_PAYLOAD_BYTES = 65536
MAX_MESSAGE_LENGTH = 2000
MAX_PHASE_LENGTH = 140

SUPPORTED_EVENT_TYPES = frozenset(
    {
        "accepted",
        "queued",
        "started",
        "progress",
        "log",
        "heartbeat",
        "cancellation_requested",
        "completed",
        "failed",
        "cancelled",
        "recovery_required",
        "reconciled",
    }
)

SENSITIVE_KEY_PARTS = ("password", "token", "secret", "private_key", "credential")


def _reject_sensitive_values(value, path="event"):
    """Reject secret material while allowing references to an external secret store."""
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).lower()
            if any(part in normalized for part in SENSITIVE_KEY_PARTS) and not normalized.endswith("_ref"):
                frappe.throw(
                    _("Secret material is not permitted in operation events ({0}).").format(
                        f"{path}.{key}"
                    ),
                    frappe.ValidationError,
                )
            _reject_sensitive_values(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_sensitive_values(item, f"{path}[{index}]")


def _canonical_object(value, fieldname):
    if value is None:
        return None
    if not isinstance(value, dict):
        frappe.throw(_("{0} must be a JSON object.").format(fieldname), frappe.ValidationError)

    _reject_sensitive_values(value, fieldname)
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError):
        frappe.throw(_("{0} must contain JSON-serializable values.").format(fieldname), frappe.ValidationError)

    if len(encoded.encode("utf-8")) > MAX_EVENT_PAYLOAD_BYTES:
        frappe.throw(_("{0} exceeds the 64 KiB event payload limit.").format(fieldname), frappe.ValidationError)
    return encoded


def _next_sequence(operation_name):
    """Serialize sequence allocation by locking the parent operation row."""
    locked = frappe.db.sql(
        """
        SELECT name
        FROM `tabInfrastructure Operation`
        WHERE name = %s
        FOR UPDATE
        """,
        (operation_name,),
        as_dict=True,
    )
    if not locked:
        frappe.throw(_("The referenced Infrastructure Operation does not exist."), frappe.DoesNotExistError)

    result = frappe.db.sql(
        """
        SELECT COALESCE(MAX(sequence), 0) AS last_sequence
        FROM `tabInfrastructure Operation Event`
        WHERE operation = %s
        """,
        (operation_name,),
        as_dict=True,
    )
    last_sequence = result[0].get("last_sequence", 0) if result else 0
    return int(last_sequence or 0) + 1


def record_operation_event(
    operation_id,
    event_type,
    *,
    message=None,
    phase=None,
    progress=None,
    event_data=None,
):
    """Persist one immutable operation event inside the caller's transaction.

    This function does not commit, roll back or enqueue. It schedules a best-effort
    realtime publication for after the caller's transaction commits.
    Sequence allocation is serialized by locking the parent Infrastructure Operation row.
    """
    if not isinstance(operation_id, str) or not operation_id.strip():
        frappe.throw(_("A valid operation ID is required."), frappe.ValidationError)

    if not isinstance(event_type, str) or event_type not in SUPPORTED_EVENT_TYPES:
        frappe.throw(_("Unsupported operation event type."), frappe.ValidationError)

    if message is not None:
        if not isinstance(message, str) or len(message) > MAX_MESSAGE_LENGTH:
            frappe.throw(
                _("Event message must be text of at most {0} characters.").format(MAX_MESSAGE_LENGTH),
                frappe.ValidationError,
            )

    if phase is not None:
        if not isinstance(phase, str) or len(phase) > MAX_PHASE_LENGTH:
            frappe.throw(
                _("Event phase must be text of at most {0} characters.").format(MAX_PHASE_LENGTH),
                frappe.ValidationError,
            )

    progress_json = _canonical_object(progress, "progress")
    event_data_json = _canonical_object(event_data, "event_data")

    sequence = _next_sequence(operation_id)
    execution_state = frappe.db.get_value("Infrastructure Operation", operation_id, "execution_state")
    if not execution_state:
        frappe.throw(_("The referenced operation has no execution state."), frappe.ValidationError)
    occurred_at = now_datetime()

    event = frappe.get_doc(
        {
            "doctype": "Infrastructure Operation Event",
            "operation": operation_id,
            "sequence": sequence,
            "event_type": event_type,
            "execution_state": execution_state,
            "schema_version": EVENT_SCHEMA_VERSION,
            "occurred_at": occurred_at,
            "phase": phase,
            "progress": progress_json,
            "message": message,
            "event_data": event_data_json,
        }
    )

    frappe.flags.infrastructure_operation_event_create = True
    try:
        event.insert(ignore_permissions=True)
    finally:
        frappe.flags.infrastructure_operation_event_create = False

    result = {
        "name": event.name,
        "operation_id": operation_id,
        "sequence": sequence,
        "event_type": event_type,
        "execution_state": execution_state,
        "schema_version": EVENT_SCHEMA_VERSION,
        "occurred_at": occurred_at,
        "phase": phase,
        "progress": progress_json,
        "message": message,
        "event_data": event_data_json,
    }
    publish_operation_event(result)
    return result
