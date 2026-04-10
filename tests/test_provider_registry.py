"""Tests for provider registry initialization and model resolution."""

import dataclasses

import pytest

from decafclaw.config_types import ModelConfig, ProviderConfig
from decafclaw.llm import clear_providers, get_provider, init_providers, list_providers


@pytest.fixture(autouse=True)
def clean_registry():
    """Ensure a clean registry for each test."""
    clear_providers()
    yield
    clear_providers()


def test_init_providers_registers_litellm(config):
    """LiteLLM providers are registered from config."""
    config = dataclasses.replace(config, providers={
        "local": ProviderConfig(type="litellm", url="http://localhost:4000/v1/chat/completions"),
    })
    init_providers(config)
    assert "local" in list_providers()
    provider = get_provider("local")
    assert provider.url == "http://localhost:4000/v1/chat/completions"


def test_init_providers_registers_openai(config):
    """OpenAI providers are registered from config."""
    config = dataclasses.replace(config, providers={
        "oai": ProviderConfig(type="openai", api_key="sk-test"),
    })
    init_providers(config)
    assert "oai" in list_providers()


def test_init_providers_registers_vertex(config):
    """Vertex providers are registered from config."""
    config = dataclasses.replace(config, providers={
        "vertex": ProviderConfig(type="vertex", project="test-project", region="us-central1"),
    })
    init_providers(config)
    assert "vertex" in list_providers()


def test_init_providers_multiple(config):
    """Multiple providers of different types are all registered."""
    config = dataclasses.replace(config, providers={
        "local": ProviderConfig(type="litellm", url="http://localhost:4000/v1/chat/completions"),
        "oai": ProviderConfig(type="openai", api_key="sk-test"),
        "vertex": ProviderConfig(type="vertex", project="test-project"),
    })
    init_providers(config)
    assert sorted(list_providers()) == ["local", "oai", "vertex"]


def test_get_provider_unknown_raises():
    """Getting an unregistered provider raises KeyError."""
    with pytest.raises(KeyError, match="Unknown LLM provider"):
        get_provider("nonexistent")


def test_empty_registry_after_clear(config):
    """clear_providers empties the registry."""
    config = dataclasses.replace(config, providers={
        "local": ProviderConfig(type="litellm", url="http://localhost:4000/v1/chat/completions"),
    })
    init_providers(config)
    assert len(list_providers()) == 1
    clear_providers()
    assert len(list_providers()) == 0


def test_model_resolution_uses_registered_provider(config):
    """Named model resolution finds the provider in the registry."""
    from decafclaw.llm import _resolve

    config = dataclasses.replace(config, providers={
        "oai": ProviderConfig(type="openai", api_key="sk-test"),
    }, model_configs={
        "gpt4": ModelConfig(provider="oai", model="gpt-4o", timeout=120),
    }, default_model="gpt4")

    init_providers(config)

    provider, model, timeout = _resolve(config, model_name="gpt4")
    assert model == "gpt-4o"
    assert timeout == 120


def test_model_resolution_falls_back_when_registry_empty(config):
    """Without init_providers, model resolution falls back to legacy."""
    from decafclaw.llm import _resolve

    config = dataclasses.replace(config, providers={
        "oai": ProviderConfig(type="openai", api_key="sk-test"),
    }, model_configs={
        "gpt4": ModelConfig(provider="oai", model="gpt-4o"),
    }, default_model="gpt4")

    # DON'T call init_providers — simulates the bug
    provider, model, timeout = _resolve(config, model_name="gpt4")
    # Should fall back to legacy config.llm, not crash
    assert model == config.llm.model  # fell back to default


def test_migration_creates_default_provider(config):
    """Auto-migration from LlmConfig creates a default provider."""
    # Config with no explicit providers but has llm config
    # (load_config auto-migrates, but we test init_providers with the result)
    config = dataclasses.replace(config, providers={
        "default": ProviderConfig(type="litellm", url=config.llm.url, api_key=config.llm.api_key),
    }, model_configs={
        "default": ModelConfig(provider="default", model=config.llm.model),
    }, default_model="default")

    init_providers(config)
    assert "default" in list_providers()
