# Copyright (c) 2025, Dhana Technologies and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase

# On IntegrationTestCase, the doctype test records and all
# link-field test record dependencies are recursively loaded
EXTRA_TEST_RECORD_DEPENDENCIES = []
IGNORE_TEST_RECORD_DEPENDENCIES = []


class IntegrationTestInfrastructureNode(IntegrationTestCase):
	"""Integration tests for InfrastructureNode."""

	def _new_node(self):
		node = frappe.new_doc("Infrastructure Node")
		node.node_name = "docker-readiness-test-node"
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

	def test_new_node_rejects_client_supplied_inspection_timestamp(self):
		node = self._new_node()
		node.docker_inspected_on = "2026-11-18 11:00:00"
		with self.assertRaises(frappe.ValidationError):
			node.validate()
