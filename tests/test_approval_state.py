"""Durable per-conversation shell approval state across restarts (#1160)."""

import json
from dataclasses import fields
from unittest.mock import patch

import pytest

from decafclaw.approval_state import (
    ApprovalState,
    load_approval_state,
    save_approval_state,
)
from decafclaw.conversation_manager import ConversationManager, PersistedTurnState, TurnKind
from decafclaw.conversation_paths import approval_state_path, delete_conversation_files
from decafclaw.events import EventBus
from decafclaw.media import ToolResult
from decafclaw.tools.shell_tools import resolve_aux_approval_guidance

RULE = "Auto-approve `make test` in this repo"


def _new_manager(config) -> ConversationManager:
    """A fresh manager on the same data dir — simulates a server restart."""
    return ConversationManager(config, EventBus())


async def _run_turn(monkeypatch, manager, conv_id, body):
    """Run one USER turn whose agent body is ``body(ctx)``; return the ctx."""
    seen = {}

    async def fake_run_agent_turn(ctx, user_message, history, **kwargs):
        seen["ctx"] = ctx
        body(ctx)
        return ToolResult(text="ok")

    monkeypatch.setattr("decafclaw.agent.run_agent_turn", fake_run_agent_turn)
    future = await manager.enqueue_turn(conv_id=conv_id, kind=TurnKind.USER, prompt="hi", user_id="u")
    await future
    return seen["ctx"]


def _remember_rule_and_toggle_preset(ctx):
    # What "Approve + remember why" and the shell_guidance actions do.
    ctx.tools.aux_approval_guidance.append(RULE)
    ctx.tools.disabled_aux_approval_presets = ["github"]
    ctx.tools.llm_approved_shell_patterns = ["make test"]


@pytest.mark.asyncio
async def test_restart_round_trip_restores_rules_and_presets(config, monkeypatch):
    first = await _run_turn(monkeypatch, _new_manager(config), "conv-a", _remember_rule_and_toggle_preset)
    expected_guidance = resolve_aux_approval_guidance(first)
    assert RULE in expected_guidance

    captured = {}

    def capture(ctx):
        captured["guidance"] = resolve_aux_approval_guidance(ctx)
        captured["disabled"] = list(ctx.tools.disabled_aux_approval_presets)
        captured["cache"] = list(ctx.tools.llm_approved_shell_patterns)

    await _run_turn(monkeypatch, _new_manager(config), "conv-a", capture)

    assert captured["guidance"] == expected_guidance
    assert captured["disabled"] == ["github"]
    # Reviewer approval cache is deliberately not persisted.
    assert captured["cache"] == []


@pytest.mark.asyncio
async def test_mode_restored_by_manager_without_web_history_load(config, monkeypatch):
    manager = _new_manager(config)
    manager.set_flag("conv-a", "active_mode", "dev")
    manager.set_flag("conv-a", "active_aux_approval_presets", ["developer", "github"])

    # run_agent_turn is faked, so agent.py's archive mode scan never runs;
    # only the manager's sidecar restore can supply the mode here.
    ctx = await _run_turn(monkeypatch, _new_manager(config), "conv-a", lambda ctx: None)

    assert ctx.active_mode == "dev"
    assert ctx.tools.active_aux_approval_presets == ["developer", "github"]


@pytest.mark.asyncio
async def test_corrupt_sidecar_fails_open_to_defaults(config, monkeypatch):
    path = approval_state_path(config, "conv-a")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")

    ctx = await _run_turn(monkeypatch, _new_manager(config), "conv-a", lambda ctx: None)

    assert ctx.active_mode == "default"
    assert ctx.tools.aux_approval_guidance == []
    assert ctx.tools.disabled_aux_approval_presets == []


@pytest.mark.asyncio
async def test_rules_do_not_leak_between_conversations(config, monkeypatch):
    await _run_turn(monkeypatch, _new_manager(config), "conv-a", _remember_rule_and_toggle_preset)

    ctx_b = await _run_turn(monkeypatch, _new_manager(config), "conv-b", lambda ctx: None)

    assert RULE not in resolve_aux_approval_guidance(ctx_b)
    assert ctx_b.tools.disabled_aux_approval_presets == []
    assert not approval_state_path(config, "conv-b").exists()


@pytest.mark.asyncio
async def test_delete_conversation_removes_sidecar(config, monkeypatch):
    await _run_turn(monkeypatch, _new_manager(config), "conv-a", _remember_rule_and_toggle_preset)
    path = approval_state_path(config, "conv-a")
    assert path.exists()

    delete_conversation_files(config, "conv-a")

    assert not path.exists()


def test_sidecar_lives_outside_agent_writable_workspace(config):
    path = approval_state_path(config, "conv-a")
    assert not path.is_relative_to(config.workspace_path.resolve())
    assert path.is_relative_to(config.agent_path.resolve())


@pytest.mark.asyncio
async def test_unchanged_state_skips_write(config, monkeypatch):
    manager = _new_manager(config)
    with patch("decafclaw.conversation_manager.save_approval_state", wraps=save_approval_state) as spy:
        # Default state on a conversation with no sidecar: no file at all.
        await _run_turn(monkeypatch, manager, "conv-a", lambda ctx: None)
        assert spy.call_count == 0
        assert not approval_state_path(config, "conv-a").exists()

        await _run_turn(monkeypatch, manager, "conv-a", _remember_rule_and_toggle_preset)
        assert spy.call_count == 1

        # A turn that changes nothing writes nothing.
        await _run_turn(monkeypatch, manager, "conv-a", lambda ctx: None)
        assert spy.call_count == 1


def test_load_ignores_unknown_keys_and_defaults_missing_or_invalid(config):
    path = approval_state_path(config, "conv-a")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "aux_approval_guidance": [RULE, 42],
                "active_aux_approval_presets": "developer",
                "active_mode": 7,
                "from_the_future": True,
            }
        ),
        encoding="utf-8",
    )

    loaded = load_approval_state(config, "conv-a")

    assert loaded == ApprovalState(aux_approval_guidance=[RULE])


def test_load_non_object_returns_none(config):
    path = approval_state_path(config, "conv-a")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[]", encoding="utf-8")

    assert load_approval_state(config, "conv-a") is None


def test_approval_fields_mirror_persisted_turn_state():
    persisted = {f.name for f in fields(PersistedTurnState)}
    for f in fields(ApprovalState):
        assert f.name in persisted
    assert "llm_approved_shell_patterns" not in {f.name for f in fields(ApprovalState)}
