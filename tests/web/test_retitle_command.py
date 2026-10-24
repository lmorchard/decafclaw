"""Tests for the server-side `/retitle` command handler (#1156)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from decafclaw.archive import append_message
from decafclaw.events import EventBus
from decafclaw.web.conversations import ConversationIndex
from decafclaw.web.message_types import WSMessageType
from decafclaw.web.websocket import _handle_send


class _FakeManager:
    """Spy manager: records enqueue_turn calls, provides an emit()."""

    def __init__(self):
        self.enqueued: list[dict] = []
        self.emitted: list[tuple[str, dict]] = []

    async def enqueue_turn(self, conv_id, kind, prompt, **kwargs):
        self.enqueued.append({"conv_id": conv_id, "kind": kind, "prompt": prompt, **kwargs})
        return None

    def emit(self, conv_id: str, event: dict) -> None:
        self.emitted.append((conv_id, event))


class _Env:
    def __init__(self, config, manager, bus):
        self.config = config
        self.manager = manager
        self.bus = bus
        self.index = ConversationIndex(config)
        self.sent: list[dict] = []
        self.bus_events: list[dict] = []
        self.bus.subscribe(self.bus_events.append)
        self.state = {
            "config": config,
            "manager": manager,
            "event_bus": bus,
        }

    async def _ws_send(self, msg: dict) -> None:
        self.sent.append(msg)

    async def send(self, conv_id: str, text: str, username: str = "testuser") -> None:
        msg = {"conv_id": conv_id, "text": text}
        await _handle_send(self._ws_send, self.index, username, msg, self.state)


@pytest.fixture
def retitle_env(config):
    config.agent_path.mkdir(parents=True, exist_ok=True)
    manager = _FakeManager()
    bus = EventBus()
    return _Env(config, manager, bus)


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["/retitle", "!retitle", "/retitle   "])
async def test_retitle_command_success(retitle_env, stub_llm, command):
    """A successful /retitle regenerates the title, emits conv_renamed, runs no agent turn, and is chat-silent."""
    env = retitle_env
    conv = env.index.create("testuser", "Initial Title")
    append_message(env.config, conv.conv_id, {"role": "user", "content": "What is Python?"})
    append_message(env.config, conv.conv_id, {"role": "assistant", "content": "Python is a language."})

    stub_llm.response = {"content": "Python Overview"}

    await env.send(conv.conv_id, command)

    # 1. No agent turn was enqueued.
    assert env.manager.enqueued == []

    # 2. Success is chat-silent (no message_complete, no error).
    assert not any(m.get("type") in (WSMessageType.MESSAGE_COMPLETE, WSMessageType.ERROR) for m in env.sent)

    # 3. Conversation in index was updated.
    updated = env.index.get(conv.conv_id)
    assert updated.title == "Python Overview"
    assert updated.custom_title is False

    # 4. Global bus emitted conv_renamed.
    renamed_events = [e for e in env.bus_events if e.get("type") == "conv_renamed"]
    assert len(renamed_events) == 1
    assert renamed_events[0]["conv_id"] == conv.conv_id
    assert renamed_events[0]["title"] == "Python Overview"


@pytest.mark.asyncio
async def test_retitle_command_unauthorized(retitle_env):
    """A user who does not own the conversation is refused."""
    env = retitle_env
    conv = env.index.create("otheruser", "Secret Topic")
    append_message(env.config, conv.conv_id, {"role": "user", "content": "secret"})

    await env.send(conv.conv_id, "/retitle", username="testuser")
    assert any(
        m.get("type") == WSMessageType.ERROR
        and ("Not authorized" in m.get("message", "") or "Conversation not found" in m.get("message", ""))
        for m in env.sent
    )
    assert env.index.get(conv.conv_id).title == "Secret Topic"


@pytest.mark.asyncio
async def test_retitle_command_empty_history_error(retitle_env):
    """An empty conversation returns an error notice without mutating title."""
    env = retitle_env
    conv = env.index.create("testuser", "Blank Chat")

    await env.send(conv.conv_id, "/retitle")

    assert env.manager.enqueued == []
    assert any(
        m.get("type") == WSMessageType.ERROR and "Not enough conversation history" in m.get("message", "")
        for m in env.sent
    )
    assert env.index.get(conv.conv_id).title == "Blank Chat"


@pytest.mark.asyncio
async def test_retitle_word_boundary_guard(retitle_env, monkeypatch):
    """Guard: '/retitlefoo' must NOT be intercepted as '/retitle'."""
    env = retitle_env
    conv = env.index.create("testuser", "Initial")

    dispatched = []

    async def fake_dispatch(ctx, text, **kwargs):
        dispatched.append(text)
        from decafclaw.commands import CommandResult

        return CommandResult(mode="unknown", text="", display_text=text, skill=None)

    monkeypatch.setattr("decafclaw.commands.dispatch_command", fake_dispatch)

    await env.send(conv.conv_id, "/retitlefoo")

    assert "/retitlefoo" in dispatched
    assert env.index.get(conv.conv_id).title == "Initial"
