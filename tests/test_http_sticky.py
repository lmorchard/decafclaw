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
        http_config, owned_conv, "markdown_document", {"content": "# Doc"},
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
    assert resp.status_code in (401, 302, 403)


@pytest.mark.asyncio
async def test_get_sticky_state_other_user_conv_404(authed_client, other_user_conv):
    """Accessing another user's sticky state must return 404, not the actual state."""
    resp = await authed_client.get(f"/api/sticky/{other_user_conv}")
    assert resp.status_code == 404
