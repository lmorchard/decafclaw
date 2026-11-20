"""HTTP tests for the general fork endpoint (#1570)."""

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


def _seed_exchange(config, conv_id: str, prompt: str, reply: str):
    append_message(config, conv_id, {"role": "user", "content": prompt})
    append_message(config, conv_id, {"role": "assistant", "content": reply})


@pytest.mark.asyncio
async def test_fork_endpoint_creates_a_general_child(client, http_config, app):
    manager = _FakeManager()
    app.state.manager = manager
    index = ConversationIndex(http_config)
    parent = index.create("testuser", "Design thread")
    _seed_exchange(http_config, parent.conv_id, "Compare two designs", "Design A.")

    resp = await client.post(
        f"/api/conversations/{parent.conv_id}/fork",
        json={"instruction": "Explore the queue approach"},
    )

    assert resp.status_code == 201
    body = resp.json()
    child = index.get(body["conv_id"])
    assert child is not None
    assert child.forked_from == parent.conv_id
    assert body["template"] == "general"
    assert body["title"] == "Fork: Design thread"
    assert "Explore the queue approach" in manager.sent[0]["text"]
    assert "worktree" not in manager.sent[0]["text"]
    notices = [m for m in read_archive(http_config, parent.conv_id) if m.get("role") == "fork_notice"]
    assert notices[0]["data"]["template"] == "general"


@pytest.mark.asyncio
async def test_fork_endpoint_rejects_an_unknown_template(client, http_config, app):
    app.state.manager = _FakeManager()
    index = ConversationIndex(http_config)
    parent = index.create("testuser", "Thread")
    _seed_exchange(http_config, parent.conv_id, "hello", "hi")

    resp = await client.post(
        f"/api/conversations/{parent.conv_id}/fork",
        json={"template": "review"},
    )

    assert resp.status_code == 400
    assert "Unknown fork template" in resp.json()["error"]
