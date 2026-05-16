"""Sandbox unit tests for the `code_execution` skill.

Exercises `_sandbox.run_script` directly with a synthetic handler. Covers:
  - happy-path RPC round-trip via the generated stub module
  - wall-clock timeout kills the subprocess
  - script crashes are captured as `error` status with traceback in stderr
  - plain stdout capture
  - cleanup invariants (server, tmp dir) when subprocess spawn raises
  - cancellation kills the subprocess group
  - server-side validation of malformed RPC payloads

These tests spin real subprocesses + Unix-domain sockets — each call gets
its own tempdir, so parallel test workers don't collide.
"""

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from decafclaw.skills.code_execution import _sandbox
from decafclaw.skills.code_execution.tools import SkillConfig


async def _ping_handler(tool: str, args: dict) -> dict:
    return {"text": "pong", "data": None, "error": None}


async def _unused_handler(tool: str, args: dict) -> dict:
    raise AssertionError(
        f"handler should not be invoked: tool={tool!r} args={args!r}"
    )


@pytest.mark.asyncio
async def test_ping_round_trip():
    settings = SkillConfig(timeout_seconds=10.0)
    result = await _sandbox.run_script(
        ctx=None,
        code="from decafclaw_tools import dc\nprint(dc.ping().text)\n",
        settings=settings,
        handler=_ping_handler,
        allowed=("ping",),
    )
    assert result.status == "success", (
        f"expected success, got {result.status!r}; stderr={result.stderr!r}"
    )
    assert result.stdout == "pong\n", f"stdout={result.stdout!r}"
    assert result.exit_code == 0
    assert len(result.tool_calls) == 1
    call = result.tool_calls[0]
    assert call["tool"] == "ping"
    assert call["ok"] is True
    assert call["args_keys"] == []
    assert isinstance(call["duration_ms"], int)


@pytest.mark.asyncio
async def test_timeout_kills_subprocess():
    settings = SkillConfig(timeout_seconds=0.5)
    result = await _sandbox.run_script(
        ctx=None,
        code="import time\ntime.sleep(10)\n",
        settings=settings,
        handler=_unused_handler,
        allowed=("ping",),
    )
    assert result.status == "timeout", (
        f"expected timeout, got {result.status!r}; stderr={result.stderr!r}"
    )
    # Should kill quickly — 2.0s upper bound accounts for the post-kill
    # wait_for window in `run_script` plus test-machine jitter.
    assert result.elapsed_seconds < 2.0, (
        f"elapsed={result.elapsed_seconds:.2f}s — kill path too slow"
    )
    assert result.exit_code is None
    assert result.tool_calls == []


@pytest.mark.asyncio
async def test_script_crash_captures_traceback():
    settings = SkillConfig(timeout_seconds=10.0)
    result = await _sandbox.run_script(
        ctx=None,
        code='raise RuntimeError("boom")\n',
        settings=settings,
        handler=_unused_handler,
        allowed=("ping",),
    )
    assert result.status == "error", (
        f"expected error, got {result.status!r}; stderr={result.stderr!r}"
    )
    assert "boom" in result.stderr, f"stderr did not contain 'boom': {result.stderr!r}"
    assert result.exit_code is not None
    assert result.exit_code != 0


@pytest.mark.asyncio
async def test_stdout_capture():
    settings = SkillConfig(timeout_seconds=10.0)
    result = await _sandbox.run_script(
        ctx=None,
        code='print("hello")\n',
        settings=settings,
        handler=_unused_handler,
        allowed=("ping",),
    )
    assert result.status == "success", (
        f"expected success, got {result.status!r}; stderr={result.stderr!r}"
    )
    assert result.stdout == "hello\n", f"stdout={result.stdout!r}"
    assert result.exit_code == 0
    assert result.tool_calls == []


@pytest.mark.asyncio
async def test_server_and_tmp_cleanup_on_subprocess_spawn_failure(
    monkeypatch,
):
    """If `create_subprocess_exec` raises, the RPC server must still be
    closed and the temp dir removed — otherwise we leak the socket file
    and the staged proxy/script files.

    Patches `tempfile.mkdtemp` so we know exactly which directory this run
    creates; checking the global `dc-codeexec-*` set would race with the
    parallel test workers that share the system tempdir."""
    import tempfile as _tempfile

    created: list[Path] = []
    real_mkdtemp = _tempfile.mkdtemp

    def _spy_mkdtemp(*args, **kwargs):
        d = real_mkdtemp(*args, **kwargs)
        created.append(Path(d))
        return d

    monkeypatch.setattr(
        "decafclaw.skills.code_execution._sandbox.tempfile.mkdtemp",
        _spy_mkdtemp,
    )

    async def _boom(*args, **kwargs):
        raise OSError("simulated spawn failure")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _boom)

    settings = SkillConfig(timeout_seconds=10.0)
    with pytest.raises(OSError, match="simulated spawn failure"):
        await _sandbox.run_script(
            ctx=None,
            code="print('unreachable')\n",
            settings=settings,
            handler=_unused_handler,
            allowed=("ping",),
        )

    assert created, "tempfile.mkdtemp was never invoked"
    for d in created:
        assert not d.exists(), (
            f"tmp dir leaked after spawn failure: {d}"
        )


@pytest.mark.asyncio
async def test_cancellation_kills_subprocess():
    """If the outer caller cancels the `run_script` task, the subprocess
    must be killed (process-group SIGKILL) rather than left running.

    Drives `run_script` against a slow-sleep script, cancels mid-run,
    then verifies the cancellation surfaced and the subprocess exited."""
    settings = SkillConfig(timeout_seconds=60.0)

    # We need a reference to the spawned proc so we can verify it died.
    # Patch `create_subprocess_exec` to record what it spawned.
    spawned: list = []
    real = asyncio.create_subprocess_exec

    async def _recording(*args, **kwargs):
        proc = await real(*args, **kwargs)
        spawned.append(proc)
        return proc

    with patch.object(asyncio, "create_subprocess_exec", _recording):
        task = asyncio.create_task(
            _sandbox.run_script(
                ctx=None,
                code="import time\ntime.sleep(60)\n",
                settings=settings,
                handler=_unused_handler,
                allowed=("ping",),
            )
        )
        # Let the subprocess actually start.
        await asyncio.sleep(0.2)
        assert spawned, "subprocess was never spawned"
        proc = spawned[0]
        assert proc.returncode is None, "proc unexpectedly already exited"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        # Subprocess should have been signalled by run_script's cancel
        # branch. Give the OS a brief grace period to reap.
        for _ in range(20):
            if proc.returncode is not None:
                break
            await asyncio.sleep(0.05)
        assert proc.returncode is not None, (
            "subprocess survived after run_script cancellation"
        )


@pytest.mark.asyncio
async def test_serve_rpc_rejects_non_dict_args():
    """A raw RPC with `args` as a list (not a dict) must produce an error
    response rather than crashing the connection. The script-side proxy
    only ever sends dicts, so this is a defense-in-depth check against a
    misbehaving alternate client."""
    settings = SkillConfig(timeout_seconds=10.0)
    # Script bypasses the generated stub and writes a raw RPC line with
    # `args` as a list. It then reads the response and prints it as JSON.
    script = (
        "import json, os, socket\n"
        "s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)\n"
        "s.connect(os.environ['DECAFCLAW_RPC_SOCKET'])\n"
        "s.sendall((json.dumps({'tool': 'ping', 'args': []}) + chr(10))"
        ".encode())\n"
        "f = s.makefile('rb')\n"
        "line = f.readline()\n"
        "print(line.decode().strip())\n"
    )
    result = await _sandbox.run_script(
        ctx=None,
        code=script,
        settings=settings,
        handler=_unused_handler,  # Handler must NOT be invoked.
        allowed=("ping",),
    )
    assert result.status == "success", (
        f"expected success, got {result.status!r}; stderr={result.stderr!r}"
    )
    resp = json.loads(result.stdout.strip())
    assert resp["error"] is not None, (
        f"expected error response for non-dict args, got: {resp!r}"
    )
    assert "must be a dict" in resp["error"], (
        f"expected 'must be a dict' in error, got: {resp['error']!r}"
    )
    # No call should be logged because validation rejected before dispatch.
    assert result.tool_calls == [], (
        f"non-dict args should not log a tool call: {result.tool_calls!r}"
    )


