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
    data = resp.json()
    assert data["folder"] == ""
    assert len(data["conversations"]) == 2
    # Virtual folders at top level
    virtual = [f for f in data["folders"] if f.get("virtual")]
    assert len(virtual) == 2
    paths = {f["path"] for f in virtual}
    assert "_archived" in paths
    assert "_system" in paths


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


# -- Folder-aware listing tests -----------------------------------------------


@pytest.fixture
async def folder_index(http_config):
    """Create a folder index for testuser."""
    from decafclaw.web.conversation_folders import ConversationFolderIndex
    return ConversationFolderIndex(http_config, "testuser")


@pytest.mark.asyncio
async def test_list_convs_with_folder(authed_client, folder_index):
    """Conversations in a folder only appear when that folder is requested."""
    # Create folder and conversations
    await folder_index.create_folder("projects")
    r1 = await authed_client.post("/api/conversations", json={"title": "In folder"})
    await authed_client.post("/api/conversations", json={"title": "Top level"})
    conv_in_folder = r1.json()["conv_id"]
    await folder_index.set_folder(conv_in_folder, "projects")

    # Top level should only have "Top level"
    resp = await authed_client.get("/api/conversations")
    data = resp.json()
    titles = [c["title"] for c in data["conversations"]]
    assert "Top level" in titles
    assert "In folder" not in titles
    # Should have "projects" user folder + virtual folders
    folder_names = [f["name"] for f in data["folders"]]
    assert "projects" in folder_names

    # Folder listing should have "In folder"
    resp = await authed_client.get("/api/conversations?folder=projects")
    data = resp.json()
    assert data["folder"] == "projects"
    titles = [c["title"] for c in data["conversations"]]
    assert "In folder" in titles
    assert "Top level" not in titles


@pytest.mark.asyncio
async def test_list_archived_convs(authed_client, http_config):
    """Archived conversations appear in /archived endpoint."""
    r = await authed_client.post("/api/conversations", json={"title": "To archive"})
    conv_id = r.json()["conv_id"]
    await authed_client.post(f"/api/conversations/{conv_id}/archive")

    resp = await authed_client.get("/api/conversations/archived")
    assert resp.status_code == 200
    data = resp.json()
    titles = [c["title"] for c in data["conversations"]]
    assert "To archive" in titles

    # Should not appear in active list
    resp = await authed_client.get("/api/conversations")
    active_titles = [c["title"] for c in resp.json()["conversations"]]
    assert "To archive" not in active_titles


@pytest.mark.asyncio
async def test_list_archived_with_folder(authed_client, folder_index, http_config):
    """Archived conversations preserve folder assignment."""
    await folder_index.create_folder("projects")
    r = await authed_client.post("/api/conversations", json={"title": "Archived in folder"})
    conv_id = r.json()["conv_id"]
    await folder_index.set_folder(conv_id, "projects")
    await authed_client.post(f"/api/conversations/{conv_id}/archive")

    # Top-level archived should not have it
    resp = await authed_client.get("/api/conversations/archived")
    data = resp.json()
    top_titles = [c["title"] for c in data["conversations"]]
    assert "Archived in folder" not in top_titles
    # But should show "projects" as a child folder
    folder_names = [f["name"] for f in data["folders"]]
    assert "projects" in folder_names

    # Folder-level archived should have it
    resp = await authed_client.get("/api/conversations/archived?folder=projects")
    data = resp.json()
    titles = [c["title"] for c in data["conversations"]]
    assert "Archived in folder" in titles


@pytest.mark.asyncio
async def test_list_system_convs(authed_client, http_config):
    """System conversations appear in /system endpoint with type sub-folders."""
    # Create some system conversation archive files
    conv_dir = http_config.workspace_path / "conversations"
    conv_dir.mkdir(parents=True, exist_ok=True)
    (conv_dir / "heartbeat-20260401-100000-0.jsonl").write_text("{}\n")
    (conv_dir / "schedule-daily-20260401-090000.jsonl").write_text("{}\n")

    # Top level should show sub-folder types, no conversations
    resp = await authed_client.get("/api/conversations/system")
    assert resp.status_code == 200
    data = resp.json()
    assert data["conversations"] == []
    folder_names = [f["name"] for f in data["folders"]]
    assert "Heartbeat" in folder_names
    assert "Schedule" in folder_names
    assert "Delegated" in folder_names

    # Filter by heartbeat
    resp = await authed_client.get("/api/conversations/system?folder=heartbeat")
    data = resp.json()
    assert len(data["conversations"]) == 1
    assert data["conversations"][0]["conv_type"] == "heartbeat"

    # Filter by schedule
    resp = await authed_client.get("/api/conversations/system?folder=schedule")
    data = resp.json()
    assert len(data["conversations"]) == 1
    assert data["conversations"][0]["conv_type"] == "schedule"


@pytest.mark.asyncio
async def test_list_convs_invalid_folder(authed_client):
    """Path traversal in folder param is rejected."""
    resp = await authed_client.get("/api/conversations?folder=../escape")
    assert resp.status_code == 400

    resp = await authed_client.get("/api/conversations?folder=/absolute")
    assert resp.status_code == 400

    resp = await authed_client.get("/api/conversations?folder=a//b")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_folder_with_dots_in_name(authed_client, folder_index):
    """Folder names containing dots (not as path segments) should be allowed."""
    await folder_index.create_folder("foo..bar")
    resp = await authed_client.get("/api/conversations?folder=foo..bar")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_archived_nested_folder_ancestors(authed_client, folder_index, http_config):
    """Archived conversations in nested folders should expose ancestor folders."""
    await folder_index.create_folder("projects/bot-redesign")
    r = await authed_client.post("/api/conversations", json={"title": "Deep"})
    conv_id = r.json()["conv_id"]
    await folder_index.set_folder(conv_id, "projects/bot-redesign")
    await authed_client.post(f"/api/conversations/{conv_id}/archive")

    # Top-level archived should show "projects" as a child folder
    resp = await authed_client.get("/api/conversations/archived")
    data = resp.json()
    folder_names = [f["name"] for f in data["folders"]]
    assert "projects" in folder_names

    # Navigate into "projects" should show "bot-redesign"
    resp = await authed_client.get("/api/conversations/archived?folder=projects")
    data = resp.json()
    folder_names = [f["name"] for f in data["folders"]]
    assert "bot-redesign" in folder_names


@pytest.mark.asyncio
async def test_list_system_invalid_folder(authed_client):
    resp = await authed_client.get("/api/conversations/system?folder=invalid")
    assert resp.status_code == 400


# -- Action endpoint tests ----------------------------------------------------


@pytest.mark.asyncio
async def test_unarchive_conv(authed_client):
    r = await authed_client.post("/api/conversations", json={"title": "Test"})
    conv_id = r.json()["conv_id"]
    await authed_client.post(f"/api/conversations/{conv_id}/archive")

    resp = await authed_client.post(f"/api/conversations/{conv_id}/unarchive")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    # Should be back in active list
    resp = await authed_client.get("/api/conversations")
    titles = [c["title"] for c in resp.json()["conversations"]]
    assert "Test" in titles


@pytest.mark.asyncio
async def test_rename_and_move_conv(authed_client, folder_index):
    await folder_index.create_folder("target")
    r = await authed_client.post("/api/conversations", json={"title": "Original"})
    conv_id = r.json()["conv_id"]

    resp = await authed_client.patch(
        f"/api/conversations/{conv_id}",
        json={"title": "Renamed", "folder": "target"},
    )
    assert resp.status_code == 200
    assert resp.json()["title"] == "Renamed"
    assert resp.json()["folder"] == "target"

    # Should appear in target folder
    resp = await authed_client.get("/api/conversations?folder=target")
    titles = [c["title"] for c in resp.json()["conversations"]]
    assert "Renamed" in titles


@pytest.mark.asyncio
async def test_move_conv_to_top_level(authed_client, folder_index):
    await folder_index.create_folder("source")
    r = await authed_client.post("/api/conversations", json={"title": "Movable"})
    conv_id = r.json()["conv_id"]
    await folder_index.set_folder(conv_id, "source")

    resp = await authed_client.patch(
        f"/api/conversations/{conv_id}", json={"folder": ""}
    )
    assert resp.status_code == 200

    # Should be at top level now
    resp = await authed_client.get("/api/conversations")
    titles = [c["title"] for c in resp.json()["conversations"]]
    assert "Movable" in titles


@pytest.mark.asyncio
async def test_create_conv_in_folder(authed_client, folder_index):
    await folder_index.create_folder("projects")
    r = await authed_client.post(
        "/api/conversations", json={"title": "In folder", "folder": "projects"}
    )
    assert r.status_code == 201
    assert r.json()["folder"] == "projects"

    resp = await authed_client.get("/api/conversations?folder=projects")
    titles = [c["title"] for c in resp.json()["conversations"]]
    assert "In folder" in titles


@pytest.mark.asyncio
async def test_create_conv_with_model(authed_client, http_config):
    from decafclaw.config_types import ModelConfig, ProviderConfig
    # Add model configs to the test config
    http_config.providers = {"vertex": ProviderConfig(type="vertex", project="test")}
    http_config.model_configs = {"gemini-pro": ModelConfig(provider="vertex", model="gemini-2.5-pro")}
    http_config.workspace_path.mkdir(parents=True, exist_ok=True)
    r = await authed_client.post(
        "/api/conversations", json={"title": "Pro", "model": "gemini-pro"}
    )
    assert r.status_code == 201
    assert r.json()["model"] == "gemini-pro"


# -- Folder CRUD endpoint tests -----------------------------------------------


@pytest.mark.asyncio
async def test_create_conv_folder_route(authed_client):
    resp = await authed_client.post(
        "/api/conversations/folders", json={"path": "projects"}
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    # Should appear in listing
    resp = await authed_client.get("/api/conversations")
    folder_names = [f["name"] for f in resp.json()["folders"] if not f.get("virtual")]
    assert "projects" in folder_names


@pytest.mark.asyncio
async def test_delete_conv_folder_route(authed_client):
    await authed_client.post("/api/conversations/folders", json={"path": "empty"})
    resp = await authed_client.delete("/api/conversations/folders/empty")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


@pytest.mark.asyncio
async def test_delete_nonempty_conv_folder(authed_client, folder_index):
    await folder_index.create_folder("notempty")
    r = await authed_client.post("/api/conversations", json={"title": "Blocking"})
    await folder_index.set_folder(r.json()["conv_id"], "notempty")

    resp = await authed_client.delete("/api/conversations/folders/notempty")
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_rename_conv_folder_route(authed_client, folder_index):
    await folder_index.create_folder("old-name")
    await folder_index.set_folder("dummy-conv", "old-name")

    resp = await authed_client.put(
        "/api/conversations/folders/old-name", json={"path": "new-name"}
    )
    assert resp.status_code == 200

    # Check that the folder was renamed
    resp = await authed_client.get("/api/conversations")
    folder_names = [f["name"] for f in resp.json()["folders"] if not f.get("virtual")]
    assert "new-name" in folder_names
    assert "old-name" not in folder_names


@pytest.mark.asyncio
async def test_create_conv_folder_reserved_prefix(authed_client):
    resp = await authed_client.post(
        "/api/conversations/folders", json={"path": "_reserved"}
    )
    assert resp.status_code == 400


# -- Delete conversation tests ------------------------------------------------


def test_delete_from_index(config):
    config.agent_path.mkdir(parents=True, exist_ok=True)
    index = ConversationIndex(config)
    conv = index.create("alice", "Deletable")
    assert index.get(conv.conv_id) is not None
    assert index.delete(conv.conv_id) is True
    assert index.get(conv.conv_id) is None


def test_delete_nonexistent_from_index(config):
    config.agent_path.mkdir(parents=True, exist_ok=True)
    index = ConversationIndex(config)
    assert index.delete("nonexistent") is False


@pytest.mark.asyncio
async def test_delete_conv_route(authed_client, http_config):
    http_config.workspace_path.mkdir(parents=True, exist_ok=True)
    r = await authed_client.post("/api/conversations", json={"title": "To delete"})
    conv_id = r.json()["conv_id"]

    # Add some archive content so we can verify file cleanup
    append_message(http_config, conv_id, {"role": "user", "content": "hello"})
    conv_dir = http_config.workspace_path / "conversations"
    (conv_dir / f"{conv_id}.compacted.jsonl").write_text("{}\n")
    (conv_dir / f"{conv_id}.context.json").write_text("{}\n")

    resp = await authed_client.delete(f"/api/conversations/{conv_id}")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    # Should not appear in any listing
    resp = await authed_client.get("/api/conversations")
    ids = [c["conv_id"] for c in resp.json()["conversations"]]
    assert conv_id not in ids

    # Files should be gone
    assert not (conv_dir / f"{conv_id}.jsonl").exists()
    assert not (conv_dir / f"{conv_id}.compacted.jsonl").exists()
    assert not (conv_dir / f"{conv_id}.context.json").exists()


@pytest.mark.asyncio
async def test_delete_conv_not_found(authed_client):
    resp = await authed_client.delete("/api/conversations/nonexistent")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_conv_wrong_user(authed_client, http_config):
    """Cannot delete another user's conversation."""
    http_config.agent_path.mkdir(parents=True, exist_ok=True)
    index = ConversationIndex(http_config)
    conv = index.create("otheruser", "Not yours")

    resp = await authed_client.delete(f"/api/conversations/{conv.conv_id}")
    assert resp.status_code == 404

    # Should still exist
    assert index.get(conv.conv_id) is not None
