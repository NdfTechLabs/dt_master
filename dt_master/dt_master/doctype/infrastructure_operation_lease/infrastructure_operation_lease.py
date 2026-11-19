# Copyright (c) 2026, Dhana Technologies and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class InfrastructureOperationLease(Document):
    def before_insert(self):
        if not frappe.flags.get("infrastructure_operation_lease_create"):
            frappe.throw(_("Operation leases can only be created by the lease service."))

    def validate(self):
        if not self.lock_key or not self.operation or not self.worker_token:
            frappe.throw(_("Lease identity fields are required."))

    def on_trash(self):
        frappe.throw(_("Operation lease records cannot be deleted."))
