"""Tests that the Infrastructure Node Docker button uses durable adapter inspection."""

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from dt_master.api import infrastructure_node


class TestDockerInspectionRouting(unittest.TestCase):
    def test_inspection_endpoint_submits_docker_adapter_operation(self):
        node = SimpleNamespace(
            name="NODE-DOCKER-01",
            access_verified=1,
            check_permission=lambda permission: self.assertEqual(permission, "read"),
        )
        with (
            patch.object(infrastructure_node.frappe, "get_doc", return_value=node),
            patch.object(infrastructure_node.frappe, "only_for") as only_for,
            patch.object(infrastructure_node, "start_operation", return_value={"operation_id": "op-123"}) as start,
        ):
            result = infrastructure_node.inspect_docker_runtime("NODE-DOCKER-01")

        only_for.assert_called_once_with("System Manager")
        self.assertEqual(result, {"operation_id": "op-123"})
        kwargs = start.call_args.kwargs
        self.assertEqual(kwargs["operation_type"], "runtime.adapter.inspect")
        self.assertEqual(kwargs["target_doctype"], "Infrastructure Node")
        self.assertEqual(kwargs["target_name"], "NODE-DOCKER-01")
        self.assertEqual(kwargs["payload"], {
            "adapter_id": "docker-compose-v1",
            "runtime_type": "docker",
            "method": "inspect",
            "payload": {},
        })
        self.assertTrue(kwargs["idempotency_key"].startswith("docker-inspect:NODE-DOCKER-01:"))

    def test_inspection_endpoint_rejects_unverified_ssh_before_dispatch(self):
        node = SimpleNamespace(
            name="NODE-DOCKER-01",
            access_verified=0,
            check_permission=lambda permission: None,
        )
        with (
            patch.object(infrastructure_node.frappe, "get_doc", return_value=node),
            patch.object(infrastructure_node.frappe, "only_for"),
            patch.object(infrastructure_node, "start_operation") as start,
            self.assertRaises(Exception),
        ):
            infrastructure_node.inspect_docker_runtime("NODE-DOCKER-01")
        start.assert_not_called()

    def test_browser_button_no_longer_opens_legacy_script_log_stream(self):
        script = Path(__file__).parents[1] / "dt_master" / "doctype" / "infrastructure_node" / "infrastructure_node.js"
        content = script.read_text()
        start = content.index("function run_docker_inspection(frm) {")
        end = content.index("\n\nfunction render_docker_readiness_summary", start)
        action = content[start:end]
        self.assertIn("dt_master.api.infrastructure_node.inspect_docker_runtime", action)
        self.assertIn("Task Manager", action)
        self.assertNotIn("create_log_dialog", action)
        self.assertNotIn("start_execution_log_stream", action)


if __name__ == "__main__":
    unittest.main()
