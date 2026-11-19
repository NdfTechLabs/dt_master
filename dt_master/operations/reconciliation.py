# Copyright (c) 2026, Dhana Technologies and contributors
# For license information, please see license.txt

import json

import frappe
from frappe import _
from frappe.utils import now_datetime

from dt_master.operations.events import record_operation_event

DECISIONS = {"completed", "failed", "retry"}
MAX_REASON_LENGTH = 2000
MAX_EVIDENCE_LENGTH = 4000


def _validate_text(value, fieldname, maximum):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        frappe.throw(_("{0} is required and must be at most {1} characters.").format(fieldname, maximum),
                     frappe.ValidationError)
    return value.strip()


def reconcile_operation(operation_id, decision, reason, evidence, *, target_state=None, result=None):
    """Apply an explicit, audited operator decision to a Recovery Required operation.

    Retry is permitted only when the operator attests that the target is in the
    not-applied state. This service never inspects or mutates the target itself.
    """
    if decision not in DECISIONS:
        frappe.throw(_("Decision must be completed, failed, or retry."), frappe.ValidationError)
    reason = _validate_text(reason, "Reason", MAX_REASON_LENGTH)
    evidence = _validate_text(evidence, "Evidence", MAX_EVIDENCE_LENGTH)
    if result is not None and not isinstance(result, dict):
        frappe.throw(_("Result must be a JSON object."), frappe.ValidationError)
    if result is not None:
        encoded_result = json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        if len(encoded_result.encode("utf-8")) > 65536:
            frappe.throw(_("Result exceeds the 64 KiB limit."), frappe.ValidationError)
    else:
        encoded_result = None

    # Lock current-attempt lease rows before locking the operation, matching worker
    # and supervisor lock ordering. A live lease means execution may still be active.
    current = frappe.db.get_value("Infrastructure Operation", operation_id, ["attempt_count"], as_dict=True)
    if not current:
        frappe.throw(_("Infrastructure Operation not found."), frappe.DoesNotExistError)
    attempt = int(current.attempt_count or 0)
    leases = frappe.db.sql(
        """SELECT name, expires_on FROM `tabInfrastructure Operation Lease`
           WHERE operation = %s AND attempt = %s ORDER BY lock_key ASC FOR UPDATE""",
        (operation_id, attempt), as_dict=True,
    ) or []
    operation_rows = frappe.db.sql(
        """SELECT name, execution_state, business_state, attempt_count, max_attempts
           FROM `tabInfrastructure Operation` WHERE name = %s FOR UPDATE""",
        (operation_id,), as_dict=True,
    )
    if not operation_rows:
        frappe.throw(_("Infrastructure Operation not found."), frappe.DoesNotExistError)
    operation = operation_rows[0]
    if operation.execution_state != "Recovery Required" or int(operation.attempt_count or 0) != attempt:
        frappe.throw(_("Only the current Recovery Required attempt can be reconciled."), frappe.ValidationError)

    now = now_datetime()
    if any(row.expires_on and row.expires_on > now for row in leases):
        frappe.throw(_("A current resource lease is still active; reconciliation is blocked."), frappe.ValidationError)

    values = {
        "reconciliation_decision": None,
        "reconciled_by": frappe.session.user,
        "reconciled_on": now,
        "reconciliation_reason": reason,
        "reconciliation_evidence": evidence,
        "recovery_required": 0,
        "lock_key": None,
        "lock_owner": None,
        "lock_expires_on": None,
    }
    if decision == "completed":
        values.update({
            "reconciliation_decision": "Completed",
            "execution_state": "Succeeded",
            "business_state": "Completed",
            "result_payload": encoded_result or "{}",
            "error_code": None,
            "error_summary": None,
            "completed_on": now,
        })
        event_type = "reconciled"
    elif decision == "failed":
        values.update({
            "reconciliation_decision": "Failed",
            "execution_state": "Failed",
            "business_state": "Failed",
            "error_code": "OPERATOR_RECONCILED_FAILED",
            "error_summary": "An authorized operator reconciled the operation as failed.",
            "completed_on": now,
        })
        event_type = "reconciled"
    else:
        if target_state != "not_applied":
            frappe.throw(_("Retry requires explicit confirmation that the target state is not_applied."),
                         frappe.ValidationError)
        if int(operation.attempt_count or 0) >= int(operation.max_attempts or 0):
            frappe.throw(_("The operation has exhausted its maximum execution attempts."), frappe.ValidationError)
        values.update({
            "reconciliation_decision": "Retry Authorized",
            "execution_state": "Pending Dispatch",
            "business_state": "Accepted",
            "dispatch_state": "Pending",
            "completed_on": None,
            "heartbeat_on": None,
            "started_on": None,
            "error_code": None,
            "error_summary": None,
        })
        event_type = "reconciled"

    frappe.flags.infrastructure_operation_update = True
    try:
        frappe.db.set_value("Infrastructure Operation", operation_id, values, update_modified=True)
    finally:
        frappe.flags.infrastructure_operation_update = False

    record_operation_event(
        operation_id, event_type, phase="operator_reconciliation",
        message="An authorized operator reconciled the operation.",
        event_data={
            "decision": values["reconciliation_decision"],
            "reason": reason,
            "evidence": evidence,
            "target_state": target_state,
            "attempt": attempt,
        },
    )
    frappe.db.commit()
    return {
        "operation_id": operation_id,
        "decision": values["reconciliation_decision"],
        "execution_state": values["execution_state"],
        "attempt_count": attempt,
    }
