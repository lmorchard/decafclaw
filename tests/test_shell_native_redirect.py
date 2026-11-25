"""Native-tool redirects for simple read commands (#1751).

`cat`, `head`, `tail`, `grep`, and `rg` in simple shapes run through
`workspace_read`/`workspace_search` instead of spending an approval. The
substitution is visible in the result and budgeted to one redirect per
(command, purpose) fingerprint per turn (#1626).
"""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from decafclaw.tools.shell_tools import tool_shell


def _workspace(ctx) -> Path:
    ws = Path(ctx.config.workspace_path)
    ws.mkdir(parents=True, exist_ok=True)
    return ws


async def _run(ctx, command: str, **kwargs):
    with (
        patch("decafclaw.tools.shell_tools.check_shell_approval", new_callable=AsyncMock) as mock_approval,
        patch("decafclaw.tools.shell_tools._execute_command", new_callable=AsyncMock) as mock_exec,
    ):
        result = await tool_shell(ctx, command, **kwargs)
    return result, mock_approval, mock_exec


@pytest.mark.asyncio
async def test_cat_redirects_to_workspace_read(ctx):
    ws = _workspace(ctx)
    (ws / "hello.txt").write_text("hello world\n")

    result, approval, execute = await _run(ctx, "cat hello.txt")

    assert "(ran as workspace_read)" in result.text
    assert "hello world" in result.text
    approval.assert_not_awaited()
    execute.assert_not_called()


@pytest.mark.asyncio
async def test_head_redirects_with_line_limit(ctx):
    ws = _workspace(ctx)
    (ws / "lines.txt").write_text("one\ntwo\nthree\n")

    result, _, _ = await _run(ctx, "head -n 1 lines.txt")

    assert "(ran as workspace_read)" in result.text
    assert "one" in result.text
    assert "three" not in result.text


@pytest.mark.asyncio
async def test_tail_redirects_with_line_limit(ctx):
    ws = _workspace(ctx)
    (ws / "lines.txt").write_text("one\ntwo\nthree\n")

    result, _, _ = await _run(ctx, "tail -n 1 lines.txt")

    assert "(ran as workspace_read)" in result.text
    assert "three" in result.text
    assert "one" not in result.text


@pytest.mark.asyncio
async def test_grep_redirects_to_workspace_search(ctx):
    ws = _workspace(ctx)
    (ws / "a.txt").write_text("needle here\n")

    result, _, _ = await _run(ctx, "grep needle a.txt")

    assert "(ran as workspace_search)" in result.text
    assert "needle here" in result.text


@pytest.mark.asyncio
async def test_grep_case_insensitive_flag(ctx):
    ws = _workspace(ctx)
    (ws / "a.txt").write_text("NEEDLE here\n")

    result, _, _ = await _run(ctx, "grep -i needle a.txt")

    assert "(ran as workspace_search)" in result.text
    assert "NEEDLE here" in result.text


@pytest.mark.asyncio
async def test_redirect_uses_explicit_cwd(ctx):
    ws = _workspace(ctx)
    (ws / "sub").mkdir()
    (ws / "sub" / "x.txt").write_text("sub needle\n")

    result, _, _ = await _run(ctx, "cat x.txt", cwd="sub")

    assert "(ran as workspace_read)" in result.text
    assert "sub needle" in result.text


@pytest.mark.asyncio
async def test_pipeline_is_not_redirected(ctx):
    ws = _workspace(ctx)
    (ws / "hello.txt").write_text("hello\n")

    with (
        patch(
            "decafclaw.tools.shell_tools.check_shell_approval",
            new_callable=AsyncMock,
            return_value={"approved": False, "reason": "test denial"},
        ) as mock_approval,
        patch("decafclaw.tools.shell_tools._execute_command", new_callable=AsyncMock) as mock_exec,
    ):
        result = await tool_shell(ctx, "cat hello.txt | wc -l")

    mock_approval.assert_awaited_once()
    mock_exec.assert_not_called()
    assert "(ran as" not in result.text
    assert "denied" in result.text


@pytest.mark.asyncio
async def test_redirect_obeys_one_per_fingerprint_budget(ctx):
    ws = _workspace(ctx)
    (ws / "hello.txt").write_text("hello\n")

    first, _, _ = await _run(ctx, "cat hello.txt")
    assert "(ran as workspace_read)" in first.text

    with (
        patch(
            "decafclaw.tools.shell_tools.check_shell_approval",
            new_callable=AsyncMock,
            return_value={"approved": False, "reason": "test denial"},
        ) as mock_approval,
        patch("decafclaw.tools.shell_tools._execute_command", new_callable=AsyncMock) as mock_exec,
    ):
        second = await tool_shell(ctx, "cat hello.txt")

    mock_approval.assert_awaited_once()
    mock_exec.assert_not_called()
    assert "(ran as" not in second.text
    assert "denied" in second.text


@pytest.mark.asyncio
async def test_unsupported_grep_flag_falls_through(ctx):
    ws = _workspace(ctx)
    (ws / "a.txt").write_text("needle\n")

    with (
        patch(
            "decafclaw.tools.shell_tools.check_shell_approval",
            new_callable=AsyncMock,
            return_value={"approved": False, "reason": "test denial"},
        ) as mock_approval,
        patch("decafclaw.tools.shell_tools._execute_command", new_callable=AsyncMock) as mock_exec,
    ):
        result = await tool_shell(ctx, "grep -c needle a.txt")

    mock_approval.assert_awaited_once()
    mock_exec.assert_not_called()
    assert "(ran as" not in result.text
    assert "denied" in result.text
