# Copyright (c) 2026, Dhana Technologies and contributors
# For license information, please see license.txt

from dataclasses import dataclass
from typing import Callable

import frappe
from frappe import _


@dataclass(frozen=True)
class OperationDefinition:
    operation_type: str
    handler: Callable
    validate_payload: Callable
    authorize: Callable
    queue: str = "short"
    timeout_seconds: int = 300
    max_attempts: int = 1
    priority: str = "Normal"
    execution_identity: str = "frappe-worker"
    contract_version: str = "1.0.0"


_REGISTRY = {}


def register_operation(definition):
    """Register a server-owned operation definition; duplicate IDs are errors."""
    if not isinstance(definition, OperationDefinition):
        raise TypeError("definition must be an OperationDefinition")
    if not definition.operation_type or definition.operation_type in _REGISTRY:
        raise ValueError(f"Invalid or duplicate operation type: {definition.operation_type!r}")
    if not callable(definition.handler) or not callable(definition.validate_payload):
        raise TypeError("Operation handler and payload validator must be callable")
    if not callable(definition.authorize):
        raise TypeError("Operation authorizer must be callable")
    if definition.queue not in {"short", "default", "long"}:
        raise ValueError("Operation queue must be a supported Frappe queue")
    if not 1 <= definition.timeout_seconds <= 86400:
        raise ValueError("Operation timeout must be between 1 and 86400 seconds")
    if not 1 <= definition.max_attempts <= 5:
        raise ValueError("Operation max_attempts must be between 1 and 5")
    _REGISTRY[definition.operation_type] = definition
    return definition


def get_operation(operation_type):
    definition = _REGISTRY.get(operation_type)
    if not definition:
        frappe.throw(_("Unsupported operation type: {0}").format(operation_type), frappe.ValidationError)
    return definition


def registered_operation_types():
    return tuple(sorted(_REGISTRY))


def validate_object_payload(payload):
    if not isinstance(payload, dict):
        frappe.throw(_("Operation payload must be a JSON object."), frappe.ValidationError)
    return payload


def authorize_system_manager(target):
    """Default narrow authorizer for the initial platform-only integration."""
    frappe.only_for("System Manager")
    target.check_permission("read")


def _noop_handler(context, payload):
    """Harmless integration handler; deliberately performs no infrastructure mutation."""
    return {"status": "ok", "message": "Operation coordinator worker executed successfully."}


def _noop_payload(payload):
    payload = validate_object_payload(payload)
    if payload:
        frappe.throw(_("The test.noop operation does not accept payload fields."), frappe.ValidationError)
    return {}


# The first integration proves acceptance, dispatch and worker execution without
# changing Infrastructure Node, Docker, Bench or Tenant Site state.
register_operation(
    OperationDefinition(
        operation_type="test.noop",
        handler=_noop_handler,
        validate_payload=_noop_payload,
        authorize=authorize_system_manager,
        queue="short",
        timeout_seconds=60,
        max_attempts=1,
        priority="Normal",
        execution_identity="frappe-worker",
    )
)
