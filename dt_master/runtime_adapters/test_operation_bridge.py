"""Tests for the durable operation/runtime adapter bridge."""

import unittest

from dt_master.operations.execution_context import WorkerExecutionContext
from dt_master.runtime_adapters.base import RuntimeAdapter
from dt_master.runtime_adapters.contracts import RuntimeAdapterError
from dt_master.runtime_adapters.operation_bridge import (
    invoke_runtime_mutation,
    validate_runtime_mutation_payload,
)
from dt_master.runtime_adapters.registry import clear_registry_for_tests, register_adapter


class MutatingTestAdapter(RuntimeAdapter):
    adapter_id = "test-host-v1"
    runtime_type = "host"

    def capabilities(self):
        return ("start",)

    def _start(self, request, execution_context=None):
        self.last_request = request
        return self.result_for(
            request,
            outcome="succeeded",
            observed_state="Running",
            capabilities=self.capabilities(),
            data={"fencing_token": execution_context.resource_leases[0]["fencing_token"]},
        )


def context(**overrides):
    values = {
        "operation_id": "operation-123",
        "correlation_id": "correlation-123",
        "initiator_type": "Human",
        "initiated_by": "operator@example.com",
        "execution_identity": "frappe-worker",
        "idempotency_key": "start-node-0001",
        "target_doctype": "Infrastructure Node",
        "target_name": "NODE-0001",
        "attempt": 1,
        "resource_leases": ({"lock_key": "node:NODE-0001", "fencing_token": 17},),
        "heartbeat": lambda: None,
        "assert_leases": lambda: None,
    }
    values.update(overrides)
    return WorkerExecutionContext(**values)


def payload(**overrides):
    values = {
        "adapter_id": "test-host-v1",
        "runtime_type": "host",
        "method": "start",
        "payload": {},
    }
    values.update(overrides)
    return values


class TestRuntimeOperationBridge(unittest.TestCase):
    def setUp(self):
        clear_registry_for_tests()
        self.adapter = MutatingTestAdapter()
        register_adapter(self.adapter)

    def tearDown(self):
        clear_registry_for_tests()

    def test_bridge_binds_request_to_worker_identity_and_returns_result(self):
        result = invoke_runtime_mutation(context(), payload())
        self.assertEqual(result["operation_id"], "operation-123")
        self.assertEqual(result["correlation_id"], "correlation-123")
        self.assertEqual(result["target_name"], "NODE-0001")
        self.assertEqual(result["observed_state"], "Running")
        self.assertEqual(result["data"]["fencing_token"], 17)

    def test_plain_mapping_cannot_authorize_mutation(self):
        with self.assertRaises(RuntimeAdapterError) as raised:
            invoke_runtime_mutation({}, payload())
        self.assertEqual(raised.exception.code, "COORDINATOR_CONTEXT_REQUIRED")

    def test_observation_methods_cannot_use_mutation_operation(self):
        with self.assertRaisesRegex(ValueError, "mutating"):
            validate_runtime_mutation_payload(payload(method="inspect"))

    def test_unknown_or_unregistered_adapter_is_rejected(self):
        with self.assertRaises(RuntimeAdapterError) as raised:
            validate_runtime_mutation_payload(payload(adapter_id="untrusted-import"))
        self.assertEqual(raised.exception.code, "UNKNOWN_ADAPTER")

    def test_adapter_without_mutation_capability_is_rejected_before_dispatch(self):
        clear_registry_for_tests()

        class ReadOnlyAdapter(MutatingTestAdapter):
            def capabilities(self):
                return ("inspect",)

        register_adapter(ReadOnlyAdapter())
        with self.assertRaises(RuntimeAdapterError) as raised:
            validate_runtime_mutation_payload(payload())
        self.assertEqual(raised.exception.code, "UNSUPPORTED_CAPABILITY")

    def test_request_identity_is_bound_to_worker_context(self):
        worker_context = context(
            idempotency_key="different-key",
            target_name="NODE-0002",
        )
        invoke_runtime_mutation(worker_context, payload())

        # Target and idempotency identity come exclusively from the trusted
        # worker context, not from caller-controlled operation payload fields.
        self.assertEqual(self.adapter.last_request.target_doctype, "Infrastructure Node")
        self.assertEqual(self.adapter.last_request.target_name, "NODE-0002")
        self.assertEqual(self.adapter.last_request.idempotency_key, "different-key")

    def test_caller_cannot_override_target_or_idempotency_identity(self):
        for field, value in (
            ("target_doctype", "Bench Environment"),
            ("target_name", "NODE-9999"),
            ("idempotency_key", "forged-key"),
        ):
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    validate_runtime_mutation_payload(payload(**{field: value}))

    def test_arbitrary_command_payload_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Caller-controlled"):
            validate_runtime_mutation_payload(payload(payload={"command": "docker rm -f all"}))


if __name__ == "__main__":
    unittest.main()
