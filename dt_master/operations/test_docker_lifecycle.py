"""Contract tests for Infrastructure Node Docker lifecycle requests."""

import json
import unittest
from types import SimpleNamespace

from dt_master.operations.docker_lifecycle import (
	DOCKER_LIFECYCLE_OPERATIONS,
	DOCKER_SETUP_CHECKPOINTS,
	MUTATING_OPERATIONS,
	READ_ONLY_OPERATIONS,
	authorize_docker_lifecycle_node,
	docker_lifecycle_resource_lock_keys,
	docker_setup_restart_updates,
	validate_docker_lifecycle_payload,
)


class TestDockerLifecycleContracts(unittest.TestCase):
	def test_catalogue_contains_expected_observation_and_mutation_operations(self):
		self.assertEqual(len(READ_ONLY_OPERATIONS), 6)
		self.assertEqual(len(MUTATING_OPERATIONS), 11)
		self.assertEqual(len(DOCKER_LIFECYCLE_OPERATIONS), 17)

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

	def test_repository_preparation_is_a_closed_empty_payload_mutation(self):
		operation = "host.node.docker.repository.prepare"
		self.assertIn(operation, MUTATING_OPERATIONS)
		self.assertEqual(validate_docker_lifecycle_payload(operation, {}), {})
		with self.assertRaises(ValueError):
			validate_docker_lifecycle_payload(operation, {"confirm": True})

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


class TestDockerRepositoryPreparationEndpoint(unittest.TestCase):
	def test_repository_preparation_queues_the_registered_mutation(self):
		from unittest.mock import Mock, patch

		from dt_master.api import infrastructure_node

		node = SimpleNamespace(
			name="NODE-01",
			access_verified=True,
			check_permission=Mock(),
		)
		with (
			patch.object(infrastructure_node.frappe, "only_for"),
			patch.object(infrastructure_node.frappe, "get_doc", return_value=node),
			patch.object(
				infrastructure_node,
				"start_operation",
				return_value={"operation_id": "operation-01"},
			) as start_operation,
		):
			result = infrastructure_node.prepare_docker_repository("NODE-01")

		self.assertEqual(result, {"operation_id": "operation-01"})
		node.check_permission.assert_called_once_with("read")
		kwargs = start_operation.call_args.kwargs
		self.assertEqual(kwargs["operation_type"], "host.node.docker.repository.prepare")
		self.assertEqual(kwargs["target_doctype"], "Infrastructure Node")
		self.assertEqual(kwargs["target_name"], "NODE-01")
		self.assertEqual(kwargs["payload"], {})
		self.assertTrue(kwargs["idempotency_key"].startswith("docker-repository-prepare:NODE-01:"))

	def test_repository_preparation_rejects_unverified_ssh_access(self):
		from unittest.mock import Mock, patch

		from dt_master.api import infrastructure_node

		node = SimpleNamespace(
			name="NODE-01",
			access_verified=False,
			check_permission=Mock(),
		)
		with (
			patch.object(infrastructure_node.frappe, "only_for"),
			patch.object(infrastructure_node.frappe, "get_doc", return_value=node),
			patch.object(infrastructure_node.frappe, "throw", side_effect=ValueError),
			patch.object(infrastructure_node, "start_operation") as start_operation,
			self.assertRaises(ValueError),
		):
			infrastructure_node.prepare_docker_repository("NODE-01")

		start_operation.assert_not_called()


class TestDockerSetupCheckpointRecovery(unittest.TestCase):
	def test_restart_payload_accepts_all_eight_checkpoints_and_rejects_unknown(self):
		operation = "host.node.docker.setup.restart"
		self.assertEqual(len(DOCKER_SETUP_CHECKPOINTS), 8)
		for checkpoint in DOCKER_SETUP_CHECKPOINTS:
			with self.subTest(checkpoint=checkpoint):
				self.assertEqual(
					validate_docker_lifecycle_payload(operation, {"checkpoint": checkpoint}),
					{"checkpoint": checkpoint},
				)
		for payload in ({}, {"checkpoint": "unknown"}, {"checkpoint": "connection", "force": True}):
			with self.subTest(payload=payload), self.assertRaises(ValueError):
				validate_docker_lifecycle_payload(operation, payload)

	def test_restart_from_privilege_helper_preserves_host_and_clears_downstream_evidence(self):
		node = SimpleNamespace(
			access_verified=True,
			docker_host_management_snapshot=(
				'{"host_inspection":{"inspection_status":"Successful","os_support_status":"Supported"},'
				'"privilege_bootstrap":{"status":"Succeeded"},'
				'"privilege_readiness":{"status":"Ready"},'
				'"docker_inspection":{"inspection_status":"Successful"},'
				'"docker_install_plan":{"status":"blocked"}}'
			),
		)
		updates = docker_setup_restart_updates(node, "privilege_helper")
		management = json.loads(updates["docker_host_management_snapshot"])
		self.assertIn("host_inspection", management)
		self.assertNotIn("privilege_bootstrap", management)
		self.assertNotIn("privilege_readiness", management)
		self.assertNotIn("docker_inspection", management)
		self.assertNotIn("docker_install_plan", management)
		self.assertEqual(updates["docker_host_state"], "Privilege Setup Required")
		self.assertEqual(updates["docker_inspection_status"], "Not Inspected")

	def test_restart_from_preflight_preserves_runtime_inspection(self):
		node = SimpleNamespace(
			access_verified=True,
			docker_inspection_status="Successful",
			docker_host_management_snapshot=(
				'{"host_inspection":{"inspection_status":"Successful","os_support_status":"Supported"},'
				'"privilege_bootstrap":{"status":"Succeeded"},"privilege_readiness":{"status":"Ready"},'
				'"docker_inspection":{"inspection_status":"Successful"},"docker_install_plan":{"status":"blocked"}}'
			),
		)
		updates = docker_setup_restart_updates(node, "preflight")
		import json

		management = json.loads(updates["docker_host_management_snapshot"])
		self.assertIn("docker_inspection", management)
		self.assertNotIn("docker_install_plan", management)
		self.assertNotIn("docker_inspection_snapshot", updates)
		self.assertEqual(updates["docker_host_state"], "Docker Inspection Required")

	def test_restart_from_connection_invalidates_connection_and_all_downstream_evidence(self):
		node = SimpleNamespace(
			access_verified=True,
			docker_host_management_snapshot=(
				'{"connection_status":"Connected","connection_operation_id":"op-1",'
				'"host_inspection":{"inspection_status":"Successful"},"privilege_bootstrap":{"status":"Succeeded"}}'
			),
		)
		updates = docker_setup_restart_updates(node, "connection")
		import json

		management = json.loads(updates["docker_host_management_snapshot"])
		self.assertNotIn("connection_status", management)
		self.assertNotIn("connection_operation_id", management)
		self.assertNotIn("host_inspection", management)
		self.assertEqual(updates["access_verified"], 0)
		self.assertEqual(updates["docker_host_state"], "Connection Required")

	def test_restart_checkpoint_requires_its_upstream_evidence(self):
		node = SimpleNamespace(
			access_verified=True,
			docker_host_management_snapshot=json.dumps(
				{
					"host_inspection": {"inspection_status": "Successful", "os_support_status": "Supported"},
					"privilege_bootstrap": {"status": "Succeeded"},
				}
			),
			docker_inspection_status="Not Inspected",
		)
		with self.assertRaisesRegex(ValueError, "Host inspection"):
			docker_setup_restart_updates(
				SimpleNamespace(access_verified=True, docker_host_management_snapshot="{}"),
				"privilege_helper",
			)
		with self.assertRaisesRegex(ValueError, "privilege readiness"):
			docker_setup_restart_updates(node, "docker_inspection")


class TestDockerSetupRestartEndpoint(unittest.TestCase):
	def test_restart_endpoint_queues_durable_checkpoint_operation(self):
		from unittest.mock import Mock, patch

		from dt_master.api import infrastructure_node

		node = SimpleNamespace(
			name="NODE-01",
			ssh_host_alias="node-alias",
			ip_address=None,
			ssh_user="ubuntu",
			check_permission=Mock(),
		)
		with (
			patch.object(infrastructure_node.frappe, "only_for"),
			patch.object(infrastructure_node.frappe, "get_doc", return_value=node),
			patch.object(
				infrastructure_node, "start_operation", return_value={"operation_id": "restart-01"}
			) as start,
		):
			result = infrastructure_node.restart_docker_setup_from("NODE-01", "privilege_helper")
		self.assertEqual(result, {"operation_id": "restart-01"})
		start.assert_called_once()
		self.assertEqual(start.call_args.kwargs["operation_type"], "host.node.docker.setup.restart")
		self.assertEqual(start.call_args.kwargs["payload"], {"checkpoint": "privilege_helper"})

	def test_restart_endpoint_rejects_unconfigured_ssh(self):
		from unittest.mock import Mock, patch

		from dt_master.api import infrastructure_node

		node = SimpleNamespace(
			name="NODE-01",
			ssh_host_alias=None,
			ip_address=None,
			ssh_user=None,
			check_permission=Mock(),
		)
		with (
			patch.object(infrastructure_node.frappe, "only_for"),
			patch.object(infrastructure_node.frappe, "get_doc", return_value=node),
			patch.object(infrastructure_node.frappe, "throw", side_effect=ValueError),
			patch.object(infrastructure_node, "start_operation") as start,
			self.assertRaises(ValueError),
		):
			infrastructure_node.restart_docker_setup_from("NODE-01", "connection")
		start.assert_not_called()


if __name__ == "__main__":
	unittest.main()
