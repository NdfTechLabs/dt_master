"""Registry definitions for Docker Engine installation, privilege bootstrap and observations.

Only fixed, server-owned handlers are registered. Engine installation uses the
existing durable coordinator, worker, node-scoped resource lease and adapter.
"""

from dt_master.operations.docker_lifecycle import (
	authorize_docker_lifecycle_node,
	docker_lifecycle_resource_lock_keys,
	validate_docker_lifecycle_payload,
)
from dt_master.operations.registry import OperationDefinition, register_operation


def _docker_payload_validator(operation_type):
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


def _docker_install_engine_handler(context, payload):
	from dt_master.runtime_adapters.operation_bridge import invoke_runtime_mutation

	return invoke_runtime_mutation(
		context,
		{
			"adapter_id": "docker-compose-v1",
			"runtime_type": "docker",
			"method": "install_engine",
			"payload": payload,
		},
	)


def _docker_privilege_bootstrap_handler(context, payload):
	from dt_master.runtime_adapters.operation_bridge import invoke_runtime_mutation

	return invoke_runtime_mutation(
		context,
		{
			"adapter_id": "docker-compose-v1",
			"runtime_type": "docker",
			"method": "bootstrap_privileges",
			"payload": {},
		},
	)


for _operation_type, _method in (
	("host.node.docker.privileges.check", "privilege_check"),
	("host.node.docker.inspect", "inspect"),
	("host.node.docker.preflight", "preflight"),
	("host.node.docker.updates.check", "updates_check"),
	("host.node.docker.verify", "verify"),
):
	register_operation(
		OperationDefinition(
			operation_type=_operation_type,
			handler=_docker_observation_handler(_method),
			validate_payload=_docker_payload_validator(_operation_type),
			authorize=authorize_docker_lifecycle_node,
			queue="short",
			timeout_seconds=90,
			max_attempts=1,
			priority="Normal",
			execution_identity="frappe-worker",
			resource_lock_keys=_docker_observation_lock,
		)
	)


register_operation(
	OperationDefinition(
		operation_type="host.node.docker.install",
		handler=_docker_install_engine_handler,
		validate_payload=_docker_payload_validator("host.node.docker.install"),
		authorize=authorize_docker_lifecycle_node,
		queue="long",
		timeout_seconds=1200,
		max_attempts=1,
		priority="High",
		execution_identity="frappe-worker",
		resource_lock_keys=docker_lifecycle_resource_lock_keys,
	)
)


def _docker_repository_prepare_handler(context, payload):
	from dt_master.runtime_adapters.operation_bridge import invoke_runtime_mutation

	return invoke_runtime_mutation(
		context,
		{
			"adapter_id": "docker-compose-v1",
			"runtime_type": "docker",
			"method": "prepare_repository",
			"payload": {},
		},
	)


register_operation(
	OperationDefinition(
		operation_type="host.node.docker.repository.prepare",
		handler=_docker_repository_prepare_handler,
		validate_payload=_docker_payload_validator("host.node.docker.repository.prepare"),
		authorize=authorize_docker_lifecycle_node,
		queue="long",
		timeout_seconds=600,
		max_attempts=1,
		priority="High",
		execution_identity="frappe-worker",
		resource_lock_keys=docker_lifecycle_resource_lock_keys,
	)
)


register_operation(
	OperationDefinition(
		operation_type="host.node.docker.privileges.bootstrap",
		handler=_docker_privilege_bootstrap_handler,
		validate_payload=_docker_payload_validator("host.node.docker.privileges.bootstrap"),
		authorize=authorize_docker_lifecycle_node,
		queue="long",
		timeout_seconds=240,
		max_attempts=1,
		priority="High",
		execution_identity="frappe-worker",
		resource_lock_keys=docker_lifecycle_resource_lock_keys,
	)
)
