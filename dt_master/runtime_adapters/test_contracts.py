"""Tests for the versioned runtime adapter contract and registry."""

import unittest

from dt_master.operations.execution_context import WorkerExecutionContext
from dt_master.runtime_adapters.base import RuntimeAdapter
from dt_master.runtime_adapters.contracts import (
    ADAPTER_CONTRACT_VERSION,
    RuntimeAdapterError,
    RuntimeAdapterRequest,
    RuntimeAdapterResult,
    validate_request,
    validate_result,
)
from dt_master.runtime_adapters.registry import (
    clear_registry_for_tests,
    get_adapter,
    register_adapter,
    registered_adapters,
    resolve_adapter,
)


def observation_request(**overrides):
    values = {
        "adapter_id": "host-v1",
        "runtime_type": "host",
        "target_doctype": "Infrastructure Node",
        "target_name": "NODE-0001",
        "method": "inspect",
        "payload": {},
    }
    values.update(overrides)
    return RuntimeAdapterRequest(**values)


def mutation_request(**overrides):
    values = {
        "adapter_id": "host-v1",
        "runtime_type": "host",
        "target_doctype": "Bench Environment",
        "target_name": "BENCH-0001",
        "method": "start",
        "operation_id": "operation-123",
        "correlation_id": "correlation-123",
        "initiator_type": "Human",
        "initiated_by": "operator@example.com",
        "execution_identity": "worker@example.com",
        "idempotency_key": "start-bench-0001",
        "payload": {},
    }
    values.update(overrides)
    return RuntimeAdapterRequest(**values)



def worker_context(**overrides):
    values = {
        "operation_id": "operation-123",
        "correlation_id": "correlation-123",
        "initiator_type": "Human",
        "initiated_by": "operator@example.com",
        "execution_identity": "worker@example.com",
        "idempotency_key": "start-bench-0001",
        "target_doctype": "Bench Environment",
        "target_name": "BENCH-0001",
        "attempt": 1,
        "resource_leases": ({"lock_key": "bench:BENCH-0001", "fencing_token": 1},),
        "assert_leases": lambda: None,
        "heartbeat": lambda: None,
    }
    values.update(overrides)
    return WorkerExecutionContext(**values)

class HostTestAdapter(RuntimeAdapter):
    adapter_id = "host-v1"
    runtime_type = "host"

    def capabilities(self):
        return ("inspect", "preflight")

    def _inspect(self, request, execution_context=None):
        return self.result_for(
            request, outcome="succeeded", observed_state="Available",
            capabilities=self.capabilities(),
        )


class DockerTestAdapter(HostTestAdapter):
    adapter_id = "docker-compose-v1"
    runtime_type = "docker"


class TestRuntimeAdapterContracts(unittest.TestCase):
    def test_valid_observation_request(self):
        request = validate_request(observation_request())
        self.assertEqual(request.contract_version, ADAPTER_CONTRACT_VERSION)

    def test_request_mapping_round_trip(self):
        request = observation_request()
        self.assertEqual(validate_request(request.to_dict()), request)

    def test_unsupported_contract_version_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "version"):
            validate_request(observation_request(contract_version="2.0.0"))

    def test_unknown_method_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "method"):
            validate_request(observation_request(method="exec"))

    def test_secret_values_are_rejected_but_references_are_allowed(self):
        with self.assertRaisesRegex(ValueError, "Secret material"):
            validate_request(observation_request(payload={"database_password": "secret"}))
        self.assertEqual(
            validate_request(observation_request(payload={"database_secret_ref": "vault://db"})).payload,
            {"database_secret_ref": "vault://db"},
        )

    def test_caller_runtime_commands_and_compose_text_are_rejected(self):
        for key in ("command", "script_path", "docker_host", "docker_context", "compose_yaml", "use_sudo"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_request(observation_request(payload={key: "caller-controlled"}))

    def test_mutation_requires_complete_coordinator_identity(self):
        with self.assertRaisesRegex(ValueError, "coordinator identity"):
            validate_request(mutation_request(operation_id=None))

    def test_request_payload_must_be_json_compatible(self):
        with self.assertRaisesRegex(ValueError, "JSON-compatible"):
            validate_request(observation_request(payload={"not_json": object()}))

    def test_request_size_is_bounded(self):
        with self.assertRaisesRegex(ValueError, "64 KiB"):
            validate_request(observation_request(payload={"large": "x" * 70000}))

    def test_valid_result(self):
        result = RuntimeAdapterResult(
            adapter_id="host-v1", runtime_type="host",
            target_doctype="Infrastructure Node", target_name="NODE-0001",
            method="inspect", outcome="succeeded", observed_state="Available",
            capabilities=("inspect",), diagnostics=(),
        )
        self.assertEqual(validate_result(result), result)

    def test_result_rejects_invalid_diagnostic(self):
        result = RuntimeAdapterResult(
            adapter_id="host-v1", runtime_type="host",
            target_doctype="Infrastructure Node", target_name="NODE-0001",
            method="inspect", outcome="failed", observed_state="Unknown",
            diagnostics=({"code": "bad code", "severity": "fatal", "message": "x"},),
        )
        with self.assertRaises(ValueError):
            validate_result(result)

    def test_result_rejects_secret_material(self):
        result = RuntimeAdapterResult(
            adapter_id="host-v1", runtime_type="host",
            target_doctype="Infrastructure Node", target_name="NODE-0001",
            method="inspect", outcome="succeeded", observed_state="Available",
            data={"api_token": "do-not-return"},
        )
        with self.assertRaisesRegex(ValueError, "Secret material"):
            validate_result(result)

    def test_result_size_is_bounded(self):
        result = RuntimeAdapterResult(
            adapter_id="host-v1", runtime_type="host",
            target_doctype="Infrastructure Node", target_name="NODE-0001",
            method="inspect", outcome="succeeded", observed_state="Available",
            data={"large": "x" * 70000},
        )
        with self.assertRaisesRegex(ValueError, "64 KiB"):
            validate_result(result)


class TestRuntimeAdapterRegistry(unittest.TestCase):
    def setUp(self):
        clear_registry_for_tests()

    def tearDown(self):
        clear_registry_for_tests()

    def test_register_and_resolve_by_runtime(self):
        adapter = register_adapter(HostTestAdapter())
        self.assertIs(resolve_adapter("host"), adapter)
        self.assertEqual(get_adapter("host-v1"), adapter)

    def test_explicit_adapter_id_is_server_resolved_and_type_checked(self):
        register_adapter(HostTestAdapter())
        with self.assertRaises(RuntimeAdapterError) as raised:
            resolve_adapter("docker", "host-v1")
        self.assertEqual(raised.exception.code, "RUNTIME_TYPE_MISMATCH")

    def test_unknown_adapter_is_rejected(self):
        with self.assertRaises(RuntimeAdapterError) as raised:
            get_adapter("caller-selected-import-path")
        self.assertEqual(raised.exception.code, "UNKNOWN_ADAPTER")

    def test_ambiguous_runtime_requires_explicit_selection(self):
        register_adapter(HostTestAdapter())
        class AlternateHost(HostTestAdapter):
            adapter_id = "host-alt-v1"
        register_adapter(AlternateHost())
        with self.assertRaises(RuntimeAdapterError) as raised:
            resolve_adapter("host")
        self.assertEqual(raised.exception.code, "AMBIGUOUS_ADAPTER")

    def test_duplicate_adapter_id_is_rejected(self):
        register_adapter(HostTestAdapter())
        with self.assertRaisesRegex(ValueError, "already registered"):
            register_adapter(HostTestAdapter())

    def test_registry_lists_only_server_registered_adapters(self):
        register_adapter(HostTestAdapter())
        self.assertEqual(registered_adapters(), ({
            "adapter_id": "host-v1", "runtime_type": "host",
            "contract_version": ADAPTER_CONTRACT_VERSION,
            "capabilities": ("inspect", "preflight"),
        },))


class TestRuntimeAdapterInvocation(unittest.TestCase):
    def setUp(self):
        self.adapter = HostTestAdapter()

    def test_inspection_dispatches_and_returns_correlated_result(self):
        result = self.adapter.invoke("inspect", observation_request())
        self.assertEqual(result.outcome, "succeeded")
        self.assertEqual(result.adapter_id, "host-v1")
        self.assertEqual(result.target_name, "NODE-0001")

    def test_unsupported_capability_fails_explicitly(self):
        with self.assertRaises(RuntimeAdapterError) as raised:
            self.adapter.invoke("start", mutation_request())
        self.assertEqual(raised.exception.code, "UNSUPPORTED_CAPABILITY")

    def test_mutation_requires_worker_context_before_handler(self):
        class MutatingAdapter(HostTestAdapter):
            def capabilities(self):
                return ("start",)
            def _start(self, request, execution_context=None):
                raise AssertionError("Handler must not run without coordinator context")
        with self.assertRaises(RuntimeAdapterError) as raised:
            MutatingAdapter().invoke("start", mutation_request())
        self.assertEqual(raised.exception.code, "COORDINATOR_CONTEXT_REQUIRED")

    def test_mutation_rejects_request_identity_not_matching_worker_context(self):
        class MutatingAdapter(HostTestAdapter):
            def capabilities(self):
                return ("start",)
            def _start(self, request, execution_context=None):
                raise AssertionError("Handler must not run when request identity differs")
        context = worker_context(operation_id="different-operation")
        with self.assertRaises(RuntimeAdapterError) as raised:
            MutatingAdapter().invoke("start", mutation_request(), execution_context=context)
        self.assertEqual(raised.exception.code, "COORDINATOR_CONTEXT_MISMATCH")


    def test_mutation_rejects_plain_dictionary_context(self):
        class MutatingAdapter(HostTestAdapter):
            def capabilities(self):
                return ("start",)
            def _start(self, request, execution_context=None):
                raise AssertionError("Handler must not run with an untrusted mapping")
        with self.assertRaises(RuntimeAdapterError) as raised:
            MutatingAdapter().invoke("start", mutation_request(), execution_context={
                "operation_id": "operation-123", "correlation_id": "correlation-123",
                "initiator_type": "Human", "initiated_by": "operator@example.com",
                "execution_identity": "worker@example.com", "idempotency_key": "start-bench-0001",
                "target_doctype": "Bench Environment", "target_name": "BENCH-0001",
                "assert_leases": lambda: None, "heartbeat": lambda: None,
            })
        self.assertEqual(raised.exception.code, "COORDINATOR_CONTEXT_REQUIRED")

    def test_mutation_checks_initiator_and_idempotency_identity(self):
        class MutatingAdapter(HostTestAdapter):
            def capabilities(self):
                return ("start",)
            def _start(self, request, execution_context=None):
                raise AssertionError("Handler must not run when identity differs")
        for field, value in (("initiator_type", "System"), ("idempotency_key", "other-key")):
            with self.subTest(field=field):
                with self.assertRaises(RuntimeAdapterError) as raised:
                    MutatingAdapter().invoke(
                        "start", mutation_request(), execution_context=worker_context(**{field: value})
                    )
                self.assertEqual(raised.exception.code, "COORDINATOR_CONTEXT_MISMATCH")

    def test_mutation_asserts_leases_before_and_after_handler(self):
        calls = []
        class MutatingAdapter(HostTestAdapter):
            def capabilities(self):
                return ("start",)
            def _start(self, request, execution_context=None):
                return self.result_for(request, outcome="succeeded", observed_state="Running")
        context = worker_context(
            assert_leases=lambda: calls.append("assert"),
            heartbeat=lambda: calls.append("heartbeat"),
        )
        result = MutatingAdapter().invoke("start", mutation_request(), execution_context=context)
        self.assertEqual(result.observed_state, "Running")
        self.assertEqual(calls, ["assert", "assert"])


if __name__ == "__main__":
    unittest.main()
