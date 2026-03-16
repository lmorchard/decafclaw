"""Tests for HTTP server routes and button building."""

import asyncio
import json

import pytest
from httpx import ASGITransport, AsyncClient

from decafclaw.events import EventBus
from decafclaw.http_server import build_confirm_buttons, create_app


@pytest.fixture
def http_config(config):
    """Config with HTTP enabled."""
    config.http_enabled = True
    config.http_secret = "test-secret"
    config.http_host = "127.0.0.1"
    config.http_port = 18880
    config.http_base_url = ""
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


# -- Health route tests --------------------------------------------------------


@pytest.mark.asyncio
async def test_health(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# -- Confirm callback tests ----------------------------------------------------


def _confirm_body(action="approve", context_id="ctx-123", tool="shell",
                  original_message="test message", **extra_context):
    context = {
        "action": action,
        "context_id": context_id,
        "tool": tool,
        "original_message": original_message,
        **extra_context,
    }
    return {
        "user_id": "user-abc",
        "post_id": "post-def",
        "channel_id": "chan-ghi",
        "team_id": "team-jkl",
        "context": context,
    }


@pytest.mark.asyncio
async def test_confirm_rejects_bad_secret(client):
    resp = await client.post(
        "/actions/confirm?secret=wrong",
        json=_confirm_body(),
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_confirm_approve_publishes_event(client, bus):
    received = []
    bus.subscribe(lambda e: received.append(e))

    resp = await client.post(
        "/actions/confirm?secret=test-secret",
        json=_confirm_body(action="approve", context_id="ctx-1", tool="shell"),
    )
    assert resp.status_code == 200

    # Give the async subscriber a tick
    await asyncio.sleep(0)

    assert len(received) == 1
    assert received[0]["type"] == "tool_confirm_response"
    assert received[0]["context_id"] == "ctx-1"
    assert received[0]["tool"] == "shell"
    assert received[0]["approved"] is True


@pytest.mark.asyncio
async def test_confirm_deny_publishes_event(client, bus):
    received = []
    bus.subscribe(lambda e: received.append(e))

    resp = await client.post(
        "/actions/confirm?secret=test-secret",
        json=_confirm_body(action="deny"),
    )
    assert resp.status_code == 200
    await asyncio.sleep(0)

    assert received[0]["approved"] is False


@pytest.mark.asyncio
async def test_confirm_always_publishes_event(client, bus):
    received = []
    bus.subscribe(lambda e: received.append(e))

    resp = await client.post(
        "/actions/confirm?secret=test-secret",
        json=_confirm_body(action="always", tool="activate_skill"),
    )
    assert resp.status_code == 200
    await asyncio.sleep(0)

    assert received[0]["approved"] is True
    assert received[0].get("always") is True


@pytest.mark.asyncio
async def test_confirm_add_pattern_publishes_event(client, bus):
    received = []
    bus.subscribe(lambda e: received.append(e))

    resp = await client.post(
        "/actions/confirm?secret=test-secret",
        json=_confirm_body(action="add_pattern", tool="shell"),
    )
    assert resp.status_code == 200
    await asyncio.sleep(0)

    assert received[0]["approved"] is True
    assert received[0].get("add_pattern") is True


@pytest.mark.asyncio
async def test_confirm_response_updates_message(client):
    resp = await client.post(
        "/actions/confirm?secret=test-secret",
        json=_confirm_body(action="approve", original_message="original text"),
    )
    data = resp.json()
    assert "update" in data
    assert "original text" in data["update"]["message"]
    assert "Approved" in data["update"]["message"]
    assert data["update"]["props"]["attachments"] == []


# -- Button building tests -----------------------------------------------------


def test_buttons_empty_when_http_disabled(config):
    config.http_enabled = False
    result = build_confirm_buttons(
        config, "shell", "ls", "ls *", "ctx-1", "msg"
    )
    assert result == []


def test_buttons_have_callback_url(http_config):
    result = build_confirm_buttons(
        http_config, "shell", "ls", "ls *", "ctx-1", "msg"
    )
    assert len(result) == 1
    actions = result[0]["actions"]
    for action in actions:
        url = action["integration"]["url"]
        assert "test-secret" in url
        assert "/actions/confirm" in url


def test_shell_buttons_approve_deny_pattern(http_config):
    result = build_confirm_buttons(
        http_config, "shell", "ls -la", "ls *", "ctx-1", "msg"
    )
    actions = result[0]["actions"]
    action_ids = [a["id"] for a in actions]
    assert action_ids == ["approve", "deny", "add_pattern"]
    # No "always" for shell
    assert "always" not in action_ids


def test_other_tool_buttons_approve_deny_always(http_config):
    result = build_confirm_buttons(
        http_config, "activate_skill", "Activate: tabstack", "", "ctx-1", "msg"
    )
    actions = result[0]["actions"]
    action_ids = [a["id"] for a in actions]
    assert action_ids == ["approve", "deny", "always"]
    # No "add_pattern" for non-shell
    assert "add_pattern" not in action_ids


def test_buttons_context_includes_required_fields(http_config):
    result = build_confirm_buttons(
        http_config, "shell", "ls", "ls *", "ctx-abc", "original msg"
    )
    ctx = result[0]["actions"][0]["integration"]["context"]
    assert ctx["context_id"] == "ctx-abc"
    assert ctx["tool"] == "shell"
    assert ctx["original_message"] == "original msg"


def test_buttons_styles(http_config):
    result = build_confirm_buttons(
        http_config, "activate_skill", "cmd", "", "ctx-1", "msg"
    )
    actions = result[0]["actions"]
    styles = {a["id"]: a.get("style") for a in actions}
    assert styles["approve"] == "primary"
    assert styles["deny"] == "danger"


def test_buttons_truncate_long_original_message(http_config):
    long_msg = "x" * 5000
    result = build_confirm_buttons(
        http_config, "shell", "cmd", "cmd *", "ctx-1", long_msg
    )
    ctx = result[0]["actions"][0]["integration"]["context"]
    assert len(ctx["original_message"]) == 2000
