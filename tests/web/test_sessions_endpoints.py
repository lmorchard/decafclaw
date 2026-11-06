"""Session hierarchy endpoint tests with REAL delegated-child archives.

Regression guard for the #1223 review finding 1: manager-spawned
`{parent}--child-{hex}` conversations never receive ConversationIndex
entries, so the session endpoints must discover them from the archive
layer (`iter_conversation_archives`) rather than the index. Children here
are created exactly as delegate.py does — messages appended under the
child conv id — never via hand-written index entries.
"""

import pytest

from decafclaw.archive import append_message
from decafclaw.web.conversation_folders import ConversationFolderIndex
from decafclaw.web.conversations import ConversationIndex


def _spawn_child(config, parent_conv_id: str, suffix: str = "abcdef12") -> str:
    """Create a real delegated-child archive, as the delegate path does.

    Appends messages under `{parent}--child-{hex}` — no index entry — so
    the child exists only as an archive directory, exactly like a
    manager-spawned subagent in production.
    """
    child_id = f"{parent_conv_id}--child-{suffix}"
    append_message(config, child_id, {"role": "user", "content": "subtask prompt"})
    append_message(config, child_id, {"role": "assistant", "content": "subtask result"})
    return child_id


@pytest.mark.asyncio
async def test_list_sessions_discovers_children_from_archives(client, http_config):
    """Children with only archive files (no index entry) join their root's session."""
    parent = ConversationIndex(http_config).create("testuser", "Parent chat")
    child_id = _spawn_child(http_config, parent.conv_id)
    # Document the premise: the child has NO index entry — discovery must
    # come from the archive layer.
    assert ConversationIndex(http_config).get(child_id) is None

    data = (await client.get("/api/sessions")).json()
    assert data["folder"] == ""
    assert len(data["sessions"]) == 1
    session = data["sessions"][0]
    assert session["session_id"] == parent.conv_id
    assert session["title"] == "Parent chat"
    assert len(session["conversations"]) == 2
    assert session["conversations"][0]["kind"] == "parent"
    assert session["conversations"][0]["conv_id"] == parent.conv_id
    assert session["conversations"][1]["kind"] == "subagent"
    assert session["conversations"][1]["conv_id"] == child_id
    # Synthesized title follows _classify_conv_id's delegated pattern.
    assert session["conversations"][1]["title"] == "Subtask abcdef12"


@pytest.mark.asyncio
async def test_child_session_id_resolves_to_parent_session(client, http_config):
    """A subagent conversation id resolves to its parent session's list."""
    parent = ConversationIndex(http_config).create("testuser", "Parent chat")
    child_id = _spawn_child(http_config, parent.conv_id, "cafe1234")

    resp = await client.get(f"/api/sessions/{child_id}/conversations")
    assert resp.status_code == 200
    data = resp.json()
    assert data["session_id"] == parent.conv_id
    assert [c["conv_id"] for c in data["conversations"]] == [parent.conv_id, child_id]
    assert [c["kind"] for c in data["conversations"]] == ["parent", "subagent"]


@pytest.mark.asyncio
async def test_child_of_foreign_root_is_not_listed(client, http_config):
    """Another user's delegated child never appears in the listing."""
    foreign_root = ConversationIndex(http_config).create("otheruser", "Foreign chat")
    _spawn_child(http_config, foreign_root.conv_id)

    data = (await client.get("/api/sessions")).json()
    assert data["sessions"] == []


@pytest.mark.asyncio
async def test_child_of_archived_root_is_hidden(client, http_config):
    """Archiving a root hides its session including discovered children."""
    parent = ConversationIndex(http_config).create("testuser", "Parent chat")
    _spawn_child(http_config, parent.conv_id)
    ConversationIndex(http_config).archive(parent.conv_id)

    data = (await client.get("/api/sessions")).json()
    assert data["sessions"] == []


@pytest.mark.asyncio
async def test_child_of_deleted_root_is_not_listed(client, http_config):
    """Orphaned children (root removed from the index) cannot be attributed."""
    parent = ConversationIndex(http_config).create("testuser", "Parent chat")
    _spawn_child(http_config, parent.conv_id)
    ConversationIndex(http_config).delete(parent.conv_id)

    data = (await client.get("/api/sessions")).json()
    assert data["sessions"] == []


@pytest.mark.asyncio
async def test_folder_filter_keeps_children_with_their_root(client, http_config):
    """A session's folder is its root's assignment; children ride along."""
    parent = ConversationIndex(http_config).create("testuser", "Parent chat")
    child_id = _spawn_child(http_config, parent.conv_id)
    folder_index = ConversationFolderIndex(http_config, "testuser")
    ok, _ = await folder_index.create_folder("work")
    assert ok
    ok, _ = await folder_index.set_folder(parent.conv_id, "work")
    assert ok

    top = (await client.get("/api/sessions")).json()
    assert top["sessions"] == []

    foldered = (await client.get("/api/sessions", params={"folder": "work"})).json()
    assert foldered["folder"] == "work"
    assert len(foldered["sessions"]) == 1
    assert [c["conv_id"] for c in foldered["sessions"][0]["conversations"]] == [parent.conv_id, child_id]


@pytest.mark.asyncio
async def test_session_conversations_requires_root_ownership(client, http_config):
    """A subagent id under a foreign root 404s, like the root itself."""
    foreign_root = ConversationIndex(http_config).create("otheruser", "Foreign chat")
    foreign_child = _spawn_child(http_config, foreign_root.conv_id)

    resp_root = await client.get(f"/api/sessions/{foreign_root.conv_id}/conversations")
    resp_child = await client.get(f"/api/sessions/{foreign_child}/conversations")
    assert resp_root.status_code == 404
    assert resp_child.status_code == 404
