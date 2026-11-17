"""Runtime middleware extensions protocol and manager."""

from __future__ import annotations

import logging
from typing import Any, Protocol, runtime_checkable

from decafclaw.events import EventBus

log = logging.getLogger(__name__)


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
    """Initialize runtime extensions based on configuration."""
    manager = ExtensionManager(bus, config)
    memory_cfg = getattr(config, "memory", None)
    if memory_cfg and getattr(memory_cfg, "provider", None) == "mem0":
        from .mem0 import Mem0Extension

        ext = Mem0Extension(config)
        manager.register("mem0", ext)
        manager.enable("mem0")
    return manager
