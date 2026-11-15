"""Scoped reminders (#1127) — the dispatch gate, budgets, and ephemeral injection.

The design lives in
``docs/dev-sessions/2026-11-13-1445-issue-1127-scoped-reminders-experiment/spec.md``.
This file pins the behavior that spec promises: predicates over finalized
arguments, reject-without-dispatch, remind-with-ephemeral-message, budgets that
stop repetition, durable match records, and fail-open parsing.
"""

import json
from unittest.mock import AsyncMock, patch

import pytest

from decafclaw import reminders
from decafclaw.config_types import RemindersConfig
from decafclaw.context import Context
from decafclaw.events import EventBus
from decafclaw.reminders import (
    clear_rules_cache,
    ledger_path,
    load_rules,
    reminder_message,
    rules_path,
)
from decafclaw.tools import execute_tool


@pytest.fixture(autouse=True)
def _clear_rule_cache():
    clear_rules_cache()
    yield
    clear_rules_cache()


@pytest.fixture
def enabled(ctx):
    """Enable the experiment on the shared ctx fixture."""
    ctx.config.reminders = RemindersConfig(enabled=True)
    return ctx


def _write_rules(config, rules):
    path = rules_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"rules": rules}), encoding="utf-8")
    clear_rules_cache()


def _register(ctx, name, fn):
    ctx.tools.extra[name] = fn


def _reject_rule(rule_id="no-rm-rf", tool="shell", **overrides):
    rule = {
        "id": rule_id,
        "tool": tool,
        "match": [{"path": "command", "pattern": r"rm\s+-rf\s+/"}],
        "action": "reject",
        "message": "Refusing: this would delete the filesystem root.",
    }
    rule.update(overrides)
    return rule


def _remind_rule(rule_id="remember-caps", tool="shell", **overrides):
    rule = {
        "id": rule_id,
        "tool": tool,
        "match": [{"path": "command", "contains": "make"}],
        "action": "remind",
        "message": "Remember: this repo runs checks with make check.",
    }
    rule.update(overrides)
    return rule


def _ledger_entries(config, conv_id="test-conv"):
    path = ledger_path(config, conv_id)
    assert path is not None and path.exists(), "expected a match ledger"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


# -- Predicates ---------------------------------------------------------------


def test_predicate_operators_cover_equals_contains_pattern_exists():
    args = {"command": "make check", "env": ["CI=1"], "nested": {"flag": True}}
    assert reminders._predicate_matches({"path": "command", "equals": "make check"}, args)
    assert not reminders._predicate_matches({"path": "command", "equals": "make"}, args)
    assert reminders._predicate_matches({"path": "command", "contains": "check"}, args)
    assert reminders._predicate_matches({"path": "env", "contains": "CI=1"}, args)
    assert reminders._predicate_matches({"path": "command", "pattern": r"^make\s"}, args)
    assert reminders._predicate_matches({"path": "nested.flag", "exists": True}, args)
    assert not reminders._predicate_matches({"path": "nested.missing", "exists": True}, args)


def test_value_predicate_never_matches_an_absent_path():
    """A missing argument is not a match: the gate must not remind on absence."""
    assert not reminders._predicate_matches({"path": "command", "contains": "xyz"}, {})
    assert not reminders._predicate_matches({"path": "command", "pattern": ".*"}, {})


def test_list_index_path_reaches_positional_arguments():
    args = {"argv": ["status", "--short"]}
    assert reminders._predicate_matches({"path": "argv.1", "equals": "--short"}, args)


def test_rule_predicates_are_anded_and_tool_must_match():
    rule = reminders.ReminderRule(
        id="r",
        tool="shell",
        match=(
            {"path": "command", "contains": "make"},
            {"path": "cwd", "equals": "/repo"},
        ),
        action="reject",
        message="m",
    )
    assert reminders._rule_matches(rule, "shell", {"command": "make test", "cwd": "/repo"})
    assert not reminders._rule_matches(rule, "shell", {"command": "make test", "cwd": "/elsewhere"})
    assert not reminders._rule_matches(rule, "workspace_write", {"command": "make test", "cwd": "/repo"})


def test_wildcard_tool_and_empty_match_cover_every_call():
    rule = reminders.ReminderRule(id="r", tool="*", match=(), action="remind", message="m")
    assert reminders._rule_matches(rule, "shell", {})
    assert reminders._rule_matches(rule, "workspace_write", {"path": "x"})


# -- Loading: fail open -------------------------------------------------------


def test_malformed_json_yields_no_rules(config):
    path = rules_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    clear_rules_cache()
    assert load_rules(config) == []


def test_one_bad_rule_does_not_disable_the_file(config):
    _write_rules(
        config,
        [
            {"id": "bad", "tool": "shell", "match": [{"path": "command", "explode": "x"}], "action": "reject"},
            _reject_rule(),
        ],
    )
    rules = load_rules(config)
    assert [rule.id for rule in rules] == ["no-rm-rf"]


@pytest.mark.parametrize(
    "bad_rule",
    [
        "not-an-object",
        {"id": "", "tool": "shell", "match": [], "action": "reject", "message": "m"},
        {"id": "x", "tool": "shell", "match": [], "action": "allow", "message": "m"},
        {"id": "x", "tool": "shell", "match": [{"path": "c", "pattern": "(["}], "action": "reject", "message": "m"},
        {"id": "x", "tool": "shell", "match": "nope", "action": "reject", "message": "m"},
    ],
)
def test_invalid_rules_are_skipped(config, bad_rule):
    _write_rules(config, [bad_rule])
    assert load_rules(config) == []


# -- Reject -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reject_stops_the_call_before_dispatch(enabled):
    calls = []

    async def _shell(ctx, command):
        calls.append(command)
        return "ran"

    _register(enabled, "shell", _shell)
    _write_rules(enabled.config, [_reject_rule()])

    result = await execute_tool(enabled, "shell", {"command": "rm -rf /"})

    assert result.text == "[error: Refusing: this would delete the filesystem root.]"
    assert calls == [], "a rejected call must not reach the tool"


@pytest.mark.asyncio
async def test_reject_records_event_and_ledger_entry(enabled):
    events = []
    enabled.event_bus.subscribe(events.append)
    _write_rules(enabled.config, [_reject_rule()])

    await execute_tool(enabled, "shell", {"command": "rm -rf /"})

    [entry] = _ledger_entries(enabled.config)
    assert entry["rule_id"] == "no-rm-rf"
    assert entry["tool"] == "shell"
    assert entry["action"] == "reject"
    assert entry["terse"] is False

    [event] = [e for e in events if e.get("type") == "scoped_reminder"]
    assert event["rule_id"] == "no-rm-rf"
    assert event["action"] == "reject"


@pytest.mark.asyncio
async def test_reject_turns_terse_after_the_message_budget(enabled):
    enabled.config.reminders = RemindersConfig(enabled=True, max_matches_per_rule=2)
    _write_rules(enabled.config, [_reject_rule()])

    messages = []
    for _ in range(3):
        result = await execute_tool(enabled, "shell", {"command": "rm -rf /"})
        messages.append(result.text)

    assert "Refusing" in messages[0] and "Refusing" in messages[1]
    assert "still blocks this call" in messages[2]
    assert "Refusing" not in messages[2]
    assert len(_ledger_entries(enabled.config)) == 3, "a reject rule never stops rejecting"


@pytest.mark.asyncio
async def test_reject_wins_over_remind_for_the_same_call(enabled):
    _write_rules(enabled.config, [_remind_rule(), _reject_rule(rule_id="also-reject", tool="shell", match=[])])

    result = await execute_tool(enabled, "shell", {"command": "make check"})

    assert result.text.startswith("[error:")
    assert enabled.reminder_hits == []


# -- Remind -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_remind_runs_the_call_and_queues_one_hit(enabled):
    calls = []

    async def _shell(ctx, command):
        calls.append(command)
        return "ran"

    _register(enabled, "shell", _shell)
    _write_rules(enabled.config, [_remind_rule()])

    result = await execute_tool(enabled, "shell", {"command": "make check"})

    assert result.text == "ran"
    assert calls == ["make check"]
    [hit] = enabled.reminder_hits
    assert hit.rule_id == "remember-caps"
    assert hit.action == "remind"
    [entry] = _ledger_entries(enabled.config)
    assert entry["action"] == "remind"


@pytest.mark.asyncio
async def test_remind_expires_after_the_match_budget(enabled):
    enabled.config.reminders = RemindersConfig(enabled=True, max_matches_per_rule=2, max_per_turn=99)
    _write_rules(enabled.config, [_remind_rule()])

    async def _stub(ctx, **kwargs):
        return "ok"

    _register(enabled, "shell", _stub)

    for _ in range(3):
        await execute_tool(enabled, "shell", {"command": "make check"})

    assert len(enabled.reminder_hits) == 2, "advisory reminders stop once repeated"
    assert len(_ledger_entries(enabled.config)) == 2, "no record for a suppressed reminder"


@pytest.mark.asyncio
async def test_per_turn_injection_cap_bounds_two_rules(enabled):
    enabled.config.reminders = RemindersConfig(enabled=True, max_matches_per_rule=99, max_per_turn=1)
    _write_rules(
        enabled.config,
        [
            _remind_rule(rule_id="r1", tool="shell", match=[]),
            _remind_rule(rule_id="r2", tool="workspace_write", match=[]),
        ],
    )

    async def _stub(ctx, **kwargs):
        return "ok"

    _register(enabled, "shell", _stub)
    _register(enabled, "workspace_write", _stub)

    await execute_tool(enabled, "shell", {"command": "x"})
    await execute_tool(enabled, "workspace_write", {"path": "y"})

    assert [hit.rule_id for hit in enabled.reminder_hits] == ["r1"]
    assert enabled.reminder_state.injections_this_turn == 1


@pytest.mark.asyncio
async def test_budget_survives_a_context_restart_via_the_ledger(enabled):
    """Match counts seed from the ledger, so a restart does not reset budgets."""
    enabled.config.reminders = RemindersConfig(enabled=True, max_matches_per_rule=1, max_per_turn=99)
    _write_rules(enabled.config, [_remind_rule()])

    async def _stub(ctx, **kwargs):
        return "ok"

    _register(enabled, "shell", _stub)
    await execute_tool(enabled, "shell", {"command": "make check"})
    assert len(enabled.reminder_hits) == 1

    restarted = Context(config=enabled.config, event_bus=EventBus())
    restarted.conv_id = enabled.conv_id
    _register(restarted, "shell", _stub)
    await execute_tool(restarted, "shell", {"command": "make check"})
    assert restarted.reminder_hits == [], "the regenerated context must not re-inject"


@pytest.mark.asyncio
async def test_disabled_config_is_a_true_no_op(ctx):
    calls = []

    async def _shell(ctx, command):
        calls.append(command)
        return "ran"

    _register(ctx, "shell", _shell)
    _write_rules(ctx.config, [_reject_rule(), _remind_rule()])
    assert ctx.config.reminders.enabled is False

    result = await execute_tool(ctx, "shell", {"command": "rm -rf /"})

    assert result.text == "ran"
    assert calls == ["rm -rf /"]
    assert ctx.reminder_hits == []


def test_reminder_message_is_user_role_with_disclaimer():
    hit = reminders.ReminderHit(rule_id="r1", tool="shell", action="remind", message="Use make check.", terse=False)
    message = reminder_message(hit)
    assert message["role"] == "user"
    assert message["content"].startswith("[scoped-reminder]")
    assert "Use make check." in message["content"]
    assert "the user did not send this" in message["content"]
    assert "rule: r1" in message["content"]


# -- Agent-loop injection -----------------------------------------------------


@pytest.mark.asyncio
async def test_agent_turn_injects_ephemeral_reminder_never_archived(enabled):
    """The loop injects a user-role reminder after the batch, and only into the
    LLM-facing message list — an archived user message would be re-read by
    restore_history and pollute every later turn (#680 lesson)."""
    from decafclaw.agent import run_agent_turn
    from decafclaw.archive import read_archive

    enabled.config.llm.streaming = False
    enabled.config.reminders = RemindersConfig(enabled=True)
    _write_rules(enabled.config, [_remind_rule()])

    async def _shell(ctx, command):
        return "ran"

    _register(enabled, "shell", _shell)

    tool_response = {
        "content": None,
        "tool_calls": [{"id": "tc1", "function": {"name": "shell", "arguments": '{"command": "make check"}'}}],
        "role": "assistant",
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }
    final_response = {"content": "Done.", "tool_calls": None, "role": "assistant", "usage": {}}

    with patch("decafclaw.agent.call_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.side_effect = [tool_response, final_response]
        result = await run_agent_turn(enabled, "run make check", [])

    assert result.text == "Done."
    reminder_msgs = [m for m in mock_llm.call_args_list[-1][0][1] if "[scoped-reminder]" in (m.get("content") or "")]
    assert len(reminder_msgs) == 1
    assert reminder_msgs[0]["role"] == "user"

    archived = read_archive(enabled.config, enabled.conv_id)
    assert not any("[scoped-reminder]" in (m.get("content") or "") for m in archived)
