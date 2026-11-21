# Copyright (c) 2026, Dhana Technologies and contributors
# For license information, please see license.txt

import json

import frappe
from frappe import _
from frappe.utils import now_datetime

from dt_master.operations.events import record_operation_event
from dt_master.operations.execution_context import create_worker_execution_context
from dt_master.operations.identity import as_execution_identity
from dt_master.operations.leases import (
    acquire_resource_leases,
    assert_resource_lease,
    release_resource_lease,
    renew_resource_lease,
)
from dt_master.operations.registry import get_operation


def _set_operation_fields(operation_id, values):
    frappe.flags.infrastructure_operation_update = True
    try:
        frappe.db.set_value("Infrastructure Operation", operation_id, values, update_modified=True)
    finally:
        frappe.flags.infrastructure_operation_update = False


def _resource_lock_keys(definition, target, operation):
    resolver = definition.resource_lock_keys
    if resolver is None:
        # Until a definition supplies a narrower set, serialize all work on a Node.
        return [f"node:{target.name}"]
    keys = resolver(target, operation)
    if not isinstance(keys, (list, tuple)) or not keys:
        frappe.throw(_("Registered operations must resolve at least one resource lock key."), frappe.ValidationError)
    return keys


def _release_leases(leases):
    for lease in reversed(leases or []):
        try:
            release_resource_lease(lease)
        except Exception:
            frappe.log_error(frappe.get_traceback(), "Infrastructure Operation lease release failed")


def _lease_checkpoint(operation_id, attempt, leases):
    """Renew every held lease and persist a heartbeat; fail closed on any lost fence."""
    for lease in leases:
        if not renew_resource_lease(lease):
            frappe.throw(_("The operation lost its resource lease and must stop."), frappe.ValidationError)
        assert_resource_lease(lease)
    frappe.db.sql(
        """UPDATE `tabInfrastructure Operation`
           SET heartbeat_on = %s, lock_expires_on = %s
           WHERE name = %s AND execution_state = 'Running' AND attempt_count = %s""",
        (now_datetime(), min(item["expires_on"] for item in leases), operation_id, attempt),
    )
    if frappe.db._cursor.rowcount != 1:
        frappe.throw(_("The operation attempt is no longer current."), frappe.ValidationError)
    frappe.db.commit()
    return True


def _assert_leases(leases):
    for lease in leases:
        assert_resource_lease(lease)


def _lock_and_verify_lease_row(lease, operation_id, attempt):
    """Lock the exact current lease row and return whether it still exists."""
    rows = frappe.db.sql(
        """SELECT name FROM `tabInfrastructure Operation Lease`
           WHERE lock_key = %s AND operation = %s AND attempt = %s
             AND worker_token = %s AND fencing_token = %s AND expires_on > %s
           FOR UPDATE""",
        (lease["lock_key"], operation_id, attempt, lease["worker_token"],
         lease["fencing_token"], now_datetime()),
        as_dict=True,
    )
    return bool(rows)


def execute_operation(operation_id):
    """Claim and execute one operation while holding every declared resource lease."""
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
                operation_id, "failed", phase="contract_validation",
                message="Operation failed because its registered contract version is no longer supported.",
                event_data={"error_code": "UNSUPPORTED_CONTRACT_VERSION"},
            )
        frappe.db.commit()
        return

    payload = json.loads(operation.request_payload or "{}")
    if not isinstance(payload, dict):
        frappe.throw(_("Persisted operation payload is not an object."), frappe.ValidationError)

    target = frappe.get_doc(operation.target_doctype, operation.target_name)
    worker_user = frappe.session.user
    try:
        frappe.set_user(operation.initiated_by)
        definition.authorize(target)
    finally:
        frappe.set_user(worker_user)

    attempt = int(operation.attempt_count or 0) + 1
    leases = []
    try:
        with as_execution_identity(operation.execution_identity):
            keys = _resource_lock_keys(definition, target, operation)
            leases = acquire_resource_leases(keys, operation_id, attempt)
            if leases is None:
                # No handler ran and no mutation began. Return the durable record
                # to Pending Dispatch so the all-interval dispatcher can retry.
                frappe.db.sql(
                    """UPDATE `tabInfrastructure Operation`
                       SET execution_state = 'Pending Dispatch', dispatch_state = 'Pending',
                           error_code = 'RESOURCE_BUSY',
                           error_summary = 'A required resource is currently leased by another operation.'
                       WHERE name = %s AND execution_state IN ('Pending Dispatch', 'Queued')
                         AND attempt_count = %s""",
                    (operation_id, int(operation.attempt_count or 0)),
                )
                frappe.db.commit()
                return

            claimed_at = now_datetime()
            frappe.db.sql(
                """UPDATE `tabInfrastructure Operation`
                   SET execution_state = 'Running', dispatch_state = 'Dispatched', queue_name = %s,
                       attempt_count = attempt_count + 1,
                       started_on = COALESCE(started_on, %s), heartbeat_on = %s,
                       lock_key = %s, lock_owner = %s, lock_expires_on = %s,
                       error_code = NULL, error_summary = NULL
                   WHERE name = %s
                     AND execution_state IN ('Pending Dispatch', 'Queued')
                     AND attempt_count = %s AND attempt_count < max_attempts""",
                (definition.queue, claimed_at, claimed_at, ",".join(item["lock_key"] for item in leases),
                 frappe.session.user, min(item["expires_on"] for item in leases),
                 operation_id, int(operation.attempt_count or 0)),
            )
            if frappe.db._cursor.rowcount != 1:
                _release_leases(leases)
                frappe.db.commit()
                return

            record_operation_event(
                operation_id, "started", phase="execution",
                message="Worker acquired resource leases and started operation execution.",
                event_data={"attempt": attempt, "resource_count": len(leases)},
            )
            frappe.db.commit()

            operation.reload()
            execution_context = create_worker_execution_context(
                operation=operation,
                attempt=attempt,
                leases=leases,
                heartbeat=lambda: _lease_checkpoint(operation_id, attempt, leases),
                assert_leases=lambda: _assert_leases(leases),
            )
            execution_context["assert_leases"]()
            result = definition.handler(execution_context, payload)
            execution_context["assert_leases"]()
            if not isinstance(result, dict):
                raise ValueError("Operation handlers must return a JSON object")

            from dt_master.operations.coordinator import _reject_secret_values
            _reject_secret_values(result, "result")
            result_json = json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            if len(result_json) > 65536:
                raise ValueError("Operation result exceeds the 64 KiB result limit")

            # Lock every lease row before the terminal write. This prevents an
            # expired lease takeover from racing the fenced completion transaction.
            for lease in leases:
                if not _lock_and_verify_lease_row(lease, operation_id, attempt):
                    frappe.throw(_("The operation lost its resource lease before completion."), frappe.ValidationError)

            completed_on = now_datetime()
            _set_operation_fields(operation_id, {
                "execution_state": "Succeeded", "business_state": "Completed",
                "result_payload": result_json, "error_code": None, "error_summary": None,
                "completed_on": completed_on, "heartbeat_on": completed_on,
                "lock_key": None, "lock_owner": None, "lock_expires_on": None,
            })
            record_operation_event(
                operation_id, "completed", phase="completed",
                message="Operation completed successfully.",
                event_data={"result_keys": sorted(str(key) for key in result)},
            )
            _release_leases(leases)
            frappe.db.commit()
    except Exception as exc:
        frappe.db.rollback()
        _release_leases(leases)
        frappe.db.commit()
        frappe.log_error(frappe.get_traceback(), f"Infrastructure Operation {operation_id} failed")
        completed_on = now_datetime()

        from dt_master.operations.outcomes import resolve_operation_disposition

        disposition_fields = resolve_operation_disposition(exc, lease_acquired=bool(leases))
        disposition = disposition_fields["execution_state"]
        error_code = disposition_fields["error_code"]
        error_summary = disposition_fields["error_summary"]
        recovery_required = disposition_fields["recovery_required"]
        recovery_reason = disposition_fields["recovery_reason"]
        failure_result = disposition_fields.get("result_payload")
        failure_result_json = None
        if isinstance(failure_result, dict):
            try:
                from dt_master.operations.coordinator import _reject_secret_values
                _reject_secret_values(failure_result, "failure_result")
                candidate_json = json.dumps(
                    failure_result, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                )
                if len(candidate_json.encode("utf-8")) <= 65536:
                    failure_result_json = candidate_json
            except (TypeError, ValueError):
                failure_result_json = None
        event_status = "recovery_required" if recovery_required else "failed"
        event_message = (
            "Operation requires reconciliation before it can be safely retried."
            if recovery_required else
            "Operation failed without an uncertain mutation outcome."
        )

        _set_operation_fields(operation_id, {
            "execution_state": disposition,
            "business_state": "Failed",
            "error_code": error_code,
            "error_summary": error_summary,
            "completed_on": completed_on,
            "recovery_required": 1 if recovery_required else 0,
            "recovery_reason": recovery_reason,
            "result_payload": failure_result_json,
            "lock_key": None, "lock_owner": None, "lock_expires_on": None,
        })
        record_operation_event(
            operation_id,
            event_status,
            phase="execution",
            message=event_message,
            event_data={
                "error_code": error_code,
                "result_available": failure_result_json is not None,
                "result": failure_result if failure_result_json is not None else None,
            },
        )
        frappe.db.commit()
