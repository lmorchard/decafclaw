"""Tests for HTTP server routes and button building."""

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from decafclaw.events import EventBus
from decafclaw.http_server import create_app
from decafclaw.mattermost_ui import build_confirm_buttons, get_token_registry


@pytest.fixture
def http_config(config):
    """Config with HTTP enabled."""
    config.http.enabled = True
    config.http.secret = "test-secret"
    config.http.host = "127.0.0.1"
    config.http.port = 18880
    config.http.base_url = ""
    return config


@pytest.fixture
def bus():
    return EventBus()


@pytest.fixture
def app(http_config, bus):
    return create_app(http_config, bus)


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture(autouse=True)
def clean_token_registry():
    """Ensure token registry is clean between tests."""
    registry = get_token_registry()
    registry._tokens.clear()
    yield
    registry._tokens.clear()


# -- Health route tests --------------------------------------------------------


@pytest.mark.asyncio
async def test_health(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# -- Token registry tests -----------------------------------------------------


def test_token_create_and_consume():
    registry = get_token_registry()
    token = registry.create("ctx-1", "shell", "test msg")
    assert len(token) > 16  # urlsafe base64, should be ~32 chars

    data = registry.consume(token)
    assert data is not None
    assert data["context_id"] == "ctx-1"
    assert data["tool"] == "shell"
    assert data["original_message"] == "test msg"


def test_token_single_use():
    registry = get_token_registry()
    token = registry.create("ctx-1", "shell", "msg")
    registry.consume(token)
    # Second consume returns None
    assert registry.consume(token) is None


def test_token_invalid():
    registry = get_token_registry()
    assert registry.consume("nonexistent-token") is None


# -- Confirm callback tests ----------------------------------------------------


def _confirm_body(action="approve", **extra_context):
    context = {"action": action, **extra_context}
    return {
        "user_id": "user-abc",
        "post_id": "post-def",
        "channel_id": "chan-ghi",
        "team_id": "team-jkl",
        "context": context,
    }


@pytest.mark.asyncio
async def test_confirm_rejects_no_auth(client):
    resp = await client.post(
        "/actions/confirm",
        json=_confirm_body(),
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_confirm_rejects_bad_token(client):
    resp = await client.post(
        "/actions/confirm?token=fake-token",
        json=_confirm_body(),
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_confirm_with_valid_token(client, bus):
    received = []
    bus.subscribe(lambda e: received.append(e))

    token = get_token_registry().create("ctx-1", "shell", "original msg")
    resp = await client.post(
        f"/actions/confirm?token={token}",
        json=_confirm_body(action="approve"),
    )
    assert resp.status_code == 200
    await asyncio.sleep(0)

    assert len(received) == 1
    assert received[0]["context_id"] == "ctx-1"
    assert received[0]["tool"] == "shell"
    assert received[0]["approved"] is True


@pytest.mark.asyncio
async def test_confirm_token_is_single_use(client, bus):
    token = get_token_registry().create("ctx-1", "shell", "msg")

    resp1 = await client.post(
        f"/actions/confirm?token={token}",
        json=_confirm_body(action="approve"),
    )
    assert resp1.status_code == 200

    # Second use of same token fails
    resp2 = await client.post(
        f"/actions/confirm?token={token}",
        json=_confirm_body(action="approve"),
    )
    assert resp2.status_code == 403


@pytest.mark.asyncio
async def test_confirm_with_static_secret_fallback(client, bus):
    """Static secret still works as fallback."""
    received = []
    bus.subscribe(lambda e: received.append(e))

    resp = await client.post(
        "/actions/confirm?secret=test-secret",
        json=_confirm_body(action="approve", context_id="ctx-2", tool="shell"),
    )
    assert resp.status_code == 200
    await asyncio.sleep(0)

    assert received[0]["context_id"] == "ctx-2"


@pytest.mark.asyncio
async def test_confirm_deny(client, bus):
    received = []
    bus.subscribe(lambda e: received.append(e))

    token = get_token_registry().create("ctx-1", "shell", "msg")
    resp = await client.post(
        f"/actions/confirm?token={token}",
        json=_confirm_body(action="deny"),
    )
    assert resp.status_code == 200
    await asyncio.sleep(0)

    assert received[0]["approved"] is False


@pytest.mark.asyncio
async def test_confirm_always(client, bus):
    received = []
    bus.subscribe(lambda e: received.append(e))

    token = get_token_registry().create("ctx-1", "activate_skill", "msg")
    resp = await client.post(
        f"/actions/confirm?token={token}",
        json=_confirm_body(action="always"),
    )
    assert resp.status_code == 200
    await asyncio.sleep(0)

    assert received[0]["approved"] is True
    assert received[0].get("always") is True


@pytest.mark.asyncio
async def test_confirm_add_pattern(client, bus):
    received = []
    bus.subscribe(lambda e: received.append(e))

    token = get_token_registry().create("ctx-1", "shell", "msg")
    resp = await client.post(
        f"/actions/confirm?token={token}",
        json=_confirm_body(action="add_pattern"),
    )
    assert resp.status_code == 200
    await asyncio.sleep(0)

    assert received[0]["approved"] is True
    assert received[0].get("add_pattern") is True


@pytest.mark.asyncio
async def test_confirm_response_includes_original_message(client):
    token = get_token_registry().create("ctx-1", "shell", "original text here")
    resp = await client.post(
        f"/actions/confirm?token={token}",
        json=_confirm_body(action="approve"),
    )
    data = resp.json()
    assert "original text here" in data["update"]["message"]
    assert "Approved" in data["update"]["message"]
    assert data["update"]["props"]["attachments"] == []


# -- Button building tests -----------------------------------------------------


def test_buttons_empty_when_http_disabled(config):
    config.http.enabled = False
    result = build_confirm_buttons(
        config, "shell", "ls", "ls *", "ctx-1", "msg"
    )
    assert result == []


def test_buttons_have_token_in_callback_url(http_config):
    result = build_confirm_buttons(
        http_config, "shell", "ls", "ls *", "ctx-1", "msg"
    )
    assert len(result) == 1
    actions = result[0]["actions"]
    for action in actions:
        url = action["integration"]["url"]
        assert "token=" in url
        assert "/actions/confirm" in url


def test_shell_buttons_approve_deny_pattern(http_config):
    result = build_confirm_buttons(
        http_config, "shell", "ls -la", "ls *", "ctx-1", "msg"
    )
    actions = result[0]["actions"]
    action_ids = [a["id"] for a in actions]
    assert action_ids == ["approve", "deny", "allowpattern"]
    assert "always" not in action_ids


def test_other_tool_buttons_approve_deny_always(http_config):
    result = build_confirm_buttons(
        http_config, "activate_skill", "Activate: tabstack", "", "ctx-1", "msg"
    )
    actions = result[0]["actions"]
    action_ids = [a["id"] for a in actions]
    assert action_ids == ["approve", "deny", "always"]
    assert "add_pattern" not in action_ids


def test_buttons_context_includes_required_fields(http_config):
    result = build_confirm_buttons(
        http_config, "shell", "ls", "ls *", "ctx-abc", "original msg"
    )
    ctx = result[0]["actions"][0]["integration"]["context"]
    assert ctx["context_id"] == "ctx-abc"
    assert ctx["tool"] == "shell"


def test_buttons_styles(http_config):
    result = build_confirm_buttons(
        http_config, "activate_skill", "cmd", "", "ctx-1", "msg"
    )
    actions = result[0]["actions"]
    styles = {a["id"]: a.get("style") for a in actions}
    assert styles["approve"] == "primary"
    assert styles["deny"] == "danger"


def test_buttons_create_tokens_in_registry(http_config):
    registry = get_token_registry()
    before = len(registry)
    build_confirm_buttons(http_config, "shell", "ls", "ls *", "ctx-1", "msg")
    # One token per button (3 for shell: approve, deny, add_pattern)
    assert len(registry) == before + 3
