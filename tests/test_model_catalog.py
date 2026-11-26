"""Model catalog tests (#1211).

Covers URL derivation, gateway payload parsing + capability filtering,
sidecar write/read, materialization safe defaults, picker computation,
default-model resolution order, and disable-the-default fallback.
HTTP endpoint behavior lives in tests/web/test_models_catalog_http.py.
"""

import json
import logging
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
    picker_model_groups,
    picker_models,
    refresh_catalog,
    save_catalog,
    set_default_model,
    set_model_enabled,
    warn_unresolvable_model_configs,
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
        # Full metadata: every entry reports chat + tools + context.
        assert m.chat is True
        assert m.tools is True
        assert m.context_window_size and m.context_window_size > 0

    from decafclaw.model_catalog import default_enabled

    by_id = {m.id: m for m in models}
    current = {
        "gemini-3.5-flash-lite",
        "gemini-3.5-flash",
        "gemini-3.8-flash",
        "gemini-4-hydrogen",
        "gemini-4-carbon",
        "gemini-4-neon",
        "gemini-4-argon",
    }
    assert set(by_id) == current
    # Every curated entry is live-confirmed, so it lands enabled under the
    # default-on policy.
    for model_id in current:
        assert by_id[model_id].confirmed is True
        assert default_enabled(by_id[model_id]) is True
    # Retired ids are removed rather than left in the fallback. Gemini 2.5
    # retired 2026-10-20; 3.6 flash retired 2026-11-19; 3.7 flash retires
    # 2027-01-28.
    for retired in (
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
        "gemini-2.5-pro",
        "gemini-3.6-flash",
        "gemini-3.7-flash",
    ):
        assert retired not in by_id


def test_default_enabled_requires_live_confirmation():
    """Identical gateway metadata, but an unconfirmed (curated static guess)
    id lands disabled under the default-on policy."""
    from dataclasses import replace

    from decafclaw.llm.types import DiscoveredModel
    from decafclaw.model_catalog import default_enabled

    full = DiscoveredModel(id="m", chat=True, tools=True, context_window_size=8192)
    assert default_enabled(full) is True
    assert default_enabled(replace(full, confirmed=False)) is False


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
    assert "local/llama3.2" in config.runtime.catalog_materialized
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
    assert "local/m1" not in config.runtime.catalog_materialized

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
    assert "local/b" not in config.runtime.catalog_materialized


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


def test_warn_unresolvable_model_configs(config, caplog):
    config.providers = {
        "local": ProviderConfig(type="openai-compat", url="http://x/v1"),
        "other": ProviderConfig(type="openai-compat", url="http://y/v1"),
    }
    config.model_configs["good"] = ModelConfig(provider="local", model="m1")
    config.model_configs["stale"] = ModelConfig(provider="local", model="gone")
    config.model_configs["unknown-provider"] = ModelConfig(provider="nope", model="x")
    config.model_configs["no-discovery"] = ModelConfig(provider="other", model="anything")
    config.model_configs["local/materialized"] = ModelConfig(provider="local", model="m1")
    config.runtime.catalog_materialized.add("local/materialized")
    catalog = ModelCatalog(providers={"local": CatalogProvider(models=[CatalogModel(id="m1")])})

    with caplog.at_level(logging.WARNING, logger="decafclaw.model_catalog"):
        problems = warn_unresolvable_model_configs(config, catalog)

    assert problems == [
        "stale: local/gone not in the discovered catalog",
        "unknown-provider: unknown provider 'nope'",
    ]
    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "stale" in warnings[0].message
    assert "unknown-provider" in warnings[0].message


def test_warn_unresolvable_skips_failed_discovery(config):
    config.providers = {"local": ProviderConfig(type="openai-compat", url="http://x/v1")}
    config.model_configs["hand"] = ModelConfig(provider="local", model="anything")
    catalog = ModelCatalog(providers={"local": CatalogProvider(error="gateway down", models=[CatalogModel(id="m1")])})

    assert warn_unresolvable_model_configs(config, catalog) == []


def test_picker_groups_after_alias_cleanup(config):
    """Post-cleanup shape: one discovered entry per model, policy entries in Custom."""
    config.providers = {"otari": ProviderConfig(type="openai-compat", url="http://x/v1")}
    save_catalog(
        config,
        ModelCatalog(
            providers={
                "otari": CatalogProvider(
                    models=[
                        CatalogModel(id="moz-deepseek", enabled=True),
                        CatalogModel(id="other", enabled=True),
                    ]
                )
            }
        ),
    )
    apply_catalog(config, load_catalog(config))

    assert picker_model_groups(config) == [{"name": "otari", "models": ["otari/moz-deepseek", "otari/other"]}]

    # A hand-authored policy entry that pins an explicit id stays in Custom.
    config.model_configs["claude-opus"] = ModelConfig(provider="otari", model="anthropic/claude-opus")
    assert {"name": "Custom", "models": ["claude-opus"]} in picker_model_groups(config)


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
# Embedding-only providers are not chat model sources
# ---------------------------------------------------------------------------


def test_refresh_catalog_skips_embedding_only_provider(config):
    """fastembed is built into init_providers but must not enter the model picker."""
    import asyncio

    from decafclaw.llm.providers.fastembed import FastembedProvider
    from decafclaw.llm.registry import clear_providers, register_provider

    clear_providers()
    register_provider("fastembed", FastembedProvider())
    try:
        catalog = asyncio.run(refresh_catalog(config))
        assert "fastembed" not in catalog.providers
    finally:
        clear_providers()


async def test_refresh_prunes_provider_removed_from_config(config):
    """A provider dropped from config is removed from the catalog (#1867)."""
    from decafclaw.llm.registry import clear_providers, register_provider
    from decafclaw.llm.types import DiscoveredModel

    class Gateway:
        async def discover_models(self):
            return [DiscoveredModel(id="m1", chat=True, tools=True)]

    config.providers = {"a": ProviderConfig(type="openai-compat", url="http://x/v1")}
    save_catalog(
        config,
        ModelCatalog(
            providers={
                "a": CatalogProvider(models=[CatalogModel(id="m1", enabled=True)]),
                "b": CatalogProvider(models=[CatalogModel(id="old", enabled=True)]),
            }
        ),
    )
    clear_providers()
    register_provider("a", Gateway())
    try:
        catalog = await refresh_catalog(config)
        assert "a" in catalog.providers
        assert "b" not in catalog.providers
    finally:
        clear_providers()


async def test_refresh_keeps_failed_provider_cache(config):
    """A registered provider whose discovery fails keeps its cached models (#1867)."""
    from decafclaw.llm.registry import clear_providers, register_provider
    from decafclaw.llm.types import DiscoveredModel

    class Good:
        async def discover_models(self):
            return [DiscoveredModel(id="m1", chat=True, tools=True)]

    class Bad:
        async def discover_models(self):
            raise RuntimeError("gateway down")

    config.providers = {
        "a": ProviderConfig(type="openai-compat", url="http://x/v1"),
        "b": ProviderConfig(type="openai-compat", url="http://y/v1"),
    }
    save_catalog(
        config,
        ModelCatalog(
            providers={
                "a": CatalogProvider(models=[CatalogModel(id="m1", enabled=True)]),
                "b": CatalogProvider(models=[CatalogModel(id="cached", enabled=True)]),
            }
        ),
    )
    clear_providers()
    register_provider("a", Good())
    register_provider("b", Bad())
    try:
        catalog = await refresh_catalog(config)
        assert "b" in catalog.providers
        assert [model.id for model in catalog.providers["b"].models] == ["cached"]
        assert catalog.providers["b"].error
    finally:
        clear_providers()


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


# ---------------------------------------------------------------------------
# Aux-approval model resolution (#1213)
# ---------------------------------------------------------------------------


def test_set_aux_approval_model_writes_pointer(config):
    """Sidecar aux-approval pointer write/clear; empty means ride on the conversation model."""
    import asyncio

    from decafclaw.model_catalog import set_aux_approval_model

    catalog = asyncio.run(set_aux_approval_model(config, "local/on"))
    assert catalog.aux_approval_model == "local/on"
    assert load_catalog(config).aux_approval_model == "local/on"

    cleared = asyncio.run(set_aux_approval_model(config, ""))
    assert cleared.aux_approval_model == ""


def test_aux_approval_resolution_inherit_and_fallback(config):
    """#1213: aux-approval resolution — sidecar pointer → config seed → conversation model."""
    _config_with_models(config)
    config.shell.aux_approval_model = "hand-model"

    from decafclaw.model_catalog import effective_aux_approval_model

    # No pointer → inherit the config seed.
    assert effective_aux_approval_model(config, "active") == "hand-model"

    import asyncio

    from decafclaw.model_catalog import set_aux_approval_model

    asyncio.run(set_aux_approval_model(config, "local/on"))
    assert effective_aux_approval_model(config, "active") == "local/on"

    # Disabled → fall back to the config seed.
    from decafclaw.model_catalog import set_model_enabled

    asyncio.run(set_model_enabled(config, "local/on", False))
    assert effective_aux_approval_model(config, "active") == "hand-model"


def test_aux_approval_falls_back_to_auxiliary_model(config):
    """#1213 review: with no pointer and no seed, the judge keeps riding the
    legacy aux chain — auxiliary_model first, mirroring Context.aux_llm."""
    _config_with_models(config)
    config.auxiliary_model = "hand-model"

    from decafclaw.model_catalog import effective_aux_approval_model

    # auxiliary_model outranks the conversation model, exactly as aux_llm().
    assert effective_aux_approval_model(config, "active") == "hand-model"

    # Without auxiliary_model, the conversation model is next.
    config.auxiliary_model = ""
    assert effective_aux_approval_model(config, "active") == "active"

    # A dedicated pointer still outranks the legacy aux chain.
    config.auxiliary_model = "hand-model"
    import asyncio

    from decafclaw.model_catalog import set_aux_approval_model

    asyncio.run(set_aux_approval_model(config, "local/on"))
    assert effective_aux_approval_model(config, "active") == "local/on"


def test_aux_approval_non_key_seed_treated_as_unset(config, caplog):
    """#1213 review: a raw seed name is not routable (the shell group has no
    legacy endpoint of its own) — warn and fall through instead of handing
    it to the provider layer, where resolution would silently default."""
    import logging

    _config_with_models(config)
    config.shell.aux_approval_model = "raw-name-not-a-key"

    from decafclaw.model_catalog import effective_aux_approval_model

    with caplog.at_level(logging.WARNING, logger="decafclaw.model_catalog"):
        # Falls through to the conversation model, not the raw name.
        assert effective_aux_approval_model(config, "active") == "active"
    assert any("raw-name-not-a-key" in r.message for r in caplog.records)


def test_aux_approval_payload_inherit_sentinel(config):
    """#1213 review: with nothing explicit anywhere, the payload emits the
    empty sentinel rather than naming the global default — the actual judge
    is each conversation's model."""
    _config_with_models(config)
    config.default_model = "hand-model"

    from decafclaw.model_catalog import catalog_payload

    payload = catalog_payload(config)
    assert payload["aux_approval_pointer"] == ""
    assert payload["aux_approval_model"] == ""

    # Naming the global default would imply it is judging; an explicit
    # pointer or aux chain still resolves to a name.
    config.auxiliary_model = "hand-model"
    assert catalog_payload(config)["aux_approval_model"] == "hand-model"


# ---------------------------------------------------------------------------
# Reflection / verifier model pointers (#1212)
# ---------------------------------------------------------------------------


def test_set_reflection_model_writes_pointer(config):
    """Sidecar reflection pointer write/clear; empty means inherit."""
    import asyncio

    from decafclaw.model_catalog import set_reflection_model

    catalog = asyncio.run(set_reflection_model(config, "local/on"))
    assert catalog.reflection_model == "local/on"
    assert load_catalog(config).reflection_model == "local/on"
    assert asyncio.run(set_reflection_model(config, "")).reflection_model == ""


def test_set_verifier_model_writes_pointer(config):
    """Sidecar verifier pointer write/clear; empty means inherit."""
    import asyncio

    from decafclaw.model_catalog import set_verifier_model

    catalog = asyncio.run(set_verifier_model(config, "local/on"))
    assert catalog.verifier_model == "local/on"
    assert load_catalog(config).verifier_model == "local/on"


def test_reflection_payload_exposes_pointers(config):
    """Catalog payload exposes raw pointers + effective reflection/verifier names."""
    from decafclaw.model_catalog import catalog_payload

    payload = catalog_payload(config)
    assert payload["reflection_pointer"] == ""
    assert payload["verifier_pointer"] == ""
    assert payload["reflection_model"] == ""
    assert payload["verifier_model"] == ""


def test_reflection_resolution_pointer_overrides_config_seed(config):
    """#1212: sidecar reflection pointer outranks the config-level seed."""
    _config_with_models(config)
    config.reflection.model = "hand-model"

    import asyncio

    from decafclaw.model_catalog import effective_reflection_model, effective_verifier_model, set_reflection_model

    # No pointer → inherit the config seed.
    assert effective_reflection_model(config) == "hand-model"
    assert effective_verifier_model(config) == ""

    asyncio.run(set_reflection_model(config, "local/on"))
    assert effective_reflection_model(config) == "local/on"

    # Clearing the pointer → inherit the seed again.
    asyncio.run(set_reflection_model(config, ""))
    assert effective_reflection_model(config) == "hand-model"


def test_reflection_resolution_pointer_disabled_falls_back(config):
    """#1212: disabling a referenced reflection model falls back cleanly."""
    _config_with_models(config)
    config.reflection.model = "hand-model"

    import asyncio

    from decafclaw.model_catalog import effective_reflection_model, set_model_enabled, set_reflection_model

    asyncio.run(set_reflection_model(config, "local/on"))
    assert effective_reflection_model(config) == "local/on"

    # Disable the referenced model → pointer invalid → config seed takes over.
    asyncio.run(set_model_enabled(config, "local/on", False))
    assert effective_reflection_model(config) == "hand-model"


def test_verifier_resolution_inherit_by_default(config):
    """#1212: with no pointers and no seed, the verifier resolves empty."""
    _config_with_models(config)

    from decafclaw.model_catalog import effective_verifier_model

    assert effective_verifier_model(config) == ""


# ---------------------------------------------------------------------------
# Centralized resolution ladders (#1360)
#
# One definition per pointer: turn (active), auxiliary, judge, verifier, and
# context window. Callers adopt these in 8.4; the ladders below are the whole
# contract, so each test walks the full fallback chain rather than one rung.
# ---------------------------------------------------------------------------


def _two_key_config(config):
    """Two selectable hand configs plus a legacy llm group (no sidecar)."""
    config.providers = {"local": ProviderConfig(type="openai-compat", url="http://x/v1")}
    config.model_configs["hand-model"] = ModelConfig(provider="local", model="hand")
    config.model_configs["other-model"] = ModelConfig(provider="local", model="other")
    config.default_model = "other-model"
    return config


def test_turn_model_resolution_chain(config):
    """Turn ladder: selectable active model → effective default → "" (legacy llm)."""
    from decafclaw.model_catalog import effective_turn_model

    _two_key_config(config)

    # Active model that names a model_configs key outranks the default.
    assert effective_turn_model(config, "hand-model") == "hand-model"

    # No active model → the effective default (config.json here).
    assert effective_turn_model(config, "") == "other-model"

    # An unknown active name never leaks through: the default takes over.
    assert effective_turn_model(config, "not-a-key") == "other-model"

    # Nothing configured at all → empty string, meaning "use config.llm".
    config.default_model = ""
    assert effective_turn_model(config, "") == ""


def test_active_model_resolution_chain(config):
    """Conversation-model ladder: active name (even unroutable) → effective default."""
    from decafclaw.model_catalog import effective_active_model

    _two_key_config(config)

    # Raw active name passes through unvalidated — callers downstream decide.
    assert effective_active_model(config, "raw-name") == "raw-name"
    assert effective_active_model(config, "") == "other-model"

    config.default_model = ""
    assert effective_active_model(config, "") == ""


def test_aux_model_resolution_chain(config):
    """Aux ladder: auxiliary_model → conversation model → effective default."""
    from decafclaw.model_catalog import effective_aux_model

    _two_key_config(config)

    # auxiliary_model outranks the conversation model, exactly as aux_llm().
    config.auxiliary_model = "hand-model"
    assert effective_aux_model(config, "raw-active") == "hand-model"

    # Without auxiliary_model, the conversation model is next.
    config.auxiliary_model = ""
    assert effective_aux_model(config, "raw-active") == "raw-active"

    # Without either, the effective default.
    assert effective_aux_model(config, "") == "other-model"

    # Nothing anywhere → empty string, meaning "use config.llm".
    config.default_model = ""
    assert effective_aux_model(config, "") == ""


def test_judge_model_verifier_key_wins(config):
    """Judge ladder rung 1: a verifier naming a model_configs key routes the judge."""
    from decafclaw.model_catalog import effective_judge_model

    _two_key_config(config)
    config.reflection.model = "hand-model"
    config.reflection.verifier_model = "other-model"

    target = effective_judge_model(config)
    assert target.model_name == "other-model"
    assert target.llm_url == ""
    assert target.llm_model == ""
    assert target.llm_api_key == ""


def test_judge_model_reflection_key_beats_default(config):
    """Judge ladder rung 2: a reflection model naming a key beats the default."""
    from decafclaw.model_catalog import effective_judge_model

    _two_key_config(config)
    config.reflection.model = "hand-model"

    target = effective_judge_model(config)
    assert target.model_name == "hand-model"
    assert target.llm_model == ""


def test_judge_model_falls_back_to_default(config):
    """Judge ladder rung 3: no reflection model → the effective default."""
    from decafclaw.model_catalog import effective_judge_model

    _two_key_config(config)
    config.reflection.model = ""

    target = effective_judge_model(config)
    assert target.model_name == "other-model"
    assert target.llm_model == ""


def test_judge_model_sidecar_pointer_outranks_seed(config):
    """Judge ladder rung 2 through the #1212 pointer, not just the seed."""
    import asyncio

    from decafclaw.model_catalog import effective_judge_model, set_reflection_model

    _config_with_models(config)
    config.reflection.model = "hand-model"
    asyncio.run(set_reflection_model(config, "local/on"))

    assert effective_judge_model(config).model_name == "local/on"


def test_judge_model_non_key_reflection_beats_default(config):
    """#752 guard: an unroutable reflection.model outranks the default and
    routes through the legacy resolved() fields, never through default_model."""
    from decafclaw.model_catalog import effective_judge_model

    _two_key_config(config)
    config.llm.url = "http://legacy/v1/chat/completions"
    config.llm.model = "legacy-model"
    config.llm.api_key = "legacy-key"
    config.reflection.model = "cheap-judge-model"

    target = effective_judge_model(config)
    assert target.model_name == ""
    assert target.llm_url == "http://legacy/v1/chat/completions"
    assert target.llm_model == "cheap-judge-model"
    assert target.llm_api_key == "legacy-key"


def test_judge_model_legacy_fields_follow_reflection_resolved(config):
    """Judge ladder rung 4: reflection.url/model/api_key win over config.llm
    when they are set, because the resolver returns resolved() fields."""
    from decafclaw.model_catalog import effective_judge_model

    _two_key_config(config)
    config.llm.url = "http://legacy/v1/chat/completions"
    config.llm.api_key = "legacy-key"
    config.reflection.url = "http://judge/v1/chat/completions"
    config.reflection.model = "cheap-judge-model"
    config.reflection.api_key = "judge-key"

    target = effective_judge_model(config)
    assert target.model_name == ""
    assert target.llm_url == "http://judge/v1/chat/completions"
    assert target.llm_model == "cheap-judge-model"
    assert target.llm_api_key == "judge-key"


def test_judge_model_nothing_set_uses_llm_group(config):
    """Judge ladder rung 4 with empty reflection fields: resolved() supplies
    the llm group, and the target never claims a named model."""
    from decafclaw.model_catalog import effective_judge_model

    config.llm.url = "http://legacy/v1/chat/completions"
    config.llm.model = "legacy-model"
    config.llm.api_key = "legacy-key"
    config.default_model = ""

    target = effective_judge_model(config)
    assert target.model_name == ""
    assert target.llm_url == "http://legacy/v1/chat/completions"
    assert target.llm_model == "legacy-model"
    assert target.llm_api_key == "legacy-key"


def test_judge_model_unknown_verifier_falls_back(config):
    """An unroutable verifier is skipped, not passed through (#591)."""
    from decafclaw.model_catalog import effective_judge_model

    _two_key_config(config)
    config.reflection.verifier_model = "no-such-model"
    config.reflection.model = ""

    assert effective_judge_model(config).model_name == "other-model"


def test_judge_model_does_not_leak_api_key_in_repr(config):
    """The target carries the judge's key only as a field; repr stays clean."""
    from decafclaw.model_catalog import effective_judge_model

    config.reflection.model = "cheap-judge-model"
    config.reflection.api_key = "judge-key"
    config.default_model = ""

    target = effective_judge_model(config)
    assert target.llm_api_key == "judge-key"
    assert "judge-key" not in repr(target)


def test_verifier_pointer_only_accepts_selectable_keys(config):
    """Verifier ladder: selectable sidecar pointer → seed (raw or empty)."""
    import asyncio

    from decafclaw.model_catalog import effective_verifier_model, save_catalog, set_verifier_model

    _config_with_models(config)
    config.reflection.verifier_model = "hand-model"
    assert effective_verifier_model(config) == "hand-model"

    # A pointer at a disabled or vanished model falls back to the seed.
    asyncio.run(set_verifier_model(config, "not-a-key"))
    assert effective_verifier_model(config) == "hand-model"

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
                "verifier_model": "local/on",
            }
        ),
    )
    apply_catalog(config, load_catalog(config))
    assert effective_verifier_model(config) == "local/on"


def test_context_window_resolution_chain(config):
    """Window ladder: the named model's window → llm window → compaction cap."""
    from decafclaw.model_catalog import effective_context_window

    config.model_configs["measured"] = ModelConfig(provider="p", model="m", context_window_size=64000)
    config.model_configs["unmeasured"] = ModelConfig(provider="p", model="m")
    config.default_model = "measured"

    # 1. A named model with a configured window wins outright.
    assert effective_context_window(config, "measured") == 64000
    # An empty name means "the default model".
    assert effective_context_window(config, "") == 64000

    # 2. An unmeasured named model falls through to the llm group.
    config.llm.context_window_size = 200000
    assert effective_context_window(config, "unmeasured") == 200000
    # An unknown name never resolves through a model config.
    assert effective_context_window(config, "no-such-model") == 200000

    # 3. No window anywhere → the conservative compaction cap.
    config.llm.context_window_size = 0
    config.compaction.max_tokens = 100000
    assert effective_context_window(config, "unmeasured") == 100000
    config.default_model = "unmeasured"
    assert effective_context_window(config, "") == 100000


def test_context_window_sync_caller_uses_resolver(config):
    """The sync (configured-only) resolver and the definition agree."""
    from decafclaw.context_diagnostics import get_context_window_size
    from decafclaw.model_catalog import effective_context_window

    config.model_configs["measured"] = ModelConfig(provider="p", model="m", context_window_size=64000)
    config.default_model = "measured"

    class _Ctx:
        active_model = "measured"

    assert get_context_window_size(config, _Ctx()) == effective_context_window(config, "measured") == 64000

    # Empty active model → the default model, exactly as before.
    _Ctx.active_model = ""
    assert get_context_window_size(config, _Ctx()) == effective_context_window(config) == 64000


@pytest.mark.asyncio
async def test_context_window_async_caller_uses_resolver(config):
    """The async (auto-detect) resolver shares the definition's fallback ladder."""
    from decafclaw.llm import ensure_model_context_window
    from decafclaw.model_catalog import effective_context_window

    config.model_configs["unmeasured"] = ModelConfig(provider="local", model="m")
    config.default_model = "unmeasured"
    config.llm.context_window_size = 0
    config.compaction.max_tokens = 12345

    # No provider info available (no registry entry) → the same cap the
    # sync definition returns.
    assert await ensure_model_context_window(config, "unmeasured") == 12345
    assert effective_context_window(config, "unmeasured") == 12345
