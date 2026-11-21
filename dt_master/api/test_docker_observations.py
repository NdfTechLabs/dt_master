"""Tests for the allowlisted Docker observation dispatch API."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import frappe

from dt_master.api import infrastructure_node


class TestDockerObservationAPI(unittest.TestCase):
	def setUp(self):
		self.node = SimpleNamespace(
			name="NODE-01",
			access_verified=1,
			check_permission=lambda permission: self.assertEqual(permission, "read"),
		)

	def test_each_browser_observation_queues_only_its_registered_read_only_operation(self):
		expected = {
			"privileges": "host.node.docker.privileges.check",
			"inspect": "host.node.docker.inspect",
			"preflight": "host.node.docker.preflight",
			"updates": "host.node.docker.updates.check",
			"verify": "host.node.docker.verify",
		}
		for observation, operation_type in expected.items():
			with self.subTest(observation=observation):
				with (
					patch.object(frappe, "only_for") as only_for,
					patch.object(frappe, "get_doc", return_value=self.node),
					patch.object(
						infrastructure_node, "start_operation", return_value={"operation_id": "op-1"}
					) as start,
				):
					result = infrastructure_node.run_docker_observation("NODE-01", observation)

				only_for.assert_called_once_with("System Manager")
				self.assertEqual(result, {"operation_id": "op-1"})
				self.assertEqual(start.call_args.kwargs["operation_type"], operation_type)
				self.assertEqual(start.call_args.kwargs["target_doctype"], "Infrastructure Node")
				self.assertEqual(start.call_args.kwargs["target_name"], "NODE-01")
				self.assertEqual(start.call_args.kwargs["payload"], {})

 
	def test_bootstrap_endpoint_queues_fixed_mutating_operation(self):
		self.node.ssh_user = "deploy"
		with (patch.object(frappe, "only_for") as only_for,
			patch.object(frappe, "get_doc", return_value=self.node),
			patch.object(infrastructure_node, "start_operation", return_value={"operation_id": "op-1"}) as start):
			result = infrastructure_node.bootstrap_docker_privileges("NODE-01")
		only_for.assert_called_once_with("System Manager")
		self.assertEqual(result, {"operation_id": "op-1"})
		self.assertEqual(start.call_args.kwargs["operation_type"], "host.node.docker.privileges.bootstrap")
		self.assertEqual(start.call_args.kwargs["payload"]["method"], "bootstrap_privileges")

	def test_bootstrap_endpoint_rejects_unverified_node(self):
		self.node.ssh_user = "deploy"
		self.node.access_verified = 0
		with (patch.object(frappe, "only_for"), patch.object(frappe, "get_doc", return_value=self.node),
			patch.object(frappe, "throw", side_effect=frappe.ValidationError),
			patch.object(infrastructure_node, "start_operation") as start):
			with self.assertRaises(frappe.ValidationError):
				infrastructure_node.bootstrap_docker_privileges("NODE-01")
		start.assert_not_called()

	def test_unknown_observation_is_rejected_before_node_lookup(self):
		with (
			patch.object(frappe, "only_for"),
			patch.object(frappe, "throw", side_effect=frappe.ValidationError),
			patch.object(frappe, "get_doc") as get_doc,
		):
			with self.assertRaises(frappe.ValidationError):
				infrastructure_node.run_docker_observation("NODE-01", "arbitrary-command")
		get_doc.assert_not_called()

	def test_unverified_node_is_rejected(self):
		self.node.access_verified = 0
		with (
			patch.object(frappe, "only_for"),
			patch.object(frappe, "get_doc", return_value=self.node),
			patch.object(frappe, "throw", side_effect=frappe.ValidationError),
			patch.object(infrastructure_node, "start_operation") as start,
		):
			with self.assertRaises(frappe.ValidationError):
				infrastructure_node.run_docker_observation("NODE-01", "inspect")
		start.assert_not_called()


if __name__ == "__main__":
	unittest.main()
