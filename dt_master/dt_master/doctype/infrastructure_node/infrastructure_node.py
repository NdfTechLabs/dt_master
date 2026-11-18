# Copyright (c) 2025, Dhana Technologies and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import get_datetime

import json

from dt_master.docker_readiness import (
	ReadinessContractError,
	build_node_readiness_requirements,
	evaluate_node_docker_readiness,
	validate_docker_readiness_requirements,
)

DOCKER_INSPECTION_FIELDS = (
	"docker_inspection_status",
	"docker_readiness",
	"docker_inspected_on",
	"docker_inspection_snapshot",
	"docker_readiness_evaluation",
)

DOCKER_INSPECTION_DEFAULTS = {
	"docker_inspection_status": "Not Inspected",
	"docker_readiness": "Unknown",
	"docker_inspected_on": None,
	"docker_inspection_snapshot": None,
	"docker_readiness_evaluation": None,
}


class InfrastructureNode(Document):
	def validate(self):
		self._prevent_direct_docker_inspection_updates()
		self._validate_docker_readiness_requirements()

	def on_update(self):
		"""Reevaluate readiness whenever this Node's requirements change."""
		previous = self.get_doc_before_save()
		if not previous or not self._docker_requirements_changed(previous, self):
			return

		self._reevaluate_docker_readiness()

	@staticmethod
	def _docker_requirements_changed(previous, current=None):
		current = current or previous
		fields = (
			"docker_engine_required",
			"docker_engine_severity",
			"docker_compose_required",
			"docker_compose_severity",
			"docker_min_cpu_cores",
			"docker_min_cpu_cores_severity",
			"docker_min_total_memory_gib",
			"docker_min_total_memory_severity",
			"docker_min_available_memory_gib",
			"docker_min_available_memory_severity",
			"docker_min_available_storage_gib",
			"docker_min_available_storage_severity",
			"docker_min_engine_version",
			"docker_min_engine_version_severity",
			"docker_min_compose_version",
			"docker_min_compose_version_severity",
			"docker_max_snapshot_age_seconds",
		)
		return any(previous.get(field) != current.get(field) for field in fields)

	def _reevaluate_docker_readiness(self):
		"""Persist evaluator output and its standalone state as one DB update."""
		snapshot = None
		if self.docker_inspection_snapshot:
			try:
				snapshot = json.loads(self.docker_inspection_snapshot)
			except (TypeError, ValueError):
				snapshot = None

		evaluation = evaluate_node_docker_readiness(self, snapshot)
		evaluation_json = json.dumps(evaluation, separators=(",", ":"), sort_keys=True)
		frappe.db.set_value(
			"Infrastructure Node",
			self.name,
			{
				"docker_readiness": evaluation["readiness"],
				"docker_readiness_evaluation": evaluation_json,
			},
			update_modified=False,
		)
		self.docker_readiness = evaluation["readiness"]
		self.docker_readiness_evaluation = evaluation_json

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
