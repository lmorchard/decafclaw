"""Direction-aware shell approval decisions (#1641).

Direction decides whether a human is asked; the aux judge decides only whether
an in-direction command carries a concrete risk. These tests exercise the
decision pipeline end to end: the read-only tier, the new-activity prompt, the
judge's advisory-only tool feedback, and the approval telemetry events.
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from decafclaw.context import TaskMode
from decafclaw.media import ToolResult
from decafclaw.tools.shell_tools import check_shell_approval, tool_shell


def _seed_activity(ctx, command: str = "touch prior-marker", cwd: str | None = None):
    ctx.history = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "prior-call",
                    "type": "function",
                    "function": {
                        "name": "shell",
                        "arguments": json.dumps({"command": command, "cwd": cwd}),
                    },
                }
            ],
        }
    ]


def _risk_judge(category: str = "irreversible", feedback: str | None = None):
    payload = {
        "auto_approve": False,
        "reason": "cannot be undone",
        "risk": "high",
        "risk_category": category,
    }
    if feedback:
        payload["feedback"] = feedback

    async def fake_call(messages):
        return {"content": json.dumps(payload)}

    return fake_call


@pytest.mark.asyncio
async def test_read_only_command_never_reaches_the_judge(ctx):
    ctx.config.shell.aux_approval_enabled = True
    judge = MagicMock()
    ctx.aux_approval_llm = judge

    result = await check_shell_approval(ctx, "ls -la")

    assert result == {"approved": True}
    judge.assert_not_called()


@pytest.mark.asyncio
async def test_new_activity_prompts_with_direction_and_skips_judge(ctx):
    ctx.config.shell.aux_approval_enabled = True
    judge = MagicMock()
    ctx.aux_approval_llm = judge
    cwd = ctx.config.workspace_path / "decafclaw"
    cwd.mkdir(parents=True)

    with patch(
        "decafclaw.tools.shell_tools.request_confirmation",
        new_callable=AsyncMock,
        return_value={"approved": False},
    ) as confirm:
        result = await check_shell_approval(ctx, "touch marker.txt", cwd=str(cwd))

    assert result == {"approved": False}
    judge.assert_not_called()
    kwargs = confirm.await_args.kwargs
    assert "first `decafclaw` command" in kwargs["message"]
    assert kwargs["decline_reason"] == "first `decafclaw` command in this conversation"
    assert kwargs["action_data"]["activity"] == "decafclaw"
    assert kwargs["action_data"]["activity_established"] is False


@pytest.mark.asyncio
async def test_new_activity_prompt_can_remember_a_scoped_rule(ctx):
    ctx.config.shell.aux_approval_enabled = False
    cwd = ctx.config.workspace_path / "decafclaw"
    cwd.mkdir(parents=True)

    with patch(
        "decafclaw.tools.shell_tools.request_confirmation",
        new_callable=AsyncMock,
        return_value={
            "approved": True,
            "add_rule": True,
            "rule": "In decafclaw: allow touch for marker files",
        },
    ):
        result = await check_shell_approval(ctx, "touch marker.txt", cwd=str(cwd))

    assert result["approved"] is True
    assert "In decafclaw: allow touch for marker files" in ctx.tools.aux_approval_guidance


@pytest.mark.asyncio
async def test_established_activity_reaches_the_judge(ctx):
    ctx.config.shell.aux_approval_enabled = True
    cwd = str(ctx.config.workspace_path)
    _seed_activity(ctx, cwd=cwd)
    captured: dict[str, str] = {}

    async def fake_call(messages):
        captured["prompt"] = messages[0]["content"]
        return {"content": json.dumps({"auto_approve": True, "reason": "safe", "risk": "low"})}

    judge = MagicMock(return_value=fake_call)
    ctx.aux_approval_llm = judge

    result = await check_shell_approval(ctx, "touch marker.txt", cwd=cwd)

    assert result == {"approved": True}
    judge.assert_called_once()
    prompt = captured["prompt"]
    assert "Conversation Activity: unknown" in prompt
    assert "established earlier in this conversation" in prompt


@pytest.mark.asyncio
async def test_judge_tool_preference_is_advisory_not_a_decline(ctx):
    """A judge with no concrete risk category must allow; its advice rides along."""
    ctx.config.shell.aux_approval_enabled = True
    _seed_activity(ctx)

    async def prefer_native(messages):
        return {
            "content": json.dumps(
                {
                    "auto_approve": False,
                    "reason": "workspace_search would fit better",
                    "risk": "low",
                    "feedback": "Use workspace_search instead of grep.",
                }
            )
        }

    ctx.aux_approval_llm = MagicMock(return_value=prefer_native)

    with (
        patch("decafclaw.tools.shell_tools.request_confirmation", new_callable=AsyncMock) as confirm,
        patch(
            "decafclaw.tools.shell_tools._execute_command",
            new_callable=AsyncMock,
            return_value=ToolResult(text="result body"),
        ),
    ):
        result = await tool_shell(ctx, "python3 scripts/scan.py")

    confirm.assert_not_awaited()
    assert result.text == "result body\n\n[advisory: Use workspace_search instead of grep.]"


@pytest.mark.asyncio
async def test_judge_decline_with_risk_category_still_redirects(ctx):
    ctx.config.shell.aux_approval_enabled = True
    _seed_activity(ctx)
    ctx.aux_approval_llm = MagicMock(return_value=_risk_judge(feedback="Use a narrower command."))

    with patch("decafclaw.tools.shell_tools.request_confirmation", new_callable=AsyncMock) as confirm:
        result = await tool_shell(ctx, "python3 scripts/scan.py")

    confirm.assert_not_awaited()
    assert result.text.startswith("[error: command not run")
    assert "Use a narrower command." in result.text


@pytest.mark.asyncio
async def test_unattended_read_only_is_allowed_and_new_activity_defers(ctx):
    """Read-only allows outright; a new activity defers rather than denies."""
    ctx.task_mode = TaskMode.HEARTBEAT

    assert await check_shell_approval(ctx, "ls -la") == {"approved": True}

    # aux judge disabled: the deferral falls through to the existing
    # unattended denial path, which explains itself as unattended rather than
    # pretending a new activity was the reason.
    with patch(
        "decafclaw.tools.shell_tools.request_confirmation",
        new_callable=AsyncMock,
    ) as confirm:
        denied = await check_shell_approval(ctx, "touch marker.txt")

    confirm.assert_not_awaited()
    assert denied["approved"] is False
    assert "unattended" in denied["reason"]


@pytest.mark.asyncio
async def test_approval_decisions_and_human_prompts_are_audited(ctx):
    events: list[dict] = []
    ctx.event_bus.subscribe(events.append)

    await check_shell_approval(ctx, "ls -la")
    decision = next(event for event in events if event["type"] == "approval_decision")
    assert decision["tier"] == "read_only"
    assert decision["decision"] == "allow"
    assert decision["conv_id"] == "test-conv"
    assert decision["command"] == "ls -la"

    _seed_activity(ctx)
    ctx.config.shell.aux_approval_enabled = False
    with patch(
        "decafclaw.tools.shell_tools.request_confirmation",
        new_callable=AsyncMock,
        return_value={"approved": False},
    ):
        await check_shell_approval(ctx, "touch marker.txt")

    human = next(event for event in events if event["type"] == "approval_human")
    assert human["approved"] is False
    assert isinstance(human["latency_ms"], int)
    assert human["tier"] == "aux_decline_prompt"
    assert human["activity"] == "unknown"
    assert human["activity_established"] is True


@pytest.mark.asyncio
async def test_human_approval_latency_is_measured(ctx):
    _seed_activity(ctx)
    ctx.config.shell.aux_approval_enabled = False
    events: list[dict] = []
    ctx.event_bus.subscribe(events.append)

    async def slow_confirm(*args, **kwargs):
        await asyncio.sleep(0.02)
        return {"approved": True}

    with patch(
        "decafclaw.tools.shell_tools._execute_command", new_callable=AsyncMock, return_value=ToolResult(text="ok")
    ):
        with patch(
            "decafclaw.tools.shell_tools.request_confirmation",
            new_callable=AsyncMock,
            side_effect=slow_confirm,
        ):
            await check_shell_approval(ctx, "touch marker.txt")

    human = next(event for event in events if event["type"] == "approval_human")
    assert human["approved"] is True
    assert human["latency_ms"] >= 15


@pytest.mark.asyncio
async def test_read_only_tier_bypasses_the_security_monitor(ctx):
    """`is_ambiguous_command` treats any `#` as ambiguous (#1641 review).

    A read-only grep carrying an issue number must not spend a Tier 2 model
    call, and must not be able to come back ASK and cost a human prompt — so
    the read-only tier has to run before the security monitor.
    """
    ctx.config.shell.aux_approval_enabled = True
    monitor = AsyncMock()
    ctx.aux_approval_llm = MagicMock()
    _seed_activity(ctx)

    with (
        patch("decafclaw.tools.shell_tools.evaluate_command_llm", monitor),
        patch("decafclaw.tools.shell_tools.request_confirmation", new_callable=AsyncMock) as confirm,
    ):
        result = await check_shell_approval(ctx, 'grep -rn "#1641" src/')

    assert result == {"approved": True}
    monitor.assert_not_awaited()
    confirm.assert_not_awaited()


@pytest.mark.asyncio
async def test_read_only_tier_still_yields_to_an_allow_pattern(ctx):
    """Reordering must not disturb the existing allow-pattern precedence."""
    ctx.tools.preapproved_shell_patterns = ["python scripts/*"]
    monitor = AsyncMock()

    with patch("decafclaw.tools.shell_tools.evaluate_command_llm", monitor):
        result = await check_shell_approval(ctx, "python scripts/build.py")

    assert result == {"approved": True}
    monitor.assert_not_awaited()


@pytest.mark.asyncio
async def test_unattended_new_activity_defers_to_the_judge(ctx):
    """An unattended new activity must not hard-deny (#1641 review).

    Scheduled work legitimately starts fresh activity every run. Denying with
    no operator and no redirect is strictly worse than the judge decline this
    tier replaced, so direction abstains and the judge keeps the last word.
    """
    ctx.task_mode = TaskMode.HEARTBEAT
    ctx.config.shell.aux_approval_enabled = True
    events: list[dict] = []
    ctx.event_bus.subscribe(events.append)

    async def allow(messages):
        return {"content": json.dumps({"auto_approve": True, "reason": "safe", "risk": "low"})}

    judge = MagicMock(return_value=allow)
    ctx.aux_approval_llm = judge

    # No history: a new activity, on an unattended turn.
    result = await check_shell_approval(ctx, "touch marker.txt")

    assert result == {"approved": True}
    judge.assert_called_once()
    defer = next(e for e in events if e["type"] == "approval_decision" and e["tier"] == "activity_new_unattended")
    assert defer["decision"] == "defer"


@pytest.mark.asyncio
async def test_unattended_new_activity_still_respects_a_judge_risk_decline(ctx):
    """Deferring to the judge does not mean the judge stops being able to refuse."""
    ctx.task_mode = TaskMode.HEARTBEAT
    ctx.config.shell.aux_approval_enabled = True
    ctx.aux_approval_llm = MagicMock(return_value=_risk_judge())

    with patch("decafclaw.tools.shell_tools.request_confirmation", new_callable=AsyncMock) as confirm:
        result = await check_shell_approval(ctx, "touch marker.txt")

    confirm.assert_not_awaited()
    assert result["approved"] is False
