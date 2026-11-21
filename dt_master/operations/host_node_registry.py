"""Durable Node-scoped Host observation operations."""

import frappe

from dt_master.operations.registry import OperationDefinition, register_operation


def _authorize_node(target):
	frappe.only_for("System Manager")
	target.check_permission("read")
	if not getattr(target, "access_verified", False):
		frappe.throw("Node SSH access must be verified before this operation.", frappe.PermissionError)


def _authorize_connection_check(target):
	"""Authorize the first SSH probe without requiring its result in advance."""
	frappe.only_for("System Manager")
	target.check_permission("read")
	if not (
		getattr(target, "ssh_host_alias", None)
		or (getattr(target, "ip_address", None) and getattr(target, "ssh_user", None))
	):
		frappe.throw("Configure an SSH host alias or IP address and SSH user first.", frappe.ValidationError)


def _validate_empty_payload(payload):
	if not isinstance(payload, dict) or payload:
		frappe.throw("Node observation operations do not accept payload fields.", frappe.ValidationError)
	return {}


def _host_observation_handler(method):
	def handle(context, payload):
		from dt_master.runtime_adapters.operation_bridge import invoke_runtime_observation

		return invoke_runtime_observation(
			context,
			{
				"adapter_id": "host-v1",
				"runtime_type": "host",
				"method": method,
				"payload": {},
			},
		)

	return handle


def _node_observation_lock(target, operation):
	if getattr(target, "doctype", None) != "Infrastructure Node" or not getattr(target, "name", None):
		raise ValueError("Host observation requires an Infrastructure Node target")
	return [f"node-host-observation:{target.name}"]


for _operation_type, _method, _timeout, _authorize in (
	("host.node.inspect", "inspect", 90, _authorize_node),
	("host.node.connection.check", "connection_check", 30, _authorize_connection_check),
):
	register_operation(
		OperationDefinition(
			operation_type=_operation_type,
			handler=_host_observation_handler(_method),
			validate_payload=_validate_empty_payload,
			authorize=_authorize,
			queue="short",
			timeout_seconds=_timeout,
			max_attempts=1,
			priority="Normal",
			execution_identity="frappe-worker",
			resource_lock_keys=_node_observation_lock,
		)
	)
