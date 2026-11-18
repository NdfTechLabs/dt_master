# Copyright (c) 2026, Dhana Technologies and contributors
# For license information, please see license.txt

import hashlib
import json
import uuid

import frappe
from frappe import _
from frappe.utils import now_datetime

from dt_master.operations.registry import get_operation

SENSITIVE_KEY_PARTS = ("password", "token", "secret", "private_key", "credential")


def _reject_secret_values(value, path="payload"):
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).lower()
            if any(part in normalized for part in SENSITIVE_KEY_PARTS) and not normalized.endswith("_ref"):
                frappe.throw(
                    _("Secret material is not permitted in operation payloads ({0}). Use an approved secret reference.").format(path + "." + str(key)),
                    frappe.ValidationError,
                )
            _reject_secret_values(item, path + "." + str(key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_secret_values(item, f"{path}[{index}]")


def _canonical_payload(definition, payload):
    normalized = definition.validate_payload(payload)
    if not isinstance(normalized, dict):
        frappe.throw(_("Registered payload validators must return an object."), frappe.ValidationError)
    _reject_secret_values(normalized)
    encoded = json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return normalized, encoded, hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _get_authorized_target(definition, target_doctype, target_name):
    if not target_doctype or not target_name:
        frappe.throw(_("Target DocType and target name are required."), frappe.ValidationError)
    if target_doctype != "Infrastructure Node":
        frappe.throw(_("This operation type does not support the requested target DocType."), frappe.ValidationError)
    target = frappe.get_doc(target_doctype, target_name)
    definition.authorize(target)
    return target


def _existing_operation(actor, operation_type, target_doctype, target_name, idempotency_key):
    name = frappe.db.get_value(
        "Infrastructure Operation",
        {
            "initiated_by": actor,
            "operation_type": operation_type,
            "target_doctype": target_doctype,
            "target_name": target_name,
            "idempotency_key": idempotency_key,
        },
        "name",
    )
    return frappe.get_doc("Infrastructure Operation", name) if name else None


def _operation_response(operation, reused=False):
    return {
        "operation_id": operation.operation_id,
        "correlation_id": operation.correlation_id,
        "business_state": operation.business_state,
        "execution_state": operation.execution_state,
        "reused": bool(reused),
        "status_url": "/api/method/dt_master.api.operations.get_operation",
    }


def start_operation(operation_type, target_doctype, target_name, payload, idempotency_key, correlation_id=None):
    """Authorize, persist and dispatch a registered operation.

    The operation row is committed before queue submission. A dispatch failure
    therefore leaves a durable Pending Dispatch record for a later dispatcher.
    """
    if frappe.session.user == "Guest":
        frappe.throw(_("Authentication is required to start an operation."), frappe.PermissionError)
    if not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > 140:
        frappe.throw(_("A non-empty idempotency key of at most 140 characters is required."), frappe.ValidationError)
    if correlation_id and (not isinstance(correlation_id, str) or len(correlation_id) > 140):
        frappe.throw(_("Invalid correlation ID."), frappe.ValidationError)

    definition = get_operation(operation_type)
    target = _get_authorized_target(definition, target_doctype, target_name)
    normalized, payload_json, fingerprint = _canonical_payload(definition, payload)
    actor = frappe.session.user

    existing = _existing_operation(actor, operation_type, target_doctype, target_name, idempotency_key)
    if existing:
        if existing.request_fingerprint != fingerprint:
            frappe.throw(
                _("This idempotency key was already used with a different request."),
                frappe.ValidationError,
            )
        return _operation_response(existing, reused=True)

    operation_id = str(uuid.uuid4())
    operation = frappe.get_doc(
        {
            "doctype": "Infrastructure Operation",
            "operation_id": operation_id,
            "correlation_id": correlation_id or str(uuid.uuid4()),
            "idempotency_key": idempotency_key.strip(),
            "request_fingerprint": fingerprint,
            "contract_version": definition.contract_version,
            "operation_type": definition.operation_type,
            "target_doctype": target.doctype,
            "target_name": target.name,
            "infrastructure_node": target.name if target.doctype == "Infrastructure Node" else None,
            "initiator_type": "Human",
            "initiated_by": actor,
            "execution_identity": definition.execution_identity,
            "business_state": "Accepted",
            "execution_state": "Pending Dispatch",
            "dispatch_state": "Pending",
            "attempt_count": 0,
            "max_attempts": definition.max_attempts,
            "priority": definition.priority,
            "timeout_seconds": definition.timeout_seconds,
            "request_payload": payload_json,
        }
    )
    frappe.flags.infrastructure_operation_create = True
    try:
        operation.insert(ignore_permissions=True)
    except frappe.UniqueValidationError:
        # The database unique constraint is the concurrency authority. A
        # concurrent identical request may have won after our pre-check.
        existing = _existing_operation(actor, operation_type, target_doctype, target_name, idempotency_key)
        if existing and existing.request_fingerprint == fingerprint:
            return _operation_response(existing, reused=True)
        if existing:
            frappe.throw(_("This idempotency key was already used with a different request."), frappe.ValidationError)
        raise
    finally:
        frappe.flags.infrastructure_operation_create = False

    # Commit before enqueue to close the accepted-but-not-durable window.
    frappe.db.commit()
    try:
        dispatch_operation(operation.name)
    except Exception:
        # The durable Pending Dispatch row remains available for reconciliation.
        frappe.log_error(frappe.get_traceback(), "Infrastructure Operation dispatch failed")
    operation.reload()
    return _operation_response(operation)


def dispatch_operation(operation_id):
    """Submit one persisted operation to its registry-selected Frappe queue."""
    operation = frappe.get_doc("Infrastructure Operation", operation_id)
    if operation.execution_state not in {"Pending Dispatch", "Queued"}:
        return _operation_response(operation)
    definition = get_operation(operation.operation_type)
    if operation.contract_version != definition.contract_version:
        frappe.throw(_("Operation contract version is no longer supported."), frappe.ValidationError)

    attempted_on = now_datetime()
    frappe.db.set_value(
        "Infrastructure Operation", operation.name, "dispatch_attempted_on", attempted_on, update_modified=False
    )
    frappe.db.commit()
    job = frappe.enqueue(
        "dt_master.operations.worker.execute_operation",
        queue=definition.queue,
        timeout=definition.timeout_seconds,
        job_name=f"dt-master-operation-{operation.operation_id}",
        enqueue_after_commit=False,
        operation_id=operation.operation_id,
    )
    job_id = getattr(job, "id", None)
    # Do not overwrite Running if a fast worker claimed the Pending Dispatch
    # row before this dispatcher recorded the enqueue acknowledgement.
    frappe.db.sql(
        """UPDATE `tabInfrastructure Operation`
           SET dispatch_state = 'Dispatched', queue_name = %s, job_id = %s
           WHERE name = %s AND execution_state IN ('Pending Dispatch', 'Queued')""",
        (definition.queue, job_id, operation.name),
    )
    frappe.db.sql(
        """UPDATE `tabInfrastructure Operation`
           SET execution_state = 'Queued'
           WHERE name = %s AND execution_state = 'Pending Dispatch'""",
        (operation.name,),
    )
    frappe.db.commit()
    return _operation_response(frappe.get_doc("Infrastructure Operation", operation.name))


def dispatch_pending_operations(batch_size=25):
    """Retry a bounded batch of durable records left Pending Dispatch."""
    pending = frappe.get_all(
        "Infrastructure Operation",
        filters={"execution_state": "Pending Dispatch", "dispatch_state": "Pending"},
        fields=["name"],
        order_by="creation asc",
        limit_page_length=max(1, min(int(batch_size), 100)),
    )
    dispatched = 0
    for row in pending:
        try:
            dispatch_operation(row.name)
            dispatched += 1
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                f"Infrastructure Operation redispatch failed: {row.name}",
            )
    return {"examined": len(pending), "submitted": dispatched}
