"""Tests for the sticky-slot REST reload-recovery endpoint."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from decafclaw import widgets as widgets_module
from decafclaw.events import EventBus
from decafclaw.http_server import create_app
from decafclaw.web.auth import create_token


@pytest.fixture
def http_config(config):
    config.http.enabled = True
    config.http.secret = "test-secret"
    config.http.host = "127.0.0.1"
    config.http.port = 18882
    config.http.base_url = ""
    config.agent_path.mkdir(parents=True, exist_ok=True)
    return config


@pytest.fixture
def md_doc_registry(monkeypatch):
    """Install a fake widget registry that knows markdown_document (sticky-capable)."""
    from types import SimpleNamespace

    class _Reg:
        _d = {
            "markdown_document": SimpleNamespace(
                modes=["inline", "canvas", "sticky"],
                accepts_input=False,
                required=["content"],
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
    from decafclaw import sticky as sticky_mod

    monkeypatch.setattr(sticky_mod, "get_widget_registry", lambda: reg)
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
async def test_get_sticky_state_pinned(authed_client, http_config, owned_conv):
    from decafclaw import sticky as sticky_mod

    result = await sticky_mod.set_sticky(
        http_config,
        owned_conv,
        "markdown_document",
        {"content": "# Doc"},
    )
    assert result.ok, result.error

    resp = await authed_client.get(f"/api/sticky/{owned_conv}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["widget_type"] == "markdown_document"
    assert body["data"] == {"content": "# Doc"}


@pytest.mark.asyncio
async def test_get_sticky_state_empty_when_unset(authed_client, owned_conv):
    resp = await authed_client.get(f"/api/sticky/{owned_conv}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["widget_type"] is None
    assert body["data"] is None


@pytest.mark.asyncio
async def test_get_sticky_state_requires_auth(unauthed_client, owned_conv):
    resp = await unauthed_client.get(f"/api/sticky/{owned_conv}")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_get_sticky_state_other_user_conv_404(authed_client, other_user_conv):
    """Accessing another user's sticky state must return 404, not the actual state."""
    resp = await authed_client.get(f"/api/sticky/{other_user_conv}")
    assert resp.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("conv_id,status", [("bad!id", 400), ("missing-conversation", 404)])
async def test_get_sticky_state_invalid_or_missing(authed_client, conv_id, status):
    resp = await authed_client.get(f"/api/sticky/{conv_id}")
    assert resp.status_code == status


@pytest.mark.asyncio
async def test_get_sticky_preserves_heterogeneous_payload(authed_client, http_config, owned_conv):
    from decafclaw import sticky as sticky_mod

    data = {"content": "# Doc", "unknown": {"rows": [1, True, None, {"name": "nested"}]}}
    result = await sticky_mod.set_sticky(http_config, owned_conv, "markdown_document", data)
    assert result.ok, result.error
    resp = await authed_client.get(f"/api/sticky/{owned_conv}")
    assert resp.status_code == 200
    assert resp.json() == {"widget_type": "markdown_document", "data": data}


# ---------------------------------------------------------------------------
# POST / DELETE /api/sticky/{conv_id} — external progress updates (#1104)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_sticky_pins_and_emits(authed_client, http_config, owned_conv, manager_mock):
    resp = await authed_client.post(
        f"/api/sticky/{owned_conv}",
        json={"widget_type": "markdown_document", "data": {"content": "# Live progress"}},
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

    from decafclaw import sticky as sticky_mod

    state = sticky_mod.read_sticky_state(http_config, owned_conv)
    assert state["widget_type"] == "markdown_document"
    assert state["data"] == {"content": "# Live progress"}

    emitted = [call.args[1] for call in manager_mock.emit.await_args_list]
    assert emitted == [
        {
            "type": "sticky_set",
            "widget_type": "markdown_document",
            "data": {"content": "# Live progress"},
        }
    ]


@pytest.mark.asyncio
async def test_post_sticky_requires_auth(unauthed_client, owned_conv):
    resp = await unauthed_client.post(
        f"/api/sticky/{owned_conv}", json={"widget_type": "markdown_document", "data": {}}
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_post_sticky_other_user_conv_404(authed_client, other_user_conv):
    resp = await authed_client.post(
        f"/api/sticky/{other_user_conv}",
        json={"widget_type": "markdown_document", "data": {"content": "x"}},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_post_sticky_invalid_conv_id_400(authed_client):
    resp = await authed_client.post("/api/sticky/bad!id", json={"widget_type": "markdown_document", "data": {}})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_post_sticky_missing_conversation_404(authed_client):
    resp = await authed_client.post(
        "/api/sticky/missing-conversation", json={"widget_type": "markdown_document", "data": {}}
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"[1, 2]",
        b"{}",
        b'{"widget_type": ""}',
        b'{"data": {"content": "x"}}',
        b'{"widget_type": "markdown_document"}',
        b'{"widget_type": "markdown_document", "data": "nope"}',
    ],
)
async def test_post_sticky_rejects_malformed_bodies(authed_client, owned_conv, body):
    resp = await authed_client.post(f"/api/sticky/{owned_conv}", content=body)
    assert resp.status_code == 400
    assert "error" in resp.json()


@pytest.mark.asyncio
async def test_post_sticky_rejects_unknown_widget(authed_client, owned_conv):
    resp = await authed_client.post(f"/api/sticky/{owned_conv}", json={"widget_type": "no_such_widget", "data": {}})
    assert resp.status_code == 400
    assert "not registered" in resp.json()["error"]


@pytest.mark.asyncio
async def test_post_sticky_rejects_schema_violation(authed_client, owned_conv):
    # markdown_document requires "content".
    resp = await authed_client.post(f"/api/sticky/{owned_conv}", json={"widget_type": "markdown_document", "data": {}})
    assert resp.status_code == 400
    assert "schema validation failed" in resp.json()["error"]


@pytest.mark.asyncio
async def test_post_sticky_does_not_leak_state_on_rejection(authed_client, http_config, owned_conv):
    from decafclaw import sticky as sticky_mod

    resp = await authed_client.post(f"/api/sticky/{owned_conv}", json={"widget_type": "no_such_widget", "data": {}})
    assert resp.status_code == 400
    assert sticky_mod.read_sticky_state(http_config, owned_conv) == sticky_mod.empty_sticky_state()


@pytest.mark.asyncio
async def test_delete_sticky_clears_and_emits(authed_client, http_config, owned_conv, manager_mock):
    from decafclaw import sticky as sticky_mod

    result = await sticky_mod.set_sticky(http_config, owned_conv, "markdown_document", {"content": "# hi"})
    assert result.ok
    resp = await authed_client.delete(f"/api/sticky/{owned_conv}")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert sticky_mod.read_sticky_state(http_config, owned_conv) == sticky_mod.empty_sticky_state()

    emitted = [call.args[1] for call in manager_mock.emit.await_args_list]
    assert emitted == [{"type": "sticky_clear"}]


@pytest.mark.asyncio
async def test_delete_sticky_requires_auth(unauthed_client, owned_conv):
    resp = await unauthed_client.delete(f"/api/sticky/{owned_conv}")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_delete_sticky_other_user_conv_404(authed_client, other_user_conv):
    resp = await authed_client.delete(f"/api/sticky/{other_user_conv}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_sticky_invalid_conv_id_400(authed_client):
    resp = await authed_client.delete("/api/sticky/bad!id")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_delete_sticky_missing_conversation_404(authed_client):
    resp = await authed_client.delete("/api/sticky/missing-conversation")
    assert resp.status_code == 404
