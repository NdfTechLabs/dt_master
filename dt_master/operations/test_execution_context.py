"""Tests for immutable typed worker execution context."""

import unittest
from types import SimpleNamespace

from dt_master.operations.execution_context import (
    WorkerExecutionContext,
    create_worker_execution_context,
)


class TestWorkerExecutionContext(unittest.TestCase):
    def test_factory_copies_operation_identity_and_fencing_evidence(self):
        operation = SimpleNamespace(
            operation_id="operation-123",
            correlation_id="correlation-123",
            initiator_type="Human",
            initiated_by="operator@example.com",
            execution_identity="frappe-worker",
            idempotency_key="idempotency-123",
            target_doctype="Infrastructure Node",
            target_name="NODE-0001",
        )
        leases = [{"lock_key": "node:NODE-0001", "fencing_token": 9, "worker_token": "secret"}]
        heartbeat = lambda: True
        assert_leases = lambda: True
        context = create_worker_execution_context(
            operation=operation,
            attempt=2,
            leases=leases,
            heartbeat=heartbeat,
            assert_leases=assert_leases,
        )
        self.assertIsInstance(context, WorkerExecutionContext)
        self.assertEqual(context["idempotency_key"], "idempotency-123")
        self.assertEqual(context["initiator_type"], "Human")
        self.assertEqual(context["resource_leases"], ({"lock_key": "node:NODE-0001", "fencing_token": 9},))
        self.assertNotIn("worker_token", str(context.resource_leases))
        self.assertIs(context.heartbeat, heartbeat)
        self.assertIs(context.assert_leases, assert_leases)

    def test_context_is_immutable(self):
        operation = SimpleNamespace(
            operation_id="operation-123", correlation_id="correlation-123",
            initiator_type="Human", initiated_by="operator@example.com",
            execution_identity="frappe-worker", idempotency_key="idempotency-123",
            target_doctype="Infrastructure Node", target_name="NODE-0001",
        )
        context = create_worker_execution_context(
            operation=operation, attempt=1, leases=[],
            heartbeat=lambda: None, assert_leases=lambda: None,
        )
        with self.assertRaises(AttributeError):
            context.operation_id = "forged"


if __name__ == "__main__":
    unittest.main()
