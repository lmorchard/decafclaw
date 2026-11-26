"""Context-window discovery: gateway shapes, id resolution, and budget flow (#1719)."""

import logging

import httpx
import pytest

from decafclaw.config_types import ModelConfig, ProviderConfig
from decafclaw.llm import _failed_model_info, ensure_model_context_window
from decafclaw.llm.providers.openai_compat import (
    OpenAICompatProvider,
    _resolve_listed_model,
    parse_models_payload,
)
from decafclaw.llm.providers.vertex import VertexProvider, _vertex_default_context_window
from decafclaw.llm.registry import clear_providers, register_provider
from decafclaw.llm.types import DiscoveredModel, ModelInfo

# A realistic otari-local listing: a configured deployment with the prefixed
# id and a bare ``context_window``, plus entries the gateway reports as
# unknown. The unknown entries must stay unknown (proxy), never guessed.
OTARI_PAYLOAD = {
    "object": "list",
    "data": [
        {
            "id": "anthropic/claude-haiku-4.5",
            "object": "model",
            "created": 1700000000,
            "owned_by": "anthropic",
            "mode": "chat",
            "supports_function_calling": True,
            "context_window": 200000,
        },
        {"id": "mandelbrot3-flint", "context_window": 262144},
        {"id": "mandelbrot3-gemma", "context_window": None},
        {"id": "moz-llama-70b"},
    ],
}


def test_parse_models_payload_reads_top_level_context_window():
    models = {m.id: m for m in parse_models_payload(OTARI_PAYLOAD)}

    haiku = models["anthropic/claude-haiku-4.5"]
    assert haiku.context_window_size == 200000
    assert haiku.chat is True
    assert haiku.tools is True
    assert models["mandelbrot3-flint"].context_window_size == 262144
    # Reported unknown (None) or omitted stays unknown — no invented value.
    assert models["mandelbrot3-gemma"].context_window_size is None
    assert models["moz-llama-70b"].context_window_size is None


@pytest.mark.parametrize(
    "entry",
    [
        {"id": "m", "capabilities": {"context_window": 8192}},
        {"id": "m", "model_info": {"context_window": 8192}},
    ],
)
def test_parse_models_payload_reads_nested_context_window(entry):
    assert parse_models_payload({"data": [entry]})[0].context_window_size == 8192


def test_context_window_does_not_override_richer_dialects():
    """A bare context_window only fills a window the richer shapes left unset."""
    payload = {"data": [{"id": "m", "max_input_tokens": 128000, "context_window": 4096}]}
    assert parse_models_payload(payload)[0].context_window_size == 128000


def test_zero_context_window_does_not_mask_a_real_limit():
    payload = {"data": [{"id": "m", "max_input_tokens": 0, "context_window": 4096}]}
    assert parse_models_payload(payload)[0].context_window_size == 4096


def test_existing_dialects_still_parse():
    """The three pre-existing shapes keep working (#1211)."""
    payload = {
        "data": [
            {"id": "litellm", "mode": "chat", "max_input_tokens": 128000},
            {"id": "openrouter", "context_length": 64000},
            {"id": "azure", "capabilities": {"chat_completion": True}},
        ]
    }
    models = {m.id: m for m in parse_models_payload(payload)}
    assert models["litellm"].context_window_size == 128000
    assert models["openrouter"].context_window_size == 64000
    assert models["azure"].chat is True
    assert models["azure"].context_window_size is None


@pytest.mark.asyncio
async def test_get_model_info_reuses_discover_models(monkeypatch):
    provider = OpenAICompatProvider(url="http://gateway:4100/v1")
    calls: list[int] = []

    async def fake_discover(*, timeout: int = 30):
        calls.append(timeout)
        return [
            DiscoveredModel(id="listed", context_window_size=262144),
            DiscoveredModel(id="bare"),
        ]

    monkeypatch.setattr(provider, "discover_models", fake_discover)

    assert await provider.get_model_info("listed") == ModelInfo(context_window_size=262144)
    assert await provider.get_model_info("bare") is None
    assert await provider.get_model_info("missing") is None
    # One discovery call per lookup — no second HTTP path was added.
    assert calls == [30, 30, 30]


@pytest.mark.asyncio
async def test_get_model_info_reads_the_real_listing(monkeypatch):
    async def fake_get(self, url, **kwargs):
        return httpx.Response(200, json=OTARI_PAYLOAD, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    provider = OpenAICompatProvider(url="http://gateway:4100/v1")

    assert await provider.get_model_info("mandelbrot3-flint") == ModelInfo(context_window_size=262144)
    assert await provider.get_model_info("moz-llama-70b") is None
    assert await provider.get_model_info("not-listed") is None


@pytest.mark.asyncio
async def test_get_model_info_normalizes_every_dialect(monkeypatch):
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

    assert await provider.get_model_info("litellm") == ModelInfo(context_window_size=128000)
    assert await provider.get_model_info("openrouter") == ModelInfo(context_window_size=64000)
    assert await provider.get_model_info("otari") == ModelInfo(context_window_size=262144)
    assert await provider.get_model_info("bare") is None


@pytest.mark.asyncio
async def test_get_model_info_resolves_bare_config_name_to_prefixed_gateway_id(monkeypatch):
    """Config may use the bare name while the gateway indexes the prefix."""
    provider = OpenAICompatProvider(url="http://gateway:4100/v1")

    async def fake_discover(*, timeout: int = 30):
        return [DiscoveredModel(id="anthropic/claude-haiku-4.5", context_window_size=200000)]

    monkeypatch.setattr(provider, "discover_models", fake_discover)

    assert await provider.get_model_info("claude-haiku-4.5") == ModelInfo(context_window_size=200000)
    # The exact prefixed id also resolves.
    assert await provider.get_model_info("anthropic/claude-haiku-4.5") == ModelInfo(context_window_size=200000)


def test_suffix_match_rejects_an_ambiguous_id():
    models = [DiscoveredModel(id="a/same"), DiscoveredModel(id="b/same")]
    assert _resolve_listed_model(models, "same") is None


class _DiscoveryProvider:
    """Reports a window through get_model_info; no defaults table."""

    async def get_model_info(self, model, **kwargs):
        return ModelInfo(context_window_size=111)

    def default_context_window(self, model):
        return None


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

    def default_context_window(self, model):
        return None


class _WindowProvider:
    def __init__(self, window: int | None):
        self.window = window

    async def get_model_info(self, model, **kwargs):
        if self.window is None:
            return None
        return ModelInfo(context_window_size=self.window)

    def default_context_window(self, model):
        return None


@pytest.mark.asyncio
async def test_config_window_beats_discovery(config):
    clear_providers()
    try:
        register_provider("p", _DiscoveryProvider())
        config.model_configs["m"] = ModelConfig(provider="p", model="x", context_window_size=999)
        assert await ensure_model_context_window(config, "m") == 999
    finally:
        clear_providers()


@pytest.mark.asyncio
async def test_discovery_beats_provider_defaults(config):
    clear_providers()
    try:
        register_provider("p", _DiscoveryProvider())
        config.model_configs["m"] = ModelConfig(provider="p", model="x")
        config.compaction.max_tokens = 300000
        assert await ensure_model_context_window(config, "m") == 111
        assert config.model_configs["m"].context_window_size == 111
    finally:
        clear_providers()


@pytest.mark.asyncio
async def test_provider_defaults_beat_the_proxy(config):
    clear_providers()
    try:
        register_provider("p", _DefaultsProvider())
        config.model_configs["m"] = ModelConfig(provider="p", model="x")
        config.compaction.max_tokens = 300000
        assert await ensure_model_context_window(config, "m") == 222
        assert config.model_configs["m"].context_window_size == 222
    finally:
        clear_providers()


@pytest.mark.asyncio
async def test_proxy_is_the_last_resort(config):
    clear_providers()
    try:
        register_provider("p", _SilentProvider())
        config.model_configs["m"] = ModelConfig(provider="p", model="x")
        config.compaction.max_tokens = 300000
        assert await ensure_model_context_window(config, "m") == 300000
        assert config.model_configs["m"].context_window_size == 0
        assert ("p", "x") in _failed_model_info
    finally:
        clear_providers()


@pytest.mark.asyncio
async def test_discovered_window_flows_to_effective_window_and_tool_budget(config):
    clear_providers()
    try:
        register_provider("gateway", _WindowProvider(200000))
        config.providers["gateway"] = ProviderConfig(type="openai-compat")
        config.model_configs["haiku"] = ModelConfig(provider="gateway", model="anthropic/claude-haiku-4.5")
        config.compaction.max_tokens = 300000
        config.agent.tool_context_budget_pct = 0.10
        config.default_model = "haiku"

        from decafclaw.model_catalog import effective_context_window

        assert await ensure_model_context_window(config, "haiku") == 200000
        assert config.model_configs["haiku"].context_window_size == 200000
        assert effective_context_window(config, "haiku") == 200000
        # 200K is below the 300K proxy, so the budget shrinks with the honest
        # window rather than inheriting the proxy's 30K.
        assert config.tool_context_budget_for("haiku") == 20000
        assert config.tool_context_budget_for("haiku") != int(300000 * 0.10)
    finally:
        clear_providers()


@pytest.mark.asyncio
async def test_window_above_the_proxy_grows_the_tool_budget(config):
    clear_providers()
    try:
        register_provider("gateway", _WindowProvider(1_048_576))
        config.providers["gateway"] = ProviderConfig(type="openai-compat")
        config.model_configs["wide"] = ModelConfig(provider="gateway", model="gemini-4-argon")
        config.compaction.max_tokens = 300000
        config.agent.tool_context_budget_pct = 0.10
        config.default_model = "wide"

        from decafclaw.model_catalog import effective_context_window

        assert await ensure_model_context_window(config, "wide") == 1_048_576
        assert effective_context_window(config, "wide") == 1_048_576
        assert config.tool_context_budget_for("wide") == 104857
        assert config.tool_context_budget_for("wide") > int(300000 * 0.10)
    finally:
        clear_providers()


@pytest.mark.asyncio
async def test_unlisted_model_falls_back_to_compaction_proxy(config):
    """A model the gateway reports as unknown stays on the proxy (#1719)."""
    clear_providers()
    try:
        register_provider("gateway", _WindowProvider(None))
        config.providers["gateway"] = ProviderConfig(type="openai-compat")
        config.model_configs["unknown"] = ModelConfig(provider="gateway", model="moz-llama-70b")
        config.compaction.max_tokens = 300000
        config.default_model = "unknown"

        from decafclaw.model_catalog import effective_context_window

        assert await ensure_model_context_window(config, "unknown") == 300000
        assert config.model_configs["unknown"].context_window_size == 0
        assert effective_context_window(config, "unknown") == 300000
    finally:
        clear_providers()


@pytest.mark.asyncio
async def test_gateway_without_a_window_warns_about_upstream_config(config, caplog):
    clear_providers()
    try:
        register_provider("otari-local", _SilentProvider())
        config.providers["otari-local"] = ProviderConfig(type="openai-compat")
        config.model_configs["m"] = ModelConfig(provider="otari-local", model="deepseek/chat")
        config.compaction.max_tokens = 300000

        with caplog.at_level(logging.WARNING, logger="decafclaw.llm"):
            assert await ensure_model_context_window(config, "m") == 300000

        assert "Model 'deepseek/chat' (otari-local) reported no context window" in caplog.text
    finally:
        clear_providers()


@pytest.mark.asyncio
async def test_vertex_gemini_resolves_to_the_family_window(config):
    """A known Gemini model gets the 1M family window, not the compaction cap (#1803)."""
    clear_providers()
    try:
        register_provider("vertex-test", VertexProvider(project="test"))
        config.providers["vertex-test"] = ProviderConfig(type="vertex")
        config.model_configs["m"] = ModelConfig(provider="vertex-test", model="gemini-3.8-flash")
        config.compaction.max_tokens = 300000

        assert await ensure_model_context_window(config, "m") == 1_048_576
        assert config.model_configs["m"].context_window_size == 1_048_576
        assert config.compaction.max_tokens == 300000
    finally:
        clear_providers()


@pytest.mark.asyncio
async def test_vertex_hole_warns_about_the_defaults_table(config, caplog):
    clear_providers()
    try:
        register_provider("vertex-test", VertexProvider(project="test"))
        config.providers["vertex-test"] = ProviderConfig(type="vertex")
        config.model_configs["m"] = ModelConfig(provider="vertex-test", model="gemini-9-future")
        config.compaction.max_tokens = 300000

        with caplog.at_level(logging.WARNING, logger="decafclaw.llm"):
            assert await ensure_model_context_window(config, "m") == 300000

        assert "Vertex model 'gemini-9-future' has no defaults entry and no configured window" in caplog.text
    finally:
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


@pytest.mark.asyncio
async def test_vertex_get_model_info_reports_none():
    """Vertex publishes no machine-readable limits; None is the contract.

    The family table (``default_context_window``) supplies the window, so
    discovery must report nothing rather than a raw wire shape.
    """
    provider = VertexProvider(project="test")
    assert await provider.get_model_info("gemini-3.8-flash") is None


@pytest.mark.asyncio
async def test_fastembed_get_model_info_reports_none():
    """Embedding-only provider: no model metadata to report."""
    from decafclaw.llm.providers.fastembed import FastembedProvider

    assert await FastembedProvider().get_model_info("all-MiniLM-L6-v2") is None
