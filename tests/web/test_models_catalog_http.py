"""Model catalog HTTP endpoint tests (#1211)."""

import json

import pytest

from decafclaw.model_catalog import (
    CatalogModel,
    CatalogProvider,
    ModelCatalog,
    catalog_path,
    load_catalog,
    save_catalog,
)


def _seed_catalog(config, enabled="local/on"):
    """Seed a saved catalog with one enabled and one disabled model."""
    catalog = ModelCatalog(
        providers={
            "local": CatalogProvider(
                discovered_at="2026-11-05T12:00:00+00:00",
                models=[
                    CatalogModel(id="on", enabled=True, chat=True, tools=True, context_window_size=4096),
                    CatalogModel(id="off", enabled=False, chat=None, tools=None),
                ],
            )
        },
        default_model=enabled,
    )
    save_catalog(config, catalog)
    return catalog


@pytest.fixture
def catalog_config(http_config):
    http_config.providers = {"local": {"type": "openai-compat", "url": "http://x/v1"}}
    from decafclaw.config_types import ModelConfig

    http_config.model_configs = {"hand-model": ModelConfig(provider="local", model="hand")}
    http_config.agent_path.mkdir(parents=True, exist_ok=True)
    return http_config


@pytest.fixture
def app(catalog_config, bus):
    from decafclaw.http_server import create_app

    return create_app(catalog_config, bus)


@pytest.mark.asyncio
async def test_models_list_returns_enabled_plus_hand(client, catalog_config):
    """GET /api/models — picker shows hand configs + enabled discovered only."""
    _seed_catalog(catalog_config)
    from decafclaw.model_catalog import apply_catalog

    apply_catalog(catalog_config, load_catalog(catalog_config))

    resp = await client.get("/api/models")
    assert resp.status_code == 200
    data = resp.json()
    assert data["models"] == ["hand-model", "local/on"]
    assert data["default"] == "local/on"


@pytest.mark.asyncio
async def test_models_catalog_get(client, catalog_config):
    """GET /api/models/catalog — full catalog with flags and effective default."""
    _seed_catalog(catalog_config)
    from decafclaw.model_catalog import apply_catalog

    apply_catalog(catalog_config, load_catalog(catalog_config))

    resp = await client.get("/api/models/catalog")
    assert resp.status_code == 200
    data = resp.json()
    assert data["default_model"] == "local/on"
    assert data["default_pointer"] == "local/on"
    assert set(data["picker_models"]) == {"hand-model", "local/on"}
    provider = data["providers"]["local"]
    assert provider["error"] == ""
    models = {m["key"]: m for m in provider["models"]}
    assert models["local/on"]["enabled"] is True
    assert models["local/on"]["selectable"] is True  # materialized by apply
    assert models["local/off"]["enabled"] is False
    assert models["local/off"]["selectable"] is False


@pytest.mark.asyncio
async def test_models_catalog_discover(client, catalog_config, monkeypatch):
    """POST /api/models/catalog/discover — explicit refresh updates the catalog."""
    from decafclaw.llm.types import DiscoveredModel

    class FakeGateway:
        async def discover_models(self):
            return [DiscoveredModel(id="fresh", chat=True, tools=True, context_window_size=8192)]

    from decafclaw.llm import registry

    monkeypatch.setattr(registry, "get_provider", lambda name: FakeGateway())
    monkeypatch.setattr(registry, "list_providers", lambda: ["local"])

    resp = await client.post("/api/models/catalog/discover")
    assert resp.status_code == 200
    data = resp.json()
    models = {m["key"]: m for m in data["providers"]["local"]["models"]}
    assert set(models) == {"local/fresh"}
    # Metadata-rich gateway report → enabled by default.
    assert models["local/fresh"]["enabled"] is True
    # Materialized server-side...
    assert "local/fresh" in catalog_config.model_configs
    # ...so the picker reflects it immediately (alongside the hand config).
    assert data["picker_models"] == ["hand-model", "local/fresh"]


@pytest.mark.asyncio
async def test_models_catalog_discover_gateway_down_keeps_cache(client, catalog_config, monkeypatch):
    """With the gateway down, discovery records the error and the cached
    catalog (plus the picker) survives."""
    _seed_catalog(catalog_config, enabled="local/on")
    from decafclaw.model_catalog import apply_catalog

    apply_catalog(catalog_config, load_catalog(catalog_config))

    class DownGateway:
        async def discover_models(self):
            raise ConnectionError("gateway unreachable")

    from decafclaw.llm import registry

    monkeypatch.setattr(registry, "get_provider", lambda name: DownGateway())
    monkeypatch.setattr(registry, "list_providers", lambda: ["local"])

    resp = await client.post("/api/models/catalog/discover")
    assert resp.status_code == 200
    data = resp.json()
    provider = data["providers"]["local"]
    assert "gateway unreachable" in provider["error"]
    # Cached entries untouched, picker still serves the enabled model.
    assert {m["key"] for m in provider["models"]} == {"local/on", "local/off"}
    assert data["picker_models"] == ["hand-model", "local/on"]


@pytest.mark.asyncio
async def test_models_catalog_toggle_and_persistence(client, catalog_config):
    """POST /api/models/catalog/model — disable removes from picker and
    persists to the sidecar."""
    _seed_catalog(catalog_config)
    from decafclaw.model_catalog import apply_catalog

    apply_catalog(catalog_config, load_catalog(catalog_config))

    resp = await client.post("/api/models/catalog/model", json={"key": "local/on", "enabled": False})
    assert resp.status_code == 200
    data = resp.json()
    assert "local/on" not in data["picker_models"]
    # Materialized entry pruned from the live config too.
    assert "local/on" not in catalog_config.model_configs

    # Sidecar persisted the flag — a fresh apply (restart) keeps it disabled.
    on_disk = json.loads(catalog_path(catalog_config).read_text())
    entry = next(m for m in on_disk["providers"]["local"]["models"] if m["id"] == "on")
    assert entry["enabled"] is False

    resp = await client.post("/api/models/catalog/model", json={"key": "local/on", "enabled": True})
    assert resp.status_code == 200
    assert "local/on" in resp.json()["picker_models"]


@pytest.mark.asyncio
async def test_models_catalog_toggle_unknown_key_404(client, catalog_config):
    resp = await client.post("/api/models/catalog/model", json={"key": "nope/m", "enabled": True})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_models_catalog_toggle_validation(client, catalog_config):
    resp = await client.post("/api/models/catalog/model", json={"key": ""})
    assert resp.status_code == 400
    resp = await client.post("/api/models/catalog/model", json={"key": "local/on", "enabled": "yes"})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_models_catalog_provider_bulk_enable_disable(client, catalog_config):
    """POST /api/models/catalog/provider — bulk enable/disable every
    discovered model of one provider, persisted to the sidecar."""
    _seed_catalog(catalog_config)
    from decafclaw.model_catalog import apply_catalog

    apply_catalog(catalog_config, load_catalog(catalog_config))

    resp = await client.post("/api/models/catalog/provider", json={"provider": "local", "enabled": True})
    assert resp.status_code == 200
    data = resp.json()
    models = {m["key"]: m for m in data["providers"]["local"]["models"]}
    assert models["local/on"]["enabled"] is True
    assert models["local/off"]["enabled"] is True
    assert set(data["picker_models"]) == {"hand-model", "local/on", "local/off"}

    resp = await client.post("/api/models/catalog/provider", json={"provider": "local", "enabled": False})
    assert resp.status_code == 200
    data = resp.json()
    models = {m["key"]: m for m in data["providers"]["local"]["models"]}
    assert models["local/on"]["enabled"] is False
    assert models["local/off"]["enabled"] is False
    assert data["picker_models"] == ["hand-model"]

    # Sidecar persisted the bulk flags.
    on_disk = json.loads(catalog_path(catalog_config).read_text())
    assert all(m["enabled"] is False for m in on_disk["providers"]["local"]["models"])


@pytest.mark.asyncio
async def test_models_catalog_provider_bulk_unknown_404(client, catalog_config):
    resp = await client.post("/api/models/catalog/provider", json={"provider": "nope", "enabled": True})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_models_catalog_provider_bulk_validation(client, catalog_config):
    resp = await client.post("/api/models/catalog/provider", json={"provider": ""})
    assert resp.status_code == 400
    resp = await client.post("/api/models/catalog/provider", json={"provider": "local", "enabled": "yes"})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_models_catalog_provider_bulk_never_touches_hand_configs(client, catalog_config):
    """Bulk disable prunes only catalog-owned entries; a hand-authored
    config under a colliding key survives."""
    from decafclaw.config_types import ModelConfig
    from decafclaw.model_catalog import apply_catalog, catalog_model_key, load_catalog

    _seed_catalog(catalog_config)
    hand = ModelConfig(provider="local", model="hand-on")
    colliding_key = catalog_model_key("local", "on")
    catalog_config.model_configs[colliding_key] = hand
    apply_catalog(catalog_config, load_catalog(catalog_config))
    assert colliding_key not in catalog_config.catalog_materialized

    resp = await client.post("/api/models/catalog/provider", json={"provider": "local", "enabled": False})
    assert resp.status_code == 200
    assert catalog_config.model_configs.get(colliding_key) == hand
    assert colliding_key in resp.json()["picker_models"]


@pytest.mark.asyncio
async def test_models_catalog_set_default(client, catalog_config):
    """POST /api/models/catalog/default — pointer takes effect without restart."""
    _seed_catalog(catalog_config)
    catalog_config.default_model = "hand-model"

    # Pointer at an enabled discovered model.
    resp = await client.post("/api/models/catalog/default", json={"model": "local/on"})
    assert resp.status_code == 200
    assert resp.json()["default_model"] == "local/on"

    # Clearing the pointer → config.json default takes over again.
    resp = await client.post("/api/models/catalog/default", json={"model": ""})
    assert resp.status_code == 200
    assert resp.json()["default_model"] == "hand-model"

    # Pointer at an unselectable model is rejected.
    resp = await client.post("/api/models/catalog/default", json={"model": "local/off"})
    assert resp.status_code == 400
