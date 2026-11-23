# Copyright (c) 2025, Dhana Technologies and Contributors
# See license.txt

import json
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import get_datetime

from dt_master.api.infrastructure_node import _docker_failure_snapshot, _validate_docker_snapshot
from dt_master.docker_readiness import evaluate_node_docker_readiness
from dt_master.dt_master.doctype.infrastructure_node.infrastructure_node import InfrastructureNode

# On IntegrationTestCase, the doctype test records and all
# link-field test record dependencies are recursively loaded
EXTRA_TEST_RECORD_DEPENDENCIES = []
IGNORE_TEST_RECORD_DEPENDENCIES = []


class IntegrationTestInfrastructureNode(IntegrationTestCase):
	"""Integration tests for InfrastructureNode."""

	def _new_node(self):
		node = frappe.new_doc("Infrastructure Node")
		node.node_name = f"docker-readiness-test-{frappe.generate_hash(length=10)}"
		node.os_type = "Ubuntu"
		node.project_base_dir = "/opt/dtsaas"
		node.project_logs_dir = "/var/log/dtsaas"
		node.access_verified = 0
		return node

	def test_node_can_leave_docker_requirements_unconfigured(self):
		node = self._new_node()
		node.validate()
		self.assertFalse(node.docker_engine_required)
		self.assertFalse(node.docker_min_cpu_cores)
		self.assertFalse(node.docker_min_engine_version)

	def test_new_node_accepts_default_host_discovery_state(self):
		node = self._new_node()
		self.assertEqual(node.host_discovery_status, "Not Discovered")
		self.assertFalse(node.host_discovered_on)
		self.assertFalse(node.host_discovery_snapshot)
		node.validate()

	def test_new_node_rejects_client_supplied_host_discovery_snapshot(self):
		node = self._new_node()
		node.host_discovery_snapshot = '{"schema_version":"2.0.0"}'
		with self.assertRaises(frappe.ValidationError):
			node.validate()

	def test_new_node_rejects_client_supplied_host_discovery_status(self):
		node = self._new_node()
		node.host_discovery_status = "Successful"
		with self.assertRaises(frappe.ValidationError):
			node.validate()

	def test_new_node_rejects_client_supplied_host_discovery_timestamp(self):
		node = self._new_node()
		node.host_discovered_on = "2026-11-20 17:00:00"
		with self.assertRaises(frappe.ValidationError):
			node.validate()

	def test_new_node_accepts_default_docker_helper_release_assessment(self):
		node = self._new_node()
		self.assertEqual(node.docker_helper_release_assessment_status, "Not Assessed")
		self.assertFalse(node.docker_helper_release_assessed_on)
		self.assertFalse(node.docker_helper_release_assessment_snapshot)
		node.validate()

	def test_new_node_rejects_client_supplied_docker_helper_assessment(self):
		node = self._new_node()
		node.docker_helper_release_assessment_status = "Current"
		with self.assertRaises(frappe.ValidationError):
			node.validate()

	def test_node_rejects_unpublished_docker_helper_release_assignment(self):
		node = self._new_node()
		node.docker_helper_release = "DRAFT-RELEASE"
		with patch.object(frappe.db, "get_value", return_value="Draft"):
			with self.assertRaises(frappe.ValidationError):
				node.validate()

	def test_node_accepts_published_docker_helper_release_assignment(self):
		node = self._new_node()
		node.docker_helper_release = "PUBLISHED-RELEASE"
		with (
			patch.object(frappe.db, "get_value", return_value="Published"),
			patch.object(node, "_prepare_docker_helper_release_manifest"),
		):
			node.validate()

	def test_published_assignment_generates_shared_physical_manifest(self):
		node = self._new_node()
		node.docker_helper_release = "PUBLISHED-RELEASE"
		release = type("Release", (), {"name": "PUBLISHED-RELEASE", "release_id": "PUBLISHED-RELEASE", "version": "0.3.3"})()
		with (
			patch.object(frappe.db, "get_value", return_value="Published"),
			patch.object(frappe, "get_doc", return_value=release),
			patch(
				"dt_master.docker_helper_releases.write_release_manifest",
				return_value='{"release":{"id":"PUBLISHED-RELEASE"}}',
			),
		):
			node.validate()
		self.assertEqual(
			node.docker_helper_release_manifest,
			'{"release":{"id":"PUBLISHED-RELEASE"}}',
		)
		self.assertEqual(node.docker_helper_release_assessment_status, "Scripts Deployment Required")
		assignment_snapshot = json.loads(node.docker_helper_release_assessment_snapshot)
		self.assertEqual(assignment_snapshot["reason"], "release_assignment_changed")

	def test_node_retires_previous_release_when_no_other_nodes_use_it(self):
		node = self._new_node()
		node.name = "NODE-1"
		node.docker_helper_release = "NEW-RELEASE"
		previous = type("Previous", (), {"docker_helper_release": "OLD-RELEASE"})()
		release = type("Release", (), {"status": "Published", "save": lambda *args, **kwargs: None})()
		with (
			patch.object(frappe.db, "count", return_value=0),
			patch.object(frappe, "get_doc", return_value=release) as get_doc,
			patch.object(release, "save") as save,
		):
			node._retire_previous_helper_release(previous)
		get_doc.assert_called_once_with("Docker Helper Release", "OLD-RELEASE")
		save.assert_called_once_with(ignore_permissions=True)
		self.assertEqual(release.status, "Retired")

	def test_node_retires_previous_release_even_when_other_nodes_still_reference_it(self):
		node = self._new_node()
		node.name = "NODE-1"
		node.docker_helper_release = "NEW-RELEASE"
		previous = type("Previous", (), {"docker_helper_release": "SHARED-RELEASE"})()
		release = type("Release", (), {"status": "Published"})()
		with (
			patch.object(frappe, "get_doc", return_value=release) as get_doc,
			patch.object(release, "save") as save,
		):
			node._retire_previous_helper_release(previous)
		get_doc.assert_called_once_with("Docker Helper Release", "SHARED-RELEASE")
		save.assert_called_once_with(ignore_permissions=True)
		self.assertEqual(release.status, "Retired")

	def test_new_node_rejects_client_supplied_release_manifest(self):
		node = self._new_node()
		node.docker_helper_release_manifest = '{"release":{"id":"forged"}}'
		with self.assertRaises(frappe.ValidationError):
			node.validate()

	def test_new_node_rejects_severity_without_requirement(self):
		node = self._new_node()
		node.docker_engine_severity = "Required"
		with self.assertRaises(frappe.ValidationError):
			node.validate()

	def test_new_node_rejects_configured_requirement_without_severity(self):
		node = self._new_node()
		node.docker_min_cpu_cores = 4
		with self.assertRaises(frappe.ValidationError):
			node.validate()

	def test_new_node_accepts_typed_requirements_with_severity(self):
		node = self._new_node()
		node.docker_engine_required = 1
		node.docker_engine_severity = "Required"
		node.docker_min_cpu_cores = 4
		node.docker_min_cpu_cores_severity = "Recommended"
		node.docker_min_engine_version = "27.0.0"
		node.docker_min_engine_version_severity = "Required"
		node.docker_max_snapshot_age_seconds = 3600
		node.validate()

	def test_new_node_rejects_invalid_numeric_requirement(self):
		node = self._new_node()
		node.docker_min_cpu_cores = -1
		node.docker_min_cpu_cores_severity = "Required"
		with self.assertRaises(frappe.ValidationError):
			node.validate()

	def test_new_node_rejects_invalid_version_requirement(self):
		node = self._new_node()
		node.docker_min_engine_version = "latest"
		node.docker_min_engine_version_severity = "Required"
		with self.assertRaises(frappe.ValidationError):
			node.validate()

	def test_new_node_accepts_default_docker_inspection_state(self):
		node = self._new_node()
		self.assertEqual(node.docker_inspection_status, "Not Inspected")
		self.assertEqual(node.docker_readiness, "Unknown")
		self.assertFalse(node.docker_inspected_on)
		self.assertFalse(node.docker_inspection_snapshot)
		self.assertFalse(node.docker_readiness_evaluation)
		node.validate()

	def test_new_node_rejects_client_supplied_docker_observations(self):
		node = self._new_node()
		node.docker_readiness = "Ready"
		with self.assertRaises(frappe.ValidationError):
			node.validate()

	def test_new_node_rejects_client_supplied_snapshot(self):
		node = self._new_node()
		node.docker_inspection_snapshot = '{"schema_version": "1.0.0"}'
		with self.assertRaises(frappe.ValidationError):
			node.validate()

	def test_saved_node_rejects_client_supplied_docker_readiness(self):
		node = self._new_node()
		node.insert(ignore_permissions=True)
		node.docker_readiness = "Ready"
		with self.assertRaises(frappe.ValidationError):
			node.save(ignore_permissions=True)

	def test_new_node_rejects_client_supplied_inspection_status(self):
		node = self._new_node()
		node.docker_inspection_status = "Successful"
		with self.assertRaises(frappe.ValidationError):
			node.validate()

	def test_equivalent_inspection_datetime_representations_are_unchanged(self):
		timestamp = "2026-11-18 11:00:00"
		self.assertEqual(
			InfrastructureNode._normalize_inspection_value("docker_inspected_on", timestamp),
			get_datetime(timestamp),
		)

	def test_new_node_rejects_client_supplied_inspection_timestamp(self):
		node = self._new_node()
		node.docker_inspected_on = "2026-11-18 11:00:00"
		with self.assertRaises(frappe.ValidationError):
			node.validate()

	def test_new_node_rejects_client_supplied_readiness_evaluation(self):
		node = self._new_node()
		node.docker_readiness_evaluation = '{"readiness":"Ready"}'
		with self.assertRaises(frappe.ValidationError):
			node.validate()

	def test_saved_node_rejects_client_supplied_readiness_evaluation(self):
		node = self._new_node()
		node.insert(ignore_permissions=True)
		node.docker_readiness_evaluation = '{"readiness":"Ready"}'
		with self.assertRaises(frappe.ValidationError):
			node.save(ignore_permissions=True)

	def test_requirements_change_detection_covers_typed_fields(self):
		class Values:
			def __init__(self, **kwargs):
				self.values = kwargs

			def get(self, key):
				return self.values.get(key)

		previous = Values(docker_engine_required=0, docker_min_cpu_cores=4)
		current = Values(docker_engine_required=1, docker_min_cpu_cores=4)
		self.assertTrue(InfrastructureNode._docker_requirements_changed(previous, current))
		self.assertFalse(InfrastructureNode._docker_requirements_changed(previous, previous))

	def test_docker_snapshot_validator_accepts_observation_without_readiness(self):
		snapshot = {
			"schema_version": "1.0.0",
			"observed_at": "2026-11-18T08:00:00Z",
			"duration_ms": 100,
			"inspection_status": "Successful",
			"host": {
				"cpu": {"logical_cores": 6},
				"memory": {"total_bytes": 12_000_000_000, "available_bytes": 5_000_000_000},
			},
			"docker_cli": {"installed": True, "path": "/usr/bin/docker", "client_version": "29.9.3"},
			"engine": {
				"availability": "Available",
				"server_version": "29.9.3",
				"api_version": "1.52",
				"minimum_api_version": "1.44",
			},
			"compose": {"installed": True, "version": "5.7.0", "compatibility": "Compatible"},
			"storage": {
				"driver": "overlay2",
				"data_root": "/var/lib/docker",
				"filesystem": {
					"mount_point": "/",
					"total_bytes": 200_000_000_000,
					"available_bytes": 100_000_000_000,
					"used_bytes": 100_000_000_000,
				},
			},
			"permissions": {"execution_identity": "frank", "inspection_access": "Allowed"},
			"diagnostics": [],
			"host_inventory": _docker_failure_snapshot("Failed", "probe failed")["host_inventory"],
		}
		self.assertEqual(_validate_docker_snapshot(snapshot), snapshot)
		self.assertNotIn("readiness", snapshot)

	def test_docker_snapshot_validator_accepts_legacy_readiness_without_using_it(self):
		snapshot = {
			"schema_version": "1.0.0",
			"observed_at": "2026-11-18T08:00:00Z",
			"duration_ms": 100,
			"inspection_status": "Successful",
			"readiness": "Not Ready",
			"host": {
				"cpu": {"logical_cores": 6},
				"memory": {"total_bytes": 12_000_000_000, "available_bytes": 5_000_000_000},
			},
			"docker_cli": {"installed": True, "path": "/usr/bin/docker", "client_version": "29.9.3"},
			"engine": {
				"availability": "Available",
				"server_version": "29.9.3",
				"api_version": "1.52",
				"minimum_api_version": "1.44",
			},
			"compose": {"installed": True, "version": "5.7.0", "compatibility": "Compatible"},
			"storage": {
				"driver": "overlay2",
				"data_root": "/var/lib/docker",
				"filesystem": {
					"mount_point": "/",
					"total_bytes": 200_000_000_000,
					"available_bytes": 100_000_000_000,
					"used_bytes": 100_000_000_000,
				},
			},
			"permissions": {"execution_identity": "frank", "inspection_access": "Allowed"},
			"diagnostics": [],
		}
		self.assertEqual(_validate_docker_snapshot(snapshot)["readiness"], "Not Ready")

	def test_docker_failure_snapshot_matches_observation_contract(self):
		snapshot = _docker_failure_snapshot("Failed", "probe failed")
		self.assertNotIn("readiness", snapshot)
		self.assertEqual(_validate_docker_snapshot(snapshot), snapshot)

	def test_docker_snapshot_validator_rejects_unexpected_host_package(self):
		snapshot = _docker_failure_snapshot("Failed", "probe failed")
		snapshot["host_inventory"]["docker_packages"] = [
			{"name": "unexpected-package", "version": "1", "status": "installed"}
		]
		with self.assertRaises(ValueError):
			_validate_docker_snapshot(snapshot)

	def test_docker_snapshot_validator_rejects_malformed_capacity_measurement(self):
		snapshot = _docker_failure_snapshot("Failed", "probe failed")
		snapshot["storage"]["filesystem"]["available_bytes"] = "not-a-byte-count"
		with self.assertRaises(ValueError):
			_validate_docker_snapshot(snapshot)

	def test_timeout_failure_snapshot_is_structured_and_not_ready(self):
		snapshot = _docker_failure_snapshot("Timed Out", "inspection exceeded timeout", 30000)
		self.assertEqual(snapshot["inspection_status"], "Timed Out")
		self.assertEqual(snapshot["duration_ms"], 30000)
		self.assertEqual(_validate_docker_snapshot(snapshot), snapshot)

	def test_missing_snapshot_evaluates_to_unknown(self):
		node = self._new_node()
		node.docker_engine_required = 1
		node.docker_engine_severity = "Required"
		result = evaluate_node_docker_readiness(node, None)
		self.assertEqual(result["readiness"], "Unknown")
		self.assertEqual(
			result["diagnostics"][0]["code"],
			"DOCKER_INSPECTION_EVIDENCE_UNAVAILABLE",
		)

	def test_requirement_change_reevaluates_and_persists_latest_snapshot(self):
		node = self._new_node()
		node.insert(ignore_permissions=True)
		snapshot = {
			"schema_version": "1.0.0",
			"observed_at": "2026-11-18T08:00:00Z",
			"inspection_status": "Successful",
			"readiness": "Not Ready",
			"engine": {"availability": "Available"},
		}
		frappe.db.set_value(
			"Infrastructure Node",
			node.name,
			{
				"docker_inspection_status": "Successful",
				"docker_inspection_snapshot": json.dumps(snapshot),
			},
			update_modified=False,
		)
		node.reload()
		node.docker_engine_required = 1
		node.docker_engine_severity = "Required"
		node.save(ignore_permissions=True)
		node.reload()

		self.assertEqual(node.docker_readiness, "Ready")
		evaluation = json.loads(node.docker_readiness_evaluation)
		self.assertEqual(evaluation["readiness"], "Ready")
		self.assertTrue(evaluation["requirements_fingerprint"])
		self.assertEqual(evaluation["inspection_observed_at"], snapshot["observed_at"])

	def test_new_node_accepts_default_docker_host_lifecycle_state(self):
		node = self._new_node()
		self.assertEqual(node.docker_host_state, "Not Evaluated")
		self.assertFalse(node.docker_host_operation)
		self.assertFalse(node.docker_host_state_updated_on)
		self.assertFalse(node.docker_host_management_snapshot)
		node.validate()

	def test_equivalent_docker_host_lifecycle_datetime_representations_are_unchanged(self):
		timestamp = "2026-11-18 11:00:00"
		self.assertEqual(
			InfrastructureNode._normalize_inspection_value("docker_host_state_updated_on", timestamp),
			get_datetime(timestamp),
		)

	def test_saved_node_accepts_equivalent_docker_host_lifecycle_datetime_representation(self):
		node = self._new_node()
		node.insert(ignore_permissions=True)
		timestamp = "2026-11-18 11:00:00"
		frappe.db.set_value(
			"Infrastructure Node",
			node.name,
			"docker_host_state_updated_on",
			get_datetime(timestamp),
			update_modified=False,
		)
		node.reload()
		node.docker_host_state_updated_on = timestamp
		node.node_name = f"{node.node_name}-updated"
		node.save(ignore_permissions=True)

	def test_saved_node_rejects_changed_docker_host_lifecycle_timestamp(self):
		node = self._new_node()
		node.insert(ignore_permissions=True)
		frappe.db.set_value(
			"Infrastructure Node",
			node.name,
			"docker_host_state_updated_on",
			get_datetime("2026-11-18 11:00:00"),
			update_modified=False,
		)
		node.reload()
		node.docker_host_state_updated_on = "2026-11-18 11:01:00"
		with self.assertRaises(frappe.ValidationError):
			node.save(ignore_permissions=True)

	def test_new_node_rejects_client_supplied_docker_host_lifecycle_state(self):
		node = self._new_node()
		node.docker_host_state = "Engine Ready"
		with self.assertRaises(frappe.ValidationError):
			node.validate()

	def test_new_node_rejects_client_supplied_docker_host_lifecycle_snapshot(self):
		node = self._new_node()
		node.docker_host_management_snapshot = '{"schema_version":"1.0.0"}'
		with self.assertRaises(frappe.ValidationError):
			node.validate()

	def test_saved_node_rejects_client_supplied_docker_host_lifecycle_update(self):
		node = self._new_node()
		node.insert(ignore_permissions=True)
		node.docker_host_state = "Engine Ready"
		with self.assertRaises(frappe.ValidationError):
			node.save(ignore_permissions=True)
