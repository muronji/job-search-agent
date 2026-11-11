"""Hardcoded adapter registry."""
from core.adapters.base import BaseAdapter


class AdapterNotRegistered(Exception):
    """Raised when an unknown adapter key is requested."""


ADAPTER_REGISTRY: dict[str, type[BaseAdapter]] = {}


def register_adapter(key: str, adapter_class: type[BaseAdapter]):
    ADAPTER_REGISTRY[key] = adapter_class


def get_adapter_class(key: str) -> type[BaseAdapter]:
    if key not in ADAPTER_REGISTRY:
        raise AdapterNotRegistered(f"Unknown adapter: {key}")
    return ADAPTER_REGISTRY[key]
