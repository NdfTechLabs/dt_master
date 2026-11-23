# Copyright (c) 2025, Dhana Technologies and contributors
# For license information, please see license.txt

import hashlib
import json

import frappe
from frappe.model.document import Document
from frappe.utils import get_datetime

from dt_master.docker_readiness import (
	ReadinessContractError,
	build_node_readiness_requirements,
	evaluate_node_docker_readiness,
	validate_docker_readiness_requirements,
)

DOCKER_HELPER_RELEASE_ASSESSMENT_FIELDS = (
	"docker_helper_release_assessment_status",
	"docker_helper_release_assessed_on",
	"docker_helper_release_assessment_snapshot",
	"docker_helper_release_manifest",
)

DOCKER_HELPER_RELEASE_ASSESSMENT_DEFAULTS = {
	"docker_helper_release_assessment_status": "Not Assessed",
	"docker_helper_release_assessed_on": None,
	"docker_helper_release_assessment_snapshot": None,
	"docker_helper_release_manifest": None,
}

DOCKER_INSPECTION_FIELDS = (
	"docker_inspection_status",
	"docker_readiness",
	"docker_inspected_on",
	"docker_inspection_snapshot",
	"docker_readiness_evaluation",
)

HOST_DISCOVERY_FIELDS = (
	"host_discovery_status",
	"host_discovered_on",
	"host_discovery_snapshot",
)

HOST_DISCOVERY_DEFAULTS = {
	"host_discovery_status": "Not Discovered",
	"host_discovered_on": None,
	"host_discovery_snapshot": None,
}

DOCKER_HOST_LIFECYCLE_FIELDS = (
	"docker_host_state",
	"docker_host_operation",
	"docker_host_state_updated_on",
	"docker_host_management_snapshot",
)

DOCKER_HOST_LIFECYCLE_DEFAULTS = {
	"docker_host_state": "Not Evaluated",
	"docker_host_operation": None,
	"docker_host_state_updated_on": None,
	"docker_host_management_snapshot": None,
}

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
		self._prevent_direct_host_discovery_updates()
		self._prevent_direct_docker_host_lifecycle_updates()
		self._prevent_direct_docker_helper_release_assessment_updates()
		self._validate_docker_helper_release_assignment()
		self._prepare_docker_helper_release_manifest()
		self._validate_docker_readiness_requirements()

	def on_update(self):
		"""Reevaluate readiness whenever this Node's requirements change."""
		previous = self.get_doc_before_save()
		if previous:
			self._retire_previous_helper_release(previous)
		if previous and self._docker_requirements_changed(previous, self):
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

	def _prevent_direct_docker_host_lifecycle_updates(self):
		"""Keep Docker host lifecycle projection server-managed."""
		if getattr(frappe.flags, "docker_host_lifecycle_update", False):
			return

		previous = self.get_doc_before_save()
		if previous:
			changed_fields = [
				field
				for field in DOCKER_HOST_LIFECYCLE_FIELDS
				if self._normalize_inspection_value(field, self.get(field))
				!= self._normalize_inspection_value(field, previous.get(field))
			]
		else:
			changed_fields = [
				field
				for field, default in DOCKER_HOST_LIFECYCLE_DEFAULTS.items()
				if self.get(field) not in (None, "", default)
			]

		if changed_fields:
			frappe.throw(
				frappe._("Docker host lifecycle fields are managed by the server: {0}").format(
					", ".join(changed_fields)
				),
				title=frappe._("Read-only Docker Host Lifecycle"),
			)

	def _prevent_direct_docker_helper_release_assessment_updates(self):
		"""Keep release assessment evidence server-managed."""
		if getattr(frappe.flags, "docker_helper_release_assessment_update", False):
			return

		previous = self.get_doc_before_save()
		if previous:
			changed_fields = [
				field
				for field in DOCKER_HELPER_RELEASE_ASSESSMENT_FIELDS
				if self._normalize_inspection_value(field, self.get(field))
				!= self._normalize_inspection_value(field, previous.get(field))
			]
		else:
			changed_fields = [
				field
				for field, default in DOCKER_HELPER_RELEASE_ASSESSMENT_DEFAULTS.items()
				if self.get(field) not in (None, "", default)
			]

		if changed_fields:
			frappe.throw(
				frappe._("Docker Helper Release assessment fields are server-managed: {0}").format(
					", ".join(changed_fields)
				),
				title=frappe._("Read-only Docker Helper Release Assessment"),
			)

	def _prepare_docker_helper_release_manifest(self):
		"""Generate the shared physical manifest when a Published release assignment changes."""
		previous = self.get_doc_before_save()
		previous_release = previous.docker_helper_release if previous else None
		if previous_release == self.docker_helper_release:
			return
		if not self.docker_helper_release:
			self.docker_helper_release_manifest = None
			self.docker_helper_release_assessment_status = "Not Assessed"
			self.docker_helper_release_assessed_on = None
			self.docker_helper_release_assessment_snapshot = None
			return

		from pathlib import Path

		from dt_master.docker_helper_releases import (
			DockerHelperReleaseError,
			write_release_manifest,
		)

		release = frappe.get_doc("Docker Helper Release", self.docker_helper_release)
		try:
			manifest = write_release_manifest(
				release,
				source_root=Path(__file__).resolve().parents[3],
			)
		except DockerHelperReleaseError as exc:
			frappe.throw(
				frappe._("The assigned release manifest could not be generated: {0}").format(str(exc)),
				title=frappe._("Invalid Docker Helper Release"),
			)
		self.docker_helper_release_manifest = manifest
		self.docker_helper_release_assessment_status = "Scripts Deployment Required"
		self.docker_helper_release_assessed_on = None
		self.docker_helper_release_assessment_snapshot = json.dumps(
			{
				"schema_version": "1.0.0",
				"assessment_status": "Scripts Deployment Required",
				"reason": "release_assignment_changed",
				"assigned_release": {
					"release_id": release.release_id,
					"version": release.version,
				},
				"manifest_sha256": hashlib.sha256(manifest.encode("utf-8")).hexdigest(),
				"operator_action": "Deploy the updated Docker helper scripts and manifest, then start release assessment.",
			},
			sort_keys=True,
			separators=(",", ":"),
		)

	def _retire_previous_helper_release(self, previous):
		"""Retire the previous release as a signal to every node still using it."""
		old_release = previous.docker_helper_release
		if not old_release or old_release == self.docker_helper_release:
			return
		release = frappe.get_doc("Docker Helper Release", old_release)
		if release.status == "Published":
			release.status = "Retired"
			release.save(ignore_permissions=True)

	def _validate_docker_helper_release_assignment(self):
		"""Only published catalog releases may be assigned to an Infrastructure Node."""
		if not self.docker_helper_release:
			return
		status = frappe.db.get_value("Docker Helper Release", self.docker_helper_release, "status")
		if status != "Published":
			frappe.throw(
				frappe._("Only a published Docker Helper Release can be assigned to an Infrastructure Node."),
				title=frappe._("Invalid Docker Helper Release Assignment"),
			)

	def _prevent_direct_host_discovery_updates(self):
		"""Keep Host discovery evidence server-managed, like Docker inspection evidence."""
		previous = self.get_doc_before_save()
		if previous:
			changed_fields = [
				field
				for field in HOST_DISCOVERY_FIELDS
				if self._normalize_inspection_value(field, self.get(field))
				!= self._normalize_inspection_value(field, previous.get(field))
			]
		else:
			changed_fields = [
				field
				for field, default in HOST_DISCOVERY_DEFAULTS.items()
				if self.get(field) not in (None, "", default)
			]
		if changed_fields:
			frappe.throw(
				frappe._(
					"Host discovery fields are managed by the server-side inspection service: {0}"
				).format(", ".join(changed_fields)),
				title=frappe._("Read-only Host Discovery Data"),
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
		if value in (None, "") and field in ("host_discovery_status", "host_discovery_snapshot"):
			return HOST_DISCOVERY_DEFAULTS[field]
		if (
			field
			in (
				"docker_inspected_on",
				"host_discovered_on",
				"docker_helper_release_assessed_on",
				"docker_host_state_updated_on",
			)
			and value
		):
			# Form submissions may carry a datetime as a string while the
			# database-loaded document exposes a Python datetime object.
			return get_datetime(value)
		return value
