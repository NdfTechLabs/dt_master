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
