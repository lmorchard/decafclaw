"""Server-side `/dissect` command tests (#1570)."""

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


@pytest.fixture
def dissect_env(config):
    config.agent_path.mkdir(parents=True, exist_ok=True)
    return _Env(config, _FakeManager())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command,instruction", [("/dissect", ""), ("!dissect focus on the timeout", "focus on the timeout")]
)
async def test_dissect_command_starts_a_child_and_notifies_parent(dissect_env, command, instruction):
    env = dissect_env
    parent = env.index.create("testuser", "Flaky test")
    _seed_failure(env.config, parent.conv_id)

    await env.send(parent.conv_id, command)

    # No parent agent turn was enqueued; the only turn belongs to the child.
    assert len(env.manager.sent) == 1
    child_id = env.manager.sent[0]["conv_id"]
    assert child_id != parent.conv_id
    child = env.index.get(child_id)
    assert child is not None
    assert child.forked_from == parent.conv_id
    if instruction:
        assert instruction in env.manager.sent[0]["text"]

    # The still-subscribed parent socket receives the link card.
    notices = [m for m in env.sent if m.get("type") == WSMessageType.DISSECT_NOTICE]
    assert len(notices) == 1
    assert notices[0]["conv_id"] == parent.conv_id
    assert notices[0]["fork_conv_id"] == child_id
    assert notices[0]["tool"] == "shell"

    assert not any(m.get("type") == WSMessageType.ERROR for m in env.sent)


@pytest.mark.asyncio
async def test_dissect_command_reports_an_empty_conversation(dissect_env):
    env = dissect_env
    parent = env.index.create("testuser", "Empty")

    await env.send(parent.conv_id, "/dissect")

    assert env.manager.sent == []
    assert any(m.get("type") == WSMessageType.ERROR and "no messages" in m.get("message", "") for m in env.sent)


@pytest.mark.asyncio
async def test_dissect_command_refuses_another_users_conversation(dissect_env):
    env = dissect_env
    parent = env.index.create("otheruser", "Private")
    _seed_failure(env.config, parent.conv_id)

    await env.send(parent.conv_id, "/dissect", username="testuser")

    assert env.manager.sent == []
    assert any(
        m.get("type") == WSMessageType.ERROR and "Conversation not found" in m.get("message", "") for m in env.sent
    )
