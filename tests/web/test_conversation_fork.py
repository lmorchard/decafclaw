"""Conversation fork from a message anchor (#1581)."""

import pytest
from httpx import ASGITransport, AsyncClient

from decafclaw.archive import append_message, read_archive
from decafclaw.web.conversation_fork import (
    ForkError,
    count_turns,
    fork_conversation,
    latest_model,
    resolve_anchor,
    turn_boundary,
)
from decafclaw.web.conversations import ConversationIndex

# -- Slice helpers -------------------------------------------------------------


def _messages():
    return [
        {"role": "user", "content": "hi", "timestamp": "t1"},
        {"role": "assistant", "content": "calling", "tool_calls": [{"id": "c1"}], "timestamp": "t2"},
        {"role": "tool", "tool_call_id": "c1", "content": "result", "timestamp": "t3"},
        {"role": "assistant", "content": "done", "timestamp": "t4"},
        {"role": "user", "content": "again", "timestamp": "t5"},
    ]


def test_resolve_anchor_by_index_and_timestamp():
    messages = _messages()
    assert resolve_anchor(messages, message_index=3, message_timestamp="") == 3
    assert resolve_anchor(messages, message_index=None, message_timestamp="t3") == 2


@pytest.mark.parametrize(
    "kwargs",
    [
        {"message_index": 9, "message_timestamp": ""},
        {"message_index": -1, "message_timestamp": ""},
        {"message_index": None, "message_timestamp": "missing"},
        {"message_index": None, "message_timestamp": ""},
        {"message_index": True, "message_timestamp": ""},
    ],
)
def test_resolve_anchor_rejects_bad_input(kwargs):
    with pytest.raises(ForkError):
        resolve_anchor(_messages(), **kwargs)


def test_resolve_anchor_rejects_empty_conversation():
    with pytest.raises(ForkError):
        resolve_anchor([], message_index=0, message_timestamp="")


def test_turn_boundary_keeps_plain_assistant():
    messages = _messages()
    assert turn_boundary(messages, 3) == 3


@pytest.mark.parametrize("anchor", [1, 2])
def test_turn_boundary_walks_back_over_tool_sequence(anchor):
    """An assistant with tool calls and its tool result are not safe endings."""
    messages = _messages()
    assert turn_boundary(messages, anchor) == 0


def test_turn_boundary_requires_a_complete_turn():
    messages = [{"role": "tool", "content": "orphan"}]
    with pytest.raises(ForkError):
        turn_boundary(messages, 0)


def test_count_turns_and_latest_model():
    messages = _messages()
    assert count_turns(messages) == 2
    assert latest_model(messages) == ""
    assert latest_model([{"role": "model", "content": "a"}, {"role": "model", "content": "b"}]) == "b"


def test_fork_conversation_slices_and_titles(config):
    config.agent_path.mkdir(parents=True, exist_ok=True)
    config.workspace_path.mkdir(parents=True, exist_ok=True)
    index = ConversationIndex(config)
    source = index.create("alice", "Original")
    for message in _messages():
        append_message(config, source.conv_id, message)
    append_message(config, source.conv_id, {"role": "model", "content": "some-model", "timestamp": "t6"})

    conv, model = fork_conversation(config, index, "alice", source.conv_id, message_timestamp="t4")

    assert conv.conv_id != source.conv_id
    assert conv.title == "Fork of Original (turn 1)"
    assert conv.custom_title is True
    assert model == ""
    forked = read_archive(config, conv.conv_id)
    assert [m["content"] for m in forked] == ["hi", "calling", "result", "done"]
    # Source archive is unchanged and the fork owns a separate file.
    assert len(read_archive(config, source.conv_id)) == 6


def test_fork_conversation_inherits_model_record(config):
    config.agent_path.mkdir(parents=True, exist_ok=True)
    config.workspace_path.mkdir(parents=True, exist_ok=True)
    index = ConversationIndex(config)
    source = index.create("alice", "Original")
    append_message(config, source.conv_id, {"role": "model", "content": "some-model", "timestamp": "t1"})
    append_message(config, source.conv_id, {"role": "user", "content": "hi", "timestamp": "t2"})

    conv, model = fork_conversation(config, index, "alice", source.conv_id, message_index=1)
    assert model == "some-model"
    assert [m["role"] for m in read_archive(config, conv.conv_id)] == ["model", "user"]


# -- REST route ----------------------------------------------------------------


def _seed(http_config, title="Original", messages=None):
    conv = ConversationIndex(http_config).create("testuser", title)
    for message in messages if messages is not None else _messages():
        append_message(http_config, conv.conv_id, message)
    return conv


@pytest.mark.asyncio
async def test_fork_route_creates_independent_conversation(client, http_config):
    source = _seed(http_config)

    resp = await client.post(
        f"/api/conversations/{source.conv_id}/fork",
        json={"message_timestamp": "t4"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["conv_id"].startswith("web-testuser-")
    assert body["conv_id"] != source.conv_id
    assert body["title"] == "Fork of Original (turn 1)"

    history = await client.get(f"/api/conversations/{body['conv_id']}/history")
    assert history.status_code == 200
    assert [m["content"] for m in history.json()["messages"]] == ["hi", "calling", "result", "done"]

    source_history = await client.get(f"/api/conversations/{source.conv_id}/history")
    assert len(source_history.json()["messages"]) == 5


@pytest.mark.asyncio
async def test_fork_route_accepts_message_index(client, http_config):
    source = _seed(http_config)
    resp = await client.post(
        f"/api/conversations/{source.conv_id}/fork",
        json={"message_index": 4},
    )
    assert resp.status_code == 201
    assert resp.json()["title"] == "Fork of Original (turn 2)"


@pytest.mark.asyncio
async def test_fork_route_inherits_folder_and_model(client, http_config):
    source = ConversationIndex(http_config).create("testuser", "Original")
    # Model records are written when the conversation is created, before any
    # message, so the slice inherits the active model.
    append_message(http_config, source.conv_id, {"role": "model", "content": "some-model", "timestamp": "t0"})
    for message in _messages():
        append_message(http_config, source.conv_id, message)
    await client.post("/api/conversations/folders", json={"path": "Work"})
    await client.patch(f"/api/conversations/{source.conv_id}", json={"folder": "Work"})

    resp = await client.post(
        f"/api/conversations/{source.conv_id}/fork",
        json={"message_timestamp": "t4"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["folder"] == "Work"
    assert body["model"] == "some-model"

    listing = await client.get("/api/conversations", params={"folder": "Work"})
    assert body["conv_id"] in {c["conv_id"] for c in listing.json()["conversations"]}


@pytest.mark.asyncio
async def test_fork_route_rejects_unknown_anchor(client, http_config):
    source = _seed(http_config)
    resp = await client.post(
        f"/api/conversations/{source.conv_id}/fork",
        json={"message_timestamp": "nope"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "message not found in conversation archive"


@pytest.mark.asyncio
async def test_fork_route_requires_an_anchor(client, http_config):
    source = _seed(http_config)
    resp = await client.post(f"/api/conversations/{source.conv_id}/fork", json={})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_fork_route_hides_another_users_conversation(client, http_config):
    conv = ConversationIndex(http_config).create("someone-else", "Private")
    append_message(http_config, conv.conv_id, {"role": "user", "content": "secret", "timestamp": "t1"})
    resp = await client.post(
        f"/api/conversations/{conv.conv_id}/fork",
        json={"message_index": 0},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_fork_route_requires_auth(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as anon:
        resp = await anon.post("/api/conversations/anything/fork", json={"message_index": 0})
    assert resp.status_code == 401


def test_fork_conversation_copies_attachment_files(config):
    from decafclaw.attachments import save_attachment

    config.agent_path.mkdir(parents=True, exist_ok=True)
    config.workspace_path.mkdir(parents=True, exist_ok=True)
    index = ConversationIndex(config)
    source = index.create("alice", "Original")
    attachment = save_attachment(config, source.conv_id, "photo.png", b"png-bytes", "image/png")
    append_message(
        config,
        source.conv_id,
        {"role": "user", "content": "look", "attachments": [attachment], "timestamp": "t1"},
    )

    conv, _ = fork_conversation(config, index, "alice", source.conv_id, message_index=0)

    forked = read_archive(config, conv.conv_id)
    copied = forked[0]["attachments"][0]
    assert copied["path"] != attachment["path"]
    assert conv.conv_id in copied["path"]
    assert (config.workspace_path / copied["path"]).read_bytes() == b"png-bytes"
    # The source keeps its own copy.
    assert (config.workspace_path / attachment["path"]).read_bytes() == b"png-bytes"


def test_fork_conversation_keeps_missing_attachment_path(config):
    config.agent_path.mkdir(parents=True, exist_ok=True)
    config.workspace_path.mkdir(parents=True, exist_ok=True)
    index = ConversationIndex(config)
    source = index.create("alice", "Original")
    missing = {"filename": "gone.png", "path": "sessions/elsewhere/gone.png", "mime_type": "image/png"}
    append_message(
        config,
        source.conv_id,
        {"role": "user", "content": "look", "attachments": [missing], "timestamp": "t1"},
    )

    conv, _ = fork_conversation(config, index, "alice", source.conv_id, message_index=0)
    assert read_archive(config, conv.conv_id)[0]["attachments"][0]["path"] == missing["path"]
