"""Tests for web gateway conversation management."""

import time

import pytest
from httpx import ASGITransport, AsyncClient

from decafclaw.archive import append_message
from decafclaw.events import EventBus
from decafclaw.http_server import create_app
from decafclaw.web.auth import create_token
from decafclaw.web.conversations import ConversationIndex

# -- ConversationIndex tests ---------------------------------------------------


def test_create_conversation(config):
    config.agent_path.mkdir(parents=True, exist_ok=True)
    index = ConversationIndex(config)
    conv = index.create("alice", title="Test chat")
    assert conv.conv_id.startswith("web-alice-")
    assert conv.title == "Test chat"
    assert conv.user_id == "alice"


def test_create_default_title(config):
    config.agent_path.mkdir(parents=True, exist_ok=True)
    index = ConversationIndex(config)
    conv = index.create("alice")
    assert conv.title == "New conversation"


def test_list_for_user(config):
    config.agent_path.mkdir(parents=True, exist_ok=True)
    index = ConversationIndex(config)
    index.create("alice", "Chat 1")
    index.create("bob", "Chat 2")
    index.create("alice", "Chat 3")

    alice_convs = index.list_for_user("alice")
    assert len(alice_convs) == 2
    assert all(c.user_id == "alice" for c in alice_convs)

    bob_convs = index.list_for_user("bob")
    assert len(bob_convs) == 1


def test_list_sorted_by_updated(config):
    config.agent_path.mkdir(parents=True, exist_ok=True)
    index = ConversationIndex(config)
    index.create("alice", "Old")
    time.sleep(0.01)
    c2 = index.create("alice", "New")

    convs = index.list_for_user("alice")
    assert convs[0].conv_id == c2.conv_id  # newest first


def test_get_conversation(config):
    config.agent_path.mkdir(parents=True, exist_ok=True)
    index = ConversationIndex(config)
    conv = index.create("alice", "Test")
    found = index.get(conv.conv_id)
    assert found is not None
    assert found.title == "Test"


def test_get_nonexistent(config):
    config.agent_path.mkdir(parents=True, exist_ok=True)
    index = ConversationIndex(config)
    assert index.get("nonexistent") is None


def test_rename_conversation(config):
    config.agent_path.mkdir(parents=True, exist_ok=True)
    index = ConversationIndex(config)
    conv = index.create("alice", "Old title")
    updated = index.rename(conv.conv_id, "New title")
    assert updated is not None
    assert updated.title == "New title"
    assert updated.updated_at > conv.updated_at


def test_touch_updates_timestamp(config):
    config.agent_path.mkdir(parents=True, exist_ok=True)
    index = ConversationIndex(config)
    conv = index.create("alice", "Test")
    original = conv.updated_at
    time.sleep(0.01)
    index.touch(conv.conv_id)
    updated = index.get(conv.conv_id)
    assert updated.updated_at > original


def test_load_empty_history(config):
    config.agent_path.mkdir(parents=True, exist_ok=True)
    index = ConversationIndex(config)
    conv = index.create("alice")
    messages, has_more = index.load_history(conv.conv_id)
    assert messages == []
    assert has_more is False


def test_load_history_with_messages(config):
    config.agent_path.mkdir(parents=True, exist_ok=True)
    config.workspace_path.mkdir(parents=True, exist_ok=True)
    index = ConversationIndex(config)
    conv = index.create("alice")

    # Add some messages to the archive
    for i in range(5):
        append_message(config, conv.conv_id, {"role": "user", "content": f"msg {i}"})

    messages, has_more = index.load_history(conv.conv_id, limit=50)
    assert len(messages) == 5
    assert has_more is False


def test_load_history_pagination(config):
    config.agent_path.mkdir(parents=True, exist_ok=True)
    config.workspace_path.mkdir(parents=True, exist_ok=True)
    index = ConversationIndex(config)
    conv = index.create("alice")

    for i in range(10):
        append_message(config, conv.conv_id, {"role": "user", "content": f"msg {i}"})

    messages, has_more = index.load_history(conv.conv_id, limit=3)
    assert len(messages) == 3
    assert has_more is True
    # Should be the last 3 messages
    assert messages[0]["content"] == "msg 7"
    assert messages[2]["content"] == "msg 9"


# -- Conversation REST route tests --------------------------------------------


@pytest.fixture
def http_config(config):
    config.http.enabled = True
    config.http.secret = "test-secret"
    config.http.host = "127.0.0.1"
    config.http.port = 18880
    config.http.base_url = ""
    config.agent_path.mkdir(parents=True, exist_ok=True)
    return config


@pytest.fixture
def bus():
    return EventBus()


@pytest.fixture
def app(http_config, bus):
    return create_app(http_config, bus)


@pytest.fixture
async def authed_client(app, http_config):
    """Client with a valid auth cookie."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        token = create_token(http_config, "testuser")
        resp = await client.post("/api/auth/login", json={"token": token})
        client.cookies = resp.cookies
        yield client


@pytest.fixture
async def unauthed_client(app):
    """Client without auth."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.mark.asyncio
async def test_create_conv_route(authed_client):
    resp = await authed_client.post(
        "/api/conversations", json={"title": "My chat"}
    )
    assert resp.status_code == 201
    assert resp.json()["title"] == "My chat"
    assert resp.json()["conv_id"].startswith("web-testuser-")


@pytest.mark.asyncio
async def test_list_convs_route(authed_client):
    await authed_client.post("/api/conversations", json={"title": "Chat 1"})
    await authed_client.post("/api/conversations", json={"title": "Chat 2"})

    resp = await authed_client.get("/api/conversations")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


@pytest.mark.asyncio
async def test_rename_conv_route(authed_client):
    create_resp = await authed_client.post(
        "/api/conversations", json={"title": "Old"}
    )
    conv_id = create_resp.json()["conv_id"]

    resp = await authed_client.patch(
        f"/api/conversations/{conv_id}", json={"title": "New"}
    )
    assert resp.status_code == 200
    assert resp.json()["title"] == "New"


@pytest.mark.asyncio
async def test_conv_routes_require_auth(unauthed_client):
    resp = await unauthed_client.get("/api/conversations")
    assert resp.status_code == 401

    resp = await unauthed_client.post("/api/conversations", json={})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_get_conv_route(authed_client):
    create_resp = await authed_client.post(
        "/api/conversations", json={"title": "Test"}
    )
    conv_id = create_resp.json()["conv_id"]

    resp = await authed_client.get(f"/api/conversations/{conv_id}")
    assert resp.status_code == 200
    assert resp.json()["title"] == "Test"


@pytest.mark.asyncio
async def test_history_route(authed_client, http_config):
    http_config.workspace_path.mkdir(parents=True, exist_ok=True)
    create_resp = await authed_client.post(
        "/api/conversations", json={"title": "Test"}
    )
    conv_id = create_resp.json()["conv_id"]

    # Add messages
    for i in range(3):
        append_message(http_config, conv_id, {"role": "user", "content": f"msg {i}"})

    resp = await authed_client.get(f"/api/conversations/{conv_id}/history")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["messages"]) == 3
    assert data["has_more"] is False
