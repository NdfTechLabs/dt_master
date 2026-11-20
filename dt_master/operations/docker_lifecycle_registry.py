"""Registry definitions for safe, read-only Docker lifecycle operations.

Only read-only observations with fixed, tested handlers are registered here.
Post-change verification remains deferred until its independent contract is implemented.
"""

from dt_master.operations.registry import OperationDefinition, register_operation
from dt_master.operations.docker_lifecycle import (
    authorize_docker_lifecycle_node,
    validate_docker_lifecycle_payload,
)


def _docker_observation_payload(operation_type):
    def validate(payload):
        try:
            return validate_docker_lifecycle_payload(operation_type, payload)
        except (TypeError, ValueError) as exc:
            import frappe
            frappe.throw(str(exc), frappe.ValidationError)
    return validate


def _docker_observation_handler(method):
    def handle(context, payload):
        from dt_master.runtime_adapters.operation_bridge import invoke_runtime_observation

        return invoke_runtime_observation(
            context,
            {
                "adapter_id": "docker-compose-v1",
                "runtime_type": "docker",
                "method": method,
                "payload": {},
            },
        )
    return handle


def _docker_observation_lock(target, operation):
    if getattr(target, "doctype", None) != "Infrastructure Node" or not getattr(target, "name", None):
        raise ValueError("Docker observation requires an Infrastructure Node target")
    return [f"node-docker-observation:{target.name}"]


for _operation_type, _method in (
    ("host.node.docker.inspect", "inspect"),
    ("host.node.docker.preflight", "preflight"),
    ("host.node.docker.updates.check", "updates_check"),
):
    register_operation(
        OperationDefinition(
            operation_type=_operation_type,
            handler=_docker_observation_handler(_method),
            validate_payload=_docker_observation_payload(_operation_type),
            authorize=authorize_docker_lifecycle_node,
            queue="short",
            timeout_seconds=90,
            max_attempts=1,
            priority="Normal",
            execution_identity="frappe-worker",
            resource_lock_keys=_docker_observation_lock,
        )
    )
