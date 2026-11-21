"""Tests for durable Node-scoped Host operations."""

import unittest
from types import SimpleNamespace

from dt_master.operations.registry import get_operation


class TestHostNodeRegistry(unittest.TestCase):
	def test_node_operations_are_registered_as_read_only_single_attempts(self):
		for operation_type in ("host.node.inspect", "host.node.connection.check"):
			with self.subTest(operation_type=operation_type):
				definition = get_operation(operation_type)
				self.assertEqual(definition.queue, "short")
				self.assertEqual(definition.max_attempts, 1)
				self.assertEqual(definition.validate_payload({}), {})
				self.assertIsNotNone(definition.resource_lock_keys)

	def test_node_operations_share_node_scoped_observation_lock(self):
		target = SimpleNamespace(doctype="Infrastructure Node", name="NODE-01")
		for operation_type in ("host.node.inspect", "host.node.connection.check"):
			definition = get_operation(operation_type)
			self.assertEqual(
				definition.resource_lock_keys(target, SimpleNamespace(operation_type=operation_type)),
				["node-host-observation:NODE-01"],
			)

	def test_node_operation_payload_rejects_caller_instructions(self):
		definition = get_operation("host.node.connection.check")
		with self.assertRaises(Exception):
			definition.validate_payload({"command": "id"})


if __name__ == "__main__":
	unittest.main()
