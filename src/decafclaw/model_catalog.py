"""Model catalog — auto-discovered models sidecar (#1211).

Discovery results, per-model enable/disable flags, and the global
default-model pointer live in a single admin-tier sidecar at
``data/{agent_id}/model_catalog.json``. ``config.json`` stays pure
hand-authored intent: nothing here writes it, and the UI-managed model
state survives restarts through this sidecar alone.

Shape:

```json
{
  "version": 1,
  "providers": {
    "local": {
      "discovered_at": "2026-11-05T12:00:00+00:00",
      "error": "",
      "models": [
        {"id": "llama3.2", "enabled": true, "chat": null, "tools": null,
         "context_window_size": null, "owned_by": ""}
      ]
    }
  },
  "default_model": "local/llama3.2"
}
```

Keyed by ``{provider}/{model_id}`` to avoid collisions with hand-named
``model_configs`` entries. Enabled discovered models are materialized
into the in-memory ``config.model_configs`` under that same key with
safe defaults, so selection resolves through the normal single
``ModelConfig`` path — no catalog-aware resolution anywhere else.
"""

import asyncio
import json
import logging
import os
from dataclasses import asdict, dataclass, field
from dataclasses import fields as dc_fields
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from .config_types import ModelConfig
from .llm.types import DiscoveredModel

if TYPE_CHECKING:
    from .config import Config

log = logging.getLogger(__name__)

CATALOG_FILENAME = "model_catalog.json"

# Published on the event bus after the catalog (or its default-model
# pointer) changed, so connected web UIs refresh their model picker.
MODELS_CHANGED_EVENT_TYPE = "models_changed"

# Serializes sidecar read-modify-write cycles (refresh / toggle /
# set-default) within one server process. The read happens inside the
# lock too — discovery itself runs unlocked, and the lock covers
# reload-merge-save-apply so user changes made during discovery survive.
_catalog_lock = asyncio.Lock()


@dataclass
class CatalogModel:
    """One discovered model and its user-managed enable flag."""

    id: str
    enabled: bool = False
    chat: bool | None = None
    tools: bool | None = None
    context_window_size: int | None = None
    owned_by: str = ""


@dataclass
class CatalogProvider:
    """Discovery state for one configured provider."""

    discovered_at: str = ""
    error: str = ""
    models: list[CatalogModel] = field(default_factory=list)


@dataclass
class ModelCatalog:
    """Root sidecar document."""

    version: int = 1
    providers: dict[str, CatalogProvider] = field(default_factory=dict)
    default_model: str = ""
    # Shell auto-approval judge pointer (#1213). Empty means "ride on the
    # active conversation model" (today's behavior). Same shape and
    # resolution rule as the default-model pointer and the #1212 reflection
    # pointers: sidecar pointer outranks the config seed, which becomes the
    # seed only when the pointer is empty.
    aux_approval_model: str = ""


def catalog_path(config: "Config") -> Any:
    """Sidecar location: ``{agent_path}/model_catalog.json`` (admin-tier)."""
    return config.agent_path / CATALOG_FILENAME


def _coerce_model(raw: Any) -> CatalogModel | None:
    if not isinstance(raw, dict):
        return None
    known = {f.name for f in dc_fields(CatalogModel)}
    kwargs = {key: value for key, value in raw.items() if key in known}
    model_id = kwargs.get("id")
    if not isinstance(model_id, str) or not model_id:
        return None
    if not isinstance(kwargs.get("enabled"), bool):
        kwargs["enabled"] = False
    return CatalogModel(**kwargs)


def _coerce_provider(raw: Any) -> CatalogProvider:
    if not isinstance(raw, dict):
        return CatalogProvider()
    models_raw = raw.get("models")
    models = []
    if isinstance(models_raw, list):
        models = [m for m in (_coerce_model(entry) for entry in models_raw) if m is not None]
    error = raw.get("error")
    discovered_at = raw.get("discovered_at")
    return CatalogProvider(
        discovered_at=discovered_at if isinstance(discovered_at, str) else "",
        error=error if isinstance(error, str) else "",
        models=models,
    )


def catalog_from_dict(raw: Any) -> ModelCatalog:
    """Parse and validate a sidecar document. Malformed parts are dropped."""
    if not isinstance(raw, dict):
        return ModelCatalog()
    providers_raw = raw.get("providers")
    providers: dict[str, CatalogProvider] = {}
    if isinstance(providers_raw, dict):
        for name, entry in providers_raw.items():
            if isinstance(name, str) and name:
                providers[name] = _coerce_provider(entry)
    pointer = raw.get("default_model")
    aux_approval = raw.get("aux_approval_model")
    return ModelCatalog(
        version=raw.get("version", 1) if isinstance(raw.get("version", 1), int) else 1,
        providers=providers,
        default_model=pointer if isinstance(pointer, str) else "",
        aux_approval_model=aux_approval if isinstance(aux_approval, str) else "",
    )


def load_catalog(config: "Config") -> ModelCatalog:
    """Load the sidecar, or an empty catalog when missing/unreadable.

    Never raises — a corrupt file logs a warning and the catalog starts
    empty rather than taking the picker down with it.
    """
    path = catalog_path(config)
    try:
        if not path.exists():
            return ModelCatalog()
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning("Failed to load model catalog %s: %s", path, exc)
        return ModelCatalog()
    return catalog_from_dict(raw)


def save_catalog(config: "Config", catalog: ModelCatalog) -> bool:
    """Write the sidecar atomically. Failures log a warning; never raise."""
    try:
        path = catalog_path(config)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(asdict(catalog), indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)
        return True
    except Exception as exc:
        log.warning("Failed to save model catalog %s: %s", catalog_path(config), exc)
        return False


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_enabled(entry: DiscoveredModel) -> bool:
    """Default-on policy (#1211): a newly discovered model lands enabled
    only when the gateway reports chat + tool support + context-window
    metadata and the id is live-confirmed (a curated static-list guess is
    not); otherwise it lands disabled and the user opts in."""
    return bool(entry.chat and entry.tools and entry.context_window_size and entry.confirmed)


async def _discover_provider(provider_name: str, provider: Any) -> tuple[list[DiscoveredModel] | None, str]:
    """Run one provider's discovery, without the catalog loaded or locked.

    Returns ``(found, error)``. ``found`` is None on any failure —
    unsupported provider or gateway error — and ``error`` carries the
    message; the caller then keeps the cached entries untouched. A
    gateway outage must not wipe the picker.
    """
    if not hasattr(provider, "discover_models"):
        return None, "provider does not support model discovery"
    try:
        found: list[DiscoveredModel] = [m for m in await provider.discover_models() if isinstance(m, DiscoveredModel)]
    except Exception as exc:
        log.warning("Model discovery failed for provider %r: %s", provider_name, exc)
        return None, str(exc)
    return found, ""


def _merge_discovered(catalog: ModelCatalog, name: str, found: list[DiscoveredModel] | None, error: str) -> None:
    """Merge one provider's discovery result into ``catalog`` (in-lock).

    On success the model list is replaced, preserving the enable flag of
    models still present. On failure the cached entries are kept untouched
    and the error recorded. An unknown provider name leaves its cached
    entry (if any) exactly as it was.
    """
    existing = catalog.providers.get(name) or CatalogProvider()
    if found is None:
        if not error:
            return
        catalog.providers[name] = CatalogProvider(
            discovered_at=existing.discovered_at, error=error, models=existing.models
        )
        return
    prev_enabled = {m.id: m.enabled for m in existing.models}
    models = [
        CatalogModel(
            id=d.id,
            enabled=prev_enabled.get(d.id, default_enabled(d)),
            chat=d.chat,
            tools=d.tools,
            context_window_size=d.context_window_size,
            owned_by=d.owned_by,
        )
        for d in found
    ]
    catalog.providers[name] = CatalogProvider(discovered_at=_now_iso(), error="", models=models)


async def refresh_catalog(
    config: "Config", provider_names: list[str] | None = None, event_bus: Any = None
) -> ModelCatalog:
    """Discover models from providers, update the sidecar, apply to config.

    Runs on demand only — at startup and on explicit refresh, never a
    heartbeat poll. Discovers concurrently across providers; one failing
    gateway does not affect the others. Discovery runs without the catalog
    loaded or locked; inside the lock the sidecar is reloaded and the
    results merged into it, so user toggles and default-pointer changes
    made during a slow discovery survive. When ``event_bus`` is given,
    publishes ``models_changed`` so connected UIs refresh their picker.
    """
    from .llm.registry import get_provider, list_providers

    names = list(provider_names) if provider_names else list_providers()

    async def one(name: str) -> tuple[str, list[DiscoveredModel] | None, str]:
        try:
            provider = get_provider(name)
        except KeyError:
            return name, None, ""
        found, error = await _discover_provider(name, provider)
        return name, found, error

    results = await asyncio.gather(*(one(name) for name in names))
    async with _catalog_lock:
        # Reload under the lock and merge into the fresh sidecar — never
        # into a pre-discovery copy, which would revert changes made while
        # discovery ran (lost-update review finding).
        catalog = load_catalog(config)
        for name, found, error in results:
            _merge_discovered(catalog, name, found, error)
        save_catalog(config, catalog)
        apply_catalog(config, catalog)
    await _publish_models_changed(event_bus)
    return catalog


async def startup_discovery(config: "Config", event_bus: Any = None) -> None:
    """Fire-and-forget startup discovery. Never raises, never blocks turns."""
    try:
        await refresh_catalog(config, event_bus=event_bus)
    except Exception as exc:
        log.warning("Startup model discovery failed (catalog unchanged): %s", exc)


async def _publish_models_changed(event_bus: Any) -> None:
    """Announce a catalog change to connected UIs. Fail-open."""
    if event_bus is None:
        return
    try:
        await event_bus.publish({"type": MODELS_CHANGED_EVENT_TYPE})
    except Exception as exc:
        log.debug("models_changed publish failed: %s", exc)


# ---------------------------------------------------------------------------
# Materialization and resolution
# ---------------------------------------------------------------------------


def materialize_model_config(provider_name: str, entry: CatalogModel) -> ModelConfig:
    """Build a ModelConfig from a catalog entry with safe defaults (#1211).

    ``context_window_size`` from the gateway when reported, else 0 (the
    existing compaction default); ``timeout`` 300; ``streaming`` true;
    ``reasoning_effort`` never guessed. After materialization the model
    resolves through the normal single ModelConfig path.
    """
    return ModelConfig(
        provider=provider_name,
        model=entry.id,
        context_window_size=entry.context_window_size or 0,
        timeout=300,
        streaming=True,
        reasoning_effort=None,
    )


def catalog_model_key(provider_name: str, model_id: str) -> str:
    """Catalog models are keyed ``{provider}/{model_id}`` in model_configs."""
    return f"{provider_name}/{model_id}"


def apply_catalog(config: "Config", catalog: ModelCatalog) -> None:
    """Sync the in-memory Config with the catalog.

    Materializes every enabled discovered model into ``config.model_configs``
    (never overwriting a hand-authored entry under the same key) and prunes
    materialized entries that were disabled or vanished from the catalog.
    Only entries this function actually creates are tracked in
    ``catalog_materialized`` — a colliding hand-authored entry is never
    catalog-owned, so disabling the discovered model cannot prune it.
    Writes no files; ``config.json`` is untouched.
    """
    live_keys: set[str] = set()
    for provider_name, pentry in catalog.providers.items():
        for entry in pentry.models:
            if not entry.enabled:
                continue
            key = catalog_model_key(provider_name, entry.id)
            live_keys.add(key)
            if key in config.model_configs:
                # Hand-authored entry under the same key: never overwrite it,
                # and never claim it as catalog-owned — disabling the
                # discovered model must not prune the user's config. It is
                # already tracked from the apply that created it.
                continue
            config.model_configs[key] = materialize_model_config(provider_name, entry)
            config.catalog_materialized.add(key)

    # Drop entries this issue materialized earlier that are no longer
    # enabled. Hand-authored configs are never in catalog_materialized.
    for key in config.catalog_materialized - live_keys:
        config.model_configs.pop(key, None)
        config.catalog_materialized.discard(key)


def enabled_catalog_keys(catalog: ModelCatalog) -> set[str]:
    return {
        catalog_model_key(provider_name, entry.id)
        for provider_name, pentry in catalog.providers.items()
        for entry in pentry.models
        if entry.enabled
    }


def picker_models(config: "Config") -> list[str]:
    """Model names for the conversation picker (#1211): hand-authored
    configs plus enabled discovered models. Disabled discovered models
    are excluded; the default pointer does not re-add anything."""
    catalog = load_catalog(config)
    hand = set(config.model_configs) - config.catalog_materialized
    return sorted(hand | enabled_catalog_keys(catalog))


def effective_default_model(config: "Config") -> str:
    """Global default model, resolution order (#1211):

    1. The sidecar's ``default_model`` pointer, when it names a
       selectable model (enabled discovered or hand-configured).
    2. ``config.json``'s ``default_model`` (the ``Config`` field, kept
       pristine — the sidecar never overwrites it).
    3. Empty string; callers fall through to ``config.llm.model`` exactly
       as they did before this feature existed.

    A pointer at a disabled or vanished model falls back cleanly.
    """
    pointer = load_catalog(config).default_model
    if pointer and pointer in config.model_configs:
        return pointer
    return config.default_model


def effective_aux_approval_model(config: "Config", active_model: str = "") -> str:
    """Shell auto-approval judge model, resolution order (#1213):

    1. The sidecar's ``aux_approval_model`` pointer, when it names a
       selectable model (enabled discovered or hand-configured).
    2. ``config.json``'s ``shell.aux_approval_model`` (the seed; may itself
       be a ``model_configs`` key or a legacy raw name).
    3. ``active_model`` — the active conversation model, which is today's
       behavior when nothing is set.

    An empty pointer means "ride on the active conversation model"; an empty
    seed likewise falls through. A pointer at a disabled or vanished model
    falls back cleanly to the seed, then to the active conversation model.
    """
    pointer = load_catalog(config).aux_approval_model
    if pointer and pointer in config.model_configs:
        return pointer
    seed = config.shell.aux_approval_model
    if seed and seed in config.model_configs:
        return seed
    if seed:
        # Legacy raw name — the same shape the judge path accepts.
        return seed
    return active_model or effective_default_model(config)


# ---------------------------------------------------------------------------
# Mutations (UI write path)
# ---------------------------------------------------------------------------


def _find_entry(catalog: ModelCatalog, key: str) -> CatalogModel | None:
    for provider_name, pentry in catalog.providers.items():
        for entry in pentry.models:
            if catalog_model_key(provider_name, entry.id) == key:
                return entry
    return None


def _save_and_apply(config: "Config", catalog: ModelCatalog) -> None:
    save_catalog(config, catalog)
    apply_catalog(config, catalog)


async def set_model_enabled(config: "Config", key: str, enabled: bool, event_bus: Any = None) -> ModelCatalog | None:
    """Enable or disable a discovered model. Returns the updated catalog,
    or None when the key names no catalog entry.

    The sidecar is loaded inside the lock so a concurrent refresh cannot
    silently revert the toggle (lost-update review finding)."""
    async with _catalog_lock:
        catalog = load_catalog(config)
        entry = _find_entry(catalog, key)
        if entry is None:
            return None
        entry.enabled = enabled
        _save_and_apply(config, catalog)
    await _publish_models_changed(event_bus)
    return catalog


async def set_default_model(config: "Config", name: str, event_bus: Any = None) -> ModelCatalog:
    """Set (name non-empty) or clear (name empty) the default-model pointer.

    The sidecar is loaded inside the lock so a concurrent refresh cannot
    silently revert the change (lost-update review finding)."""
    async with _catalog_lock:
        catalog = load_catalog(config)
        catalog.default_model = name
        _save_and_apply(config, catalog)
    await _publish_models_changed(event_bus)
    return catalog


async def set_aux_approval_model(config: "Config", name: str, event_bus: Any = None) -> ModelCatalog:
    """Set (name non-empty) or clear (name empty) the shell auto-approval judge pointer (#1213).

    Empty means "ride on the active conversation model" — resolution then
    falls through to ``config.shell.aux_approval_model`` and on to the active
    conversation model. The sidecar is loaded inside the lock so a concurrent
    refresh or toggle cannot silently revert the change.
    """
    async with _catalog_lock:
        catalog = load_catalog(config)
        catalog.aux_approval_model = name
        _save_and_apply(config, catalog)
    await _publish_models_changed(event_bus)
    return catalog


async def set_provider_models_enabled(
    config: "Config", provider_name: str, enabled: bool, event_bus: Any = None
) -> ModelCatalog | None:
    """Enable or disable every discovered model of one provider (bulk action).

    Applies to the provider's catalog entries only — hand-authored
    ``model_configs`` entries (including one under a colliding key) are
    never touched, and ``config.json`` is never written. One lock
    acquisition, one sidecar write, one ``models_changed`` broadcast, so
    the atomicity story matches :func:`set_model_enabled`. Returns the
    updated catalog, or None when the provider has no catalog entry.
    """
    async with _catalog_lock:
        catalog = load_catalog(config)
        pentry = catalog.providers.get(provider_name)
        if pentry is None:
            return None
        for entry in pentry.models:
            entry.enabled = enabled
        _save_and_apply(config, catalog)
    await _publish_models_changed(event_bus)
    return catalog


# ---------------------------------------------------------------------------
# UI payload
# ---------------------------------------------------------------------------


def catalog_payload(config: "Config") -> dict:
    """Payload for the catalog management UI (GET /api/models/catalog)."""
    catalog = load_catalog(config)
    providers: dict[str, dict] = {}
    for provider_name, pentry in catalog.providers.items():
        models = []
        for entry in pentry.models:
            key = catalog_model_key(provider_name, entry.id)
            models.append(
                {
                    **asdict(entry),
                    "key": key,
                    "selectable": key in config.model_configs,
                }
            )
        providers[provider_name] = {
            "discovered_at": pentry.discovered_at,
            "error": pentry.error,
            "models": models,
        }
    aux_approval_model = effective_aux_approval_model(config, "")
    return {
        "default_model": effective_default_model(config),
        "default_pointer": catalog.default_model,
        "picker_models": picker_models(config),
        "providers": providers,
        # Shell auto-approval judge pointer (#1213): raw sidecar pointer plus
        # the effective model it resolves to (empty = ride on the active
        # conversation model).
        "aux_approval_model": aux_approval_model,
        "aux_approval_pointer": catalog.aux_approval_model,
    }
