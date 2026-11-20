"""HTTP tests for the dissect template over POST /fork (#1570)."""

from __future__ import annotations

import pytest

from decafclaw.archive import append_message, read_archive
from decafclaw.web.conversations import ConversationIndex


class _FakeManager:
    def __init__(self):
        self.sent: list[dict] = []
        self.emitted: list[tuple[str, dict]] = []

    async def send_message(self, conv_id, text, **kwargs):
        self.sent.append({"conv_id": conv_id, "text": text, **kwargs})

    async def emit(self, conv_id, event):
        self.emitted.append((conv_id, event))

    def get_state(self, conv_id):
        return None


def _seed_failure(config, conv_id: str):
    append_message(config, conv_id, {"role": "user", "content": "Fix the flaky test"})
    append_message(
        config,
        conv_id,
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "call-1",
                    "function": {"name": "shell", "arguments": '{"command": "make test"}'},
                }
            ],
        },
    )
    append_message(
        config,
        conv_id,
        {"role": "tool", "tool_call_id": "call-1", "content": "[error: command timed out after 180s]"},
    )


@pytest.mark.asyncio
async def test_dissect_template_creates_linked_child(client, http_config, app):
    manager = _FakeManager()
    app.state.manager = manager
    index = ConversationIndex(http_config)
    parent = index.create("testuser", "Flaky test")
    _seed_failure(http_config, parent.conv_id)

    resp = await client.post(
        f"/api/conversations/{parent.conv_id}/fork",
        json={"template": "dissect", "tool_call_id": "call-1", "instruction": "Check the timeout"},
    )

    assert resp.status_code == 201
    body = resp.json()
    child = index.get(body["conv_id"])
    assert child is not None
    assert child.forked_from == parent.conv_id
    assert body["forked_from"] == parent.conv_id
    assert body["template"] == "dissect"
    assert body["title"] == "Dissect: Flaky test"
    assert manager.sent[0]["conv_id"] == child.conv_id
    assert "Check the timeout" in manager.sent[0]["text"]
    assert "[error: command timed out after 180s]" in manager.sent[0]["text"]
    notices = [m for m in read_archive(http_config, parent.conv_id) if m.get("role") == "fork_notice"]
    assert notices[0]["data"]["template"] == "dissect"


@pytest.mark.asyncio
async def test_dissect_template_falls_back_to_general_without_a_latest_error(client, http_config, app):
    manager = _FakeManager()
    app.state.manager = manager
    index = ConversationIndex(http_config)
    parent = index.create("testuser", "Design thread")
    _seed_failure(http_config, parent.conv_id)
    append_message(http_config, parent.conv_id, {"role": "user", "content": "Compare two approaches"})
    append_message(http_config, parent.conv_id, {"role": "assistant", "content": "Here are two options."})

    resp = await client.post(
        f"/api/conversations/{parent.conv_id}/fork",
        json={"template": "dissect"},
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["template"] == "general"
    assert body["title"] == "Fork: Design thread"
    assert "What failed" not in manager.sent[0]["text"]


@pytest.mark.asyncio
async def test_fork_endpoint_refuses_another_users_conversation(client, http_config, app):
    app.state.manager = _FakeManager()
    index = ConversationIndex(http_config)
    parent = index.create("otheruser", "Private")
    _seed_failure(http_config, parent.conv_id)

    resp = await client.post(f"/api/conversations/{parent.conv_id}/fork", json={"template": "dissect"})

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_fork_endpoint_rejects_a_conversation_without_messages(client, http_config, app):
    app.state.manager = _FakeManager()
    index = ConversationIndex(http_config)
    parent = index.create("testuser", "Empty")

    resp = await client.post(f"/api/conversations/{parent.conv_id}/fork", json={})

    assert resp.status_code == 400
    assert "no messages" in resp.json()["error"]


@pytest.mark.asyncio
async def test_fork_endpoint_reports_an_unavailable_manager(client, http_config, app):
    app.state.manager = None
    index = ConversationIndex(http_config)
    parent = index.create("testuser", "Flaky test")

    resp = await client.post(f"/api/conversations/{parent.conv_id}/fork", json={})

    assert resp.status_code == 503
