"""Server-side `/fork`, `/branch`, and `/dissect` command tests (#1570)."""

from __future__ import annotations

import pytest

from decafclaw.archive import append_message
from decafclaw.events import EventBus
from decafclaw.web.conversations import ConversationIndex
from decafclaw.web.message_types import WSMessageType
from decafclaw.web.websocket import _handle_send


class _FakeManager:
    """Records turns/emits and dispatches emits to subscribed sockets."""

    def __init__(self):
        self.sent: list[dict] = []
        self.emitted: list[tuple[str, dict]] = []
        self.subscriptions: dict[str, dict[str, object]] = {}

    async def send_message(self, conv_id, text, **kwargs):
        self.sent.append({"conv_id": conv_id, "text": text, **kwargs})

    async def emit(self, conv_id, event):
        self.emitted.append((conv_id, event))
        payload = {**event, "conv_id": conv_id}
        for callback in list(self.subscriptions.get(conv_id, {}).values()):
            await callback(payload)  # type: ignore[misc]

    def get_state(self, conv_id):
        return None

    def subscribe(self, conv_id, callback):
        sub_id = f"sub-{len(self.subscriptions.get(conv_id, {}))}"
        self.subscriptions.setdefault(conv_id, {})[sub_id] = callback
        return sub_id

    def unsubscribe(self, conv_id, subscription_id):
        self.subscriptions.get(conv_id, {}).pop(subscription_id, None)


class _Env:
    def __init__(self, config, manager):
        self.config = config
        self.manager = manager
        self.index = ConversationIndex(config)
        self.sent: list[dict] = []
        self.state = {
            "config": config,
            "manager": manager,
            "event_bus": EventBus(),
            "ws_send": self._ws_send,
        }

    async def _ws_send(self, msg):
        self.sent.append(msg)

    async def send(self, conv_id, text, username="testuser"):
        await _handle_send(
            self._ws_send,
            self.index,
            username,
            {"conv_id": conv_id, "text": text},
            self.state,
        )


def _seed_exchange(config, conv_id: str, prompt: str, reply: str):
    append_message(config, conv_id, {"role": "user", "content": prompt})
    append_message(config, conv_id, {"role": "assistant", "content": reply})


def _seed_failure(config, conv_id: str, *, call_id: str = "call-1", error: str = "[error: command timed out]"):
    append_message(config, conv_id, {"role": "user", "content": "Fix the flaky test"})
    append_message(
        config,
        conv_id,
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": call_id,
                    "function": {"name": "shell", "arguments": '{"command": "make test"}'},
                }
            ],
        },
    )
    append_message(config, conv_id, {"role": "tool", "tool_call_id": call_id, "content": error})


@pytest.fixture
def fork_env(config):
    config.agent_path.mkdir(parents=True, exist_ok=True)
    return _Env(config, _FakeManager())


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["/fork", "!fork", "/branch", "!branch"])
async def test_fork_commands_start_a_general_child_and_notify_parent(fork_env, command):
    env = fork_env
    parent = env.index.create("testuser", "Design thread")
    _seed_exchange(env.config, parent.conv_id, "Compare two designs", "Design A.")

    await env.send(parent.conv_id, f"{command} explore the queue approach")

    # No parent agent turn was enqueued; the only turn belongs to the child.
    assert len(env.manager.sent) == 1
    child_id = env.manager.sent[0]["conv_id"]
    assert child_id != parent.conv_id
    child = env.index.get(child_id)
    assert child is not None
    assert child.forked_from == parent.conv_id
    assert child.title == "Fork: Design thread"
    assert "explore the queue approach" in env.manager.sent[0]["text"]
    assert "worktree" not in env.manager.sent[0]["text"]

    notices = [m for m in env.sent if m.get("type") == WSMessageType.FORK_NOTICE]
    assert len(notices) == 1
    assert notices[0]["conv_id"] == parent.conv_id
    assert notices[0]["fork_conv_id"] == child_id
    assert notices[0]["template"] == "general"

    assert not any(m.get("type") == WSMessageType.ERROR for m in env.sent)


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["/dissect", "!dissect"])
async def test_dissect_commands_start_a_failure_fork(fork_env, command):
    env = fork_env
    parent = env.index.create("testuser", "Flaky test")
    _seed_failure(env.config, parent.conv_id)

    await env.send(parent.conv_id, f"{command} focus on the timeout")

    assert len(env.manager.sent) == 1
    child_id = env.manager.sent[0]["conv_id"]
    child = env.index.get(child_id)
    assert child is not None
    assert child.forked_from == parent.conv_id
    assert child.title == "Dissect: Flaky test"
    assert "focus on the timeout" in env.manager.sent[0]["text"]
    assert "[error: command timed out]" in env.manager.sent[0]["text"]

    notices = [m for m in env.sent if m.get("type") == WSMessageType.FORK_NOTICE]
    assert len(notices) == 1
    assert notices[0]["template"] == "dissect"
    assert notices[0]["tool"] == "shell"
    assert notices[0]["had_error"] is True


@pytest.mark.asyncio
async def test_dissect_command_does_not_hijack_an_older_error(fork_env):
    """A failure from a previous turn must not become the new fork's subject."""
    env = fork_env
    parent = env.index.create("testuser", "Design thread")
    _seed_failure(env.config, parent.conv_id)
    _seed_exchange(env.config, parent.conv_id, "Actually, compare two approaches", "Here are two options.")

    await env.send(parent.conv_id, "/dissect")

    assert len(env.manager.sent) == 1
    child_id = env.manager.sent[0]["conv_id"]
    child = env.index.get(child_id)
    assert child is not None
    assert child.title == "Fork: Design thread"
    assert "[error: command timed out]" not in env.manager.sent[0]["text"]
    assert "What failed" not in env.manager.sent[0]["text"]

    notice = next(m for m in env.sent if m.get("type") == WSMessageType.FORK_NOTICE)
    assert notice["template"] == "general"
    assert notice["tool"] == ""


@pytest.mark.asyncio
async def test_fork_command_stays_general_even_when_the_latest_turn_failed(fork_env):
    env = fork_env
    parent = env.index.create("testuser", "Flaky test")
    _seed_failure(env.config, parent.conv_id)

    await env.send(parent.conv_id, "/fork compare recovery options")

    assert len(env.manager.sent) == 1
    assert "What failed" not in env.manager.sent[0]["text"]
    notice = next(m for m in env.sent if m.get("type") == WSMessageType.FORK_NOTICE)
    assert notice["template"] == "general"


@pytest.mark.asyncio
async def test_fork_command_reports_an_empty_conversation(fork_env):
    env = fork_env
    parent = env.index.create("testuser", "Empty")

    await env.send(parent.conv_id, "/fork")

    assert env.manager.sent == []
    assert any(m.get("type") == WSMessageType.ERROR and "no messages" in m.get("message", "") for m in env.sent)


@pytest.mark.asyncio
async def test_fork_command_refuses_another_users_conversation(fork_env):
    env = fork_env
    parent = env.index.create("otheruser", "Private")
    _seed_exchange(env.config, parent.conv_id, "hello", "hi")

    await env.send(parent.conv_id, "/fork", username="testuser")

    assert env.manager.sent == []
    assert any(
        m.get("type") == WSMessageType.ERROR and "Conversation not found" in m.get("message", "") for m in env.sent
    )
