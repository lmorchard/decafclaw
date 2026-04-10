"""Provider registry — maps provider names to provider instances."""

import logging
from typing import Any

from .types import (
    PROVIDER_LITELLM,
    PROVIDER_OPENAI,
    PROVIDER_OPENAI_COMPAT,
    PROVIDER_VERTEX,
    Provider,
)

log = logging.getLogger(__name__)

# Module-level registry: provider_name -> Provider instance
_providers: dict[str, Provider] = {}


def register_provider(name: str, provider: Provider) -> None:
    """Register a provider instance by name."""
    _providers[name] = provider
    log.debug("Registered LLM provider: %s", name)


def get_provider(name: str) -> Provider:
    """Get a provider by name. Raises KeyError if not found."""
    if name not in _providers:
        available = ", ".join(sorted(_providers.keys())) or "(none)"
        raise KeyError(
            f"Unknown LLM provider '{name}'. Available: {available}"
        )
    return _providers[name]


def list_providers() -> list[str]:
    """Return sorted list of registered provider names."""
    return sorted(_providers.keys())


def clear_providers() -> None:
    """Clear all registered providers (for testing)."""
    _providers.clear()


def init_providers(config: Any) -> None:
    """Initialize providers from config.providers dict.

    Each entry maps a name to a ProviderConfig with a 'type' field
    that determines which provider class to instantiate.
    """
    from .providers.openai import OpenAIProvider
    from .providers.openai_compat import OpenAICompatProvider
    from .providers.vertex import VertexProvider

    clear_providers()

    for name, pc in config.providers.items():
        provider_type = pc.type
        if provider_type in (PROVIDER_OPENAI_COMPAT, PROVIDER_LITELLM):
            register_provider(name, OpenAICompatProvider(url=pc.url, api_key=pc.api_key))
        elif provider_type == PROVIDER_OPENAI:
            register_provider(name, OpenAIProvider(url=pc.url, api_key=pc.api_key))
        elif provider_type == PROVIDER_VERTEX:
            register_provider(name, VertexProvider(
                project=pc.project, region=pc.region or "us-central1",
                service_account_file=pc.service_account_file,
            ))
        else:
            log.warning("Unknown provider type '%s' for '%s', skipping",
                        provider_type, name)
