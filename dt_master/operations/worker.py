# Copyright (c) 2026, Dhana Technologies and contributors
# For license information, please see license.txt

import json

import frappe
from frappe.utils import now_datetime

from dt_master.operations.events import record_operation_event
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
    if operation.execution_state not in {"Pending Dispatch", "Queued"}:
        return

    definition = get_operation(operation.operation_type)
    if operation.contract_version != definition.contract_version:
        frappe.db.sql(
            """UPDATE `tabInfrastructure Operation`
               SET execution_state = 'Failed', business_state = 'Failed',
                   error_code = 'UNSUPPORTED_CONTRACT_VERSION',
                   error_summary = 'The registered operation contract version has changed.',
                   completed_on = %s
               WHERE name = %s
                 AND execution_state IN ('Pending Dispatch', 'Queued')""",
            (now_datetime(), operation_id),
        )
        if frappe.db._cursor.rowcount == 1:
            record_operation_event(
                operation_id,
                "failed",
                phase="contract_validation",
                message="Operation failed because its registered contract version is no longer supported.",
                event_data={"error_code": "UNSUPPORTED_CONTRACT_VERSION"},
            )
        frappe.db.commit()
        return

    claimed_at = now_datetime()
    frappe.db.sql(
        """UPDATE `tabInfrastructure Operation`
           SET execution_state = 'Running', dispatch_state = 'Dispatched', queue_name = %s,
               attempt_count = attempt_count + 1,
               started_on = COALESCE(started_on, %s), heartbeat_on = %s
           WHERE name = %s
             AND execution_state IN ('Pending Dispatch', 'Queued')
             AND attempt_count < max_attempts""",
        (definition.queue, claimed_at, claimed_at, operation_id),
    )
    if frappe.db._cursor.rowcount != 1:
        frappe.db.commit()
        return

    record_operation_event(
        operation_id,
        "started",
        phase="execution",
        message="Worker started operation execution.",
        event_data={"attempt": int(operation.attempt_count or 0) + 1},
    )
    # The Running transition and started event are committed atomically.
    frappe.db.commit()

    operation.reload()
    try:
        payload = json.loads(operation.request_payload or "{}")
        if not isinstance(payload, dict):
            raise ValueError("Persisted operation payload is not an object")
        target = frappe.get_doc(operation.target_doctype, operation.target_name)
        worker_user = frappe.session.user
        try:
            frappe.set_user(operation.initiated_by)
            definition.authorize(target)
        finally:
            frappe.set_user(worker_user)
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
        from dt_master.operations.coordinator import _reject_secret_values

        _reject_secret_values(result, "result")
        result_json = json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        if len(result_json) > 65536:
            raise ValueError("Operation result exceeds the 64 KiB result limit")
        completed_on = now_datetime()
        _set_operation_fields(
            operation_id,
            {
                "execution_state": "Succeeded",
                "business_state": "Completed",
                "result_payload": result_json,
                "error_code": None,
                "error_summary": None,
                "completed_on": completed_on,
                "heartbeat_on": completed_on,
            },
        )
        record_operation_event(
            operation_id,
            "completed",
            phase="completed",
            message="Operation completed successfully.",
            event_data={"result_keys": sorted(str(key) for key in result)},
        )
        frappe.db.commit()
    except Exception:
        frappe.db.rollback()
        frappe.log_error(frappe.get_traceback(), f"Infrastructure Operation {operation_id} failed")
        completed_on = now_datetime()
        _set_operation_fields(
            operation_id,
            {
                "execution_state": "Failed",
                "business_state": "Failed",
                "error_code": "HANDLER_FAILED",
                "error_summary": "The registered operation handler failed. See the restricted server error log.",
                "completed_on": completed_on,
            },
        )
        record_operation_event(
            operation_id,
            "failed",
            phase="execution",
            message="The registered operation handler failed.",
            event_data={"error_code": "HANDLER_FAILED"},
        )
        frappe.db.commit()
