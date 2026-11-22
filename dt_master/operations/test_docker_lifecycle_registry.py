"""Tests for registered Docker lifecycle operations."""

import unittest
from types import SimpleNamespace

from dt_master.operations.registry import get_operation, registered_operation_types


class TestDockerLifecycleRegistry(unittest.TestCase):
	def test_inspect_and_preflight_are_registered_with_read_only_handlers(self):
		for operation_type in (
			"host.node.docker.privileges.check",
			"host.node.docker.inspect",
			"host.node.docker.preflight",
			"host.node.docker.updates.check",
			"host.node.docker.verify",
		):
			with self.subTest(operation_type=operation_type):
				definition = get_operation(operation_type)
				self.assertEqual(definition.queue, "short")
				self.assertEqual(definition.max_attempts, 1)
				self.assertIsNotNone(definition.resource_lock_keys)
				self.assertEqual(definition.validate_payload({}), {})

	def test_read_only_operations_share_node_docker_lifecycle_lock(self):
		target = SimpleNamespace(doctype="Infrastructure Node", name="NODE-01")
		for operation_type in (
			"host.node.docker.privileges.check",
			"host.node.docker.inspect",
			"host.node.docker.preflight",
			"host.node.docker.updates.check",
			"host.node.docker.verify",
		):
			definition = get_operation(operation_type)
			operation = SimpleNamespace(operation_type=operation_type)
			self.assertEqual(
				definition.resource_lock_keys(target, operation),
				["node-docker:NODE-01"],
			)

	def test_release_assessment_is_registered_as_durable_mutation(self):
		definition = get_operation("host.node.docker.helper_release.assess")
		self.assertEqual(definition.queue, "long")
		self.assertEqual(definition.timeout_seconds, 360)
		self.assertEqual(definition.max_attempts, 1)
		self.assertEqual(definition.validate_payload({}), {})
		target = SimpleNamespace(doctype="Infrastructure Node", name="NODE-01")
		operation = SimpleNamespace(operation_type="host.node.docker.helper_release.assess")
		self.assertEqual(
			definition.resource_lock_keys(target, operation),
			["node-docker:NODE-01"],
		)

	def test_privilege_bootstrap_is_registered_as_single_attempt_mutation(self):
		definition = get_operation("host.node.docker.privileges.bootstrap")
		self.assertEqual(definition.queue, "long")
		self.assertEqual(definition.timeout_seconds, 240)
		self.assertEqual(definition.max_attempts, 1)
		self.assertEqual(definition.validate_payload({}), {})
		target = SimpleNamespace(doctype="Infrastructure Node", name="NODE-01")
		operation = SimpleNamespace(operation_type="host.node.docker.privileges.bootstrap")
		self.assertEqual(definition.resource_lock_keys(target, operation), ["node-docker:NODE-01"])

	def test_engine_install_is_registered_on_durable_task_manager(self):
		definition = get_operation("host.node.docker.install")
		self.assertEqual(definition.queue, "long")
		self.assertEqual(definition.timeout_seconds, 1200)
		self.assertEqual(definition.max_attempts, 1)
		self.assertEqual(definition.validate_payload({"confirm": True}), {"confirm": True})
		target = SimpleNamespace(doctype="Infrastructure Node", name="NODE-01")
		operation = SimpleNamespace(operation_type="host.node.docker.install")
		self.assertEqual(
			definition.resource_lock_keys(target, operation),
			["node-docker:NODE-01"],
		)

	def test_repository_preparation_is_registered_as_single_attempt_mutation(self):
		definition = get_operation("host.node.docker.repository.prepare")
		self.assertEqual(definition.queue, "long")
		self.assertEqual(definition.timeout_seconds, 600)
		self.assertEqual(definition.max_attempts, 1)
		self.assertEqual(definition.validate_payload({}), {})
		target = SimpleNamespace(doctype="Infrastructure Node", name="NODE-01")
		operation = SimpleNamespace(operation_type="host.node.docker.repository.prepare")
		self.assertEqual(definition.resource_lock_keys(target, operation), ["node-docker:NODE-01"])

	def test_setup_restart_is_registered_as_serialized_durable_operation(self):
		definition = get_operation("host.node.docker.setup.restart")
		self.assertEqual(definition.queue, "short")
		self.assertEqual(definition.max_attempts, 1)
		self.assertEqual(
			definition.validate_payload({"checkpoint": "privilege_helper"}),
			{"checkpoint": "privilege_helper"},
		)
		target = SimpleNamespace(doctype="Infrastructure Node", name="NODE-01")
		operation = SimpleNamespace(operation_type="host.node.docker.setup.restart")
		self.assertEqual(
			definition.resource_lock_keys(target, operation),
			["node-docker:NODE-01", "node-host-observation:NODE-01"],
		)

	def test_other_docker_package_lifecycle_mutations_are_not_registered(self):
		types = set(registered_operation_types())
		for operation_type in (
			"host.node.docker.upgrade",
			"host.node.docker.downgrade",
			"host.node.docker.uninstall",
			"host.node.docker.compose.install",
			"host.node.docker.compose.upgrade",
			"host.node.docker.compose.downgrade",
			"host.node.docker.compose.uninstall",
		):
			self.assertNotIn(operation_type, types)


if __name__ == "__main__":
	unittest.main()
