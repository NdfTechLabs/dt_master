"""Common runtime adapter interface."""

from abc import ABC
from datetime import datetime, timezone

from dt_master.runtime_adapters.contracts import (
    ADAPTER_CONTRACT_VERSION,
    MUTATING_METHODS,
    RuntimeAdapterError,
    RuntimeAdapterRequest,
    RuntimeAdapterResult,
    validate_request,
    validate_result,
)


class RuntimeAdapter(ABC):
    """Runtime mechanics boundary shared by Host and Docker implementations.

    Implementations are registered by trusted application code. They must not
    authorize users or create/dispatch durable operations themselves.
    """

    adapter_id = None
    runtime_type = None
    contract_version = ADAPTER_CONTRACT_VERSION

    def capabilities(self) -> tuple[str, ...]:
        """Return only operations safely implemented by this adapter."""
        return ()

    def supports(self, method: str) -> bool:
        return method in self.capabilities()

    def invoke(self, method: str, request: RuntimeAdapterRequest, *, execution_context=None):
        request = validate_request(request)
        if request.adapter_id != self.adapter_id or request.runtime_type != self.runtime_type:
            raise RuntimeAdapterError("ADAPTER_IDENTITY_MISMATCH", "Request identity does not match the selected adapter.")
        if request.contract_version != self.contract_version:
            raise RuntimeAdapterError("UNSUPPORTED_CONTRACT_VERSION", "Adapter contract version is not supported.")
        if request.method != method:
            raise RuntimeAdapterError("METHOD_MISMATCH", "Request method does not match the invoked method.")
        if method not in self.capabilities():
            raise RuntimeAdapterError("UNSUPPORTED_CAPABILITY", f"Adapter '{self.adapter_id}' does not support '{method}'.")
        if method in MUTATING_METHODS:
            self._require_execution_context(execution_context)

        handler = getattr(self, f"_{method}", None)
        if not callable(handler):
            raise RuntimeAdapterError("UNSUPPORTED_CAPABILITY", f"Adapter '{self.adapter_id}' has no implementation for '{method}'.")
        started = datetime.now(timezone.utc)
        if method in MUTATING_METHODS:
            execution_context["assert_leases"]()
        raw = handler(request, execution_context=execution_context)
        if method in MUTATING_METHODS:
            execution_context["assert_leases"]()
        if not isinstance(raw, RuntimeAdapterResult):
            raise RuntimeAdapterError("INVALID_ADAPTER_RESULT", "Adapter implementation returned an invalid result type.")
        if raw.adapter_id != self.adapter_id or raw.runtime_type != self.runtime_type:
            raise RuntimeAdapterError("ADAPTER_IDENTITY_MISMATCH", "Adapter returned a result with mismatched identity.")
        result = validate_result(raw)
        if result.method != method or result.target_doctype != request.target_doctype or result.target_name != request.target_name:
            raise RuntimeAdapterError("RESULT_TARGET_MISMATCH", "Adapter result does not match the requested target or method.")
        return result

    @staticmethod
    def _require_execution_context(context):
        required = ("operation_id", "correlation_id", "initiated_by", "execution_identity",
                    "target_doctype", "target_name", "assert_leases", "heartbeat")
        if not isinstance(context, dict) or any(key not in context for key in required):
            raise RuntimeAdapterError(
                "COORDINATOR_CONTEXT_REQUIRED",
                "Mutating adapter methods require the durable operation worker execution context.",
            )
        if not callable(context["assert_leases"]) or not callable(context["heartbeat"]):
            raise RuntimeAdapterError(
                "COORDINATOR_CONTEXT_REQUIRED",
                "Mutating adapter methods require active lease assertion and heartbeat callbacks.",
            )

    @staticmethod
    def result_for(request, *, outcome, observed_state, capabilities=(), diagnostics=(), data=None, duration_ms=0):
        return RuntimeAdapterResult(
            adapter_id=request.adapter_id,
            runtime_type=request.runtime_type,
            target_doctype=request.target_doctype,
            target_name=request.target_name,
            method=request.method,
            operation_id=request.operation_id,
            correlation_id=request.correlation_id,
            outcome=outcome,
            observed_state=observed_state,
            capabilities=tuple(capabilities),
            diagnostics=tuple(diagnostics),
            data=data or {},
            observed_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            duration_ms=duration_ms,
        )
