"""Provider registry — maps provider names to provider instances."""

import logging
from typing import Any

from .types import (
    PROVIDER_FASTEMBED,
    PROVIDER_LITELLM,
    PROVIDER_OPENAI,
    PROVIDER_OPENAI_COMPAT,
    PROVIDER_OPENAI_RESPONSES,
    PROVIDER_VERTEX,
    Provider,
)

log = logging.getLogger(__name__)

# Module-level registry: provider_name -> Provider instance
_providers: dict[str, Provider] = {}

# Negative cache of (provider_name, model) pairs whose get_model_info lookup
# failed or returned no usable limit. Entries are only valid for the current
# provider generation: a re-initialized provider may resolve where the
# previous instance failed, so the cache resets with the registry (#1304).
_failed_model_info: set[tuple[str, str]] = set()


def register_provider(name: str, provider: Provider) -> None:
    """Register a provider instance by name."""
    _providers[name] = provider
    log.debug("Registered LLM provider: %s", name)


def get_provider(name: str) -> Provider:
    """Get a provider by name. Raises KeyError if not found."""
    if name not in _providers:
        available = ", ".join(sorted(_providers.keys())) or "(none)"
        raise KeyError(f"Unknown LLM provider '{name}'. Available: {available}")
    return _providers[name]


def unregister_provider(name: str) -> None:
    """Remove a registered provider by name. No-op if not registered."""
    _providers.pop(name, None)
    log.debug("Unregistered LLM provider: %s", name)


def list_providers() -> list[str]:
    """Return sorted list of registered provider names."""
    return sorted(_providers.keys())


def clear_providers() -> None:
    """Clear all registered providers and the failed model-info cache.

    ``init_providers`` (the config-reload point) calls this, so a reload
    resets both the registry and the negative cache in one step (#1304).
    """
    _providers.clear()
    _failed_model_info.clear()


def init_providers(config: Any) -> None:
    """Initialize providers from config.providers dict.

    Each entry maps a name to a ProviderConfig with a 'type' field
    that determines which provider class to instantiate.
    """
    from .providers.fastembed import FastembedProvider
    from .providers.openai import OpenAIProvider
    from .providers.openai_compat import OpenAICompatProvider
    from .providers.openai_responses import OpenAIResponsesProvider
    from .providers.vertex import VertexProvider

    clear_providers()

    # Built-in local embedding provider. Always available, no config entry
    # needed; the fastembed package is imported lazily on first use.
    register_provider(PROVIDER_FASTEMBED, FastembedProvider())

    for name, pc in config.providers.items():
        provider_type = pc.type
        if provider_type in (PROVIDER_OPENAI_COMPAT, PROVIDER_LITELLM):
            register_provider(name, OpenAICompatProvider(url=pc.url, api_key=pc.api_key))
        elif provider_type == PROVIDER_OPENAI_RESPONSES:
            register_provider(name, OpenAIResponsesProvider(url=pc.url, api_key=pc.api_key, name=name))
        elif provider_type == PROVIDER_OPENAI:
            register_provider(name, OpenAIProvider(url=pc.url, api_key=pc.api_key))
        elif provider_type == PROVIDER_VERTEX:
            register_provider(
                name,
                VertexProvider(
                    project=pc.project,
                    region=pc.region or "us-central1",
                    service_account_file=pc.service_account_file,
                ),
            )
        else:
            log.warning("Unknown provider type '%s' for '%s', skipping", provider_type, name)
