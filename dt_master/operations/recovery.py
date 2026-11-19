# Copyright (c) 2026, Dhana Technologies and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import add_to_date, now_datetime

from dt_master.operations.events import record_operation_event
from dt_master.operations.leases import DEFAULT_LEASE_SECONDS

DEFAULT_STALE_AFTER_SECONDS = DEFAULT_LEASE_SECONDS
DEFAULT_BATCH_SIZE = 50
MAX_BATCH_SIZE = 200
MAX_STALE_AFTER_SECONDS = 900


def _validate_limits(batch_size, stale_after_seconds):
    if isinstance(batch_size, bool) or not isinstance(batch_size, int) or not 1 <= batch_size <= MAX_BATCH_SIZE:
        frappe.throw(_("Recovery batch size must be between 1 and {0}.").format(MAX_BATCH_SIZE),
                     frappe.ValidationError)
    if (
        isinstance(stale_after_seconds, bool)
        or not isinstance(stale_after_seconds, int)
        or not DEFAULT_STALE_AFTER_SECONDS <= stale_after_seconds <= MAX_STALE_AFTER_SECONDS
    ):
        frappe.throw(
            _("Stale-operation threshold must be between {0} and {1} seconds.").format(
                DEFAULT_STALE_AFTER_SECONDS, MAX_STALE_AFTER_SECONDS
            ),
            frappe.ValidationError,
        )


def _lock_running_operation(operation_name):
    rows = frappe.db.sql(
        """
        SELECT name, operation_id, attempt_count, heartbeat_on, started_on
        FROM `tabInfrastructure Operation`
        WHERE name = %s AND execution_state = 'Running'
        FOR UPDATE
        """,
        (operation_name,),
        as_dict=True,
    )
    return rows[0] if rows else None


def _lock_current_attempt_leases(operation_name, attempt):
    return frappe.db.sql(
        """
        SELECT name, lock_key, operation, attempt, worker_token, fencing_token, expires_on
        FROM `tabInfrastructure Operation Lease`
        WHERE operation = %s AND attempt = %s
        ORDER BY lock_key ASC
        FOR UPDATE
        """,
        (operation_name, attempt),
        as_dict=True,
    ) or []


def _mark_recovery_required(operation):
    operation_name = operation.name
    attempt = int(operation.attempt_count or 0)
    now = now_datetime()

    # Locking lease rows serializes this decision with lease renewal. If a
    # worker renewed before this lock, the unexpired lease prevents recovery.
    leases = _lock_current_attempt_leases(operation_name, attempt)
    active_leases = [lease for lease in leases if lease.expires_on and lease.expires_on > now]
    if active_leases:
        return False

    reason = (
        "The worker heartbeat is stale and no unexpired resource lease exists "
        "for the current attempt. The execution outcome is unknown and requires reconciliation."
    )
    frappe.db.sql(
        """
        UPDATE `tabInfrastructure Operation`
        SET execution_state = 'Recovery Required',
            business_state = 'Failed',
            recovery_required = 1,
            recovery_reason = %s,
            error_code = 'WORKER_HEARTBEAT_EXPIRED',
            error_summary = 'Worker heartbeat and resource lease expired; reconcile the target before retrying.',
            lock_key = NULL,
            lock_owner = NULL,
            lock_expires_on = NULL
        WHERE name = %s
          AND execution_state = 'Running'
          AND attempt_count = %s
        """,
        (reason, operation_name, attempt),
    )
    if int(getattr(frappe.db._cursor, "rowcount", 0) or 0) != 1:
        return False

    record_operation_event(
        operation_name,
        "recovery_required",
        phase="worker_supervision",
        message="Worker heartbeat and resource lease expired; operation requires reconciliation.",
        event_data={
            "error_code": "WORKER_HEARTBEAT_EXPIRED",
            "attempt": attempt,
            "expired_lease_count": len(leases),
        },
    )
    return True


def supervise_stale_operations(batch_size=DEFAULT_BATCH_SIZE, stale_after_seconds=DEFAULT_STALE_AFTER_SECONDS):
    """Move abandoned Running operations to Recovery Required, never retry them.

    A stale heartbeat alone is insufficient: the current attempt's lease rows
    are locked and checked. Any unexpired lease means the worker may still be
    active. An expired/missing lease plus a stale heartbeat is ambiguous and
    requires explicit reconciliation.
    """
    _validate_limits(batch_size, stale_after_seconds)
    now = now_datetime()
    cutoff = add_to_date(now, seconds=-stale_after_seconds)
    candidates = frappe.db.sql(
        """
        SELECT name
        FROM `tabInfrastructure Operation`
        WHERE execution_state = 'Running'
          AND COALESCE(heartbeat_on, started_on) <= %s
        ORDER BY COALESCE(heartbeat_on, started_on) ASC
        LIMIT %s
        """,
        (cutoff, batch_size),
        as_dict=True,
    ) or []

    examined = 0
    recovered = 0
    for candidate in candidates:
        operation = _lock_running_operation(candidate.name)
        if not operation:
            continue
        heartbeat = operation.heartbeat_on or operation.started_on
        if not heartbeat or heartbeat > cutoff:
            continue
        examined += 1
        if _mark_recovery_required(operation):
            recovered += 1

    if recovered:
        frappe.db.commit()
    return {"examined": examined, "recovery_required": recovered}
