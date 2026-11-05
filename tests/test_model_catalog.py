"""Model catalog tests (#1211).

Covers URL derivation, gateway payload parsing + capability filtering,
sidecar write/read, materialization safe defaults, picker computation,
default-model resolution order, and disable-the-default fallback.
HTTP endpoint behavior lives in tests/web/test_models_catalog_http.py.
"""

import json
from unittest.mock import patch

import httpx
import pytest

from decafclaw.config import resolve_model
from decafclaw.config_types import ModelConfig, ProviderConfig
from decafclaw.llm.providers.openai_compat import (
    OpenAICompatProvider,
    parse_models_payload,
)
from decafclaw.llm.providers.vertex import VertexProvider
from decafclaw.model_catalog import (
    CatalogModel,
    CatalogProvider,
    ModelCatalog,
    apply_catalog,
    catalog_from_dict,
    catalog_path,
    effective_default_model,
    load_catalog,
    materialize_model_config,
    picker_models,
    refresh_catalog,
    save_catalog,
    set_default_model,
    set_model_enabled,
)

# ---------------------------------------------------------------------------
# URL derivation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url,expected",
    [
        ("http://localhost:11434/v1/chat/completions", "http://localhost:11434/v1/models"),
        ("http://localhost:11434/v1/", "http://localhost:11434/v1/models"),
        ("http://localhost:11434/v1", "http://localhost:11434/v1/models"),
        ("http://localhost:11434", "http://localhost:11434/v1/models"),
        ("https://api.openai.com/v1/responses", "https://api.openai.com/v1/models"),
        ("https://api.openai.com/v1/embeddings", "https://api.openai.com/v1/models"),
        ("https://litellm.example.com/v1", "https://litellm.example.com/v1/models"),
    ],
)
def test_models_url_derivation(url, expected):
    provider = OpenAICompatProvider(url=url, api_key="k")
    assert provider._models_url() == expected


# ---------------------------------------------------------------------------
# Payload parsing and capability filtering
# ---------------------------------------------------------------------------


LITELLM_PAYLOAD = {
    "object": "list",
    "data": [
        {"id": "gpt-4o", "mode": "chat", "supports_function_calling": True, "max_input_tokens": 128000},
        {"id": "text-embedding-3-large", "mode": "embedding", "supports_function_calling": False},
        {"id": "whisper-1", "mode": "audio_transcription"},
        {"id": "old-completion", "mode": "completion"},
        {"id": "bare-model"},
    ],
}

OPENROUTER_PAYLOAD = {
    "data": [
        {
            "id": "openai/gpt-4o",
            "architecture": {"modality": "text+image->text"},
            "supported_parameters": ["tools", "tool_choice", "temperature"],
            "context_length": 128000,
            "owned_by": "openai",
        },
        {
            "id": "stability/sdxl",
            "architecture": {"modality": "text->image"},
            "supported_parameters": [],
            "context_length": 77,
        },
        {
            "id": "openai/o1-mini",
            "architecture": {"modality": "text->text"},
            "supported_parameters": ["temperature"],
            "context_length": 128000,
        },
    ]
}

OPENAI_BARE_PAYLOAD = {
    "object": "list",
    "data": [
        {"id": "gpt-4o", "object": "model", "owned_by": "openai"},
        {"id": "gpt-4o-mini", "object": "model", "owned_by": "system"},
    ],
}


def test_parse_litellm_payload_filters_non_chat():
    models = parse_models_payload(LITELLM_PAYLOAD)
    ids = [m.id for m in models]
    # Embeddings, transcription, and legacy completion models are excluded;
    # the bare-ID entry is kept as an unknown candidate.
    assert ids == ["gpt-4o", "bare-model"]
    gpt = models[0]
    assert gpt.chat is True
    assert gpt.tools is True
    assert gpt.context_window_size == 128000
    assert models[1].chat is None
    assert models[1].tools is None
    assert models[1].context_window_size is None


def test_parse_openrouter_payload():
    models = parse_models_payload(OPENROUTER_PAYLOAD)
    by_id = {m.id: m for m in models}
    # Image-generation output is not chat; chat-without-tool-support stays
    # but is marked not tool-capable.
    assert set(by_id) == {"openai/gpt-4o", "openai/o1-mini"}
    assert by_id["openai/gpt-4o"].tools is True
    assert by_id["openai/gpt-4o"].context_window_size == 128000
    assert by_id["openai/gpt-4o"].owned_by == "openai"
    assert by_id["openai/o1-mini"].tools is False


def test_parse_bare_openai_payload_keeps_unknown_candidates():
    models = parse_models_payload(OPENAI_BARE_PAYLOAD)
    assert [m.id for m in models] == ["gpt-4o", "gpt-4o-mini"]
    assert all(m.chat is None and m.tools is None and m.context_window_size is None for m in models)
    assert models[0].owned_by == "openai"


def test_parse_malformed_payloads_yield_empty():
    assert parse_models_payload(None) == []
    assert parse_models_payload("nope") == []
    assert parse_models_payload({}) == []
    assert parse_models_payload({"data": "nope"}) == []
    assert parse_models_payload({"data": [None, 3, {"id": ""}, {"id": "ok"}]})[0].id == "ok"


def test_parse_dedupes_ids():
    payload = {"data": [{"id": "m"}, {"id": "m"}]}
    assert len(parse_models_payload(payload)) == 1


async def test_discover_models_http_get():
    """discover_models GETs the derived URL with auth headers."""
    provider = OpenAICompatProvider(url="http://gateway:8000/v1", api_key="sekrit")
    captured = {}

    def fake_get(self, url, **kwargs):
        captured["url"] = url
        captured["headers"] = kwargs.get("headers")
        return httpx.Response(200, json=OPENAI_BARE_PAYLOAD, request=httpx.Request("GET", url))

    async def async_fake_get(self, url, **kwargs):
        return fake_get(self, url, **kwargs)

    with patch("httpx.AsyncClient.get", new=async_fake_get):
        models = await provider.discover_models()

    assert captured["url"] == "http://gateway:8000/v1/models"
    assert captured["headers"]["Authorization"] == "Bearer sekrit"
    assert [m.id for m in models] == ["gpt-4o", "gpt-4o-mini"]


def test_vertex_discovery_static_fallback():
    provider = VertexProvider(project="p")

    import asyncio

    models = asyncio.run(provider.discover_models())
    assert models, "static fallback must not be empty"
    for m in models:
        # Full metadata: every entry lands enabled under the default-on policy.
        assert m.chat is True
        assert m.tools is True
        assert m.context_window_size and m.context_window_size > 0


# ---------------------------------------------------------------------------
# Catalog sidecar write/read
# ---------------------------------------------------------------------------


def test_catalog_round_trip(config):
    config.agent_path.mkdir(parents=True, exist_ok=True)
    catalog = ModelCatalog(
        providers={
            "local": CatalogProvider(
                discovered_at="2026-11-05T12:00:00+00:00",
                error="",
                models=[
                    CatalogModel(id="llama3.2", enabled=True, chat=None, tools=None, context_window_size=None),
                    CatalogModel(id="qwen2.5", enabled=False, chat=True, tools=True, context_window_size=131072),
                ],
            )
        },
        default_model="local/llama3.2",
    )
    assert save_catalog(config, catalog)

    loaded = load_catalog(config)
    assert loaded == catalog
    # Sidecar contains no provider credentials by construction — assert the
    # api_key given to the provider never lands in the file.
    raw = json.loads(catalog_path(config).read_text())
    assert "api_key" not in json.dumps(raw)


def test_load_missing_or_corrupt_catalog(config):
    # Missing file → empty catalog, no raise.
    assert load_catalog(config) == ModelCatalog()

    # Corrupt file → empty catalog + warning, never a crash.
    config.agent_path.mkdir(parents=True, exist_ok=True)
    catalog_path(config).write_text("{not json")
    assert load_catalog(config) == ModelCatalog()

    # Malformed entries are dropped rather than crashing.
    catalog_path(config).write_text(
        json.dumps(
            {
                "version": 1,
                "providers": {"local": {"models": [{"id": "ok", "enabled": True}, "junk", {"no_id": True}]}},
                "default_model": 42,
            }
        )
    )
    catalog = load_catalog(config)
    assert [m.id for m in catalog.providers["local"].models] == ["ok"]
    assert catalog.providers["local"].models[0].enabled is True
    assert catalog.default_model == ""


# ---------------------------------------------------------------------------
# Materialization safe defaults
# ---------------------------------------------------------------------------


def test_materialize_safe_defaults_with_gateway_context():
    entry = CatalogModel(id="llama3.2", enabled=True, chat=True, tools=True, context_window_size=131072)
    mc = materialize_model_config("local", entry)
    assert mc == ModelConfig(
        provider="local",
        model="llama3.2",
        context_window_size=131072,
        timeout=300,
        streaming=True,
        reasoning_effort=None,
    )


def test_materialize_safe_defaults_without_metadata():
    entry = CatalogModel(id="llama3.2", enabled=True)
    mc = materialize_model_config("local", entry)
    # 0 = existing compaction default; never guesses reasoning_effort.
    assert mc.context_window_size == 0
    assert mc.timeout == 300
    assert mc.streaming is True
    assert mc.reasoning_effort is None


def test_apply_materializes_enabled_only_and_never_overwrites_hand(config):
    config.providers = {"local": ProviderConfig(type="openai-compat", url="http://x/v1")}
    config.model_configs["hand-model"] = ModelConfig(provider="local", model="hand", timeout=99)
    catalog = ModelCatalog(
        providers={
            "local": CatalogProvider(
                models=[
                    CatalogModel(id="llama3.2", enabled=True, context_window_size=4096),
                    CatalogModel(id="qwen", enabled=False),
                ]
            )
        }
    )
    apply_catalog(config, catalog)

    # Enabled discovered model materialized under {provider}/{model_id}...
    assert "local/llama3.2" in config.model_configs
    assert config.model_configs["local/llama3.2"].context_window_size == 4096
    # ...tracked as catalog-owned...
    assert "local/llama3.2" in config.catalog_materialized
    # ...disabled model NOT registered...
    assert "local/qwen" not in config.model_configs
    # ...and a hand-authored entry under the same name is never overwritten.
    config.model_configs["local/llama3.2"] = ModelConfig(provider="local", model="llama3.2", timeout=99)
    apply_catalog(config, catalog)
    assert config.model_configs["local/llama3.2"].timeout == 99


def test_disable_colliding_discovered_key_keeps_hand_config(config):
    """Disabling a discovered model whose {provider}/{model_id} key collides
    with a hand-authored model_configs entry must never prune the hand
    config: apply_catalog may only claim keys it actually created."""
    config.providers = {"local": ProviderConfig(type="openai-compat", url="http://x/v1")}
    config.model_configs["local/m1"] = ModelConfig(provider="local", model="m1", timeout=99)
    save_catalog(
        config,
        ModelCatalog(providers={"local": CatalogProvider(models=[CatalogModel(id="m1", enabled=True)])}),
    )
    apply_catalog(config, load_catalog(config))

    # Collision: the hand entry survives untouched and is not catalog-owned.
    assert config.model_configs["local/m1"].timeout == 99
    assert "local/m1" not in config.catalog_materialized

    # Disabling the discovered model keeps the hand config alive...
    import asyncio

    assert asyncio.run(set_model_enabled(config, "local/m1", False)) is not None
    assert "local/m1" in config.model_configs
    assert config.model_configs["local/m1"].timeout == 99
    # ...and the picker still serves it.
    assert picker_models(config) == ["local/m1"]


def test_apply_prunes_disabled_and_vanished(config):
    config.providers = {"local": ProviderConfig(type="openai-compat", url="http://x/v1")}
    catalog = ModelCatalog(
        providers={
            "local": CatalogProvider(
                models=[
                    CatalogModel(id="a", enabled=True),
                    CatalogModel(id="b", enabled=True),
                ]
            )
        }
    )
    apply_catalog(config, catalog)
    assert {"local/a", "local/b"} <= set(config.model_configs)

    # Disable one, remove the other entirely.
    catalog.providers["local"].models = [CatalogModel(id="a", enabled=True)]
    apply_catalog(config, catalog)
    assert "local/a" in config.model_configs
    assert "local/b" not in config.model_configs
    assert "local/b" not in config.catalog_materialized


# ---------------------------------------------------------------------------
# Picker computation
# ---------------------------------------------------------------------------


def test_picker_models_enabled_only(config):
    config.providers = {"local": ProviderConfig(type="openai-compat", url="http://x/v1")}
    config.model_configs["hand-model"] = ModelConfig(provider="local", model="hand")
    catalog = ModelCatalog(
        providers={
            "local": CatalogProvider(
                models=[
                    CatalogModel(id="on", enabled=True),
                    CatalogModel(id="off", enabled=False),
                ]
            )
        }
    )
    save_catalog(config, catalog)
    apply_catalog(config, load_catalog(config))
    assert picker_models(config) == ["hand-model", "local/on"]


# ---------------------------------------------------------------------------
# Default-model resolution order
# ---------------------------------------------------------------------------


def _config_with_models(config):
    """Seed hand configs + a saved catalog (sidecar is the source of truth;
    production always saves before applying)."""
    config.providers = {"local": ProviderConfig(type="openai-compat", url="http://x/v1")}
    config.model_configs["hand-model"] = ModelConfig(provider="local", model="hand")
    catalog = ModelCatalog(
        providers={
            "local": CatalogProvider(
                models=[
                    CatalogModel(id="on", enabled=True),
                    CatalogModel(id="off", enabled=False),
                ]
            )
        }
    )
    save_catalog(config, catalog)
    apply_catalog(config, load_catalog(config))
    return catalog


def test_default_resolution_pointer_wins(config):
    _config_with_models(config)
    config.default_model = "hand-model"
    # 1. Sidecar pointer at an enabled discovered model outranks config.json.
    save_catalog(
        config,
        catalog_from_dict(
            {
                "providers": {
                    "local": {
                        "models": [
                            {"id": "on", "enabled": True},
                            {"id": "off", "enabled": False},
                        ]
                    }
                },
                "default_model": "local/on",
            }
        ),
    )
    apply_catalog(config, load_catalog(config))
    assert effective_default_model(config) == "local/on"


def test_default_resolution_pointer_at_hand_config(config):
    _config_with_models(config)
    config.default_model = "hand-model"
    save_catalog(config, catalog_from_dict({"providers": {}, "default_model": "hand-model"}))
    # Pointer may name a hand-written config too.
    assert effective_default_model(config) == "hand-model"


def test_default_resolution_fallback_config_json(config):
    _config_with_models(config)
    config.default_model = "hand-model"
    # 2. No pointer → config.json default_model.
    save_catalog(config, ModelCatalog())
    assert effective_default_model(config) == "hand-model"

    # A pointer at a disabled or unknown model falls back cleanly.
    save_catalog(config, catalog_from_dict({"providers": {}, "default_model": "local/off"}))
    assert effective_default_model(config) == "hand-model"
    save_catalog(config, catalog_from_dict({"providers": {}, "default_model": "nope/gone"}))
    assert effective_default_model(config) == "hand-model"


def test_default_resolution_final_fallback_empty(config):
    """3. Empty pointer + empty default_model → '' (callers use config.llm.model)."""
    _config_with_models(config)
    config.default_model = ""
    save_catalog(config, ModelCatalog())
    assert effective_default_model(config) == ""


def test_resolve_model_uses_catalog_pointer(config):
    """resolve_model integrates the pointer: named discovery key resolves."""
    _config_with_models(config)
    config.default_model = "hand-model"
    save_catalog(
        config,
        catalog_from_dict(
            {
                "providers": {"local": {"models": [{"id": "on", "enabled": True}]}},
                "default_model": "local/on",
            }
        ),
    )
    apply_catalog(config, load_catalog(config))
    pc, mc = resolve_model(config)  # no name → effective default
    assert mc.model == "on"
    assert pc.url == "http://x/v1"


# ---------------------------------------------------------------------------
# Enable/disable persistence across restart + default disable fallback
# ---------------------------------------------------------------------------


def test_enable_disable_persists_across_restart(config):
    """Toggle → sidecar on disk → a fresh Config (restart) applies it."""
    config.agent_path.mkdir(parents=True, exist_ok=True)
    config.providers = {"local": ProviderConfig(type="openai-compat", url="http://x/v1")}

    import asyncio

    from decafclaw.llm.registry import clear_providers, register_provider
    from decafclaw.llm.types import DiscoveredModel

    class FakeGateway:
        async def discover_models(self):
            return [
                DiscoveredModel(id="llama3.2", chat=True, tools=True, context_window_size=131072),
                DiscoveredModel(id="qwen2.5", chat=True, tools=True, context_window_size=131072),
            ]

    clear_providers()
    register_provider("local", FakeGateway())
    try:
        catalog = asyncio.run(refresh_catalog(config))
        # Metadata-rich → both enabled by default.
        assert picker_models(config) == ["local/llama3.2", "local/qwen2.5"]

        assert asyncio.run(set_model_enabled(config, "local/qwen2.5", False)) is not None
        assert picker_models(config) == ["local/llama3.2"]

        # "Restart": brand-new Config over the same data dir.
        from decafclaw.config import Config
        from decafclaw.config_types import AgentConfig, ReflectionConfig

        fresh = Config(
            agent=AgentConfig(data_home=str(config.agent.data_home), id="test-agent", user_id="testuser"),
            reflection=ReflectionConfig(enabled=False),
        )
        fresh.providers = dict(config.providers)
        assert load_catalog(fresh).default_model == catalog.default_model
        apply_catalog(fresh, load_catalog(fresh))
        assert picker_models(fresh) == ["local/llama3.2"]
    finally:
        clear_providers()


async def test_changes_during_refresh_survive(config):
    """Lost-update review finding: a toggle and a default-pointer change made
    while discovery is in flight must survive the refresh. Discovery runs
    without the catalog loaded; inside the lock the sidecar is reloaded and
    the discovered results merged into it — never into a stale copy."""
    config.providers = {"local": ProviderConfig(type="openai-compat", url="http://x/v1")}
    import asyncio

    from decafclaw.llm.registry import clear_providers, register_provider
    from decafclaw.llm.types import DiscoveredModel

    def _models():
        return [
            DiscoveredModel(id="m1", chat=True, tools=True, context_window_size=131072),
            DiscoveredModel(id="qwen", chat=True, tools=True, context_window_size=131072),
        ]

    class Gateway:
        async def discover_models(self):
            return _models()

    clear_providers()
    register_provider("local", Gateway())
    try:
        await refresh_catalog(config)  # seed: m1 + qwen enabled, materialized

        entered = asyncio.Event()
        release = asyncio.Event()

        class SlowGateway:
            async def discover_models(self):
                entered.set()
                await release.wait()
                return _models()

        clear_providers()
        register_provider("local", SlowGateway())

        refresh = asyncio.create_task(refresh_catalog(config))
        await entered.wait()  # discovery in flight, catalog lock free

        # User changes land while discovery runs.
        assert await set_model_enabled(config, "local/m1", False) is not None
        await set_default_model(config, "local/qwen")

        release.set()
        await refresh

        # Neither change was clobbered by the refresh landing.
        catalog = load_catalog(config)
        assert catalog.default_model == "local/qwen"
        by_id = {m.id: m for m in catalog.providers["local"].models}
        assert by_id["m1"].enabled is False
        assert by_id["qwen"].enabled is True
        assert picker_models(config) == ["local/qwen"]
        assert effective_default_model(config) == "local/qwen"
        assert "local/m1" not in config.model_configs
    finally:
        clear_providers()


def test_disable_default_model_falls_back(config):
    """Disabling the model the pointer names → resolution falls back."""
    _config_with_models(config)
    config.default_model = "hand-model"

    import asyncio

    # Pointer at an enabled discovered model...
    asyncio.run(set_default_model(config, "local/on"))
    assert effective_default_model(config) == "local/on"

    # ...then disabled → pointer invalid → config.json default_model.
    asyncio.run(set_model_enabled(config, "local/on", False))
    assert effective_default_model(config) == "hand-model"
