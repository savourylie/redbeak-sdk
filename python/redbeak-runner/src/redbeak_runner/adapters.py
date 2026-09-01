"""Load a ``TargetAdapter`` from a configured import path.

The runner never hard-codes a target. Discovery is an import path so demo
targets, a customer's integration, and in-test doubles all load the same way.
"""

from __future__ import annotations

import importlib
from typing import Any, cast

from redbeak_adapter_sdk import TargetAdapter

from redbeak_runner.errors import AdapterDiscoveryError


def load_adapter(spec: str) -> TargetAdapter:
    """Import ``module`` or ``module:attribute`` and return an adapter instance.

    Resolution order for a bare module: ``create_adapter``, then ``ADAPTER``,
    then the module itself if it already looks like an adapter.
    """
    if not spec or spec.strip() != spec or " " in spec:
        raise AdapterDiscoveryError(f"adapter spec is not an import path: {spec!r}")
    module_name, sep, attribute = spec.partition(":")
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:
        raise AdapterDiscoveryError(f"could not import adapter {module_name!r}: {exc}") from exc

    if sep:
        try:
            target: Any = getattr(module, attribute)
        except AttributeError as exc:
            raise AdapterDiscoveryError(f"adapter {spec!r} has no attribute {attribute!r}") from exc
        return _instantiate(target, spec)

    if hasattr(module, "create_adapter"):
        return _instantiate(module.create_adapter, spec)
    if hasattr(module, "ADAPTER"):
        return _instantiate(module.ADAPTER, spec)
    if _looks_like_adapter(module):
        return module
    raise AdapterDiscoveryError(
        f"adapter {spec!r} does not define create_adapter(), ADAPTER, or the protocol"
    )


def _instantiate(target: Any, spec: str) -> TargetAdapter:
    try:
        adapter = target() if callable(target) else target
    except TypeError as exc:
        raise AdapterDiscoveryError(f"adapter {spec!r} could not be constructed: {exc}") from exc
    if not _looks_like_adapter(adapter):
        raise AdapterDiscoveryError(f"{spec!r} did not produce a TargetAdapter")
    return cast(TargetAdapter, adapter)


def _looks_like_adapter(value: Any) -> bool:
    return all(
        callable(getattr(value, name, None))
        for name in ("capabilities", "reset", "send", "observe", "close")
    )
