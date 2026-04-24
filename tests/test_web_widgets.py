"""Tests for /api/widgets and /widgets/{tier}/{name}/widget.js."""

import json

import pytest
from httpx import ASGITransport, AsyncClient

from decafclaw import widgets as widgets_module
from decafclaw.events import EventBus
from decafclaw.http_server import create_app
from decafclaw.web.auth import create_token

_SIMPLE_SCHEMA = {
    "type": "object",
    "required": ["value"],
    "properties": {"value": {"type": "string"}},
}


def _write_widget(root, name, tier_subdir, *, description="desc",
                  js_body="// stub\n"):
    d = root / name
    d.mkdir(parents=True)
    (d / "widget.json").write_text(json.dumps({
        "name": name,
        "description": description,
        "modes": ["inline"],
        "data_schema": _SIMPLE_SCHEMA,
    }))
    (d / "widget.js").write_text(js_body)


@pytest.fixture
def http_config(config):
    config.http.enabled = True
    config.http.secret = "test-secret"
    config.http.host = "127.0.0.1"
    config.http.port = 18881
    config.http.base_url = ""
    config.agent_path.mkdir(parents=True, exist_ok=True)
    return config


@pytest.fixture
def bus():
    return EventBus()


@pytest.fixture
def widget_registry(tmp_path, monkeypatch):
    bundled = tmp_path / "widgets-bundled"
    admin = tmp_path / "widgets-admin"
    _write_widget(bundled, "data_table", "bundled",
                  description="Tabular display",
                  js_body="export class Bundled{};\n")
    _write_widget(admin, "custom_thing", "admin",
                  description="Admin-only",
                  js_body="export class AdminThing{};\n")

    class _Cfg:
        agent_path = tmp_path / "agent_home"
    registry = widgets_module.load_widget_registry(
        _Cfg(), bundled_dir=bundled, admin_dir=admin)
    monkeypatch.setattr(widgets_module, "_registry", registry)
    return registry


@pytest.fixture
def app(http_config, bus, widget_registry):
    return create_app(http_config, bus)


@pytest.fixture
async def authed_client(app, http_config):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        token = create_token(http_config, "testuser")
        resp = await client.post("/api/auth/login", json={"token": token})
        client.cookies = resp.cookies
        yield client


@pytest.fixture
async def unauthed_client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


# ---------------- /api/widgets ----------------


@pytest.mark.asyncio
async def test_list_widgets_returns_descriptors(authed_client):
    resp = await authed_client.get("/api/widgets")
    assert resp.status_code == 200
    body = resp.json()
    names = {w["name"] for w in body["widgets"]}
    assert names == {"data_table", "custom_thing"}
    # Each entry carries a cache-busted js_url
    for w in body["widgets"]:
        assert "?v=" in w["js_url"]
        assert w["js_url"].startswith(f"/widgets/{w['tier']}/{w['name']}/widget.js")
    # Schema + tier present
    dt = next(w for w in body["widgets"] if w["name"] == "data_table")
    assert dt["tier"] == "bundled"
    assert dt["data_schema"]["type"] == "object"


@pytest.mark.asyncio
async def test_list_widgets_requires_auth(unauthed_client):
    resp = await unauthed_client.get("/api/widgets")
    assert resp.status_code == 401


# ---------------- /widgets/{tier}/{name}/widget.js ----------------


@pytest.mark.asyncio
async def test_serve_bundled_widget(authed_client):
    resp = await authed_client.get("/widgets/bundled/data_table/widget.js")
    assert resp.status_code == 200
    assert "javascript" in resp.headers["content-type"]
    assert "class Bundled" in resp.text


@pytest.mark.asyncio
async def test_serve_admin_widget(authed_client):
    resp = await authed_client.get("/widgets/admin/custom_thing/widget.js")
    assert resp.status_code == 200
    assert "class AdminThing" in resp.text


@pytest.mark.asyncio
async def test_serve_widget_tier_mismatch(authed_client):
    """A bundled widget requested via the admin URL is 404, not served."""
    resp = await authed_client.get("/widgets/admin/data_table/widget.js")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_serve_widget_unknown_tier(authed_client):
    resp = await authed_client.get("/widgets/workspace/data_table/widget.js")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_serve_widget_unknown_name(authed_client):
    resp = await authed_client.get("/widgets/bundled/no_such_widget/widget.js")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_serve_widget_requires_auth(unauthed_client):
    resp = await unauthed_client.get("/widgets/bundled/data_table/widget.js")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_serve_widget_rejects_symlink_escape(
        tmp_data, monkeypatch, http_config, bus):
    """An admin widget.js that's a symlink to a file outside the admin
    tier root must not be served — defense against admins (or compromised
    workspace tooling later) creating arbitrary-file-read symlinks."""
    # Admin tier root is config.agent_path / "widgets"; secret file
    # lives outside that root so a symlink crossing it is detectable.
    admin_root = http_config.agent_path / "widgets"
    secret_dir = tmp_data / "secret"
    secret_dir.mkdir()
    secret_file = secret_dir / "leak.txt"
    secret_file.write_text("super-secret")

    widget_dir = admin_root / "evil"
    widget_dir.mkdir(parents=True)
    (widget_dir / "widget.json").write_text(
        '{"name": "evil", "description": "x", "modes": ["inline"], '
        '"data_schema": {"type": "object"}}')
    (widget_dir / "widget.js").symlink_to(secret_file)

    registry = widgets_module.load_widget_registry(
        http_config,
        bundled_dir=tmp_data / "no-bundled",
        admin_dir=admin_root)
    monkeypatch.setattr(widgets_module, "_registry", registry)

    app = create_app(http_config, bus)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        token = create_token(http_config, "testuser")
        await client.post("/api/auth/login", json={"token": token})
        resp = await client.get("/widgets/admin/evil/widget.js")
    assert resp.status_code == 404
    assert "super-secret" not in resp.text
