"""Tests for the server-side `/update` command handler (#981).

`/update` is a side-effect command intercepted in `_handle_send` before
command dispatch reaches the agent — it must run git/uv/systemctl update checks
with NO agent turn and NO archive write.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from decafclaw import archive as archive_mod
from decafclaw.self_update import UpdateResult
from decafclaw.web.conversations import ConversationIndex
from decafclaw.web.websocket import _handle_send


class _FakeManager:
    """Spy manager: records enqueue_turn calls, provides a working emit()."""

    def __init__(self):
        self.enqueue_turn = AsyncMock()
        self.emitted = []
        self._conversations = {}

    async def emit(self, conv_id, event):
        self.emitted.append((conv_id, event))


class _Env:
    def __init__(self, config, manager):
        self.config = config
        self.manager = manager
        self.sent = []
        self.archive_appends = []
        self.index = ConversationIndex(config)
        self.state = {
            "config": config,
            "event_bus": None,
            "manager": manager,
        }

    async def _ws_send(self, msg):
        self.sent.append(msg)

    async def handle_send(self, msg):
        await _handle_send(self._ws_send, self.index, "testuser", msg, self.state)


@pytest.fixture
def update_send_env(config, monkeypatch):
    """Factory fixture: build a state dict + spies for `_handle_send`."""

    def _make():
        cfg = config
        cfg.agent_path.mkdir(parents=True, exist_ok=True)
        cfg.workspace_path.mkdir(parents=True, exist_ok=True)

        index = ConversationIndex(cfg)
        now = datetime.now(timezone.utc).isoformat()
        index._save(
            [
                {
                    "conv_id": "c1",
                    "user_id": "testuser",
                    "title": "Test Conversation",
                    "created_at": now,
                    "updated_at": now,
                    "archived": False,
                }
            ]
        )

        manager = _FakeManager()
        env = _Env(cfg, manager)

        orig_append_message = archive_mod.append_message

        def spy_append_message(config, conv_id, message):
            env.archive_appends.append((conv_id, message))
            return orig_append_message(config, conv_id, message)

        monkeypatch.setattr(archive_mod, "append_message", spy_append_message)

        return env

    return _make


@pytest.mark.asyncio
async def test_update_command_no_turn_no_archive(update_send_env, monkeypatch):
    env = update_send_env()

    async def fake_update(*, confirmed=False, manager=None, index=None, repo_dir=None, cmd_runner=None):
        return UpdateResult(ok=True, message="DecafClaw is already up to date.")

    monkeypatch.setattr("decafclaw.self_update.perform_self_update", fake_update)

    await env.handle_send({"conv_id": "c1", "text": "/update"})

    env.manager.enqueue_turn.assert_not_called()
    assert env.archive_appends == []
    assert any("already up to date" in (m.get("text") or "").lower() for m in env.sent)


@pytest.mark.asyncio
async def test_update_command_refuses_when_active_turn(update_send_env):
    env = update_send_env()
    from unittest.mock import MagicMock

    busy_state = MagicMock(busy=True, agent_task=None)
    env.manager._conversations = {"c1": busy_state}

    await env.handle_send({"conv_id": "c1", "text": "/update"})

    env.manager.enqueue_turn.assert_not_called()
    assert env.archive_appends == []
    assert any("active turn(s)" in (m.get("text") or "").lower() for m in env.sent)


@pytest.mark.asyncio
async def test_update_command_confirm_passed(update_send_env, monkeypatch):
    env = update_send_env()

    confirmed_values = []

    async def fake_update(*, confirmed=False, manager=None, index=None, repo_dir=None, cmd_runner=None):
        confirmed_values.append(confirmed)
        return UpdateResult(ok=True, message="Restarting DecafClaw service...", restarted=True)

    monkeypatch.setattr("decafclaw.self_update.perform_self_update", fake_update)

    await env.handle_send({"conv_id": "c1", "text": "/update confirm"})

    assert confirmed_values == [True]
    env.manager.enqueue_turn.assert_not_called()
    assert env.archive_appends == []
    assert any("restarting" in (m.get("text") or "").lower() for m in env.sent)


@pytest.mark.asyncio
async def test_update_bang_syntax_handled(update_send_env, monkeypatch):
    env = update_send_env()

    async def fake_update(*, confirmed=False, manager=None, index=None, repo_dir=None, cmd_runner=None):
        return UpdateResult(ok=True, message="DecafClaw is already up to date.")

    monkeypatch.setattr("decafclaw.self_update.perform_self_update", fake_update)

    await env.handle_send({"conv_id": "c1", "text": "!update"})

    env.manager.enqueue_turn.assert_not_called()
    assert env.archive_appends == []
    assert any("already up to date" in (m.get("text") or "").lower() for m in env.sent)


@pytest.mark.asyncio
async def test_updatefoo_does_not_match(update_send_env):
    """Guard: '/updatefoo' must NOT be intercepted as '/update'."""
    env = update_send_env()
    await env.handle_send({"conv_id": "c1", "text": "/updatefoo"})
    # It falls through to command dispatch / normal message handling
    assert not any("already up to date" in (m.get("text") or "").lower() for m in env.sent)
