"""Tests for pre-execution security monitor for tool calls."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from decafclaw.security_monitor import SecurityStatus, evaluate_command
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
