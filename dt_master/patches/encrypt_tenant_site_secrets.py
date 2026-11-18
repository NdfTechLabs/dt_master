"""Move Tenant Site secrets from plaintext columns into encrypted storage.

The fields became Password fields; rows written before that still hold the
plaintext value in the column. Idempotent: masked values are skipped.
"""

import frappe
from frappe.utils.password import set_encrypted_password

SECRET_FIELDS = ("tenant_api_secret", "admin_password", "mysql_root_user_password")


def execute():
	for row in frappe.get_all("Tenant Site", fields=["name", *SECRET_FIELDS]):
		for fieldname in SECRET_FIELDS:
			value = row.get(fieldname)
			if not value or set(value) == {"*"}:
				continue
			set_encrypted_password("Tenant Site", row.name, value, fieldname)
			frappe.db.set_value("Tenant Site", row.name, fieldname, "*" * len(value), update_modified=False)
