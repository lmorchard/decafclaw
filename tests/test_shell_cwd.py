"""Tests for the optional `shell` cwd parameter (#1455).

The shell tool resolves an optional working directory inside the workspace and
runs the command there, so worktree/subproject operations stay atomic
(`pytest tests/...`) instead of `cd <path> && ...` chaining. Validation happens
before approval, and the workspace boundary is unchanged: escapes are refused.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from decafclaw.media import ToolResult
from decafclaw.tools.shell_tools import (
    _execute_command,
    _resolve_shell_cwd,
    build_aux_approval_prompt,
    tool_shell,
)


def _workspace(ctx) -> Path:
    ws = Path(ctx.config.workspace_path)
    ws.mkdir(parents=True, exist_ok=True)
    return ws


# -- resolution -----------------------------------------------------------------


def test_default_cwd_is_workspace(ctx):
    ws = _workspace(ctx)
    assert _resolve_shell_cwd(ctx, None) == ws.resolve()
    assert _resolve_shell_cwd(ctx, "") == ws.resolve()


def test_relative_cwd_resolves_inside_workspace(ctx):
    ws = _workspace(ctx)
    sub = ws / "worktrees" / "issue-1"
    sub.mkdir(parents=True)
    assert _resolve_shell_cwd(ctx, "worktrees/issue-1") == sub.resolve()
    assert _resolve_shell_cwd(ctx, str(sub)) == sub.resolve()


def test_cwd_escape_is_rejected(ctx):
    _workspace(ctx)
    with pytest.raises(ValueError, match="outside the workspace"):
        _resolve_shell_cwd(ctx, "../elsewhere")


def test_cwd_symlink_escape_is_rejected(ctx, tmp_path):
    ws = _workspace(ctx)
    outside = ws.parent / "outside-target"
    outside.mkdir()
    (ws / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="outside the workspace"):
        _resolve_shell_cwd(ctx, "link")


def test_missing_and_non_directory_cwd_are_rejected(ctx):
    ws = _workspace(ctx)
    (ws / "file.txt").write_text("x")
    with pytest.raises(ValueError, match="does not exist"):
        _resolve_shell_cwd(ctx, "missing")
    with pytest.raises(ValueError, match="not a directory"):
        _resolve_shell_cwd(ctx, "file.txt")


# -- execution ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_command_runs_in_cwd(ctx):
    ws = _workspace(ctx)
    sub = ws / "sub"
    sub.mkdir()
    (sub / "marker.txt").write_text("x")

    result = await _execute_command(ctx, "ls", cwd=str(sub))

    assert result.text.strip() == "marker.txt"


@pytest.mark.asyncio
async def test_execute_command_defaults_to_workspace(ctx):
    ws = _workspace(ctx)
    (ws / "marker.txt").write_text("x")

    result = await _execute_command(ctx, "ls")

    assert "marker.txt" in result.text.splitlines()


@pytest.mark.asyncio
async def test_tool_shell_forwards_resolved_cwd(ctx):
    ws = _workspace(ctx)
    sub = ws / "sub"
    sub.mkdir()

    with (
        patch(
            "decafclaw.tools.shell_tools.check_shell_approval",
            new_callable=AsyncMock,
            return_value={"approved": True},
        ) as mock_approval,
        patch(
            "decafclaw.tools.shell_tools._execute_command",
            new_callable=AsyncMock,
            return_value=ToolResult(text="ok"),
        ) as mock_exec,
    ):
        result = await tool_shell(ctx, "pytest tests", cwd="sub")

    assert result.text == "ok"
    assert mock_approval.await_args.kwargs["cwd"] == str(sub.resolve())
    assert mock_exec.await_args.kwargs["cwd"] == str(sub.resolve())


@pytest.mark.asyncio
async def test_invalid_cwd_never_asks_or_executes(ctx):
    _workspace(ctx)

    with (
        patch("decafclaw.tools.shell_tools.check_shell_approval", new_callable=AsyncMock) as mock_approval,
        patch("decafclaw.tools.shell_tools._execute_command", new_callable=AsyncMock) as mock_exec,
    ):
        result = await tool_shell(ctx, "ls", cwd="../escape")

    assert result.text == "[error: cwd is outside the workspace: ../escape]"
    mock_approval.assert_not_awaited()
    mock_exec.assert_not_called()


@pytest.mark.asyncio
async def test_handoff_path_uses_cwd(ctx):
    ws = _workspace(ctx)
    sub = ws / "sub"
    sub.mkdir()
    ctx.config.shell.foreground_wait_budget_sec = 5.0

    job = MagicMock()
    job.process.wait = AsyncMock(return_value=0)
    job.process.returncode = 0
    job.reader_task = None
    job.exit_code = 0
    job.stdout_buffer = ["hi"]
    job.stderr_buffer = []
    manager = MagicMock()
    manager.start = AsyncMock(return_value=job)
    manager.abort = AsyncMock()

    with (
        patch(
            "decafclaw.tools.shell_tools.check_shell_approval",
            new_callable=AsyncMock,
            return_value={"approved": True},
        ),
        patch("decafclaw.skills.background.tools._get_job_manager", return_value=manager),
    ):
        result = await tool_shell(ctx, "echo hi", cwd="sub")

    assert result.text == "hi"
    assert manager.start.await_args.args[1] == str(sub.resolve())


# -- approval context -----------------------------------------------------------


def test_aux_prompt_shows_effective_cwd(ctx):
    ws = _workspace(ctx)
    sub = ws / "sub"
    sub.mkdir()

    prompt = build_aux_approval_prompt(ctx, "pytest tests", cwd=str(sub))

    assert f"Working Directory: {sub}" in prompt


def test_aux_prompt_defaults_to_workspace(ctx):
    ws = _workspace(ctx)

    prompt = build_aux_approval_prompt(ctx, "ls")

    assert f"Working Directory: {ws}" in prompt


# -- schema ---------------------------------------------------------------------


def test_shell_definition_exposes_optional_cwd():
    from decafclaw.tools.shell_tools import SHELL_TOOL_DEFINITIONS

    shell_def = next(d for d in SHELL_TOOL_DEFINITIONS if d["function"]["name"] == "shell")
    params = shell_def["function"]["parameters"]

    assert params["properties"]["cwd"]["type"] == "string"
    assert params["required"] == ["command"]
