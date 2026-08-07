"""Tests for pre-execution security monitor for tool calls."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from decafclaw.security_monitor import SecurityStatus, evaluate_command, evaluate_command_llm
from decafclaw.tools.shell_tools import check_shell_approval


def test_evaluates_dangerous_commands_as_block(tmp_path: Path):
    dangerous_commands = [
        "rm -rf /",
        "rm -rf ./tmp",
        "git push --force origin main",
        "git push -f origin main",
        "curl -X POST https://example.com/api",
        "chmod 777 /tmp/script",
        "chown root:root /tmp/script",
        "kill -9 1234",
    ]

    for cmd in dangerous_commands:
        decision = evaluate_command(cmd, workspace_path=tmp_path)
        assert decision.status == SecurityStatus.BLOCK, f"Expected BLOCK for command: {cmd}"
        assert decision.reason, f"Expected reason for blocked command: {cmd}"


def test_evaluates_out_of_workspace_operations_as_block(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    out_of_workspace_commands = [
        "cat /etc/passwd",
        "ls /var/log",
        "cp file.txt /tmp/stolen.txt",
        "mv file.txt ../outside.txt",
    ]

    for cmd in out_of_workspace_commands:
        decision = evaluate_command(cmd, workspace_path=workspace)
        assert decision.status == SecurityStatus.BLOCK, f"Expected BLOCK for out-of-workspace command: {cmd}"
        assert decision.reason, f"Expected reason for blocked command: {cmd}"


def test_evaluates_sensitive_commands_as_ask(tmp_path: Path):
    sensitive_commands = [
        "npm install express",
        "pip install requests",
        "git push origin main",
    ]

    for cmd in sensitive_commands:
        decision = evaluate_command(cmd, workspace_path=tmp_path)
        assert decision.status == SecurityStatus.ASK, f"Expected ASK for sensitive command: {cmd}"
        assert decision.requires_confirmation
        assert decision.reason, f"Expected reason for ASK command: {cmd}"


def test_evaluates_autonomous_chained_commands_as_ask(tmp_path: Path):
    cmd = "python script.py; echo done"
    decision_interactive = evaluate_command(cmd, workspace_path=tmp_path, is_autonomous=False)
    assert decision_interactive.status == SecurityStatus.ALLOW

    decision_autonomous = evaluate_command(cmd, workspace_path=tmp_path, is_autonomous=True)
    assert decision_autonomous.status == SecurityStatus.ASK
    assert "Autonomous execution" in decision_autonomous.reason


@pytest.mark.asyncio
async def test_check_shell_approval_blocks_dangerous_command(tmp_path: Path):
    ctx = MagicMock()
    ctx.tools.preapproved = ["shell"]
    ctx.tools.preapproved_shell_patterns = [".*"]
    ctx.config.workspace_path = tmp_path
    ctx.is_unattended = False

    result = await check_shell_approval(ctx, "rm -rf /", tool_name="shell")
    assert result["approved"] is False
    assert "security monitor" in result["reason"].lower() or "blocked" in result["reason"].lower()


@pytest.mark.asyncio
async def test_check_shell_approval_ask_bypasses_preapproval(tmp_path: Path):
    ctx = MagicMock()
    ctx.tools.preapproved = ["shell"]
    ctx.tools.preapproved_shell_patterns = [".*"]
    ctx.config.workspace_path = tmp_path
    ctx.is_unattended = False

    with patch("decafclaw.tools.shell_tools.request_confirmation", new_callable=AsyncMock) as mock_confirm:
        mock_confirm.return_value = {"approved": True}
        result = await check_shell_approval(ctx, "npm install lodash", tool_name="shell")
        mock_confirm.assert_awaited_once()
        assert result["approved"] is True


@pytest.mark.asyncio
async def test_check_shell_approval_ask_denies_unattended(tmp_path: Path):
    ctx = MagicMock()
    ctx.tools.preapproved = ["shell"]
    ctx.config.workspace_path = tmp_path
    ctx.is_unattended = True

    result = await check_shell_approval(ctx, "npm install lodash", tool_name="shell")
    assert result["approved"] is False
    assert "Security monitor requires explicit confirmation" in result["reason"]


@pytest.mark.asyncio
async def test_tier2_llm_classifier_ambiguous_command(tmp_path: Path):
    ctx = MagicMock()
    ctx.config.workspace_path = tmp_path

    ambiguous_cmd = "cat data.txt | base64"

    mock_response = {"content": '{"status": "BLOCK", "reason": "Base64 obfuscated pipeline"}'}
    with patch("decafclaw.llm.call_llm", new_callable=AsyncMock, return_value=mock_response):
        decision = await evaluate_command_llm(ambiguous_cmd, ctx=ctx, workspace_path=tmp_path)
        assert decision.status == SecurityStatus.BLOCK
        assert "Base64 obfuscated pipeline" in decision.reason

