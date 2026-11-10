"""Tests for bounded shell output collection (#922).

_execute_command streams stdout/stderr with bounded in-memory previews:
a rolling tail per stream (max_tool_output_bytes) plus real-time spill of
the full output to the conversation's tool_outputs/ artifact, capped at
max_tool_output_disk_bytes. Small limits (1KB preview) trigger spill with
moderate output; completion is awaited via process exit — no fixed sleeps.
"""

import asyncio
import os
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from decafclaw.conversation_paths import conversation_dir
from decafclaw.tools.shell_tools import _execute_command

REPLACEMENT = "\ufffd"


def _prepare(ctx, *, max_bytes=1024, max_lines=2000, disk_cap=10 * 1024 * 1024) -> Path:
    """Point the workspace at a real dir, set small limits, fix the call id."""
    ws = Path(ctx.config.workspace_path)
    ws.mkdir(parents=True, exist_ok=True)
    ctx.tools.current_call_id = "call-test"  # predictable artifact names
    ctx.config.max_tool_output_bytes = max_bytes
    ctx.config.max_tool_output_lines = max_lines
    ctx.config.max_tool_output_disk_bytes = disk_cap
    return ws


def _artifact_dir(ctx) -> Path:
    return conversation_dir(ctx.config, ctx.conv_id) / "tool_outputs"


# -- small output: today's shape, no artifact ---------------------------------


@pytest.mark.asyncio
async def test_small_output_no_truncation_no_artifact(ctx):
    _prepare(ctx)

    result = await _execute_command(ctx, "echo hello")

    assert result.text.strip() == "hello"
    assert "[Output truncated" not in result.text
    assert not _artifact_dir(ctx).exists()


@pytest.mark.asyncio
async def test_stderr_and_exit_code_preserved(ctx):
    _prepare(ctx)

    result = await _execute_command(ctx, "sh -c 'echo some-output; echo boom >&2; exit 3'")

    assert "some-output" in result.text
    assert "[stderr]" in result.text
    assert "boom" in result.text
    assert "[exit code: 3]" in result.text
    assert not _artifact_dir(ctx).exists()


# -- large output: bounded preview, full artifact ------------------------------


@pytest.mark.asyncio
async def test_large_stdout_bounded_preview_full_artifact(ctx):
    _prepare(ctx)

    result = await _execute_command(ctx, "seq 1 5000")

    # Preview is bounded (tail retention: the end of the output survives).
    assert len(result.text.encode("utf-8")) < 3000
    assert "5000" in result.text
    assert not result.text.startswith("1\n")  # the head was dropped
    assert "[Output truncated. Full output saved to" in result.text

    # The artifact holds the complete output.
    stdout_file = _artifact_dir(ctx) / "call-test.stdout.txt"
    assert stdout_file.exists()
    content = stdout_file.read_text(encoding="utf-8")
    assert content.startswith("1\n")
    assert content.endswith("5000\n")
    # stderr was empty — no stderr artifact.
    assert not (_artifact_dir(ctx) / "call-test.stderr.txt").exists()


@pytest.mark.asyncio
async def test_per_stream_identity(ctx):
    """stdout and stderr spill to their own files — identity is preserved."""
    _prepare(ctx)
    command = (
        'python3 -c "import sys; '
        "[print(f'out-{i}') for i in range(3000)]; "
        "[sys.stderr.write(f'err-{i}\\n') for i in range(3000)]\""
    )

    result = await _execute_command(ctx, command)

    out_file = _artifact_dir(ctx) / "call-test.stdout.txt"
    err_file = _artifact_dir(ctx) / "call-test.stderr.txt"
    assert out_file.exists() and err_file.exists()
    assert out_file.read_text(encoding="utf-8").startswith("out-0\n")
    assert err_file.read_text(encoding="utf-8").startswith("err-0\n")

    # Both streams are represented in the preview, each with its own note.
    assert "[stderr]" in result.text
    assert "out-2999" in result.text  # stdout tail
    assert "err-2999" in result.text  # stderr tail
    assert result.text.count("[Output truncated. Full output saved to") == 2


@pytest.mark.asyncio
async def test_single_enormous_line_is_bounded(ctx):
    """One 100KB line is bounded just like many short lines."""
    _prepare(ctx)

    result = await _execute_command(ctx, "python3 -c \"print('x' * 100000)\"")

    preview = result.text.split("\n")[0]
    assert set(preview) == {"x"}  # no U+FFFD, no other content
    assert len(preview) <= 1024
    assert "[Output truncated. Full output saved to" in result.text

    content = (_artifact_dir(ctx) / "call-test.stdout.txt").read_text(encoding="utf-8")
    assert content == "x" * 100000 + "\n"


@pytest.mark.asyncio
async def test_multibyte_output_stays_valid_utf8(ctx):
    """Multibyte characters (possibly split across pipe reads) stay valid."""
    _prepare(ctx)

    result = await _execute_command(ctx, "python3 -c \"print('é' * 50000)\"")

    assert REPLACEMENT not in result.text
    preview = result.text.split("\n")[0]
    assert set(preview) == {"é"}

    content = (_artifact_dir(ctx) / "call-test.stdout.txt").read_text(encoding="utf-8")
    assert content == "é" * 50000 + "\n"


@pytest.mark.asyncio
async def test_line_limit_triggers_spill(ctx):
    """Exceeding max_tool_output_lines (under the byte limit) also spills."""
    _prepare(ctx, max_bytes=100_000, max_lines=50)

    result = await _execute_command(ctx, "seq 1 200")

    # Tail retention: the last 50 lines survive (the note follows the preview).
    assert result.text.startswith("151\n")
    preview = result.text.split("[Output truncated")[0].rstrip()
    assert preview.endswith("200")
    assert "[Output truncated. Full output saved to" in result.text

    content = (_artifact_dir(ctx) / "call-test.stdout.txt").read_text(encoding="utf-8")
    assert len(content.splitlines()) == 200


# -- timeout and cancellation ---------------------------------------------------


@pytest.mark.asyncio
async def test_timeout_kills_process_group(ctx):
    """Timeout kills the whole group (the shell's child dies too), cleanly."""
    ws = _prepare(ctx)

    pidfile = ws / "child-pid"
    result = await _execute_command(ctx, f"sh -c 'echo $$ > {pidfile}; sleep 5'", timeout=1)

    assert result.text.startswith("[error: command timed out after 1 seconds]")
    # The shell's child (a separate process in the group) is dead.
    pid = int(pidfile.read_text().strip())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    # No output was produced — no artifact.
    assert not _artifact_dir(ctx).exists()


@pytest.mark.asyncio
async def test_cancellation_kills_process_and_propagates(ctx):
    """Turn cancellation kills the process group and re-raises CancelledError."""
    ws = _prepare(ctx)

    from decafclaw.tools.shell_tools import _kill_process_group as real_kill

    flag = ws / "cancel-flag"
    with patch("decafclaw.tools.shell_tools._kill_process_group", new=AsyncMock(wraps=real_kill)) as mock_kill:
        task = asyncio.create_task(_execute_command(ctx, f"sh -c 'echo ready > {flag}; sleep 30'"))
        # Wait (bounded) until the command has spawned and signalled readiness.
        deadline = time.monotonic() + 10
        while not flag.exists() and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        assert flag.exists(), "command never signalled readiness"

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    mock_kill.assert_awaited_once()


# -- disk cap ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disk_cap_bounds_spill_file(ctx):
    """A runaway command cannot grow the artifact past disk cap + one chunk."""
    _prepare(ctx, max_bytes=1024, disk_cap=2048)
    total = 3 * 70_001  # ~210KB of output

    result = await _execute_command(
        ctx, "python3 -c \"import sys; [sys.stdout.write('z' * 70000 + '\\n') for _ in range(3)]\""
    )

    spill_file = _artifact_dir(ctx) / "call-test.stdout.txt"
    assert spill_file.exists()
    size = spill_file.stat().st_size
    # Bounded: cap + one pipe read (64KB) + the initial tail write.
    assert size <= 2048 + 65536 + 1024
    # And writes actually stopped early (the full output is ~210KB).
    assert size < total
    # The preview stays bounded regardless.
    assert len(result.text.encode("utf-8")) < 3000
