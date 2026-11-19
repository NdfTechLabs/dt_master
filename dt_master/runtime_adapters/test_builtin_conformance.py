"""Shared contract conformance checks for the built-in runtime adapters."""

import unittest

from dt_master.runtime_adapters.contracts import RuntimeAdapterError, RuntimeAdapterRequest
from dt_master.runtime_adapters.docker import DockerRuntimeAdapter
from dt_master.runtime_adapters.host import HostRuntimeAdapter


class TestBuiltinRuntimeAdapterConformance(unittest.TestCase):
    def setUp(self):
        self.adapters = (
            (HostRuntimeAdapter(), "host-v1", "host"),
            (DockerRuntimeAdapter(), "docker-compose-v1", "docker"),
        )

    def test_builtins_share_contract_identity_and_observation_capabilities(self):
        for adapter, adapter_id, runtime_type in self.adapters:
            with self.subTest(adapter=adapter_id):
                self.assertEqual(adapter.adapter_id, adapter_id)
                self.assertEqual(adapter.runtime_type, runtime_type)
                self.assertEqual(adapter.contract_version, "1.0.0")
                self.assertEqual(adapter.capabilities(), ("inspect", "preflight"))

    def test_builtins_reject_mutations_before_target_access(self):
        for adapter, adapter_id, runtime_type in self.adapters:
            request = RuntimeAdapterRequest(
                adapter_id=adapter_id,
                runtime_type=runtime_type,
                target_doctype="Infrastructure Node",
                target_name="NODE-0001",
                method="start",
                operation_id="operation-123",
                correlation_id="correlation-123",
                initiator_type="Human",
                initiated_by="operator@example.com",
                execution_identity="worker",
                idempotency_key="start-node-0001",
                payload={},
            )
            with self.subTest(adapter=adapter_id):
                with self.assertRaises(RuntimeAdapterError) as raised:
                    adapter.invoke("start", request)
                self.assertEqual(raised.exception.code, "UNSUPPORTED_CAPABILITY")

    def test_builtins_do_not_advertise_unimplemented_lifecycle_methods(self):
        forbidden = {"provision", "start", "stop", "deploy", "storage_prepare", "backup", "restore"}
        for adapter, adapter_id, _runtime_type in self.adapters:
            with self.subTest(adapter=adapter_id):
                self.assertFalse(forbidden.intersection(adapter.capabilities()))


if __name__ == "__main__":
    unittest.main()
