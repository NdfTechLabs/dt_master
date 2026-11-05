# Copyright (c) 2025, Dhana Technologies and contributors
# For license information, please see license.txt

# import frappe
from frappe.model.document import Document
from dt_master.api.script_contract import validate_app_rows


class TenantSite(Document):
	def validate(self):
		validate_app_rows(self.get("installed_apps"))
