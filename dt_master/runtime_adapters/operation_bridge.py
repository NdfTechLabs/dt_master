"""Durable operation to runtime adapter invocation bridge.

This module is called only by a registered Infrastructure Operation handler.
It accepts the typed context created by the durable worker, resolves adapter
identity from the server registry, and binds all request identity to the
persisted operation and its active resource leases.
"""

from dt_master.operations.execution_context import WorkerExecutionContext
from dt_master.runtime_adapters.contracts import (
    MUTATING_METHODS,
    RuntimeAdapterRequest,
    RuntimeAdapterError,
    validate_request,
)
from dt_master.runtime_adapters.registry import resolve_adapter


_ALLOWED_PAYLOAD_KEYS = {"adapter_id", "runtime_type", "method", "payload"}


def validate_runtime_mutation_payload(payload):
    """Validate and normalize a Node-targeted adapter mutation request."""
    if not isinstance(payload, dict) or set(payload) != _ALLOWED_PAYLOAD_KEYS:
        raise ValueError("Runtime mutation payload must contain adapter_id, runtime_type, method and payload")
    if payload["method"] not in MUTATING_METHODS:
        raise ValueError("Durable runtime operation accepts mutating adapter methods only")
    if not isinstance(payload["runtime_type"], str) or not isinstance(payload["adapter_id"], str):
        raise ValueError("Runtime type and adapter ID must be strings")
    if not isinstance(payload["payload"], dict):
        raise ValueError("Runtime adapter payload must be an object")
    adapter = resolve_adapter(payload["runtime_type"], payload["adapter_id"])
    if not adapter.supports(payload["method"]):
        raise RuntimeAdapterError(
            "UNSUPPORTED_CAPABILITY",
            "The selected server-registered adapter does not implement the requested mutation.",
        )
    return {
        "adapter_id": payload["adapter_id"],
        "runtime_type": payload["runtime_type"],
        "method": payload["method"],
        "payload": payload["payload"],
    }


def invoke_runtime_mutation(execution_context, payload):
    """Invoke one adapter mutation under the active durable operation leases."""
    if not isinstance(execution_context, WorkerExecutionContext):
        raise RuntimeAdapterError(
            "COORDINATOR_CONTEXT_REQUIRED",
            "Runtime mutations require the typed durable worker execution context.",
        )
    normalized = validate_runtime_mutation_payload(payload)
    request = validate_request(RuntimeAdapterRequest(
        adapter_id=normalized["adapter_id"],
        runtime_type=normalized["runtime_type"],
        target_doctype=execution_context.target_doctype,
        target_name=execution_context.target_name,
        method=normalized["method"],
        payload=normalized["payload"],
        operation_id=execution_context.operation_id,
        correlation_id=execution_context.correlation_id,
        initiator_type=execution_context.initiator_type,
        initiated_by=execution_context.initiated_by,
        execution_identity=execution_context.execution_identity,
        idempotency_key=execution_context.idempotency_key,
    ))
    adapter = resolve_adapter(request.runtime_type, request.adapter_id)
    result = adapter.invoke(
        request.method,
        request,
        execution_context=execution_context,
    )
    return result.to_dict()
