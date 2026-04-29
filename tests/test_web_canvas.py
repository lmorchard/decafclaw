"""Tests for canvas REST endpoints and WebSocket event projection."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from decafclaw import widgets as widgets_module
from decafclaw.events import EventBus
from decafclaw.http_server import create_app
from decafclaw.web import websocket as ws_mod
from decafclaw.web.auth import create_token


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
def md_doc_registry(tmp_path, monkeypatch):
    """Install a fake widget registry that knows markdown_document."""
    from types import SimpleNamespace

    class _Reg:
        _d = {
            "markdown_document": SimpleNamespace(
                modes=["inline", "canvas"], required=["content"]
            ),
        }

        def get(self, name):
            return self._d.get(name)

        def validate(self, name, data):
            d = self._d.get(name)
            if not d:
                return False, "unknown"
            for r in getattr(d, "required", []):
                if r not in data:
                    return False, f"missing {r}"
            return True, None

        def normalize(self, name, data):
            return data

    reg = _Reg()
    monkeypatch.setattr(widgets_module, "_registry", reg)
    # Also patch the canvas module's import of get_widget_registry
    from decafclaw import canvas as canvas_mod
    monkeypatch.setattr(canvas_mod, "get_widget_registry", lambda: reg)
    return reg


@pytest.fixture
def manager_mock():
    m = MagicMock()
    m.emit = AsyncMock()
    return m


@pytest.fixture
def app(http_config, manager_mock, md_doc_registry):
    bus = EventBus()
    return create_app(http_config, bus, app_ctx=None, manager=manager_mock)


@pytest.fixture
def owned_conv(http_config):
    """Create a conversation owned by testuser. Returns conv_id."""
    from decafclaw.web.conversations import ConversationIndex
    index = ConversationIndex(http_config)
    return index.create("testuser", title="Test").conv_id


@pytest.fixture
def other_user_conv(http_config):
    """Create a conversation owned by a different user. Returns conv_id."""
    from decafclaw.web.conversations import ConversationIndex
    index = ConversationIndex(http_config)
    return index.create("otheruser", title="Other").conv_id


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


@pytest.mark.asyncio
async def test_get_canvas_state_empty(authed_client, owned_conv):
    resp = await authed_client.get(f"/api/canvas/{owned_conv}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["schema_version"] == 1
    assert body["active_tab"] is None
    assert body["tabs"] == []


@pytest.mark.asyncio
async def test_get_canvas_state_requires_auth(unauthed_client, owned_conv):
    resp = await unauthed_client.get(f"/api/canvas/{owned_conv}")
    assert resp.status_code in (401, 302, 403)


@pytest.mark.asyncio
async def test_get_canvas_state_invalid_conv_id(authed_client):
    resp = await authed_client.get("/api/canvas/..%2Fevil")
    # 400 (rejected) or 404 (no such conv); both acceptable, just no crash and no escape.
    assert resp.status_code in (400, 404)


@pytest.mark.asyncio
async def test_get_canvas_state_other_user_conv_404(authed_client, other_user_conv):
    """Accessing another user's canvas must return 404, not the actual state."""
    resp = await authed_client.get(f"/api/canvas/{other_user_conv}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_post_canvas_new_tab_writes_state_and_emits(
    authed_client, manager_mock, owned_conv,
):
    resp = await authed_client.post(
        f"/api/canvas/{owned_conv}/new_tab",
        json={"widget_type": "markdown_document",
              "data": {"content": "# Doc\n\nbody"}},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["tab_id"] == "canvas_1"
    follow = await authed_client.get(f"/api/canvas/{owned_conv}")
    assert follow.status_code == 200
    state = follow.json()
    assert state["active_tab"] == "canvas_1"
    assert state["tabs"][0]["data"] == {"content": "# Doc\n\nbody"}
    assert manager_mock.emit.await_count == 1
    args = manager_mock.emit.await_args
    assert args.args[0] == owned_conv
    assert args.args[1]["type"] == "canvas_update"
    assert args.args[1]["kind"] == "new_tab"


@pytest.mark.asyncio
async def test_post_canvas_new_tab_rejects_unknown_widget(authed_client, owned_conv):
    resp = await authed_client.post(
        f"/api/canvas/{owned_conv}/new_tab",
        json={"widget_type": "no_such", "data": {}},
    )
    assert resp.status_code == 400
    assert "not registered" in resp.json().get("error", "")


@pytest.mark.asyncio
async def test_post_canvas_new_tab_other_user_conv_404(authed_client, other_user_conv):
    resp = await authed_client.post(
        f"/api/canvas/{other_user_conv}/new_tab",
        json={"widget_type": "markdown_document",
              "data": {"content": "# pwn"}},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_post_canvas_new_tab_requires_auth(unauthed_client, owned_conv):
    resp = await unauthed_client.post(
        f"/api/canvas/{owned_conv}/new_tab",
        json={"widget_type": "markdown_document", "data": {"content": "x"}},
    )
    assert resp.status_code in (401, 302, 403)


@pytest.mark.asyncio
async def test_post_active_tab_changes_active(authed_client, manager_mock, owned_conv):
    # Seed: create two tabs
    await authed_client.post(
        f"/api/canvas/{owned_conv}/new_tab",
        json={"widget_type": "markdown_document", "data": {"content": "a"}},
    )
    await authed_client.post(
        f"/api/canvas/{owned_conv}/new_tab",
        json={"widget_type": "markdown_document", "data": {"content": "b"}},
    )
    # Switch active back to canvas_1
    resp = await authed_client.post(
        f"/api/canvas/{owned_conv}/active_tab",
        json={"tab_id": "canvas_1"},
    )
    assert resp.status_code == 200
    follow = await authed_client.get(f"/api/canvas/{owned_conv}")
    assert follow.json()["active_tab"] == "canvas_1"


@pytest.mark.asyncio
async def test_post_active_tab_unknown_id_400(authed_client, owned_conv):
    resp = await authed_client.post(
        f"/api/canvas/{owned_conv}/active_tab",
        json={"tab_id": "canvas_99"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_post_active_tab_other_user_conv_404(authed_client, other_user_conv):
    resp = await authed_client.post(
        f"/api/canvas/{other_user_conv}/active_tab",
        json={"tab_id": "canvas_1"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_post_close_tab(authed_client, manager_mock, owned_conv):
    await authed_client.post(
        f"/api/canvas/{owned_conv}/new_tab",
        json={"widget_type": "markdown_document", "data": {"content": "a"}},
    )
    await authed_client.post(
        f"/api/canvas/{owned_conv}/new_tab",
        json={"widget_type": "markdown_document", "data": {"content": "b"}},
    )
    resp = await authed_client.post(
        f"/api/canvas/{owned_conv}/close_tab",
        json={"tab_id": "canvas_2"},
    )
    assert resp.status_code == 200
    follow = await authed_client.get(f"/api/canvas/{owned_conv}")
    state = follow.json()
    assert state["active_tab"] == "canvas_1"
    assert len(state["tabs"]) == 1


@pytest.mark.asyncio
async def test_post_close_tab_unknown_id_400(authed_client, owned_conv):
    resp = await authed_client.post(
        f"/api/canvas/{owned_conv}/close_tab",
        json={"tab_id": "canvas_99"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_post_close_tab_other_user_conv_404(authed_client, other_user_conv):
    resp = await authed_client.post(
        f"/api/canvas/{other_user_conv}/close_tab",
        json={"tab_id": "canvas_1"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_standalone_canvas_with_tab_id(authed_client, owned_conv):
    resp = await authed_client.get(f"/canvas/{owned_conv}/canvas_2")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")
    assert "<dc-widget-host" in resp.text


@pytest.mark.asyncio
async def test_get_standalone_canvas_with_tab_id_other_user_404(
    authed_client, other_user_conv,
):
    resp = await authed_client.get(f"/canvas/{other_user_conv}/canvas_1")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_canvas_update_event_kind_new_tab():
    """The forwarder passes through kind=new_tab."""
    sent = []

    async def ws_send(payload):
        sent.append(payload)

    state = {"ws_send": ws_send, "config": None}
    callback = ws_mod._make_canvas_update_forwarder(state, conv_id="conv-x")
    await callback({
        "type": "canvas_update", "conv_id": "conv-x",
        "kind": "new_tab", "active_tab": "canvas_3",
        "tab": {"id": "canvas_3", "label": "L", "widget_type": "code_block", "data": {"code": "x"}},
    })
    assert sent[0]["kind"] == "new_tab"
    assert sent[0]["tab"]["id"] == "canvas_3"


@pytest.mark.asyncio
async def test_canvas_update_event_kind_close_tab():
    sent = []

    async def ws_send(payload):
        sent.append(payload)

    state = {"ws_send": ws_send, "config": None}
    callback = ws_mod._make_canvas_update_forwarder(state, conv_id="conv-x")
    await callback({
        "type": "canvas_update", "conv_id": "conv-x",
        "kind": "close_tab", "active_tab": "canvas_2",
        "tab": None, "closed_tab_id": "canvas_3",
    })
    assert sent[0]["kind"] == "close_tab"
    assert sent[0]["closed_tab_id"] == "canvas_3"


@pytest.mark.asyncio
async def test_canvas_update_event_kind_set_active():
    sent = []

    async def ws_send(payload):
        sent.append(payload)

    state = {"ws_send": ws_send, "config": None}
    callback = ws_mod._make_canvas_update_forwarder(state, conv_id="conv-x")
    await callback({
        "type": "canvas_update", "conv_id": "conv-x",
        "kind": "set_active", "active_tab": "canvas_1", "tab": None,
    })
    assert sent[0]["kind"] == "set_active"
    assert sent[0]["active_tab"] == "canvas_1"


@pytest.mark.asyncio
async def test_get_standalone_canvas_html(authed_client, owned_conv):
    resp = await authed_client.get(f"/canvas/{owned_conv}")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")
    assert "<dc-widget-host" in resp.text


@pytest.mark.asyncio
async def test_get_standalone_canvas_other_user_conv_404(authed_client, other_user_conv):
    resp = await authed_client.get(f"/canvas/{other_user_conv}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_standalone_canvas_requires_auth(unauthed_client, owned_conv):
    resp = await unauthed_client.get(f"/canvas/{owned_conv}")
    assert resp.status_code in (401, 302, 403)


@pytest.mark.asyncio
async def test_canvas_update_event_projected_to_client():
    """The on_conv_event callback forwards canvas_update events to the WS."""
    sent = []

    async def ws_send(payload):
        sent.append(payload)

    state = {"ws_send": ws_send, "config": None}
    callback = ws_mod._make_canvas_update_forwarder(state, conv_id="conv-x")

    await callback({
        "type": "canvas_update",
        "conv_id": "conv-x",
        "kind": "set",
        "active_tab": "canvas_1",
        "tab": {"id": "canvas_1", "label": "L",
                "widget_type": "markdown_document",
                "data": {"content": "x"}},
    })

    assert sent == [{
        "type": "canvas_update",
        "conv_id": "conv-x",
        "kind": "set",
        "active_tab": "canvas_1",
        "tab": {"id": "canvas_1", "label": "L",
                "widget_type": "markdown_document",
                "data": {"content": "x"}},
    }]


@pytest.mark.asyncio
async def test_canvas_update_event_skipped_for_other_conv():
    """A canvas_update for a different conv_id is ignored by this socket."""
    sent = []

    async def ws_send(payload):
        sent.append(payload)

    state = {"ws_send": ws_send, "config": None}
    callback = ws_mod._make_canvas_update_forwarder(state, conv_id="conv-x")
    await callback({"type": "canvas_update", "conv_id": "OTHER",
                    "kind": "set", "active_tab": None, "tab": None})
    assert sent == []
