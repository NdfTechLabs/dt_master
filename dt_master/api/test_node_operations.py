"""Tests for Infrastructure Node operation dispatch APIs."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import frappe

from dt_master.api import infrastructure_node


class TestNodeOperationAPI(unittest.TestCase):
	def setUp(self):
		self.node = SimpleNamespace(
			name="NODE-01",
			access_verified=1,
			check_permission=lambda permission: self.assertEqual(permission, "read"),
		)

	def _assert_queued(self, method, operation_type):
		with (
			patch.object(frappe, "only_for") as only_for,
			patch.object(frappe, "get_doc", return_value=self.node),
			patch.object(infrastructure_node, "start_operation", return_value={"operation_id": "op-1"}) as start,
		):
			result = method("NODE-01")
		only_for.assert_called_once_with("System Manager")
		self.assertEqual(result, {"operation_id": "op-1"})
		self.assertEqual(start.call_args.kwargs["operation_type"], operation_type)
		self.assertEqual(start.call_args.kwargs["target_doctype"], "Infrastructure Node")
		self.assertEqual(start.call_args.kwargs["target_name"], "NODE-01")
		self.assertEqual(start.call_args.kwargs["payload"], {})

	def test_inspect_node_queues_named_operation(self):
		self._assert_queued(infrastructure_node.inspect_node, "host.node.inspect")

	def test_test_node_connection_queues_named_operation(self):
		self._assert_queued(infrastructure_node.test_node_connection, "host.node.connection.check")

	def test_connection_test_rejects_unverified_node(self):
		self.node.access_verified = 0
		with (
			patch.object(frappe, "only_for"),
			patch.object(frappe, "get_doc", return_value=self.node),
			patch.object(frappe, "throw", side_effect=frappe.ValidationError),
			patch.object(infrastructure_node, "start_operation") as start,
		):
			with self.assertRaises(frappe.ValidationError):
				infrastructure_node.test_node_connection("NODE-01")
		start.assert_not_called()


if __name__ == "__main__":
	unittest.main()
