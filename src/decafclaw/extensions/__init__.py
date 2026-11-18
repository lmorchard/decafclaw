"""Runtime middleware extensions protocol and manager."""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Protocol, runtime_checkable

from decafclaw.events import EventBus

if TYPE_CHECKING:
    from decafclaw.context_types import SourceEntry

log = logging.getLogger(__name__)


class ContextSlot(enum.Enum):
    """Where a ``PRE_COMPOSE`` contribution mounts in the assembled context.

    - HEAD — static instructions mounted after the system prompt and before
      history. Contributions are not archived: replaying them every turn keeps
      the provider prompt-cache prefix stable (#1117).
    - DELTA — chronological append-only items appended to this turn's history
      slice after archived history and before tail envelopes. Contributions are
      archived so later turns replay them in place.
    - TAIL — per-turn envelopes mounted directly before the current user
      message. Contributions are archived like the legacy ``<recalled_facts>``
      envelope.
    """

    HEAD = "head"
    DELTA = "delta"
    TAIL = "tail"


@dataclass
class ContextContribution:
    """Structured result from a ``TurnLifecycle.PRE_COMPOSE`` hook.

    ``messages`` are normal chat messages. ``source_entry`` is optional
    telemetry for the Context Inspector; when present the composer appends it
    to the turn's ``sources`` list.
    """

    slot: ContextSlot
    messages: list[dict] = field(default_factory=list)
    source_entry: "SourceEntry | None" = None


@dataclass(frozen=True)
class ExtensionTypeMeta:
    """Registry metadata for one extension type.

    ``default_enabled`` marks core types that config should seed when
    ``config.json`` defines no entry for them.
    """

    name: str
    factory: Callable[[dict[str, Any], Any], "Extension"]
    default_enabled: bool = False


# Extension type registry (#1458): type name -> metadata.
# Built-in extension modules register themselves at import time; third-party
# types may register too. Factories receive extension-owned options verbatim.
_EXTENSION_TYPES: dict[str, ExtensionTypeMeta] = {}


def register_extension_type(
    name: str,
    factory: Callable[[dict[str, Any], Any], "Extension"],
    *,
    default_enabled: bool = False,
) -> None:
    """Register a factory for an extension type name.

    Re-registering a name replaces it with a warning (built-in modules import
    once, but tests and embedders can legitimately swap a type). The
    ``default_enabled`` flag is replaced with the rest of the entry.
    """
    if name in _EXTENSION_TYPES:
        log.warning("Replacing already-registered extension type %r", name)
    _EXTENSION_TYPES[name] = ExtensionTypeMeta(name=name, factory=factory, default_enabled=default_enabled)


def get_extension_type(name: str) -> Callable[[dict[str, Any], Any], "Extension"] | None:
    """Return the factory registered for an extension type, if any."""
    meta = _EXTENSION_TYPES.get(name)
    return meta.factory if meta is not None else None


def get_extension_type_meta(name: str) -> ExtensionTypeMeta | None:
    """Return the registry metadata for an extension type, if any."""
    return _EXTENSION_TYPES.get(name)


def get_default_enabled_extension_types() -> dict[str, ExtensionTypeMeta]:
    """Return a copy of the types marked ``default_enabled``, in registry order."""
    return {name: meta for name, meta in _EXTENSION_TYPES.items() if meta.default_enabled}


def list_extension_types() -> list[str]:
    """List registered extension type names."""
    return sorted(_EXTENSION_TYPES)


def clear_extension_types() -> None:
    """Drop all registered extension types (test isolation)."""
    _EXTENSION_TYPES.clear()


def ensure_builtin_extension_types() -> None:
    """Import built-in extension modules and register any missing type.

    Built-in modules register themselves at import time, but the import only
    runs once. A caller that cleared the registry explicitly (test isolation,
    embedders) needs the registrations restored, so re-register missing names
    here. Modules are imported lazily: ``mem0`` imports ``BaseExtension`` from
    this module, and a top-level import would cycle.
    """
    from . import compaction as _compaction
    from . import diagnostics_and_telemetry as _diagnostics_and_telemetry
    from . import improvement_scout as _improvement_scout
    from . import mem0 as _mem0
    from . import mentions as _mentions
    from . import recent_journal as _recent_journal
    from . import scratchpad_notes as _scratchpad_notes
    from . import vault_guide as _vault_guide
    from . import vault_references as _vault_references
    from . import vault_retrieval as _vault_retrieval

    if get_extension_type("diagnostics_and_telemetry") is None:
        register_extension_type(
            "diagnostics_and_telemetry",
            _diagnostics_and_telemetry.DiagnosticsAndTelemetryExtension,
            default_enabled=True,
        )
    if get_extension_type("scratchpad_notes") is None:
        register_extension_type(
            "scratchpad_notes",
            _scratchpad_notes.ScratchpadNotesExtension,
            default_enabled=True,
        )
    if get_extension_type("vault_retrieval") is None:
        register_extension_type(
            "vault_retrieval",
            _vault_retrieval.VaultRetrievalExtension,
            default_enabled=True,
        )
    if get_extension_type("compaction") is None:
        register_extension_type(
            "compaction",
            _compaction.CompactionExtension,
            default_enabled=True,
        )
    for _name, _cls in (
        ("vault_references", _vault_references.VaultReferencesExtension),
        ("mentions", _mentions.MentionsExtension),
        ("vault_guide", _vault_guide.VaultGuideExtension),
        ("recent_journal", _recent_journal.RecentJournalExtension),
    ):
        if get_extension_type(_name) is None:
            register_extension_type(_name, _cls, default_enabled=True)
    if get_extension_type("mem0") is None:
        register_extension_type("mem0", _mem0.Mem0Extension)
    if get_extension_type("improvement_scout") is None:
        register_extension_type("improvement_scout", _improvement_scout.ImprovementScoutExtension)


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

    # Optional per-hook bound for ``TurnLifecycle.PRE_COMPOSE`` dispatch. ``None``
    # uses ``lifecycle.pre_compose_timeout``; a heavyweight collector (such as
    # vault retrieval) may declare a larger value (#1489).
    pre_compose_timeout: float | None = None

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

    ensure_builtin_extension_types()

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
