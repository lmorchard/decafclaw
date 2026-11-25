"""Context-window resolution: precedence, provider shapes, and warnings (#1719)."""

import logging

import httpx
import pytest

from decafclaw.config_types import ModelConfig, ProviderConfig
from decafclaw.llm import _failed_model_info, ensure_model_context_window
from decafclaw.llm.providers.openai_compat import OpenAICompatProvider, parse_models_payload
from decafclaw.llm.providers.vertex import VertexProvider, _vertex_default_context_window
from decafclaw.llm.registry import clear_providers, register_provider


class _DiscoveryProvider:
    """Reports a window through get_model_info; no defaults table."""

    async def get_model_info(self, model, **kwargs):
        return {"context_window_size": 111}


class _DefaultsProvider:
    """Reports nothing; supplies a provider defaults table entry."""

    async def get_model_info(self, model, **kwargs):
        return None

    def default_context_window(self, model):
        return 222


class _SilentProvider:
    """Reports nothing and has no defaults table."""

    async def get_model_info(self, model, **kwargs):
        return None


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        ({"id": "m", "max_input_tokens": 128000}, 128000),
        ({"id": "m", "context_length": 64000}, 64000),
        ({"id": "m", "context_window": 262144}, 262144),
        # A zero/garbage context_window must not mask a real limit.
        ({"id": "m", "max_input_tokens": 0, "context_window": 4096}, 4096),
        ({"id": "m"}, None),
    ],
)
def test_parse_models_payload_normalizes_every_window_shape(entry, expected):
    models = parse_models_payload({"data": [entry]})
    assert models[0].context_window_size == expected


@pytest.mark.asyncio
async def test_get_model_info_normalizes_every_window_shape(monkeypatch):
    payload = {
        "data": [
            {"id": "litellm", "max_input_tokens": 128000},
            {"id": "openrouter", "context_length": 64000},
            {"id": "otari", "context_window": 262144},
            {"id": "bare"},
        ]
    }

    async def fake_get(self, url, **kwargs):
        return httpx.Response(200, json=payload, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    provider = OpenAICompatProvider(url="http://gateway:4100/v1")

    assert await provider.get_model_info("litellm") == {"context_window_size": 128000}
    assert await provider.get_model_info("openrouter") == {"context_window_size": 64000}
    assert await provider.get_model_info("otari") == {"context_window_size": 262144}
    assert await provider.get_model_info("bare") is None
    assert await provider.get_model_info("missing") is None


@pytest.mark.asyncio
async def test_precedence_config_beats_discovery(config):
    clear_providers()
    register_provider("p", _DiscoveryProvider())
    config.model_configs["m"] = ModelConfig(provider="p", model="x", context_window_size=999)

    assert await ensure_model_context_window(config, "m") == 999
    clear_providers()


@pytest.mark.asyncio
async def test_precedence_discovery_beats_defaults(config):
    clear_providers()
    register_provider("p", _DiscoveryProvider())
    config.model_configs["m"] = ModelConfig(provider="p", model="x")
    config.compaction.max_tokens = 300000

    assert await ensure_model_context_window(config, "m") == 111
    assert config.model_configs["m"].context_window_size == 111
    clear_providers()


@pytest.mark.asyncio
async def test_precedence_defaults_beats_proxy(config):
    clear_providers()
    register_provider("p", _DefaultsProvider())
    config.model_configs["m"] = ModelConfig(provider="p", model="x")
    config.compaction.max_tokens = 300000

    assert await ensure_model_context_window(config, "m") == 222
    assert config.model_configs["m"].context_window_size == 222
    clear_providers()


@pytest.mark.asyncio
async def test_proxy_is_last_resort(config):
    clear_providers()
    register_provider("p", _SilentProvider())
    config.model_configs["m"] = ModelConfig(provider="p", model="x")
    config.compaction.max_tokens = 300000

    assert await ensure_model_context_window(config, "m") == 300000
    assert ("p", "x") in _failed_model_info
    clear_providers()


@pytest.mark.asyncio
async def test_vertex_hole_warns_about_the_defaults_table(config, caplog):
    clear_providers()
    register_provider("vertex-test", VertexProvider(project="test"))
    config.providers["vertex-test"] = ProviderConfig(type="vertex")
    config.model_configs["m"] = ModelConfig(provider="vertex-test", model="gemini-9-future")
    config.compaction.max_tokens = 300000

    with caplog.at_level(logging.WARNING, logger="decafclaw.llm"):
        assert await ensure_model_context_window(config, "m") == 300000

    assert "Vertex model 'gemini-9-future' has no defaults entry and no configured window" in caplog.text
    clear_providers()


@pytest.mark.asyncio
async def test_gateway_without_a_window_warns_about_upstream_config(config, caplog):
    clear_providers()
    register_provider("otari-local", _SilentProvider())
    config.providers["otari-local"] = ProviderConfig(type="openai-compat")
    config.model_configs["m"] = ModelConfig(provider="otari-local", model="deepseek/chat")
    config.compaction.max_tokens = 300000

    with caplog.at_level(logging.WARNING, logger="decafclaw.llm"):
        assert await ensure_model_context_window(config, "m") == 300000

    assert "Model 'deepseek/chat' (otari-local) reported no context window" in caplog.text
    clear_providers()


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("gemini-4-argon", 1_048_576),
        ("gemini-3.8-flash", 1_048_576),
        ("gemini-3.1-pro-preview", 1_048_576),
        ("gemini-2.5-flash", 1_048_576),
        ("gemini-9-future", None),
        ("text-embedding-005", None),
    ],
)
def test_vertex_family_table_uses_longest_prefix(model, expected):
    assert _vertex_default_context_window(model) == expected
