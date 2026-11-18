"""Runtime middleware extensions protocol and manager."""

from __future__ import annotations

import logging
from typing import Any, Callable, Protocol, runtime_checkable

from decafclaw.events import EventBus

log = logging.getLogger(__name__)

# Extension type registry (#1458): type name -> factory(options, config).
# Built-in extension modules register themselves at import time; third-party
# types may register too. Factories receive extension-owned options verbatim.
_EXTENSION_TYPES: dict[str, Callable[[dict[str, Any], Any], "Extension"]] = {}


def register_extension_type(name: str, factory: Callable[[dict[str, Any], Any], "Extension"]) -> None:
    """Register a factory for an extension type name.

    Re-registering a name replaces it with a warning (built-in modules import
    once, but tests and embedders can legitimately swap a type).
    """
    if name in _EXTENSION_TYPES:
        log.warning("Replacing already-registered extension type %r", name)
    _EXTENSION_TYPES[name] = factory


def get_extension_type(name: str) -> Callable[[dict[str, Any], Any], "Extension"] | None:
    """Return the factory registered for an extension type, if any."""
    return _EXTENSION_TYPES.get(name)


def list_extension_types() -> list[str]:
    """List registered extension type names."""
    return sorted(_EXTENSION_TYPES)


def clear_extension_types() -> None:
    """Drop all registered extension types (test isolation)."""
    _EXTENSION_TYPES.clear()


@runtime_checkable
class Extension(Protocol):
    """Protocol for server runtime middleware extensions."""

    def enable(self, bus: EventBus) -> list[str]:
        """Enable extension, registering hooks on the bus. Returns subscription IDs."""
        ...

    def disable(self, bus: EventBus) -> None:
        """Disable extension, detaching hooks via bus.unsubscribe."""
        ...


class BaseExtension:
    """Convenience base class for extensions that track their own subscriptions."""

    def __init__(self, name: str = ""):
        self.name = name
        self.subscription_ids: list[str] = []

    def enable(self, bus: EventBus) -> list[str]:
        """Override to subscribe hooks and return subscription IDs."""
        return self.subscription_ids

    def disable(self, bus: EventBus) -> None:
        """Detach all registered subscriptions."""
        for sub_id in list(self.subscription_ids):
            bus.unsubscribe(sub_id)
        self.subscription_ids.clear()


class ExtensionManager:
    """Manages extension registration, activation, and deactivation at runtime."""

    def __init__(self, bus: EventBus, config: Any = None):
        self.bus = bus
        self.config = config
        self._extensions: dict[str, Extension] = {}
        self._subscriptions: dict[str, list[str]] = {}

    def register(self, name: str, extension: Extension) -> None:
        """Register an extension."""
        self._extensions[name] = extension

    def get(self, name: str) -> Extension | None:
        """Get an extension by name."""
        return self._extensions.get(name)

    def list_extensions(self) -> list[str]:
        """List registered extension names."""
        return list(self._extensions.keys())

    def enable(self, name: str) -> list[str]:
        """Enable an extension by name. Returns its subscription IDs."""
        ext = self._extensions.get(name)
        if not ext:
            raise KeyError(f"Extension '{name}' not found")
        if name in self._subscriptions:
            self.disable(name)
        sub_ids = ext.enable(self.bus)
        self._subscriptions[name] = list(sub_ids or [])
        return self._subscriptions[name]

    def disable(self, name: str) -> None:
        """Disable an extension by name."""
        ext = self._extensions.get(name)
        if ext:
            try:
                ext.disable(self.bus)
            except Exception as e:
                log.warning("Extension %s raised exception during disable: %s", name, e)
        sub_ids = self._subscriptions.pop(name, [])
        for sub_id in sub_ids:
            self.bus.unsubscribe(sub_id)

    def is_enabled(self, name: str) -> bool:
        """Return True if the extension is currently enabled."""
        return name in self._subscriptions


def init_extensions(config: Any, bus: EventBus) -> ExtensionManager:
    """Initialize runtime extensions from ``config.extensions``.

    Enabled entries are instantiated through their registered type factory and
    enabled on the manager, in config insertion order. Unknown types, factory
    errors, and enable errors are logged and skipped: startup stays fail-open.
    """
    manager = ExtensionManager(bus, config)

    # Import built-ins lazily so their module-level registrations run without
    # an import cycle (mem0 imports BaseExtension from this module). If a
    # caller cleared the registry explicitly (test isolation, embedders), the
    # modules are cached and won't re-register, so re-register the built-ins.
    from . import diagnostics_and_telemetry as _diagnostics_and_telemetry  # noqa: F401
    from . import improvement_scout as _improvement_scout  # noqa: F401
    from . import mem0 as _mem0  # noqa: F401

    if get_extension_type("diagnostics_and_telemetry") is None:
        register_extension_type(
            "diagnostics_and_telemetry", _diagnostics_and_telemetry.DiagnosticsAndTelemetryExtension
        )
    if get_extension_type("mem0") is None:
        register_extension_type("mem0", _mem0.Mem0Extension)
    if get_extension_type("improvement_scout") is None:
        register_extension_type("improvement_scout", _improvement_scout.ImprovementScoutExtension)

    for name, ext_cfg in getattr(config, "extensions", {}).items():
        if ext_cfg.enabled is False:
            continue
        type_name = ext_cfg.type or name
        factory = get_extension_type(type_name)
        if factory is None:
            log.warning("Unknown extension type %r for extension %r; skipping", type_name, name)
            continue
        try:
            ext = factory(ext_cfg.options, config)
        except Exception as e:
            log.warning("Extension %r failed to initialize: %s", name, e)
            continue
        manager.register(name, ext)
        try:
            manager.enable(name)
        except Exception as e:
            log.warning("Extension %r failed to enable: %s", name, e)
    return manager
