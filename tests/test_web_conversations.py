"""Tests for web gateway conversation management."""

import os
import time
from unittest.mock import AsyncMock
from urllib.parse import quote

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from decafclaw.archive import append_message
from decafclaw.events import EventBus
from decafclaw.http_server import create_app
from decafclaw.web.auth import create_token
from decafclaw.web.conversations import ConversationIndex, ConversationMeta

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
async def test_upload_multipart_preserves_consumed_attachment_fields(
    authed_client, http_config,
):
    conv = ConversationIndex(http_config).create("testuser", "Upload target")
    response = await authed_client.post(
        f"/api/upload/{quote(conv.conv_id, safe='')}",
        files={"file": ("report 日本語.txt", b"upload bytes", "text/plain")},
    )

    assert response.status_code == 201
    attachment = response.json()
    assert set(attachment) == {"filename", "path", "mime_type"}
    assert attachment["filename"].startswith("report 日本語-")
    assert attachment["filename"].endswith(".txt")
    assert attachment["mime_type"] == "text/plain"
    assert attachment["path"].endswith("/uploads/" + attachment["filename"])
    assert (http_config.workspace_path / attachment["path"]).read_bytes() == b"upload bytes"


@pytest.mark.asyncio
async def test_upload_rejects_missing_file(authed_client, http_config):
    conv = ConversationIndex(http_config).create("testuser", "Upload target")
    response = await authed_client.post(
        f"/api/upload/{quote(conv.conv_id, safe='')}", data={"other": "value"},
    )
    assert response.status_code == 400
    assert response.json() == {"error": "no file in request"}


@pytest.mark.asyncio
async def test_upload_rejects_file_over_size_limit(authed_client, http_config):
    conv = ConversationIndex(http_config).create("testuser", "Upload target")
    http_config.http.max_upload_bytes = 8
    response = await authed_client.post(
        f"/api/upload/{quote(conv.conv_id, safe='')}",
        files={"file": ("large.bin", b"too many bytes", "application/octet-stream")},
    )
    assert response.status_code == 413
    assert response.json() == {"error": "file too large"}


@pytest.mark.asyncio
async def test_upload_hides_conversation_owned_by_another_user(
    authed_client, http_config,
):
    conv = ConversationIndex(http_config).create("another-user", "Private")
    response = await authed_client.post(
        f"/api/upload/{quote(conv.conv_id, safe='')}",
        files={"file": ("secret.txt", b"secret", "text/plain")},
    )
    assert response.status_code == 404
    assert response.json() == {"error": "not found"}


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
async def test_delete_conv_route_kills_terminal_sessions(authed_client, app, monkeypatch):
    """Deleting a conversation kills its live terminal sessions (and only
    its own — another conversation's sessions must survive)."""
    from decafclaw.terminals import TerminalSession

    create_resp = await authed_client.post(
        "/api/conversations", json={"title": "To delete"}
    )
    conv_id = create_resp.json()["conv_id"]

    registry = app.state.terminal_registry
    killed = []

    async def fake_kill(session, grace=1.0):
        killed.append((session.conv_id, session.tab_id))

    monkeypatch.setattr(registry, "kill", fake_kill)
    target_session = TerminalSession(
        conv_id=conv_id, tab_id="canvas_1", session_id="s1",
        cwd="/tmp", shell="/bin/sh", pid=123, fd=9,
    )
    other_session = TerminalSession(
        conv_id="other-conv", tab_id="canvas_1", session_id="s2",
        cwd="/tmp", shell="/bin/sh", pid=456, fd=10,
    )
    registry._sessions[(conv_id, "canvas_1")] = target_session
    registry._sessions[("other-conv", "canvas_1")] = other_session

    resp = await authed_client.delete(f"/api/conversations/{conv_id}")
    assert resp.status_code == 200
    assert killed == [(conv_id, "canvas_1")]
    assert list(registry._sessions.keys()) == [("other-conv", "canvas_1")]


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


# -- Export endpoint tests ----------------------------------------------------


@pytest.mark.asyncio
async def test_export_jsonl_returns_raw_archive(authed_client, http_config):
    http_config.workspace_path.mkdir(parents=True, exist_ok=True)
    create_resp = await authed_client.post(
        "/api/conversations", json={"title": "Export test"}
    )
    conv_id = create_resp.json()["conv_id"]
    append_message(http_config, conv_id, {"role": "user", "content": "hello"})
    append_message(http_config, conv_id, {"role": "assistant", "content": "hi"})

    resp = await authed_client.get(
        f"/api/conversations/{conv_id}/export?format=jsonl"
    )
    assert resp.status_code == 200
    assert "application/x-ndjson" in resp.headers["content-type"]
    # Body should match the archive file byte-for-byte.
    from decafclaw.archive import archive_path
    expected = archive_path(http_config, conv_id).read_bytes()
    assert resp.content == expected


@pytest.mark.asyncio
async def test_export_markdown_renders(authed_client, http_config):
    http_config.workspace_path.mkdir(parents=True, exist_ok=True)
    create_resp = await authed_client.post(
        "/api/conversations", json={"title": "Markdown test"}
    )
    conv_id = create_resp.json()["conv_id"]
    append_message(http_config, conv_id, {"role": "user", "content": "ping"})
    append_message(http_config, conv_id, {"role": "assistant", "content": "pong"})

    resp = await authed_client.get(
        f"/api/conversations/{conv_id}/export?format=markdown"
    )
    assert resp.status_code == 200
    assert "text/markdown" in resp.headers["content-type"]
    body = resp.text
    assert body.startswith(f"# Conversation {conv_id}")
    assert "## User\n\nping" in body
    assert "## Assistant\n\npong" in body


@pytest.mark.asyncio
async def test_export_missing_format_is_400(authed_client, http_config):
    http_config.workspace_path.mkdir(parents=True, exist_ok=True)
    create_resp = await authed_client.post(
        "/api/conversations", json={"title": "x"}
    )
    conv_id = create_resp.json()["conv_id"]
    resp = await authed_client.get(f"/api/conversations/{conv_id}/export")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_export_unknown_format_is_400(authed_client, http_config):
    http_config.workspace_path.mkdir(parents=True, exist_ok=True)
    create_resp = await authed_client.post(
        "/api/conversations", json={"title": "x"}
    )
    conv_id = create_resp.json()["conv_id"]
    resp = await authed_client.get(
        f"/api/conversations/{conv_id}/export?format=html"
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_export_unknown_conv_is_404(authed_client):
    resp = await authed_client.get(
        "/api/conversations/web-testuser-doesnotexist/export?format=jsonl"
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_export_cross_user_is_404(app, http_config):
    """Another user can't read a conversation they don't own."""
    http_config.workspace_path.mkdir(parents=True, exist_ok=True)
    # Create a conv as alice, then try to fetch it as bob.
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as alice:
        token = create_token(http_config, "alice")
        resp = await alice.post("/api/auth/login", json={"token": token})
        alice.cookies = resp.cookies
        create_resp = await alice.post(
            "/api/conversations", json={"title": "alice's"}
        )
        conv_id = create_resp.json()["conv_id"]
        append_message(http_config, conv_id, {"role": "user", "content": "secret"})

    async with AsyncClient(transport=transport, base_url="http://test") as bob:
        token = create_token(http_config, "bob")
        resp = await bob.post("/api/auth/login", json={"token": token})
        bob.cookies = resp.cookies
        resp = await bob.get(
            f"/api/conversations/{conv_id}/export?format=jsonl"
        )
        assert resp.status_code == 404


@pytest.mark.asyncio
async def test_export_no_archive_file_is_404(authed_client):
    """Conv exists in the index but no archive file written yet -> 404."""
    create_resp = await authed_client.post(
        "/api/conversations", json={"title": "Empty"}
    )
    conv_id = create_resp.json()["conv_id"]
    resp = await authed_client.get(
        f"/api/conversations/{conv_id}/export?format=jsonl"
    )
    assert resp.status_code == 404
    resp = await authed_client.get(
        f"/api/conversations/{conv_id}/export?format=markdown"
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_export_system_conv_with_archive(authed_client, http_config):
    """System conversations (schedule/heartbeat) live on disk without a
    ConversationIndex entry. Export should still work — read access is
    granted by archive-on-disk for non-`web-` conv IDs, mirroring the
    behavior of the WS load_history handler."""
    conv_dir = http_config.workspace_path / "conversations"
    conv_id = "schedule-mastodon-ingest-20260520-053002"
    (conv_dir / conv_id).mkdir(parents=True, exist_ok=True)
    archive = conv_dir / conv_id / "archive.jsonl"
    archive.write_text(
        '{"role":"user","content":"run the ingest","timestamp":"2026-05-20T05:30:02Z"}\n'
        '{"role":"assistant","content":"done","timestamp":"2026-05-20T05:30:10Z"}\n'
    )

    resp = await authed_client.get(
        f"/api/conversations/{conv_id}/export?format=jsonl"
    )
    assert resp.status_code == 200
    assert resp.content == archive.read_bytes()

    resp = await authed_client.get(
        f"/api/conversations/{conv_id}/export?format=markdown"
    )
    assert resp.status_code == 200
    assert resp.text.startswith(f"# Conversation {conv_id}")
    assert "run the ingest" in resp.text


@pytest.mark.asyncio
async def test_history_system_conv_with_archive(authed_client, http_config):
    """REST /history should also serve system conversations (same access
    model as WS load_history and /export)."""
    conv_dir = http_config.workspace_path / "conversations"
    conv_id = "heartbeat-20260520-053000-0"
    (conv_dir / conv_id).mkdir(parents=True, exist_ok=True)
    (conv_dir / conv_id / "archive.jsonl").write_text(
        '{"role":"user","content":"tick"}\n'
    )

    resp = await authed_client.get(f"/api/conversations/{conv_id}/history")
    assert resp.status_code == 200
    assert len(resp.json()["messages"]) == 1


@pytest.mark.asyncio
async def test_context_diagnostics_system_conv(authed_client, http_config):
    """REST /context should also serve system conversations."""
    conv_dir = http_config.workspace_path / "conversations"
    conv_id = "schedule-newsletter-20260520-080000"
    (conv_dir / conv_id).mkdir(parents=True, exist_ok=True)
    (conv_dir / conv_id / "archive.jsonl").write_text("{}\n")
    # /context reads a sidecar; write a minimal one so the endpoint returns 200.
    import json
    (conv_dir / conv_id / "context.json").write_text(
        json.dumps({"messages": [], "tools": [], "diagnostics": {}})
    )

    resp = await authed_client.get(f"/api/conversations/{conv_id}/context")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_export_other_user_web_conv_is_404(app, http_config):
    """A `web-` conv owned by someone else must remain inaccessible even
    if the archive happens to exist on disk — the system-conv fallback
    must not leak cross-user web conversations."""
    http_config.workspace_path.mkdir(parents=True, exist_ok=True)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as alice:
        token = create_token(http_config, "alice")
        resp = await alice.post("/api/auth/login", json={"token": token})
        alice.cookies = resp.cookies
        create_resp = await alice.post(
            "/api/conversations", json={"title": "alice's"}
        )
        conv_id = create_resp.json()["conv_id"]
        append_message(http_config, conv_id, {"role": "user", "content": "secret"})

    async with AsyncClient(transport=transport, base_url="http://test") as bob:
        token = create_token(http_config, "bob")
        resp = await bob.post("/api/auth/login", json={"token": token})
        bob.cookies = resp.cookies
        # Archive exists on disk, but the conv is owned by alice — bob
        # must not get read access via the system-conv fallback.
        resp = await bob.get(
            f"/api/conversations/{conv_id}/export?format=jsonl"
        )
        assert resp.status_code == 404


@pytest.mark.asyncio
async def test_export_empty_archive_file_renders_header(authed_client, http_config):
    """Archive file exists but is empty (e.g. corrupt or never appended-to):
    JSONL returns the empty bytes, markdown returns the header-only doc.
    Either way it's a 200, not a 404 — the conversation exists."""
    http_config.workspace_path.mkdir(parents=True, exist_ok=True)
    create_resp = await authed_client.post(
        "/api/conversations", json={"title": "Empty file"}
    )
    conv_id = create_resp.json()["conv_id"]
    # Touch the archive file so it exists but is empty.
    from decafclaw.archive import archive_path
    path = archive_path(http_config, conv_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()

    resp = await authed_client.get(
        f"/api/conversations/{conv_id}/export?format=jsonl"
    )
    assert resp.status_code == 200
    assert resp.content == b""

    resp = await authed_client.get(
        f"/api/conversations/{conv_id}/export?format=markdown"
    )
    assert resp.status_code == 200
    assert resp.text.startswith(f"# Conversation {conv_id}")


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
    (conv_dir / "heartbeat-20260401-100000-0").mkdir(parents=True, exist_ok=True)
    (conv_dir / "heartbeat-20260401-100000-0" / "archive.jsonl").write_text("{}\n")
    (conv_dir / "schedule-daily-20260401-090000").mkdir(parents=True, exist_ok=True)
    (conv_dir / "schedule-daily-20260401-090000" / "archive.jsonl").write_text("{}\n")

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
    (conv_dir / conv_id / "compacted.jsonl").write_text("{}\n")
    (conv_dir / conv_id / "context.json").write_text("{}\n")

    resp = await authed_client.delete(f"/api/conversations/{conv_id}")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    # Should not appear in any listing
    resp = await authed_client.get("/api/conversations")
    ids = [c["conv_id"] for c in resp.json()["conversations"]]
    assert conv_id not in ids

    # Files should be gone
    assert not (conv_dir / conv_id).exists()


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


@pytest.mark.parametrize("route", ["/api/conversations", "/api/conversations/archived", "/api/conversations/system"])
async def test_listing_auth_and_root_contract(route, authed_client, unauthed_client):
    assert (await unauthed_client.get(route)).status_code == 401
    omitted = await authed_client.get(route)
    assert omitted.status_code == 200
    for folder in ["", "   "]:
        response = await authed_client.get(route, params={"folder": folder})
        assert response.status_code == 200
        assert response.json() == omitted.json()
    assert set(omitted.json()) == {"folder", "folders", "conversations"}
    if route == "/api/conversations":
        assert omitted.json()["folders"] == [
            {"name": "Archived", "path": "_archived", "virtual": True},
            {"name": "System", "path": "_system", "virtual": True},
        ]


@pytest.mark.parametrize("archived", [False, True])
async def test_listing_special_folder_filter_order_and_shape(archived, authed_client, folder_index, http_config):
    folder = "Work space/日本語 & plus+ #hash"
    await folder_index.create_folder(folder)
    index = ConversationIndex(http_config)
    first = index.create("testuser", "Older")
    second = index.create("testuser", "Newer")
    other = index.create("someone-else", "Private")
    for conv in [first, second, other]:
        await folder_index.set_folder(conv.conv_id, folder)
        if archived:
            index.archive(conv.conv_id)
    route = "/api/conversations" + ("/archived" if archived else "")
    response = await authed_client.get(route, params={"folder": f"  {folder}  "})
    assert response.status_code == 200
    body = response.json()
    assert body["folder"] == folder
    assert [c["conv_id"] for c in body["conversations"]] == [second.conv_id, first.conv_id]
    assert all(set(c) == {"conv_id", "title", "created_at", "updated_at"} for c in body["conversations"])
    assert body["folders"] == []
    opposite = "/api/conversations" + ("" if archived else "/archived")
    assert (await authed_client.get(opposite, params={"folder": folder})).json()["conversations"] == []
    root = (await authed_client.get(route)).json()
    assert {"name": "Work space", "path": "Work space"} in root["folders"]
    ancestor = (await authed_client.get(route, params={"folder": "Work space"})).json()
    assert ancestor["folders"] == [{"name": "日本語 & plus+ #hash", "path": folder}]


@pytest.mark.parametrize("route", ["/api/conversations", "/api/conversations/archived"])
@pytest.mark.parametrize("folder", ["../escape", "/absolute", "a//b", "a/..", "a/"])
async def test_listing_preserves_bad_folder_status(route, folder, authed_client):
    response = await authed_client.get(route, params={"folder": folder})
    assert response.status_code == 400
    assert response.json() == {"error": "invalid folder path"}


@pytest.mark.parametrize("folder", ["unknown", "heartbeat/nested", "schedule/nested", "delegated/nested"])
async def test_system_listing_rejects_noncategory_folders(folder, authed_client):
    response = await authed_client.get("/api/conversations/system", params={"folder": folder})
    assert response.status_code == 400
    assert response.json() == {"error": "invalid system folder"}


async def test_system_listing_shapes_order_and_delegated_user_filter(authed_client, http_config):
    ids = ["heartbeat-20260401-100000-0", "schedule-daily-20260401-090000",
           "web-testuser-parent--child-aabb", "web-testuser-parent--child-ccdd",
           "web-other-parent--child-eeff"]
    for i, conv_id in enumerate(ids):
        path = http_config.workspace_path / "conversations" / conv_id / "archive.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n")
        os.utime(path, (1000 + i, 1000 + i))
    for category, expected in [("heartbeat", ids[:1]), ("schedule", ids[1:2]), ("delegated", [ids[3], ids[2]])]:
        response = await authed_client.get("/api/conversations/system", params={"folder": f" {category} "})
        assert response.status_code == 200
        body = response.json()
        assert body["folder"] == category
        assert body["folders"] == []
        assert [c["conv_id"] for c in body["conversations"]] == expected
        assert all(set(c) == {"conv_id", "title", "conv_type", "updated_at"} for c in body["conversations"])


@pytest.mark.parametrize("route", ["/api/conversations", "/api/conversations/archived", "/api/conversations/system"])
async def test_listing_success_rejects_payload_contract_drift(route, authed_client, http_config, monkeypatch):
    """A schema must also guard the actual payload, not just generated callers."""
    index = ConversationIndex(http_config)
    conv = index.create("testuser", "Required title")
    if route.endswith("/archived"):
        index.archive(conv.conv_id)
    if route.endswith("/system"):
        monkeypatch.setattr("decafclaw.web.conversations.list_system_conversations", lambda *args, **kwargs: [
            {"conv_id": "heartbeat-20260401-100000-0", "conv_type": "heartbeat", "updated_at": "2026-04-01"},
        ])
        query = {"folder": "heartbeat"}
    else:
        original = ConversationMeta.to_dict

        def omit_title(self):
            payload = original(self)
            del payload["title"]
            return payload

        monkeypatch.setattr(ConversationMeta, "to_dict", omit_title)
        query = {}
    with pytest.raises(ValidationError, match="conversations.0.title"):
        await authed_client.get(route, params=query)


@pytest.mark.parametrize("body", [{}, {"title": None}, {"folder": None}, {"title": None, "folder": None},
                                  {"title": "Title 日本語 & + #"}, {"folder": "  Work space/日本語 & + #  "},
                                  {"title": "Title 日本語 & + #", "folder": ""}])
async def test_patch_contract_behavior(authed_client, folder_index, body):
    nested = "Work space/日本語 & + #"
    await folder_index.create_folder(nested)
    original = (await authed_client.post("/api/conversations", json={"title": "Original"})).json()
    conv_id = original["conv_id"]
    await folder_index.set_folder(conv_id, nested)
    response = await authed_client.patch(f"/api/conversations/{conv_id}", json=body)
    assert response.status_code == 200
    result = response.json()
    expected_keys = {"conv_id", "title", "created_at", "updated_at"}
    if body.get("folder") is not None:
        expected_keys.add("folder")
        assert result["folder"] == body["folder"].strip()
    assert set(result) == expected_keys
    assert result["conv_id"] == conv_id
    assert result["created_at"] == original["created_at"]
    assert result["title"] == (body.get("title") if body.get("title") is not None else "Original")
    persisted = (await authed_client.get(f"/api/conversations/{conv_id}")).json()
    assert persisted == {key: result[key] for key in original}
    assert await folder_index.get_folder(conv_id) == (body["folder"].strip() if body.get("folder") is not None else nested)


@pytest.mark.parametrize("folder", ["missing", "/bad", "../bad", 123, False])
async def test_patch_invalid_destination_does_not_rename(authed_client, folder):
    original = (await authed_client.post("/api/conversations", json={"title": "Original"})).json()
    url = f"/api/conversations/{original['conv_id']}"
    response = await authed_client.patch(url, json={"title": "Must not apply", "folder": folder})
    assert response.status_code == 400
    assert response.json() == {"error": "Folder does not exist"}
    assert (await authed_client.get(url)).json() == original


async def test_patch_preserves_folder_coercion_and_title_value(authed_client, folder_index):
    await folder_index.create_folder("123")
    original = (await authed_client.post("/api/conversations", json={"title": "Original"})).json()
    url = f"/api/conversations/{original['conv_id']}"
    response = await authed_client.patch(url, json={"title": 123, "folder": 123})
    assert response.status_code == 200
    assert response.json()["title"] == 123
    assert response.json()["folder"] == "123"
    assert (await authed_client.get(url)).json()["title"] == 123


def test_patch_openapi_contract(http_config):
    app = create_app(http_config, EventBus())
    schema = app.openapi()
    operation = schema["paths"]["/api/conversations/{id}"]["patch"]
    assert operation["parameters"][0] == {"name": "id", "in": "path", "required": True,
                                          "schema": {"type": "string", "title": "Id"}}
    body = operation["requestBody"]["content"]["application/json"]["schema"]
    assert set(body["properties"]) == {"title", "folder"}
    assert not body.get("required")
    for field in body["properties"].values():
        assert {part["type"] for part in field["anyOf"]} == {"string", "null"}
    response = schema["components"]["schemas"]["ConversationPatchResponse"]
    assert set(response["required"]) == {"conv_id", "title", "created_at", "updated_at"}
    assert response["properties"]["folder"]["type"] == "string"


async def test_patch_auth_ownership_and_missing(authed_client, unauthed_client, http_config):
    index = ConversationIndex(http_config)
    other = index.create("another-user", "Private")
    for conv_id in ["missing", other.conv_id]:
        url = f"/api/conversations/{conv_id}"
        assert (await unauthed_client.patch(url, json={"title": "Changed"})).status_code == 401
        response = await authed_client.patch(url, json={"title": "Changed"})
        assert response.status_code == 404
        assert response.json() == {"error": "not found"}
    assert index.get(other.conv_id).title == "Private"


@pytest.mark.parametrize("encoded", [False, True])
async def test_patch_supported_identifier_decoding(authed_client, http_config, encoded):
    index = ConversationIndex(http_config)
    conv = index.create("testuser", "Old")
    # Exercise every supported punctuation character as well as the generated ID.
    custom_id = conv.conv_id + ".part_1--child-abc"
    data = index._load()
    data[0]["conv_id"] = custom_id
    index._save(data)
    path_id = custom_id.replace(".", "%2E").replace("_", "%5F").replace("-", "%2D") if encoded else custom_id
    response = await authed_client.patch(f"/api/conversations/{path_id}", json={"title": "Decoded"})
    assert response.status_code == 200
    assert response.json()["conv_id"] == custom_id
    assert index.get(custom_id).title == "Decoded"


@pytest.mark.parametrize("method,route,model", [
    ("post", "/api/conversations/folders", "ConversationFolderCreateResponse"),
    ("put", "/api/conversations/folders/{path}", "ConversationFolderResponse"),
    ("delete", "/api/conversations/folders/{path}", "ConversationFolderResponse"),
])
def test_folder_openapi_contract(http_config, method, route, model):
    schema = create_app(http_config, EventBus()).openapi()
    operation = schema["paths"][route][method]
    if method != "post":
        assert operation["parameters"] == [{"name": "path", "in": "path", "required": True,
                                             "schema": {"type": "string", "title": "Path"}}]
    if method != "delete":
        request = operation["requestBody"]
        assert request["required"] is True
        body = request["content"]["application/json"]["schema"]
        assert body["required"] == ["path"]
        assert body["properties"]["path"]["type"] == "string"
    response = operation["responses"]["200"]["content"]["application/json"]["schema"]
    assert response == {"$ref": f"#/components/schemas/{model}"}
    response = schema["components"]["schemas"][model]
    assert set(response["required"]) == ({"ok", "path"} if method == "post" else {"ok"})
    assert response["properties"]["ok"]["const"] is True
    if method == "post":
        assert response["properties"]["path"]["type"] == "string"


@pytest.mark.parametrize("method,url", [("POST", "/api/conversations/folders"),
                                       ("PUT", "/api/conversations/folders/existing"),
                                       ("DELETE", "/api/conversations/folders/existing")])
async def test_folder_auth_precedes_body_parsing(unauthed_client, method, url):
    response = await unauthed_client.request(method, url, content="malformed")
    assert response.status_code == 401
    assert response.json() == {"error": "not authenticated"}


@pytest.mark.parametrize("method", ["POST", "PUT"])
@pytest.mark.parametrize("body,error", [
    ({}, "required"), ({"path": None}, "required"), ({"path": 123}, "required"),
    ({"path": False}, "required"), ({"path": ""}, "required"),
    ({"path": "  "}, "empty"), ({"path": "/absolute"}, "start"),
    ({"path": "a//b"}, "empty segments"), ({"path": "a/../b"}, ".."),
    ({"path": "a/_private"}, "reserved"),
])
async def test_folder_body_validation_preserved(authed_client, folder_index, method, body, error):
    await folder_index.create_folder("existing")
    url = "/api/conversations/folders" + ("/existing" if method == "PUT" else "")
    response = await authed_client.request(method, url, json=body)
    assert response.status_code == 400
    assert error in response.json()["error"]
    assert await folder_index.folder_exists("existing")


@pytest.mark.parametrize("method", ["PUT", "DELETE"])
async def test_folder_missing_and_empty_path(authed_client, method):
    for path, status, error in [("missing", 404, "Folder not found"), ("", 400, "path required")]:
        response = await authed_client.request(method, "/api/conversations/folders/" + path,
                                              json={"path": "new"})
        assert response.status_code == status
        assert response.json() == {"error": error}


async def test_folder_nested_special_create_rename_merge_delete(authed_client, folder_index):
    source = "Work space/日本語 & plus+ #hash%?"
    target = "Target space/merged % +"
    def url(path):
        return "/api/conversations/folders/" + quote(path, safe="/")
    response = await authed_client.post("/api/conversations/folders", json={"path": "  " + source + "  "})
    assert response.status_code == 200
    assert response.json() == {"ok": True, "path": source}
    assert await folder_index.folder_exists("Work space")
    duplicate = await authed_client.post("/api/conversations/folders", json={"path": source})
    assert duplicate.status_code == 409
    assert duplicate.json() == {"error": "Folder already exists"}
    await folder_index.create_folder(source + "/child")
    await folder_index.create_folder(target + "/child")
    await folder_index.create_folder(target + "/retained")
    await folder_index.set_folder("direct", source)
    await folder_index.set_folder("nested", source + "/child")
    await folder_index.set_folder("retained", target + "/child")
    renamed = await authed_client.put(url(source), json={"path": "  " + target + "  "})
    assert renamed.status_code == 200
    assert renamed.json() == {"ok": True}
    assert not await folder_index.folder_exists(source)
    assert not await folder_index.folder_exists(source + "/child")
    assert await folder_index.list_folders(target) == ["child", "retained"]
    assert await folder_index.get_folder("direct") == target
    assert await folder_index.get_folder("nested") == target + "/child"
    assert await folder_index.get_folder("retained") == target + "/child"
    blocked = await authed_client.delete(url(target))
    assert blocked.status_code == 409
    assert blocked.json() == {"error": "Folder contains conversations"}
    await folder_index.remove_assignment("direct")
    blocked = await authed_client.delete(url(target))
    assert blocked.status_code == 409
    assert blocked.json() == {"error": "Folder contains subfolders"}
    deleted = await authed_client.delete(url(target + "/retained"))
    assert deleted.status_code == 200
    assert deleted.json() == {"ok": True}
    assert not await folder_index.folder_exists(target + "/retained")


async def test_folder_operations_are_per_user(authed_client, folder_index, http_config):
    from decafclaw.web.conversation_folders import ConversationFolderIndex
    other = ConversationFolderIndex(http_config, "another-user")
    await other.create_folder("private")
    await other.set_folder("private-conv", "private")
    for method in ["PUT", "DELETE"]:
        response = await authed_client.request(method, "/api/conversations/folders/private", json={"path": "stolen"})
        assert response.status_code == 404
    created = await authed_client.post("/api/conversations/folders", json={"path": "private"})
    assert created.status_code == 200
    assert (await authed_client.put("/api/conversations/folders/private", json={"path": "ours"})).status_code == 200
    assert (await authed_client.delete("/api/conversations/folders/ours")).status_code == 200
    assert await other.folder_exists("private")
    assert await other.get_folder("private-conv") == "private"
    assert not await folder_index.folder_exists("private")


def test_lifecycle_openapi_contracts(app):
    schema = app.openapi()
    create = schema['paths']['/api/conversations']['post']
    body = create['requestBody']['content']['application/json']['schema']
    assert set(body['properties']) == {'title', 'model', 'folder', 'effort'}
    for field in body['properties'].values():
        assert field['type'] == 'string'
    response = create['responses']['201']['content']['application/json']['schema']
    model = schema['components']['schemas'][response['$ref'].split('/')[-1]]
    assert set(model['required']) == {'conv_id', 'title', 'created_at', 'updated_at'}
    assert set(model['properties']) == set(model['required']) | {'folder', 'model'}
    for path, method in [('/api/conversations/{id}', 'delete'),
                         ('/api/conversations/{id}/archive', 'post'),
                         ('/api/conversations/{id}/unarchive', 'post')]:
        operation = schema['paths'][path][method]
        assert operation['parameters'] == [{'name': 'id', 'in': 'path', 'required': True,
                                           'schema': {'type': 'string', 'title': 'Id'}}]
        response = operation['responses']['200']['content']['application/json']['schema']
        acknowledgement = schema['components']['schemas'][response['$ref'].split('/')[-1]]
        assert acknowledgement['required'] == ['ok']
        assert acknowledgement['properties']['ok']['const'] is True


@pytest.mark.parametrize('method,path', [('POST', '/api/conversations'),
    ('POST', '/api/conversations/missing/archive'), ('POST', '/api/conversations/missing/unarchive'),
    ('DELETE', '/api/conversations/missing')])
async def test_lifecycle_auth_before_body_or_lookup(unauthed_client, method, path):
    response = await unauthed_client.request(method, path, content='not json',
                                             headers={'content-type': 'application/json'})
    assert response.status_code == 401
    assert response.json() == {'error': 'not authenticated'}


@pytest.mark.parametrize('method,suffix', [('POST', '/archive'), ('POST', '/unarchive'), ('DELETE', '')])
@pytest.mark.parametrize('foreign', [False, True])
async def test_lifecycle_missing_or_foreign(authed_client, http_config, app, monkeypatch, method, suffix, foreign):
    index = ConversationIndex(http_config)
    conv_id = index.create('otheruser', 'Private').conv_id if foreign else 'missing'
    kill = AsyncMock()
    monkeypatch.setattr(app.state.terminal_registry, 'kill_sessions_for_conv', kill)
    response = await authed_client.request(method, f'/api/conversations/{conv_id}{suffix}')
    assert response.status_code == 404
    assert response.json() == {'error': 'not found'}
    kill.assert_not_awaited()
    if foreign:
        conv = ConversationIndex(http_config).get(conv_id)
        assert conv is not None and conv.archived is False


@pytest.mark.parametrize('body', [{}, {'title': ''}, {'title': None}, {'title': False}, {'title': 0}])
async def test_create_lifecycle_title_defaults(authed_client, body):
    response = await authed_client.post('/api/conversations', json=body)
    assert response.status_code == 201
    assert response.json()['title'] == 'New conversation'
    assert set(response.json()) == {'conv_id', 'title', 'created_at', 'updated_at'}


@pytest.mark.parametrize('body,expected', [
    ({'effort': ' legacy '}, 'legacy'),
    ({'model': ' chosen ', 'effort': 'legacy'}, 'chosen'),
    ({'model': '', 'effort': 'legacy'}, ''),
    ({'model': 42}, '42'),
    ({'model': None}, 'None'),
])
async def test_create_lifecycle_model_coercion(authed_client, http_config, body, expected):
    from decafclaw.archive import read_archive
    from decafclaw.config_types import ModelConfig
    http_config.model_configs = {name: ModelConfig() for name in ['legacy', 'chosen', '42', 'None']}
    response = await authed_client.post('/api/conversations', json=body)
    assert response.status_code == 201
    data = response.json()
    assert data.get('model', '') == expected
    assert ('model' in data) is bool(expected)
    messages = read_archive(http_config, data['conv_id'])
    assert [(message['role'], message['content']) for message in messages] == ([('model', expected)] if expected else [])


@pytest.mark.parametrize('folder,normalized', [('  Work/日本語 & + #  ', 'Work/日本語 & + #'), (42, '42'),
                                               (None, 'None'), ('  ', '')])
async def test_create_lifecycle_folder_coercion(authed_client, folder_index, folder, normalized):
    if normalized:
        await folder_index.create_folder(normalized)
    response = await authed_client.post('/api/conversations', json={'folder': folder, 'title': '日本語 & + #'})
    assert response.status_code == 201
    data = response.json()
    assert data['title'] == '日本語 & + #'
    assert data.get('folder', '') == normalized
    assert ('folder' in data) is bool(normalized)
    listing = await authed_client.get('/api/conversations', params={'folder': normalized})
    assert any(c['conv_id'] == data['conv_id'] for c in listing.json()['conversations'])


@pytest.mark.parametrize('body,error', [({'model': 'missing', 'folder': 'missing'}, 'Unknown model: missing'),
                                      ({'folder': 'missing'}, 'Folder does not exist')])
async def test_create_lifecycle_invalid_inputs_do_not_create(authed_client, http_config, body, error):
    response = await authed_client.post('/api/conversations', json=body)
    assert response.status_code == 400
    assert response.json() == {'error': error}
    assert ConversationIndex(http_config).list_for_user('testuser') == []


async def test_lifecycle_preserves_then_deletes_storage(authed_client, http_config, folder_index, app, monkeypatch):
    from decafclaw.archive import read_archive
    await folder_index.create_folder('Work/Nested')
    response = await authed_client.post('/api/conversations', json={'folder': 'Work/Nested'})
    conv_id = response.json()['conv_id']
    append_message(http_config, conv_id, {'role': 'user', 'content': 'kept'})
    directory = http_config.workspace_path / 'conversations' / conv_id
    (directory / 'uploads').mkdir()
    (directory / 'uploads' / 'file.txt').write_text('attachment')
    (directory / 'workflow.json').write_text('{}')
    for suffix, archived in [('archive', True), ('unarchive', False)]:
        response = await authed_client.post(f'/api/conversations/{conv_id}/{suffix}')
        assert response.status_code == 200 and response.json() == {'ok': True}
        conv = ConversationIndex(http_config).get(conv_id)
        assert conv is not None and conv.archived is archived
        assert read_archive(http_config, conv_id)[0]['content'] == 'kept'
        listing = await authed_client.get('/api/conversations/archived' if archived else '/api/conversations',
                                          params={'folder': 'Work/Nested'})
        assert [c['conv_id'] for c in listing.json()['conversations']] == [conv_id]
    killed = []

    async def kill_before_cleanup(identifier):
        assert directory.exists()
        killed.append(identifier)

    monkeypatch.setattr(app.state.terminal_registry, 'kill_sessions_for_conv', kill_before_cleanup)
    response = await authed_client.delete(f'/api/conversations/{conv_id}')
    assert response.status_code == 200 and response.json() == {'ok': True}
    assert killed == [conv_id]
    assert not directory.exists()
    assert ConversationIndex(http_config).get(conv_id) is None
    # Assignment cleanup makes the now-empty folder deletable.
    response = await authed_client.delete('/api/conversations/folders/Work/Nested')
    assert response.status_code == 200


async def test_create_lifecycle_keeps_legacy_title_value(authed_client):
    response = await authed_client.post('/api/conversations', json={'title': 123})
    assert response.status_code == 201
    assert response.json()['title'] == 123


@pytest.mark.parametrize("suffix", ["context", "export", "export?format=html", "export?format=jsonl"])
async def test_context_export_requires_auth_before_format(suffix, unauthed_client):
    response = await unauthed_client.get(f"/api/conversations/missing/{suffix}")
    assert response.status_code == 401


async def test_context_optional_and_extra_diagnostics_are_unchanged(authed_client, http_config):
    from decafclaw.context_composer import write_context_sidecar

    conv = (await authed_client.post("/api/conversations", json={})).json()
    conv_id = conv["conv_id"]
    assert (await authed_client.get(f"/api/conversations/{conv_id}/context")).status_code == 404
    payload = {"sources": [{"source": "custom", "details": {"arbitrary": [1, None, {"nested": True}]}}],
               "future_data": {"enabled": True}}
    write_context_sidecar(http_config, conv_id, payload)
    response = await authed_client.get(f"/api/conversations/{conv_id}/context")
    assert response.status_code == 200
    assert response.json() == payload
