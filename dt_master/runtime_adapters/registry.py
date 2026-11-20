"""Server-controlled runtime adapter registry."""

from dt_master.runtime_adapters.contracts import (
    ADAPTER_CONTRACT_VERSION,
    ADAPTER_METHODS,
    RuntimeAdapterError,
)


_ADAPTERS = {}


def register_adapter(adapter):
    from dt_master.runtime_adapters.base import RuntimeAdapter

    if not isinstance(adapter, RuntimeAdapter):
        raise TypeError("Registered adapters must extend RuntimeAdapter")
    if not isinstance(adapter.adapter_id, str) or not adapter.adapter_id:
        raise ValueError("Adapter ID is required")
    if adapter.runtime_type not in {"host", "docker"}:
        raise ValueError("Adapter runtime_type must be host or docker")
    if adapter.contract_version != ADAPTER_CONTRACT_VERSION:
        raise ValueError("Adapter contract version is unsupported")
    if adapter.adapter_id in _ADAPTERS:
        raise ValueError(f"Adapter is already registered: {adapter.adapter_id}")
    capabilities = adapter.capabilities()
    if not isinstance(capabilities, (tuple, list, set, frozenset)):
        raise TypeError("Adapter capabilities must be a collection")
    if len(set(capabilities)) != len(capabilities) or any(item not in ADAPTER_METHODS for item in capabilities):
        raise ValueError("Adapter capabilities contain duplicates or unsupported methods")
    _ADAPTERS[adapter.adapter_id] = adapter
    return adapter


def get_adapter(adapter_id):
    adapter = _ADAPTERS.get(adapter_id)
    if adapter is None:
        raise RuntimeAdapterError("UNKNOWN_ADAPTER", f"No runtime adapter is registered as '{adapter_id}'.")
    return adapter


def resolve_adapter(runtime_type, adapter_id=None):
    if runtime_type not in {"host", "docker"}:
        raise RuntimeAdapterError("UNSUPPORTED_RUNTIME", "Runtime type must be host or docker.")
    if adapter_id:
        adapter = _ADAPTERS.get(adapter_id)
        if adapter is None:
            # Explicit adapter IDs are used in persisted operation payloads.
            # Resolve known built-ins lazily in worker processes as well as
            # callers that request the default adapter. Never import a class
            # selected by an arbitrary caller-supplied module path.
            builtin_adapters = {
                ("host", "host-v1"): ("dt_master.runtime_adapters.host", "HostRuntimeAdapter"),
                ("docker", "docker-v1"): ("dt_master.runtime_adapters.docker", "DockerRuntimeAdapter"),
            }
            builtin = builtin_adapters.get((runtime_type, adapter_id))
            if builtin:
                from importlib import import_module

                module = import_module(builtin[0])
                register_adapter(getattr(module, builtin[1])())
                adapter = _ADAPTERS.get(adapter_id)
        if adapter is None:
            return get_adapter(adapter_id)
        if adapter.runtime_type != runtime_type:
            raise RuntimeAdapterError("RUNTIME_TYPE_MISMATCH", "Selected adapter does not implement the requested runtime type.")
        return adapter
    matches = [item for item in _ADAPTERS.values() if item.runtime_type == runtime_type]
    if not matches and runtime_type == "host":
        # Built-in adapters are imported lazily so importing the contract package
        # remains side-effect free and test/custom registrations stay isolated.
        from dt_master.runtime_adapters.host import HostRuntimeAdapter

        register_adapter(HostRuntimeAdapter())
        matches = [item for item in _ADAPTERS.values() if item.runtime_type == runtime_type]
    if not matches and runtime_type == "docker":
        from dt_master.runtime_adapters.docker import DockerRuntimeAdapter

        register_adapter(DockerRuntimeAdapter())
        matches = [item for item in _ADAPTERS.values() if item.runtime_type == runtime_type]
    if len(matches) != 1:
        code = "NO_ADAPTER_REGISTERED" if not matches else "AMBIGUOUS_ADAPTER"
        raise RuntimeAdapterError(code, f"Runtime type '{runtime_type}' does not resolve to exactly one registered adapter.")
    return matches[0]


def registered_adapters():
    return tuple(
        {
            "adapter_id": adapter.adapter_id,
            "runtime_type": adapter.runtime_type,
            "contract_version": adapter.contract_version,
            "capabilities": tuple(sorted(adapter.capabilities())),
        }
        for adapter in sorted(_ADAPTERS.values(), key=lambda item: item.adapter_id)
    )


def clear_registry_for_tests():
    """Test helper; production callers must not mutate the adapter registry."""
    _ADAPTERS.clear()
