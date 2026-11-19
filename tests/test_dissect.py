"""Dissection extraction, brief, orchestration, and tool tests (#1570)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from decafclaw.archive import append_message, read_archive
from decafclaw.dissect import build_dissection_brief, find_dissection_focus
from decafclaw.media import ToolResult
from decafclaw.tools.conversation_tools import tool_conversation_fork
from decafclaw.web.conversations import ConversationIndex
from decafclaw.web.dissection import create_dissection, resolve_conversation_model


def _seed_failure(config, conv_id: str, *, user_text: str = "Fix the timeout", error: str = "[error: timed out]"):
    append_message(config, conv_id, {"role": "user", "content": user_text})
    append_message(
        config,
        conv_id,
        {
            "role": "assistant",
            "content": "Running the tests.",
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "shell_exec", "arguments": '{"command": "pytest -q"}'},
                }
            ],
        },
    )
    append_message(config, conv_id, {"role": "tool", "tool_call_id": "call-1", "content": error})


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


# -- extraction ---------------------------------------------------------------


def test_focus_uses_last_tool_error_with_call_arguments():
    messages = [
        {"role": "user", "content": "Fix the timeout"},
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "c1",
                    "function": {"name": "shell_exec", "arguments": '{"command": "pytest -q"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "[error: timed out after 180s]"},
    ]

    focus = find_dissection_focus(messages)

    assert focus is not None
    assert focus.tool_name == "shell_exec"
    assert '"pytest -q"' in focus.arguments
    assert focus.error == "[error: timed out after 180s]"
    assert (focus.user_index, focus.assistant_index, focus.tool_index) == (0, 1, 2)


def test_focus_named_call_beats_an_older_error():
    messages = [
        {"role": "user", "content": "Try two things"},
        {
            "role": "assistant",
            "tool_calls": [
                {"id": "old", "function": {"name": "first", "arguments": "{}"}},
                {"id": "new", "function": {"name": "second", "arguments": "{}"}},
            ],
        },
        {"role": "tool", "tool_call_id": "old", "content": "[error: first failed]"},
        {"role": "tool", "tool_call_id": "new", "content": "[error: second failed]"},
    ]

    focus = find_dissection_focus(messages, "old")

    assert focus is not None
    assert focus.tool_call_id == "old"
    assert focus.tool_name == "first"
    assert "first failed" in focus.error


def test_focus_named_successful_call_is_not_reported_as_an_error():
    messages = [
        {"role": "user", "content": "Check it"},
        {
            "role": "assistant",
            "tool_calls": [{"id": "ok", "function": {"name": "read", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "ok", "content": "file contents"},
    ]

    focus = find_dissection_focus(messages, "ok")

    assert focus is not None
    assert focus.tool_name == "read"
    assert focus.has_error is False
    brief = build_dissection_brief(messages, focus, source_conv_id="parent")
    assert "file contents" in brief
    assert "No failing tool result was recorded" in brief


def test_focus_falls_back_to_latest_exchange_without_an_error():
    messages = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "one"},
        {"role": "user", "content": "second"},
        {"role": "assistant", "content": "two"},
    ]

    focus = find_dissection_focus(messages)

    assert focus is not None
    assert focus.has_error is False
    assert focus.user_index == 2
    assert focus.assistant_index == 3


def test_focus_ignores_synthetic_user_messages():
    messages = [
        {"role": "user", "content": "real prompt"},
        {"role": "user", "content": "[System Update: x]", "source": "system_update"},
        {"role": "assistant", "content": "reply"},
    ]

    focus = find_dissection_focus(messages)

    assert focus is not None
    assert focus.user_index == 0


def test_brief_quotes_failure_and_debug_instructions():
    messages = [
        {"role": "user", "content": "Fix the failing browser test"},
        {
            "role": "assistant",
            "content": "Trying the suite.",
            "tool_calls": [
                {
                    "id": "call-1",
                    "function": {"name": "shell", "arguments": '{"command": "make test"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call-1", "content": "[error: command timed out after 180s]"},
    ]
    focus = find_dissection_focus(messages)

    brief = build_dissection_brief(
        messages,
        focus,
        source_conv_id="web-testuser-abc",
        source_title="Browser flake",
        instruction="Look at the timeout path first.",
    )

    assert "Browser flake" in brief
    assert "web-testuser-abc" in brief
    assert "Fix the failing browser test" in brief
    assert "shell" in brief
    assert "make test" in brief
    assert "[error: command timed out after 180s]" in brief
    assert "Look at the timeout path first." in brief
    assert "conversation_search" in brief
    assert "`project` (this repository's workflow) and `postmortem`" in brief


# -- orchestration ------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_dissection_seeds_child_notice_and_model(config):
    manager = _FakeManager()
    index = ConversationIndex(config)
    parent = index.create("testuser", "Browser flake")
    append_message(config, parent.conv_id, {"role": "model", "content": "test-model"})
    _seed_failure(config, parent.conv_id)

    result = await create_dissection(
        config,
        parent.conv_id,
        "testuser",
        manager=manager,
        instruction="Find the root cause.",
    )

    child = index.get(result["conv_id"])
    assert child is not None
    assert child.forked_from == parent.conv_id
    assert child.title == "Dissect: Browser flake"
    assert child.custom_title is True

    assert len(manager.sent) == 1
    sent = manager.sent[0]
    assert sent["conv_id"] == child.conv_id
    assert "Find the root cause." in sent["text"]
    assert "[error: timed out]" in sent["text"]
    assert sent["user_id"] == "testuser"

    # The child inherits the parent's active model.
    assert any(
        m.get("role") == "model" and m.get("content") == "test-model" for m in read_archive(config, child.conv_id)
    )

    # The parent gets one durable notice and one live frame.
    notices = [m for m in read_archive(config, parent.conv_id) if m.get("role") == "dissect_notice"]
    assert len(notices) == 1
    assert notices[0]["data"]["fork_conv_id"] == child.conv_id
    assert notices[0]["data"]["tool"] == "shell_exec"
    assert manager.emitted == [
        (
            parent.conv_id,
            {
                "type": "dissect_notice",
                "fork_conv_id": child.conv_id,
                "fork_title": "Dissect: Browser flake",
                "source_conv_id": parent.conv_id,
                "tool": "shell_exec",
                "had_error": True,
            },
        )
    ]


@pytest.mark.asyncio
async def test_create_dissection_rejects_an_empty_conversation(config):
    manager = _FakeManager()
    index = ConversationIndex(config)
    parent = index.create("testuser", "Empty")

    from decafclaw.web.dissection import DissectionError

    with pytest.raises(DissectionError, match="no messages"):
        await create_dissection(config, parent.conv_id, "testuser", manager=manager)
    assert manager.sent == []


def test_resolve_conversation_model_prefers_archive_then_live_state(config):
    index = ConversationIndex(config)
    conv = index.create("testuser", "Model")
    live = SimpleNamespace(persisted=SimpleNamespace(active_model="live-model"))

    class _StateManager:
        def get_state(self, conv_id):
            return live

    assert resolve_conversation_model(config, conv.conv_id, _StateManager()) == "live-model"

    append_message(config, conv.conv_id, {"role": "model", "content": "archived-model"})
    assert resolve_conversation_model(config, conv.conv_id, _StateManager()) == "archived-model"


# -- tool ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_conversation_fork_tool_starts_a_dissection(config):
    manager = _FakeManager()
    index = ConversationIndex(config)
    parent = index.create("testuser", "Browser flake")
    _seed_failure(config, parent.conv_id)
    ctx = SimpleNamespace(
        config=config,
        conv_id=parent.conv_id,
        user_id="testuser",
        transport="web",
        manager=manager,
        active_model="",
    )

    result = await tool_conversation_fork(ctx, instruction="Check the watchdog")

    assert isinstance(result, ToolResult)
    assert "Dissection conversation started" in result.text
    assert "?conv=" in result.text
    assert result.data["forked_from"] == parent.conv_id
    assert len(manager.sent) == 1
    assert "Check the watchdog" in manager.sent[0]["text"]


@pytest.mark.asyncio
async def test_conversation_fork_tool_refuses_non_web_transports(config):
    ctx = SimpleNamespace(
        config=config,
        conv_id="mattermost-channel",
        user_id="testuser",
        transport="mattermost",
        manager=_FakeManager(),
        active_model="",
    )

    result = await tool_conversation_fork(ctx)

    assert isinstance(result, ToolResult)
    assert result.text.startswith("[error:")
    assert "only available in the web UI" in result.text
