"""Trusted worker execution context passed to operation handlers.

Only the durable operation worker constructs this object. Runtime adapters
require this typed context for mutation calls; plain request dictionaries are
not accepted as coordinator authority.
"""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class WorkerExecutionContext(Mapping):
    operation_id: str
    correlation_id: str
    initiator_type: str
    initiated_by: str
    execution_identity: str
    idempotency_key: str
    target_doctype: str
    target_name: str
    attempt: int
    resource_leases: tuple[dict, ...]
    heartbeat: Callable[[], Any]
    assert_leases: Callable[[], Any]

    def __getitem__(self, key: str) -> Any:
        try:
            return object.__getattribute__(self, key)
        except AttributeError as exc:
            raise KeyError(key) from exc

    def __iter__(self) -> Iterator[str]:
        return iter((
            "operation_id", "correlation_id", "initiator_type", "initiated_by",
            "execution_identity", "idempotency_key", "target_doctype",
            "target_name", "attempt", "resource_leases", "heartbeat", "assert_leases",
        ))

    def __len__(self) -> int:
        return 12


def create_worker_execution_context(*, operation, attempt, leases, heartbeat, assert_leases):
    """Create the immutable context after the worker has claimed its leases."""
    return WorkerExecutionContext(
        operation_id=operation.operation_id,
        correlation_id=operation.correlation_id,
        initiator_type=operation.initiator_type,
        initiated_by=operation.initiated_by,
        execution_identity=operation.execution_identity,
        idempotency_key=operation.idempotency_key,
        target_doctype=operation.target_doctype,
        target_name=operation.target_name,
        attempt=attempt,
        resource_leases=tuple(
            {"lock_key": item["lock_key"], "fencing_token": item["fencing_token"]}
            for item in leases
        ),
        heartbeat=heartbeat,
        assert_leases=assert_leases,
    )
