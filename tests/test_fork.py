"""Fork extraction, briefs, orchestration, and tool tests (#1570)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from decafclaw.archive import append_message, read_archive
from decafclaw.dissect import build_dissection_brief, find_dissection_focus
from decafclaw.fork import (
    build_fork_brief,
    find_fork_focus,
    normalize_fork_template,
)
from decafclaw.media import ToolResult
from decafclaw.tools.conversation_tools import tool_conversation_fork
from decafclaw.web.conversations import ConversationIndex
from decafclaw.web.forking import ForkError, create_fork, resolve_conversation_model


def _seed_exchange(config, conv_id: str, prompt: str, reply: str):
    append_message(config, conv_id, {"role": "user", "content": prompt})
    append_message(config, conv_id, {"role": "assistant", "content": reply})


def _seed_failure(
    config,
    conv_id: str,
    *,
    user_text: str = "Fix the timeout",
    error: str = "[error: timed out]",
    call_id: str = "call-1",
):
    append_message(config, conv_id, {"role": "user", "content": user_text})
    append_message(
        config,
        conv_id,
        {
            "role": "assistant",
            "content": "Running the tests.",
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": "shell_exec", "arguments": '{"command": "pytest -q"}'},
                }
            ],
        },
    )
    append_message(config, conv_id, {"role": "tool", "tool_call_id": call_id, "content": error})


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

    focus = find_fork_focus(messages)

    assert focus is not None
    assert focus.tool_name == "shell_exec"
    assert '"pytest -q"' in focus.arguments
    assert focus.error == "[error: timed out after 180s]"
    assert (focus.user_index, focus.assistant_index, focus.tool_index) == (0, 1, 2)


def test_focus_named_call_beats_a_newer_error():
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

    focus = find_fork_focus(messages, "old")

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

    focus = find_fork_focus(messages, "ok")

    assert focus is not None
    assert focus.tool_name == "read"
    assert focus.has_error is False
    brief = build_dissection_brief(messages, focus, source_conv_id="parent")
    assert "file contents" in brief
    assert "No failing tool result was recorded" in brief
    assert "What failed" not in brief


def test_focus_does_not_hijack_an_older_error_in_a_new_turn():
    """#1570: an empty tool_call_id only considers the latest exchange."""
    messages = [
        {"role": "user", "content": "Old prompt"},
        {
            "role": "assistant",
            "tool_calls": [{"id": "old", "function": {"name": "shell", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "old", "content": "[error: old failure]"},
        {"role": "user", "content": "Actually, compare two API designs"},
        {"role": "assistant", "content": "Here are two approaches."},
    ]

    focus = find_dissection_focus(messages)

    assert focus is not None
    assert focus.has_error is False
    assert focus.user_index == 3
    assert focus.assistant_index == 4
    assert focus.tool_name == ""
    # The explicit id still reaches the older failure (errored tool card).
    old_focus = find_fork_focus(messages, "old")
    assert old_focus is not None
    assert old_focus.error == "[error: old failure]"


def test_focus_finds_an_error_inside_the_latest_turn():
    messages = [
        {"role": "user", "content": "Old prompt"},
        {"role": "assistant", "content": "Old answer"},
        {"role": "user", "content": "Now run the tests"},
        {
            "role": "assistant",
            "tool_calls": [{"id": "new", "function": {"name": "shell", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "new", "content": "[error: new failure]"},
    ]

    focus = find_fork_focus(messages)

    assert focus is not None
    assert focus.has_error is True
    assert focus.tool_name == "shell"
    assert focus.user_index == 2


def test_focus_ignores_synthetic_user_messages():
    messages = [
        {"role": "user", "content": "real prompt"},
        {"role": "user", "content": "[System Update: x]", "source": "system_update"},
        {"role": "assistant", "content": "reply"},
    ]

    focus = find_fork_focus(messages)

    assert focus is not None
    assert focus.user_index == 0


def test_general_brief_quotes_recent_exchanges_and_avoids_code_assumptions():
    messages = [
        {"role": "user", "content": "First idea we discussed long ago"},
        {"role": "assistant", "content": "old reply"},
        {"role": "user", "content": "Compare the two upload designs"},
        {"role": "assistant", "content": "Design A is simpler."},
        {"role": "user", "content": "What about offline retries?"},
        {"role": "assistant", "content": "A queue would help."},
        {"role": "user", "content": "[System Update: runtime notice]", "source": "system_update"},
    ]

    brief = build_fork_brief(
        messages,
        source_conv_id="web-testuser-abc",
        source_title="Upload design",
        instruction="Explore the queue approach.",
    )

    assert "Upload design" in brief
    assert "web-testuser-abc" in brief
    assert "First idea we discussed long ago" not in brief
    assert "Compare the two upload designs" in brief
    assert "What about offline retries?" in brief
    assert "runtime notice" not in brief
    assert "Explore the queue approach." in brief
    assert "conversation_search" in brief
    # A general fork must not presume code work or a debugging workflow.
    for term in ("worktree", "postmortem", "pull request", "root cause"):
        assert term not in brief


def test_general_brief_has_a_neutral_default_instruction():
    brief = build_fork_brief(
        [{"role": "user", "content": "An idea"}, {"role": "assistant", "content": "A reply"}],
        source_conv_id="web-testuser-abc",
    )

    assert "Explore the most recent thread in this fork." in brief
    assert "worktree" not in brief


def test_dissection_brief_quotes_failure_and_debug_instructions():
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


def test_normalize_fork_template_defaults_and_rejects_unknown():
    assert normalize_fork_template("") == "general"
    assert normalize_fork_template(None) == "general"
    assert normalize_fork_template("DISSECT") == "dissect"
    with pytest.raises(ValueError, match="Unknown fork template"):
        normalize_fork_template("review")


# -- orchestration ------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_general_fork_seeds_child_notice_and_model(config):
    manager = _FakeManager()
    index = ConversationIndex(config)
    parent = index.create("testuser", "Upload design")
    append_message(config, parent.conv_id, {"role": "model", "content": "test-model"})
    _seed_exchange(config, parent.conv_id, "Compare two upload designs", "Design A is simpler.")
    _seed_exchange(config, parent.conv_id, "What about offline retries?", "A queue would help.")

    result = await create_fork(
        config,
        parent.conv_id,
        "testuser",
        manager=manager,
        instruction="Explore the queue approach.",
    )

    child = index.get(result["conv_id"])
    assert child is not None
    assert child.forked_from == parent.conv_id
    assert child.title == "Fork: Upload design"
    assert child.custom_title is True
    assert result["template"] == "general"

    assert len(manager.sent) == 1
    sent = manager.sent[0]
    assert sent["conv_id"] == child.conv_id
    assert "Explore the queue approach." in sent["text"]
    assert "What about offline retries?" in sent["text"]
    assert sent["user_id"] == "testuser"
    assert "worktree" not in sent["text"]

    assert any(
        m.get("role") == "model" and m.get("content") == "test-model" for m in read_archive(config, child.conv_id)
    )

    notices = [m for m in read_archive(config, parent.conv_id) if m.get("role") == "fork_notice"]
    assert len(notices) == 1
    assert notices[0]["data"]["fork_conv_id"] == child.conv_id
    assert notices[0]["data"]["template"] == "general"
    assert notices[0]["data"]["tool"] == ""
    assert manager.emitted == [
        (
            parent.conv_id,
            {
                "type": "fork_notice",
                "fork_conv_id": child.conv_id,
                "fork_title": "Fork: Upload design",
                "source_conv_id": parent.conv_id,
                "template": "general",
                "tool": "",
                "had_error": False,
            },
        )
    ]


@pytest.mark.asyncio
async def test_create_dissection_fork_seeds_failure_notice_and_model(config):
    manager = _FakeManager()
    index = ConversationIndex(config)
    parent = index.create("testuser", "Browser flake")
    append_message(config, parent.conv_id, {"role": "model", "content": "test-model"})
    _seed_failure(config, parent.conv_id)

    result = await create_fork(
        config,
        parent.conv_id,
        "testuser",
        manager=manager,
        template="dissect",
        focus_tool_call_id="call-1",
        instruction="Find the root cause.",
    )

    child = index.get(result["conv_id"])
    assert child is not None
    assert child.forked_from == parent.conv_id
    assert child.title == "Dissect: Browser flake"
    assert result["template"] == "dissect"

    sent = manager.sent[0]
    assert "Find the root cause." in sent["text"]
    assert "What failed" in sent["text"]
    assert "[error: timed out]" in sent["text"]
    assert "postmortem" in sent["text"]

    notices = [m for m in read_archive(config, parent.conv_id) if m.get("role") == "fork_notice"]
    assert len(notices) == 1
    assert notices[0]["data"]["template"] == "dissect"
    assert notices[0]["data"]["tool"] == "shell_exec"
    assert manager.emitted == [
        (
            parent.conv_id,
            {
                "type": "fork_notice",
                "fork_conv_id": child.conv_id,
                "fork_title": "Dissect: Browser flake",
                "source_conv_id": parent.conv_id,
                "template": "dissect",
                "tool": "shell_exec",
                "had_error": True,
            },
        )
    ]


@pytest.mark.asyncio
async def test_create_dissection_falls_back_to_general_without_a_latest_error(config):
    manager = _FakeManager()
    index = ConversationIndex(config)
    parent = index.create("testuser", "Design thread")
    _seed_failure(config, parent.conv_id)
    _seed_exchange(config, parent.conv_id, "Actually, compare two approaches", "Here are two options.")

    result = await create_fork(
        config,
        parent.conv_id,
        "testuser",
        manager=manager,
        template="dissect",
    )

    assert result["template"] == "general"
    child = index.get(result["conv_id"])
    assert child is not None
    assert child.title == "Fork: Design thread"
    sent = manager.sent[0]["text"]
    assert "What failed" not in sent
    assert "[error: timed out]" not in sent
    assert "Actually, compare two approaches" in sent

    notice = next(m for m in read_archive(config, parent.conv_id) if m.get("role") == "fork_notice")
    assert notice["data"]["template"] == "general"
    assert notice["data"]["tool"] == ""


@pytest.mark.asyncio
async def test_create_fork_rejects_an_empty_conversation(config):
    manager = _FakeManager()
    index = ConversationIndex(config)
    parent = index.create("testuser", "Empty")

    with pytest.raises(ForkError, match="no messages"):
        await create_fork(config, parent.conv_id, "testuser", manager=manager)
    assert manager.sent == []


@pytest.mark.asyncio
async def test_create_fork_rejects_an_unknown_template(config):
    manager = _FakeManager()
    index = ConversationIndex(config)
    parent = index.create("testuser", "Thread")
    _seed_exchange(config, parent.conv_id, "hello", "hi")

    with pytest.raises(ForkError, match="Unknown fork template"):
        await create_fork(config, parent.conv_id, "testuser", manager=manager, template="review")
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
async def test_conversation_fork_tool_starts_a_general_fork(config):
    manager = _FakeManager()
    index = ConversationIndex(config)
    parent = index.create("testuser", "Upload design")
    _seed_exchange(config, parent.conv_id, "Compare two designs", "Design A.")
    ctx = SimpleNamespace(
        config=config,
        conv_id=parent.conv_id,
        user_id="testuser",
        transport="web",
        manager=manager,
        active_model="",
    )

    result = await tool_conversation_fork(ctx, instruction="Explore the queue approach")

    assert isinstance(result, ToolResult)
    assert "Fork started" in result.text
    assert "?conv=" in result.text
    assert result.data["forked_from"] == parent.conv_id
    assert result.data["template"] == "general"
    assert len(manager.sent) == 1
    assert "Explore the queue approach" in manager.sent[0]["text"]


@pytest.mark.asyncio
async def test_conversation_fork_tool_can_start_a_dissection(config):
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

    result = await tool_conversation_fork(
        ctx,
        instruction="Check the watchdog",
        tool_call_id="call-1",
        template="dissect",
    )

    assert isinstance(result, ToolResult)
    assert "Dissection conversation started" in result.text
    assert result.data["template"] == "dissect"
    assert "[error: timed out]" in manager.sent[0]["text"]


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
