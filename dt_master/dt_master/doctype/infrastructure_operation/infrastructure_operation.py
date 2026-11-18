# Copyright (c) 2026, Dhana Technologies and contributors
# For license information, please see license.txt

import json
import uuid

import frappe
from frappe import _
from frappe.model.document import Document

PROTECTED_FIELDS = {
    "operation_id", "correlation_id", "idempotency_key", "request_fingerprint",
    "contract_version", "operation_type", "target_doctype", "target_name",
    "infrastructure_node", "bench_environment", "tenant_site", "initiator_type",
    "initiated_by", "execution_identity", "business_state", "execution_state",
    "dispatch_state", "attempt_count", "max_attempts", "priority", "queue_name",
    "job_id", "dispatch_attempted_on", "started_on", "heartbeat_on", "completed_on",
    "timeout_seconds", "request_payload", "result_payload", "error_code",
    "error_summary", "cancellation_requested_by", "cancellation_requested_on",
    "lock_key", "lock_owner", "lock_expires_on", "recovery_required", "recovery_reason",
}
EXECUTION_STATES = {
    "Pending Dispatch", "Queued", "Running", "Retry Waiting",
    "Cancellation Requested", "Succeeded", "Failed", "Cancelled", "Recovery Required",
}
TERMINAL_STATES = {"Succeeded", "Failed", "Cancelled"}


class InfrastructureOperation(Document):
    def autoname(self):
        if not self.operation_id:
            self.operation_id = str(uuid.uuid4())
        self.name = self.operation_id

    def before_insert(self):
        if not frappe.flags.get("infrastructure_operation_create"):
            frappe.throw(_("Infrastructure Operations can only be created by the operation coordinator."))

    def validate(self):
        if self.execution_state not in EXECUTION_STATES:
            frappe.throw(_("Invalid operation execution state."))

        if self.max_attempts < 1 or self.timeout_seconds < 1:
            frappe.throw(_("Maximum attempts and timeout must be positive."))

        if not self.operation_id:
            self.operation_id = str(uuid.uuid4())

        self._validate_json("request_payload", required=True)
        self._validate_json("result_payload", required=False)

        if not self.is_new() and not frappe.flags.get("infrastructure_operation_update"):
            old = self.get_doc_before_save()
            if old:
                changed = [field for field in PROTECTED_FIELDS if old.get(field) != self.get(field)]
                if changed:
                    frappe.throw(
                        _("Operation lifecycle fields are managed by the coordinator: {0}").format(
                            ", ".join(sorted(changed))
                        )
                    )

    def on_trash(self):
        frappe.throw(_("Infrastructure Operation records cannot be deleted."))

    def _validate_json(self, fieldname, required=False):
        value = self.get(fieldname)
        if not value:
            if required:
                frappe.throw(_("{0} is required.").format(fieldname))
            return
        try:
            parsed = json.loads(value) if isinstance(value, str) else value
        except (TypeError, ValueError):
            frappe.throw(_("{0} must contain valid JSON.").format(fieldname))
        if not isinstance(parsed, dict):
            frappe.throw(_("{0} must be a JSON object.").format(fieldname))
