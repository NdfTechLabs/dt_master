"""Versioned, runtime-neutral adapter request and result contracts.

This module is deliberately independent of Frappe and remote execution. It
validates the boundary data; adapters own runtime mechanics and callers own
authorization/orchestration.
"""

from dataclasses import dataclass, field
import json
import re
from typing import Any

ADAPTER_CONTRACT_VERSION = "1.0.0"
MUTATING_METHODS = frozenset({
    "provision", "start", "stop", "deploy", "storage_prepare", "backup", "restore",
})
OBSERVATION_METHODS = frozenset({"inspect", "preflight", "endpoints", "health"})
ADAPTER_METHODS = MUTATING_METHODS | OBSERVATION_METHODS
RUNTIME_TYPES = frozenset({"host", "docker"})
OUTCOMES = frozenset({"succeeded", "partial", "failed"})
DIAGNOSTIC_SEVERITIES = frozenset({"info", "warning", "error"})
MAX_REQUEST_BYTES = 65536
MAX_RESULT_BYTES = 65536
_MAX_DIAGNOSTICS = 100
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,139}$")
_ADAPTER_ID = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_SECRET_KEY_PARTS = ("password", "token", "secret", "private_key", "credential")
_FORBIDDEN_INPUT_KEYS = frozenset({
    "command", "shell_command", "script_path", "docker_host", "docker_context",
    "privilege_escalation", "use_sudo", "compose_yaml", "compose_file",
})


class RuntimeAdapterError(Exception):
    """Stable, safe adapter error suitable for contract-level handling."""

    def __init__(self, code: str, message: str, *, details: dict | None = None):
        if not isinstance(code, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{1,63}", code):
            raise ValueError("Adapter error code must be a stable uppercase identifier")
        super().__init__(message)
        self.code = code
        self.message = str(message)[:1000]
        self.details = details or {}


def _is_json_value(value: Any) -> bool:
    if value is None or isinstance(value, (str, bool, int, float)):
        return not isinstance(value, float) or (value == value and abs(value) != float("inf"))
    if isinstance(value, list):
        return all(_is_json_value(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _is_json_value(item) for key, item in value.items())
    return False


def _validate_payload_safety(value: Any, path: str = "payload") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = key.lower()
            if any(part in normalized for part in _SECRET_KEY_PARTS) and not normalized.endswith("_ref"):
                raise ValueError(f"Secret material is not permitted in adapter requests ({path}.{key}); use a secret reference")
            if normalized in _FORBIDDEN_INPUT_KEYS:
                raise ValueError(f"Caller-controlled runtime instruction is not permitted ({path}.{key})")
            _validate_payload_safety(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _validate_payload_safety(item, f"{path}[{index}]")


@dataclass(frozen=True)
class RuntimeAdapterRequest:
    adapter_id: str
    runtime_type: str
    target_doctype: str
    target_name: str
    method: str
    payload: dict = field(default_factory=dict)
    contract_version: str = ADAPTER_CONTRACT_VERSION
    operation_id: str | None = None
    correlation_id: str | None = None
    initiator_type: str | None = None
    initiated_by: str | None = None
    execution_identity: str | None = None
    idempotency_key: str | None = None

    def to_dict(self) -> dict:
        return {
            "contract_version": self.contract_version,
            "adapter_id": self.adapter_id,
            "runtime_type": self.runtime_type,
            "target_doctype": self.target_doctype,
            "target_name": self.target_name,
            "method": self.method,
            "operation_id": self.operation_id,
            "correlation_id": self.correlation_id,
            "initiator_type": self.initiator_type,
            "initiated_by": self.initiated_by,
            "execution_identity": self.execution_identity,
            "idempotency_key": self.idempotency_key,
            "payload": self.payload,
        }


@dataclass(frozen=True)
class RuntimeAdapterResult:
    adapter_id: str
    runtime_type: str
    target_doctype: str
    target_name: str
    method: str
    outcome: str
    observed_state: str
    diagnostics: tuple[dict, ...] = ()
    capabilities: tuple[str, ...] = ()
    data: dict = field(default_factory=dict)
    contract_version: str = ADAPTER_CONTRACT_VERSION
    operation_id: str | None = None
    correlation_id: str | None = None
    observed_at: str | None = None
    duration_ms: int = 0

    def to_dict(self) -> dict:
        return {
            "contract_version": self.contract_version,
            "adapter_id": self.adapter_id,
            "runtime_type": self.runtime_type,
            "target_doctype": self.target_doctype,
            "target_name": self.target_name,
            "method": self.method,
            "operation_id": self.operation_id,
            "correlation_id": self.correlation_id,
            "outcome": self.outcome,
            "observed_state": self.observed_state,
            "capabilities": list(self.capabilities),
            "diagnostics": list(self.diagnostics),
            "data": self.data,
            "observed_at": self.observed_at,
            "duration_ms": self.duration_ms,
        }


def validate_request(request: RuntimeAdapterRequest | dict) -> RuntimeAdapterRequest:
    if isinstance(request, dict):
        try:
            request = RuntimeAdapterRequest(**request)
        except TypeError as exc:
            raise ValueError(f"Invalid adapter request fields: {exc}") from exc
    if not isinstance(request, RuntimeAdapterRequest):
        raise TypeError("Adapter request must be RuntimeAdapterRequest or a mapping")
    if request.contract_version != ADAPTER_CONTRACT_VERSION:
        raise ValueError("Unsupported runtime adapter contract version")
    if not isinstance(request.adapter_id, str) or not _ADAPTER_ID.fullmatch(request.adapter_id):
        raise ValueError("Invalid adapter ID")
    if request.runtime_type not in RUNTIME_TYPES:
        raise ValueError("Unsupported runtime type")
    if not isinstance(request.target_doctype, str) or not request.target_doctype.strip():
        raise ValueError("Target DocType is required")
    if not isinstance(request.target_name, str) or not _ID.fullmatch(request.target_name):
        raise ValueError("Invalid target name")
    if request.method not in ADAPTER_METHODS:
        raise ValueError("Unsupported adapter method")
    if not isinstance(request.payload, dict) or not _is_json_value(request.payload):
        raise ValueError("Adapter payload must be a JSON-compatible object")
    _validate_payload_safety(request.payload)
    if request.method in MUTATING_METHODS:
        required = {
            "operation_id": request.operation_id,
            "correlation_id": request.correlation_id,
            "initiator_type": request.initiator_type,
            "initiated_by": request.initiated_by,
            "execution_identity": request.execution_identity,
            "idempotency_key": request.idempotency_key,
        }
        missing = [key for key, value in required.items() if not isinstance(value, str) or not value.strip()]
        if missing:
            raise ValueError("Mutating adapter requests require coordinator identity fields: " + ", ".join(missing))
    for field_name in ("operation_id", "correlation_id", "initiated_by", "execution_identity", "idempotency_key"):
        value = getattr(request, field_name)
        if value is not None and (not isinstance(value, str) or not _ID.fullmatch(value)):
            raise ValueError(f"Invalid {field_name}")
    encoded = json.dumps(request.to_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(encoded.encode("utf-8")) > MAX_REQUEST_BYTES:
        raise ValueError("Adapter request exceeds the 64 KiB contract limit")
    return request


def validate_result(result: RuntimeAdapterResult | dict) -> RuntimeAdapterResult:
    if isinstance(result, dict):
        try:
            result = RuntimeAdapterResult(
                **{**result,
                   "diagnostics": tuple(result.get("diagnostics") or ()),
                   "capabilities": tuple(result.get("capabilities") or ())}
            )
        except TypeError as exc:
            raise ValueError(f"Invalid adapter result fields: {exc}") from exc
    if not isinstance(result, RuntimeAdapterResult):
        raise TypeError("Adapter result must be RuntimeAdapterResult or a mapping")
    if result.contract_version != ADAPTER_CONTRACT_VERSION:
        raise ValueError("Unsupported runtime adapter result version")
    if not isinstance(result.adapter_id, str) or not _ADAPTER_ID.fullmatch(result.adapter_id):
        raise ValueError("Invalid result adapter ID")
    if result.runtime_type not in RUNTIME_TYPES or result.method not in ADAPTER_METHODS:
        raise ValueError("Invalid result runtime type or method")
    if not result.target_doctype or not isinstance(result.target_name, str) or not _ID.fullmatch(result.target_name):
        raise ValueError("Invalid result target identity")
    if result.outcome not in OUTCOMES:
        raise ValueError("Invalid adapter result outcome")
    if not isinstance(result.observed_state, str) or not result.observed_state or len(result.observed_state) > 100:
        raise ValueError("Invalid observed state")
    if not isinstance(result.duration_ms, int) or isinstance(result.duration_ms, bool) or not 0 <= result.duration_ms <= 86400000:
        raise ValueError("Invalid adapter duration")
    if result.observed_at is not None and (not isinstance(result.observed_at, str) or len(result.observed_at) > 40):
        raise ValueError("Invalid observation timestamp")
    if len(result.diagnostics) > _MAX_DIAGNOSTICS:
        raise ValueError("Too many adapter diagnostics")
    for diagnostic in result.diagnostics:
        if not isinstance(diagnostic, dict) or set(diagnostic) != {"code", "severity", "message"}:
            raise ValueError("Each diagnostic must contain exactly code, severity and message")
        if not isinstance(diagnostic["code"], str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{1,63}", diagnostic["code"]):
            raise ValueError("Invalid diagnostic code")
        if diagnostic["severity"] not in DIAGNOSTIC_SEVERITIES:
            raise ValueError("Invalid diagnostic severity")
        if not isinstance(diagnostic["message"], str) or not diagnostic["message"] or len(diagnostic["message"]) > 1000:
            raise ValueError("Invalid diagnostic message")
    if not isinstance(result.capabilities, (tuple, list)) or any(item not in ADAPTER_METHODS for item in result.capabilities):
        raise ValueError("Invalid adapter capabilities")
    if not isinstance(result.data, dict) or not _is_json_value(result.data):
        raise ValueError("Adapter result data must be a JSON-compatible object")
    _validate_payload_safety(result.data, "result")
    encoded = json.dumps(result.to_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(encoded.encode("utf-8")) > MAX_RESULT_BYTES:
        raise ValueError("Adapter result exceeds the 64 KiB contract limit")
    return result
