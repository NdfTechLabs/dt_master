"""Runtime-neutral adapter contract and server-side adapter registry."""

from dt_master.runtime_adapters.contracts import (
    ADAPTER_CONTRACT_VERSION,
    MUTATING_METHODS,
    OBSERVATION_METHODS,
    RuntimeAdapterError,
    RuntimeAdapterRequest,
    RuntimeAdapterResult,
    validate_request,
    validate_result,
)
from dt_master.runtime_adapters.registry import (
    get_adapter,
    registered_adapters,
    register_adapter,
    resolve_adapter,
)

__all__ = [
    "ADAPTER_CONTRACT_VERSION",
    "MUTATING_METHODS",
    "OBSERVATION_METHODS",
    "RuntimeAdapterError",
    "RuntimeAdapterRequest",
    "RuntimeAdapterResult",
    "validate_request",
    "validate_result",
    "get_adapter",
    "registered_adapters",
    "register_adapter",
    "resolve_adapter",
]
