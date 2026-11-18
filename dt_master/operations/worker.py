# Copyright (c) 2026, Dhana Technologies and contributors
# For license information, please see license.txt

import json

import frappe
from frappe import _
from frappe.utils import now_datetime

from dt_master.operations.registry import get_operation


def _set_operation_fields(operation_id, values):
    frappe.flags.infrastructure_operation_update = True
    try:
        frappe.db.set_value("Infrastructure Operation", operation_id, values, update_modified=True)
    finally:
        frappe.flags.infrastructure_operation_update = False


def execute_operation(operation_id):
    """Claim and execute one registered operation exactly once per active attempt."""
    operation = frappe.get_doc("Infrastructure Operation", operation_id)
    definition = get_operation(operation.operation_type)
    if operation.contract_version != definition.contract_version:
        _set_operation_fields(
            operation_id,
            {
                "execution_state": "Failed",
                "business_state": "Failed",
                "error_code": "UNSUPPORTED_CONTRACT_VERSION",
                "error_summary": "The registered operation contract version has changed.",
                "completed_on": now_datetime(),
            },
        )
        return

    # Conditional update is the worker-claim compare-and-set. Duplicate RQ
    # deliveries cannot both transition this row into Running.
    claimed_at = now_datetime()
    frappe.db.sql(
        """UPDATE `tabInfrastructure Operation`
           SET execution_state = 'Running', attempt_count = attempt_count + 1,
               started_on = COALESCE(started_on, %s), heartbeat_on = %s
           WHERE name = %s
             AND execution_state IN ('Pending Dispatch', 'Queued')
             AND attempt_count < max_attempts""",
        (claimed_at, claimed_at, operation_id),
    )
    if frappe.db.affected_rows() != 1:
        frappe.db.commit()
        return
    frappe.db.commit()

    operation.reload()
    try:
        payload = json.loads(operation.request_payload or "{}")
        if not isinstance(payload, dict):
            raise ValueError("Persisted operation payload is not an object")
        target = frappe.get_doc(operation.target_doctype, operation.target_name)
        # Re-check authorization immediately before handler execution. The
        # original actor remains recorded; the worker's session is not used
        # as a substitute for that actor.
        definition.authorize(target)
        result = definition.handler(
            {
                "operation_id": operation.operation_id,
                "correlation_id": operation.correlation_id,
                "initiated_by": operation.initiated_by,
                "initiator_type": operation.initiator_type,
                "execution_identity": operation.execution_identity,
                "target_doctype": operation.target_doctype,
                "target_name": operation.target_name,
            },
            payload,
        )
        if not isinstance(result, dict):
            raise ValueError("Operation handlers must return a JSON object")
        result_json = json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        _set_operation_fields(
            operation_id,
            {
                "execution_state": "Succeeded",
                "business_state": "Completed",
                "result_payload": result_json,
                "error_code": None,
                "error_summary": None,
                "completed_on": now_datetime(),
                "heartbeat_on": now_datetime(),
            },
        )
    except Exception as exc:
        frappe.db.rollback()
        frappe.log_error(frappe.get_traceback(), f"Infrastructure Operation {operation_id} failed")
        _set_operation_fields(
            operation_id,
            {
                "execution_state": "Failed",
                "business_state": "Failed",
                "error_code": "HANDLER_FAILED",
                "error_summary": str(exc)[:500],
                "completed_on": now_datetime(),
            },
        )
