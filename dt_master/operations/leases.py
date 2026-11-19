# Copyright (c) 2026, Dhana Technologies and contributors
# For license information, please see license.txt

"""Durable resource-scoped operation leases with monotonically increasing fencing tokens.

Transaction ownership remains with the caller. These functions never commit.
"""

import uuid

import frappe
from frappe import _
from frappe.utils import add_to_date, now_datetime

LEASE_DOCTYPE = "Infrastructure Operation Lease"
DEFAULT_LEASE_SECONDS = 60
MAX_LEASE_SECONDS = 900
MAX_LOCK_KEY_LENGTH = 140


def _validate_lock_key(lock_key):
    if not isinstance(lock_key, str) or not lock_key or len(lock_key) > MAX_LOCK_KEY_LENGTH:
        frappe.throw(_("Resource lock key must be a non-empty string of at most 140 characters."))
    if lock_key.strip() != lock_key or any(ord(char) < 32 for char in lock_key):
        frappe.throw(_("Resource lock key contains invalid whitespace or control characters."))
    return lock_key


def _validate_lease_seconds(lease_seconds):
    if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int):
        frappe.throw(_("Lease duration must be an integer number of seconds."))
    if not 1 <= lease_seconds <= MAX_LEASE_SECONDS:
        frappe.throw(_("Lease duration must be between 1 and 900 seconds."))
    return lease_seconds


def _rowcount():
    return int(getattr(frappe.db._cursor, "rowcount", 0) or 0)


def acquire_resource_lease(lock_key, operation_id, attempt, *, lease_seconds=DEFAULT_LEASE_SECONDS, worker_token=None):
    """Acquire an unowned/expired resource lease; return its fencing identity or None if busy."""
    lock_key = _validate_lock_key(lock_key)
    lease_seconds = _validate_lease_seconds(lease_seconds)
    if not operation_id or isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
        frappe.throw(_("A valid operation and positive attempt number are required."))

    now = now_datetime()
    expires = add_to_date(now, seconds=lease_seconds)
    worker_token = worker_token or str(uuid.uuid4())
    if not isinstance(worker_token, str) or not worker_token or len(worker_token) > 140:
        frappe.throw(_("Worker token is invalid."))

    # First writer creates the durable lock row. INSERT IGNORE plus the unique
    # lock_key constraint makes simultaneous first acquisition race-safe.
    frappe.db.sql(
        """INSERT IGNORE INTO `tabInfrastructure Operation Lease`
           (name, creation, modified, owner, modified_by, docstatus, lock_key,
            operation, attempt, worker_token, fencing_token, acquired_on, expires_on)
           VALUES (%s, %s, %s, %s, %s, 0, %s, %s, %s, %s, 1, %s, %s)""",
        (str(uuid.uuid4()), now, now, frappe.session.user, frappe.session.user,
         lock_key, operation_id, attempt, worker_token, now, expires),
    )
    if _rowcount() == 1:
        return {
            "lock_key": lock_key, "operation_id": operation_id, "attempt": attempt,
            "worker_token": worker_token, "fencing_token": 1, "acquired_on": now,
            "expires_on": expires,
        }

    # An expired lease may be taken over exactly once. The conditional UPDATE
    # increments the fencing token atomically, invalidating every stale holder.
    frappe.db.sql(
        """UPDATE `tabInfrastructure Operation Lease`
           SET operation = %s, attempt = %s, worker_token = %s,
               fencing_token = fencing_token + 1, acquired_on = %s, expires_on = %s,
               modified = %s, modified_by = %s
           WHERE lock_key = %s AND expires_on <= %s""",
        (operation_id, attempt, worker_token, now, expires, now, frappe.session.user, lock_key, now),
    )
    if _rowcount() != 1:
        return None

    row = frappe.db.get_value(LEASE_DOCTYPE, {"lock_key": lock_key}, "fencing_token")
    return {
        "lock_key": lock_key, "operation_id": operation_id, "attempt": attempt,
        "worker_token": worker_token, "fencing_token": int(row), "acquired_on": now,
        "expires_on": expires,
    }


def acquire_resource_leases(lock_keys, operation_id, attempt, *, lease_seconds=DEFAULT_LEASE_SECONDS):
    """Acquire a deterministic lock set; release newly acquired leases if any key is busy."""
    if not isinstance(lock_keys, (list, tuple)) or not lock_keys:
        frappe.throw(_("At least one resource lock key is required."))
    normalized = sorted({_validate_lock_key(key) for key in lock_keys})
    if len(normalized) != len(lock_keys):
        frappe.throw(_("Resource lock keys must be unique."))
    acquired = []
    for key in normalized:
        lease = acquire_resource_lease(key, operation_id, attempt, lease_seconds=lease_seconds)
        if lease is None:
            for held in reversed(acquired):
                release_resource_lease(held)
            return None
        acquired.append(lease)
    return acquired


def renew_resource_lease(lease, *, lease_seconds=DEFAULT_LEASE_SECONDS):
    """Renew only the exact current, unexpired lease holder."""
    lease_seconds = _validate_lease_seconds(lease_seconds)
    now = now_datetime()
    expires = add_to_date(now, seconds=lease_seconds)
    frappe.db.sql(
        """UPDATE `tabInfrastructure Operation Lease`
           SET expires_on = %s, modified = %s, modified_by = %s
           WHERE lock_key = %s AND operation = %s AND attempt = %s
             AND worker_token = %s AND fencing_token = %s AND expires_on > %s""",
        (expires, now, frappe.session.user, lease["lock_key"], lease["operation_id"],
         lease["attempt"], lease["worker_token"], lease["fencing_token"], now),
    )
    if _rowcount() != 1:
        return False
    lease["expires_on"] = expires
    return True


def assert_resource_lease(lease):
    """Raise if the lease is expired, superseded, or belongs to another attempt."""
    now = now_datetime()
    exists = frappe.db.exists(
        LEASE_DOCTYPE,
        {
            "lock_key": lease["lock_key"],
            "operation": lease["operation_id"],
            "attempt": lease["attempt"],
            "worker_token": lease["worker_token"],
            "fencing_token": lease["fencing_token"],
            "expires_on": (">", now),
        },
    )
    if not exists:
        frappe.throw(_("The operation no longer owns a valid resource lease."), frappe.ConflictError)
    return True


def release_resource_lease(lease):
    """Expire the exact holder without deleting its fencing-token history."""
    now = now_datetime()
    frappe.db.sql(
        """UPDATE `tabInfrastructure Operation Lease`
           SET expires_on = %s, modified = %s, modified_by = %s
           WHERE lock_key = %s AND operation = %s AND attempt = %s
             AND worker_token = %s AND fencing_token = %s""",
        (now, now, frappe.session.user, lease["lock_key"], lease["operation_id"],
         lease["attempt"], lease["worker_token"], lease["fencing_token"]),
    )
    return _rowcount() == 1
