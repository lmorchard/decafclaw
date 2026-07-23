"""Tests for the server-side `/terminal` command handler (#442).

`/terminal` is a side-effect command intercepted in `_handle_send` before
command dispatch reaches the agent — it must spawn a PTY + create a canvas
tab with NO agent turn and NO archive write.
"""

import dataclasses
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from decafclaw import archive as archive_mod
from decafclaw import canvas as canvas_mod
from decafclaw import widgets as widgets_module
from decafclaw.terminals import TerminalRegistry, TerminalSession
from decafclaw.web.conversations import ConversationIndex
from decafclaw.web.websocket import _handle_send


class _FakeManager:
    """Spy manager: records enqueue_turn calls, provides a working emit()."""

    def __init__(self):
        self.enqueue_turn = AsyncMock()
        self.emitted = []

    async def emit(self, conv_id, event):
        self.emitted.append((conv_id, event))


class _Env:
    def __init__(self, config, manager, registry):
        self.config = config
        self.manager = manager
        self.registry = registry
        self.sent = []
        self.new_tab_calls = []
        self.archive_appends = []
        self.index = ConversationIndex(config)
        self.state = {
            "config": config,
            "event_bus": None,
            "manager": manager,
            "terminal_registry": registry,
        }

    async def _ws_send(self, msg):
        self.sent.append(msg)

    async def handle_send(self, msg):
        await _handle_send(self._ws_send, self.index, "testuser", msg, self.state)


@pytest.fixture
def terminal_send_env(config, monkeypatch, tmp_path):
    """Factory fixture: build a state dict + spies for `_handle_send`.

    ``config.terminal`` overrides are passed as kwargs (``enabled``,
    ``allowed_cwd_roots``, ``max_sessions_per_conv``). The conversation
    index is seeded with conv_id="c1" owned by "testuser". `canvas.new_tab`
    and `archive.append_message` are wrapped (not replaced) so real state
    mutation still happens but calls are also recorded. `TerminalRegistry.spawn`
    is monkeypatched to an async no-op that records a fake session into the
    registry's internal table, so no real PTY / subprocess is spawned.
    """

    def _make(**terminal_overrides):
        cfg = config
        cfg.agent_path.mkdir(parents=True, exist_ok=True)
        cfg.workspace_path.mkdir(parents=True, exist_ok=True)
        cfg.terminal = dataclasses.replace(cfg.terminal, **terminal_overrides)

        # `canvas.new_tab` validates widget_type against the global widget
        # registry (populated at app startup in production) — load the real
        # bundled catalog (includes "terminal") so validation passes.
        registry_widgets = widgets_module.load_widget_registry(cfg)
        monkeypatch.setattr(widgets_module, "_registry", registry_widgets)

        # Seed the conversation index directly (bypass index.create()'s
        # random conv_id so tests can address it as "c1").
        index = ConversationIndex(cfg)
        now = datetime.now(timezone.utc).isoformat()
        index._save([{
            "conv_id": "c1", "user_id": "testuser", "title": "t",
            "created_at": now, "updated_at": now, "archived": False,
        }])

        manager = _FakeManager()
        registry = TerminalRegistry(cfg)

        spawn_calls = []

        async def fake_spawn(conv_id, tab_id, session_id, cwd, shell):
            spawn_calls.append({
                "conv_id": conv_id, "tab_id": tab_id,
                "session_id": session_id, "cwd": cwd, "shell": shell,
            })
            session = TerminalSession(
                conv_id=conv_id, tab_id=tab_id, session_id=session_id,
                cwd=cwd, shell=shell, pid=-1, fd=-1,
            )
            registry._sessions[(conv_id, tab_id)] = session
            return session

        monkeypatch.setattr(registry, "spawn", fake_spawn)

        env = _Env(cfg, manager, registry)
        env.spawn_calls = spawn_calls

        orig_new_tab = canvas_mod.new_tab

        async def spy_new_tab(config, conv_id, widget_type, data, label=None, emit=None):
            env.new_tab_calls.append({
                "conv_id": conv_id, "widget_type": widget_type,
                "data": data, "label": label,
            })
            return await orig_new_tab(config, conv_id, widget_type, data,
                                      label=label, emit=emit)

        monkeypatch.setattr(canvas_mod, "new_tab", spy_new_tab)

        orig_append_message = archive_mod.append_message

        def spy_append_message(config, conv_id, message):
            env.archive_appends.append((conv_id, message))
            return orig_append_message(config, conv_id, message)

        monkeypatch.setattr(archive_mod, "append_message", spy_append_message)

        return env

    return _make


@pytest.mark.asyncio
async def test_terminal_command_spawns_and_creates_tab(terminal_send_env):
    env = terminal_send_env()
    await env.handle_send({"conv_id": "c1", "text": "/terminal"})
    assert env.registry.count_for_conv("c1") == 1
    assert env.new_tab_calls and env.new_tab_calls[0]["widget_type"] == "terminal"
    assert any(m["type"] == "command_ack" for m in env.sent)


@pytest.mark.asyncio
async def test_terminal_command_no_turn_no_archive(terminal_send_env):
    env = terminal_send_env()
    await env.handle_send({"conv_id": "c1", "text": "/terminal"})
    env.manager.enqueue_turn.assert_not_called()  # load-bearing invariant
    assert env.archive_appends == []              # no archive write


@pytest.mark.asyncio
async def test_terminal_command_disabled_returns_message(terminal_send_env):
    env = terminal_send_env(enabled=False)
    await env.handle_send({"conv_id": "c1", "text": "/terminal"})
    assert env.registry.count_for_conv("c1") == 0
    assert any("disabled" in (m.get("text") or "").lower() for m in env.sent)


@pytest.mark.asyncio
async def test_terminal_command_rejects_cwd_outside_roots(terminal_send_env):
    env = terminal_send_env(allowed_cwd_roots=["/tmp"])
    await env.handle_send({"conv_id": "c1", "text": "/terminal /etc"})
    assert env.registry.count_for_conv("c1") == 0
    assert any("not allowed" in (m.get("text") or "").lower() for m in env.sent)


@pytest.mark.asyncio
async def test_terminal_command_session_cap(terminal_send_env):
    env = terminal_send_env(max_sessions_per_conv=1)
    await env.handle_send({"conv_id": "c1", "text": "/terminal"})
    await env.handle_send({"conv_id": "c1", "text": "/terminal"})
    assert env.registry.count_for_conv("c1") == 1
    assert any("max session" in (m.get("text") or "").lower() for m in env.sent)


@pytest.mark.asyncio
async def test_terminal_command_spawn_failure_cleans_up_tab(terminal_send_env, monkeypatch):
    """If `registry.spawn` raises after the tab was created, the handler
    must clean up the orphaned tab, send a rejection, and let no exception
    escape (#442 review finding: a spawn failure must not kill the WS)."""
    env = terminal_send_env()

    async def raising_spawn(conv_id, tab_id, session_id, cwd, shell):
        raise OSError("openpty failed")

    monkeypatch.setattr(env.registry, "spawn", raising_spawn)

    close_tab_calls = []
    orig_close_tab = canvas_mod.close_tab

    async def spy_close_tab(config, conv_id, tab_id, emit=None):
        close_tab_calls.append({"conv_id": conv_id, "tab_id": tab_id})
        return await orig_close_tab(config, conv_id, tab_id, emit=emit)

    monkeypatch.setattr(canvas_mod, "close_tab", spy_close_tab)

    # No exception should escape the handler.
    await env.handle_send({"conv_id": "c1", "text": "/terminal"})

    # The orphaned tab was created and then removed.
    assert env.new_tab_calls
    assert close_tab_calls
    assert close_tab_calls[0]["conv_id"] == "c1"
    assert close_tab_calls[0]["tab_id"] is not None

    # No lingering canvas tabs, no orphaned registry session.
    state = canvas_mod.read_canvas_state(env.config, "c1")
    assert state.get("tabs") == []
    assert env.registry.count_for_conv("c1") == 0

    # A rejection message was sent.
    assert any("could not start" in (m.get("text") or "").lower() for m in env.sent)

    # Invariants: no agent turn, no archive write, no COMMAND_ACK.
    env.manager.enqueue_turn.assert_not_called()
    assert env.archive_appends == []
    assert not any(m["type"] == "command_ack" for m in env.sent)


@pytest.mark.asyncio
async def test_terminal_command_new_tab_failure_rejects_no_spawn(terminal_send_env, monkeypatch):
    """If `canvas.new_tab` itself fails, the handler must reject cleanly
    without ever calling `spawn` (Minor review note: confirm this branch)."""
    env = terminal_send_env()

    async def failing_new_tab(config, conv_id, widget_type, data, label=None, emit=None):
        return canvas_mod.CanvasOpResult(ok=False, error="boom")

    monkeypatch.setattr(canvas_mod, "new_tab", failing_new_tab)

    await env.handle_send({"conv_id": "c1", "text": "/terminal"})

    assert env.spawn_calls == []
    assert env.registry.count_for_conv("c1") == 0
    assert any("could not open terminal tab" in (m.get("text") or "").lower() for m in env.sent)
    assert not any(m["type"] == "command_ack" for m in env.sent)
    env.manager.enqueue_turn.assert_not_called()
    assert env.archive_appends == []


@pytest.mark.asyncio
async def test_terminalfoo_does_not_match(terminal_send_env):
    """Guard: '/terminalfoo' must NOT be intercepted as '/terminal'."""
    env = terminal_send_env()
    await env.handle_send({"conv_id": "c1", "text": "/terminalfoo"})
    assert env.registry.count_for_conv("c1") == 0
    assert not env.new_tab_calls
