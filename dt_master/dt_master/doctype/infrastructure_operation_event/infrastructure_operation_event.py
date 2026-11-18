# Copyright (c) 2026, Dhana Technologies and contributors
# For license information, please see license.txt

import json

import frappe
from frappe import _
from frappe.model.document import Document


class InfrastructureOperationEvent(Document):
    def before_insert(self):
        if not frappe.flags.get("infrastructure_operation_event_create"):
            frappe.throw(_("Operation events can only be appended by the operation coordinator."))

        if not frappe.db.exists("Infrastructure Operation", self.operation):
            frappe.throw(_("The referenced Infrastructure Operation does not exist."))

        if self.sequence < 1:
            frappe.throw(_("Event sequence must be a positive integer."))

        if frappe.db.exists(
            "Infrastructure Operation Event",
            {"operation": self.operation, "sequence": self.sequence},
        ):
            frappe.throw(_("An event with this sequence already exists for this operation."))

        self._validate_json("progress")
        self._validate_json("event_data")

    def validate(self):
        if not self.event_type:
            frappe.throw(_("Event type is required."))

        if not self.is_new():
            frappe.throw(_("Operation events are append-only and cannot be modified."))

    def on_trash(self):
        frappe.throw(_("Operation events cannot be deleted."))

    def _validate_json(self, fieldname):
        value = self.get(fieldname)
        if not value:
            return
        try:
            parsed = json.loads(value) if isinstance(value, str) else value
        except (TypeError, ValueError):
            frappe.throw(_("{0} must contain valid JSON.").format(fieldname))
        if not isinstance(parsed, dict):
            frappe.throw(_("{0} must be a JSON object.").format(fieldname))
