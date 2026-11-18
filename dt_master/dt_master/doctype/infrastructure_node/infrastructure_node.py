# Copyright (c) 2025, Dhana Technologies and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import get_datetime

from dt_master.docker_readiness import (
	ReadinessContractError,
	build_node_readiness_requirements,
	validate_docker_readiness_requirements,
)

DOCKER_INSPECTION_FIELDS = (
	"docker_inspection_status",
	"docker_readiness",
	"docker_inspected_on",
	"docker_inspection_snapshot",
)

DOCKER_INSPECTION_DEFAULTS = {
	"docker_inspection_status": "Not Inspected",
	"docker_readiness": "Unknown",
	"docker_inspected_on": None,
	"docker_inspection_snapshot": None,
}


class InfrastructureNode(Document):
	def validate(self):
		self._prevent_direct_docker_inspection_updates()
		self._validate_docker_readiness_requirements()

	def _validate_docker_readiness_requirements(self):
		"""Validate individually configured, typed Node readiness fields."""
		try:
			requirements = build_node_readiness_requirements(self)
			if requirements:
				validate_docker_readiness_requirements(requirements)
		except ReadinessContractError as exc:
			frappe.throw(
				frappe._("Invalid Docker readiness requirements: {0}").format(str(exc)),
				title=frappe._("Invalid Docker Readiness Requirements"),
			)

	def _prevent_direct_docker_inspection_updates(self):
		"""Keep inspection evidence server-managed, not user-editable DocType data."""
		previous = self.get_doc_before_save()

		if previous:
			changed_fields = [
				field
				for field in DOCKER_INSPECTION_FIELDS
				if self._normalize_inspection_value(field, self.get(field))
				!= self._normalize_inspection_value(field, previous.get(field))
			]
		else:
			changed_fields = [
				field
				for field, default in DOCKER_INSPECTION_DEFAULTS.items()
				if self.get(field) not in (None, "", default)
			]

		if changed_fields:
			frappe.throw(
				frappe._(
					"Docker inspection fields are managed by the server-side inspection service: {0}"
				).format(", ".join(changed_fields)),
				title=frappe._("Read-only Inspection Data"),
			)

	@staticmethod
	def _normalize_inspection_value(field, value):
		"""Normalize equivalent Frappe form and database representations."""
		if value in (None, "") and field in ("docker_inspection_status", "docker_readiness"):
			return DOCKER_INSPECTION_DEFAULTS[field]
		if field == "docker_inspected_on" and value:
			# Form submissions may carry a datetime as a string while the
			# database-loaded document exposes a Python datetime object.
			return get_datetime(value)
		return value
