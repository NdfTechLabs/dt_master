"""Contract tests for Infrastructure Node Docker lifecycle requests."""

import unittest
from types import SimpleNamespace

from dt_master.operations.docker_lifecycle import (
	DOCKER_LIFECYCLE_OPERATIONS,
	MUTATING_OPERATIONS,
	READ_ONLY_OPERATIONS,
	authorize_docker_lifecycle_node,
	docker_lifecycle_resource_lock_keys,
	validate_docker_lifecycle_payload,
)


class TestDockerLifecycleContracts(unittest.TestCase):
	def test_catalogue_contains_expected_observation_and_mutation_operations(self):
		self.assertEqual(len(READ_ONLY_OPERATIONS), 5)
		self.assertEqual(len(MUTATING_OPERATIONS), 9)
		self.assertEqual(len(DOCKER_LIFECYCLE_OPERATIONS), 14)

	def test_read_only_operations_accept_only_empty_payload(self):
		for operation in READ_ONLY_OPERATIONS:
			with self.subTest(operation=operation):
				self.assertEqual(validate_docker_lifecycle_payload(operation, {}), {})
				with self.assertRaises(ValueError):
					validate_docker_lifecycle_payload(operation, {"confirm": True})

	def test_compose_install_requires_explicit_confirmation(self):
		with self.assertRaisesRegex(ValueError, "confirmation"):
			validate_docker_lifecycle_payload("host.node.docker.compose.install", {})
		self.assertEqual(
			validate_docker_lifecycle_payload("host.node.docker.compose.install", {"confirm": True}),
			{"confirm": True},
		)

	 def test_engine_install_requires_only_explicit_confirmation(self):
		self.assertEqual(
			validate_docker_lifecycle_payload("host.node.docker.install", {"confirm": True}),
			{"confirm": True},
		)
		for payload in ({}, {"confirm": False}, {"confirm": 1}):
			with self.subTest(payload=payload), self.assertRaises(ValueError):
				validate_docker_lifecycle_payload("host.node.docker.install", payload)
		with self.assertRaises(ValueError):
			validate_docker_lifecycle_payload(
				"host.node.docker.install", {"confirm": True, "plan_digest": "a" * 64}
			)

	def test_upgrade_and_downgrade_require_explicit_version_and_confirmation(self):
		operations = (
			"host.node.docker.upgrade",
			"host.node.docker.downgrade",
			"host.node.docker.compose.upgrade",
			"host.node.docker.compose.downgrade",
		)
		for operation in operations:
			with self.subTest(operation=operation):
				with self.assertRaises(ValueError):
					validate_docker_lifecycle_payload(operation, {"confirm": True})
				with self.assertRaises(ValueError):
					validate_docker_lifecycle_payload(operation, {"target_version": "25.0"})
				result = validate_docker_lifecycle_payload(
					operation, {"confirm": True, "target_version": "25.0.1"}
				)
				self.assertEqual(result["target_version"], "25.0.1")

	def test_uninstall_requires_confirmation_but_not_arbitrary_package_names(self):
		for operation in ("host.node.docker.uninstall", "host.node.docker.compose.uninstall"):
			with self.subTest(operation=operation):
				self.assertEqual(
					validate_docker_lifecycle_payload(operation, {"confirm": True}),
					{"confirm": True},
				)
				with self.assertRaises(ValueError):
					validate_docker_lifecycle_payload(operation, {"confirm": True, "packages": ["docker.io"]})

	def test_state_fingerprint_is_validated(self):
		digest = "a" * 64
		normalized = validate_docker_lifecycle_payload(
			"host.node.docker.upgrade",
			{"confirm": True, "target_version": "27.1", "expected_state_fingerprint": digest},
		)
		self.assertEqual(normalized["expected_state_fingerprint"], digest)
		with self.assertRaises(ValueError):
			validate_docker_lifecycle_payload(
				"host.node.docker.upgrade",
				{"confirm": True, "target_version": "27.1", "expected_state_fingerprint": "bad"},
			)

	def test_unknown_operation_and_caller_instruction_fields_are_rejected(self):
		with self.assertRaises(ValueError):
			validate_docker_lifecycle_payload("host.node.docker.run", {})
		with self.assertRaises(ValueError):
			validate_docker_lifecycle_payload(
				"host.node.docker.install", {"confirm": True, "command": "apt install docker"}
			)

	def test_mutations_share_explicit_node_scoped_lock(self):
		target = SimpleNamespace(doctype="Infrastructure Node", name="NODE-01")
		for operation_type in MUTATING_OPERATIONS:
			operation = SimpleNamespace(operation_type=operation_type)
			with self.subTest(operation=operation_type):
				self.assertEqual(
					docker_lifecycle_resource_lock_keys(target, operation),
					["node-docker:NODE-01"],
				)

	def test_read_only_operation_cannot_acquire_mutation_lock(self):
		target = SimpleNamespace(doctype="Infrastructure Node", name="NODE-01")
		with self.assertRaises(ValueError):
			docker_lifecycle_resource_lock_keys(
				target, SimpleNamespace(operation_type="host.node.docker.updates.check")
			)

	def test_install_engine_is_registered_on_existing_durable_operation_runtime(self):
		from dt_master.operations.registry import get_operation

		definition = get_operation("host.node.docker.install")
		self.assertEqual(definition.queue, "long")
		self.assertEqual(definition.timeout_seconds, 1200)
		self.assertEqual(definition.max_attempts, 1)
		self.assertEqual(
			definition.resource_lock_keys(
				SimpleNamespace(doctype="Infrastructure Node", name="NODE-01"),
				SimpleNamespace(operation_type="host.node.docker.install"),
			),
			["node-docker:NODE-01"],
		)

	def test_authorization_requires_system_manager_and_verified_ssh(self):
		target = SimpleNamespace(doctype="Infrastructure Node", access_verified=False)
		with self.assertRaises(Exception):
			authorize_docker_lifecycle_node(target)


if __name__ == "__main__":
	unittest.main()
