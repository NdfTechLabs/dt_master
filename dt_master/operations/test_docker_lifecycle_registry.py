"""Tests for registered read-only Docker lifecycle operations."""

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

    def test_read_only_operations_use_dedicated_observation_lock(self):
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
                ["node-docker-observation:NODE-01"],
            )

    def test_mutating_operations_are_not_registered(self):
        types = set(registered_operation_types())
        for operation_type in (
            "host.node.docker.install",
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
