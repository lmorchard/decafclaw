"""Tests for shell purpose metadata, denial feedback, and tool-gap logging (#1559).

`shell` and `shell_background_start` take a `purpose`. It is untrusted metadata:
Tier 1 stays absolute, the Tier 2 classifier and aux auto-approval judge use it
to check command/purpose congruence, and the human sees it on the card. A
denial may carry `feedback` (judge or human) that the tool returns to the agent.
The judge and a human reviewer can also record a missing native tool in the
improvement inbox.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from decafclaw.confirmations import ConfirmationResponse
from decafclaw.improvement_inbox import read_items
from decafclaw.security_monitor import SecurityStatus, evaluate_command_llm
from decafclaw.tools.confirmation import request_confirmation
from decafclaw.tools.shell_tools import (
    _NATIVE_TOOL_INDEX,
    build_aux_approval_prompt,
    check_shell_approval,
    tool_shell,
)

# -- prompt construction --------------------------------------------------------


def test_aux_prompt_carries_purpose_native_index_and_feedback_schema(ctx):
    prompt = build_aux_approval_prompt(ctx, "grep -rn TODO src/", purpose="Find all TODO comments")

    assert "Stated Purpose: Find all TODO comments" in prompt
    assert "untrusted metadata" in prompt
    for name in ("workspace_search", "workspace_read", "workspace_edit", "web_fetch", "vault_search"):
        assert name in _NATIVE_TOOL_INDEX
        assert name in prompt
    assert '"feedback"' in prompt
    assert '"tool_gap"' in prompt


def test_aux_prompt_marks_missing_purpose(ctx):
    prompt = build_aux_approval_prompt(ctx, "ls")
    assert "Stated Purpose: (none provided)" in prompt


# -- purpose reaches the evaluators --------------------------------------------


@pytest.mark.asyncio
async def test_purpose_reaches_aux_judge(ctx):
    ctx.config.shell.aux_approval_enabled = True
    captured = {}

    async def fake_call(messages):
        captured["prompt"] = messages[0]["content"]
        return {"content": '{"auto_approve": true, "reason": "ok", "risk": "low"}'}

    ctx.aux_approval_llm = MagicMock(return_value=fake_call)

    result = await check_shell_approval(ctx, "ls -la", purpose="List the workspace files")

    assert result == {"approved": True}
    assert "Stated Purpose: List the workspace files" in captured["prompt"]


@pytest.mark.asyncio
async def test_purpose_reaches_tier2_classifier(ctx):
    captured = {}

    async def fake_call_llm(config, messages, *args, **kwargs):
        captured["prompt"] = messages[0]["content"]
        return {"content": '{"status": "ALLOW", "reason": "plain echo"}'}

    with patch("decafclaw.llm.call_llm", fake_call_llm):
        decision = await evaluate_command_llm("echo hi # comment", ctx=ctx, purpose="Print a greeting")

    assert decision.status == SecurityStatus.ALLOW
    assert "Stated Purpose: Print a greeting" in captured["prompt"]


@pytest.mark.asyncio
async def test_tier1_block_ignores_stated_purpose(ctx):
    """A benign purpose never unlocks a Tier 1 hard block."""
    ctx.config.shell.aux_approval_enabled = True
    mock_aux = MagicMock()
    ctx.aux_approval_llm = mock_aux

    result = await tool_shell(ctx, "rm -rf /", purpose="Clean up temporary build files")

    assert result.text.startswith("[error: command denied: Blocked by security monitor")
    mock_aux.assert_not_called()


# -- denial feedback -----------------------------------------------------------


@pytest.mark.asyncio
async def test_aux_decline_feedback_reaches_denied_tool_result(ctx):
    ctx.config.shell.aux_approval_enabled = True

    async def fake_call(messages):
        return {
            "content": json.dumps(
                {
                    "auto_approve": False,
                    "reason": "Raw grep duplicates a native tool",
                    "risk": "low",
                    "feedback": "Use workspace_search(pattern='TODO', path='src/') instead of grep.",
                }
            )
        }

    ctx.aux_approval_llm = MagicMock(return_value=fake_call)

    with patch(
        "decafclaw.tools.shell_tools.request_confirmation",
        new_callable=AsyncMock,
        return_value={"approved": False},
    ):
        result = await tool_shell(ctx, "grep -rn TODO src/", purpose="Find all TODO comments")

    assert "command denied" in result.text
    assert "Feedback: Use workspace_search(pattern='TODO', path='src/') instead of grep." in result.text


@pytest.mark.asyncio
async def test_human_feedback_overrides_judge_feedback(ctx):
    ctx.config.shell.aux_approval_enabled = True

    async def fake_call(messages):
        return {
            "content": json.dumps(
                {
                    "auto_approve": False,
                    "reason": "Risky",
                    "risk": "medium",
                    "feedback": "judge advice",
                }
            )
        }

    ctx.aux_approval_llm = MagicMock(return_value=fake_call)

    with patch(
        "decafclaw.tools.shell_tools.request_confirmation",
        new_callable=AsyncMock,
        return_value={"approved": False, "feedback": "Read it with workspace_read first."},
    ):
        result = await tool_shell(ctx, "cat notes.md", purpose="Read notes")

    assert "Feedback: Read it with workspace_read first." in result.text
    assert "judge advice" not in result.text


@pytest.mark.asyncio
async def test_approved_command_ignores_feedback(ctx):
    ctx.config.shell.aux_approval_enabled = False

    with (
        patch(
            "decafclaw.tools.shell_tools.request_confirmation",
            new_callable=AsyncMock,
            return_value={"approved": True},
        ),
        patch("decafclaw.tools.shell_tools._execute_command", new_callable=AsyncMock) as mock_exec,
    ):
        mock_exec.return_value = "ok"
        result = await tool_shell(ctx, "echo hi", purpose="Say hi")

    assert result == "ok"


@pytest.mark.asyncio
async def test_manager_bridge_forwards_purpose_and_returns_feedback(ctx):
    captured = {}

    async def fake_request(request):
        captured["request"] = request
        return ConfirmationResponse(
            confirmation_id=request.confirmation_id,
            approved=False,
            feedback="Use workspace_read instead.",
        )

    ctx.request_confirmation = fake_request
    ctx.tools.current_call_id = "call-1"

    result = await request_confirmation(
        ctx,
        tool_name="shell",
        command="cat notes.md",
        message="Read notes",
        purpose="Read the notes file",
    )

    assert captured["request"].action_data["purpose"] == "Read the notes file"
    assert result["approved"] is False
    assert result["feedback"] == "Use workspace_read instead."


# -- capability gap logging ----------------------------------------------------


@pytest.mark.asyncio
async def test_judge_tool_gap_appends_improvement_inbox_item(ctx):
    ctx.config.shell.aux_approval_enabled = True

    async def fake_call(messages):
        return {
            "content": json.dumps(
                {
                    "auto_approve": False,
                    "reason": "No native tool covers branch creation",
                    "risk": "medium",
                    "feedback": "Run git switch -c instead of checkout -b.",
                    "tool_gap": {
                        "proposed_tool": "git_branch",
                        "rationale": "A dedicated tool would constrain branch mutations",
                    },
                }
            )
        }

    ctx.aux_approval_llm = MagicMock(return_value=fake_call)

    with patch(
        "decafclaw.tools.shell_tools.request_confirmation",
        new_callable=AsyncMock,
        return_value={"approved": False},
    ):
        await tool_shell(ctx, "git checkout -b issue-1559", purpose="Create a work branch")

    items = read_items(ctx.config)
    assert len(items) == 1
    item = items[0]
    assert item["trigger"] == "shell_tool_review"
    assert item["subsystem"] == "tools"
    assert item["severity"] == "low"
    assert item["status"] == "pending"
    assert item["title"] == "Opportunity for native tool: git_branch"
    assert "git checkout -b issue-1559" in item["observed_friction"]
    assert "Create a work branch" in item["observed_friction"]
    assert item["suggested_improvement"].startswith("A dedicated tool")


@pytest.mark.asyncio
async def test_reviewer_missing_tool_flag_appends_improvement_inbox_item(ctx):
    ctx.config.shell.aux_approval_enabled = False

    with patch(
        "decafclaw.tools.shell_tools.request_confirmation",
        new_callable=AsyncMock,
        return_value={
            "approved": False,
            "feedback": "There is no native way to do this.",
            "data": {"flag_missing_tool": True},
        },
    ):
        result = await tool_shell(ctx, "systemctl restart nginx", purpose="Restart the web server")

    items = read_items(ctx.config)
    assert len(items) == 1
    assert items[0]["trigger"] == "shell_tool_review"
    assert "reviewer flagged a missing capability" in items[0]["title"]
    assert "systemctl restart nginx" in items[0]["observed_friction"]
    assert "Feedback: There is no native way to do this." in result.text


@pytest.mark.asyncio
async def test_no_inbox_item_when_command_is_approved(ctx):
    ctx.config.shell.aux_approval_enabled = False

    with (
        patch(
            "decafclaw.tools.shell_tools.request_confirmation",
            new_callable=AsyncMock,
            return_value={"approved": True},
        ),
        patch("decafclaw.tools.shell_tools._execute_command", new_callable=AsyncMock) as mock_exec,
    ):
        mock_exec.return_value = "ok"
        await tool_shell(ctx, "echo hi", purpose="Say hi")

    assert read_items(ctx.config) == []
