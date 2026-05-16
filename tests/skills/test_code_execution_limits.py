"""Phase 4 property tests for the `code_execution` sandbox.

These tests assert that the resource caps shipped in Phases 2 and 3 actually
*bind* — they are deliberately not happy-path retreads. Each test exercises
one limit:

  - `max_tool_calls` — the RPC server stops dispatching past the cap
  - `max_stdout_bytes` / `max_stderr_bytes` — `_truncate` head+tail marker
  - `memory_cap_bytes` — RLIMIT_AS rejects a large allocation (Linux only)
  - per-RPC progress events — `tool_status` events are published once per
    dispatched call with a monotonically increasing `call_index`

The shared `dispatch_ctx` fixture wires the vault skill's TOOLS into
`ctx.tools.extra` so the sandbox can dispatch `notes_read` (which lives in
the global TOOLS registry and needs no wiring) and `vault_*` against the
real registry.
"""

from __future__ import annotations

import asyncio
import sys

import pytest

from decafclaw.notes import append_note
from decafclaw.skills.code_execution import _sandbox
from decafclaw.skills.code_execution.tools import (
    SANDBOX_ALLOWED_TOOLS,
    SkillConfig,
    _make_tool_handler,
)
from decafclaw.skills.vault.tools import TOOLS as VAULT_TOOLS


@pytest.fixture
def dispatch_ctx(ctx):
    """A ctx with workspace + vault dirs created and vault tools wired.

    Duplicates the same-named fixture in `test_code_execution_dispatch.py`
    to keep both test files independently runnable. If a third call site
    appears, factor into `tests/skills/conftest.py`.
    """
    ctx.config.workspace_path.mkdir(parents=True, exist_ok=True)
    (ctx.config.workspace_path / "conversations").mkdir(
        parents=True, exist_ok=True
    )
    ctx.config.vault_root.mkdir(parents=True, exist_ok=True)
    ctx.config.vault_agent_dir.mkdir(parents=True, exist_ok=True)
    ctx.tools.extra.update(VAULT_TOOLS)
    return ctx


# ---------------------------------------------------------------------------
# max_tool_calls
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_call_limit_enforced(dispatch_ctx):
    """A loop of 10 RPC calls under `max_tool_calls=3` must terminate with
    status `tool_call_limit` and only 3 entries in `tool_calls` — the server
    stops dispatching past the cap (calls 4..10 short-circuit to an error
    response before reaching the handler, so they don't get logged)."""
    settings = SkillConfig(max_tool_calls=3, timeout_seconds=10.0)
    # Seed one note so `notes_read` has something to return — irrelevant to
    # the assertion but keeps the handler path identical to production.
    append_note(dispatch_ctx.config, dispatch_ctx.conv_id, "marker")

    script = (
        "from decafclaw_tools import dc\n"
        "for i in range(10):\n"
        "    r = dc.notes_read()\n"
        "    print(i, r.error or 'ok')\n"
    )
    result = await _sandbox.run_script(
        ctx=dispatch_ctx,
        code=script,
        settings=settings,
        handler=_make_tool_handler(dispatch_ctx),
        allowed=SANDBOX_ALLOWED_TOOLS,
    )

    assert result.status == "tool_call_limit", (
        f"expected tool_call_limit, got {result.status!r}; "
        f"stderr={result.stderr!r}"
    )
    assert len(result.tool_calls) == 3, (
        f"expected 3 dispatched calls, got {len(result.tool_calls)}: "
        f"{result.tool_calls!r}"
    )
    assert all(c["tool"] == "notes_read" for c in result.tool_calls)


# ---------------------------------------------------------------------------
# stdout / stderr truncation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stdout_truncation_head_and_tail():
    """Print 1000 bytes under `max_stdout_bytes=200`. `_truncate` reserves
    64 bytes for the marker and emits `head[:cap*0.4]` + marker + tail, so
    the total length stays at or under the cap and contains the marker."""
    settings = SkillConfig(max_stdout_bytes=200, timeout_seconds=10.0)
    # 1000 'x' + the trailing newline from `print`.
    script = "print('x' * 1000)\n"
    result = await _sandbox.run_script(
        ctx=None,
        code=script,
        settings=settings,
        handler=_unused_handler,
        allowed=(),
    )
    assert result.status == "success", (
        f"expected success, got {result.status!r}; stderr={result.stderr!r}"
    )
    assert "truncated" in result.stdout, (
        f"expected truncation marker in stdout, got: {result.stdout!r}"
    )
    assert len(result.stdout) <= settings.max_stdout_bytes, (
        f"stdout exceeded cap: len={len(result.stdout)} cap="
        f"{settings.max_stdout_bytes}"
    )


@pytest.mark.asyncio
async def test_stdout_truncation_counts_bytes_not_codepoints():
    """Multi-byte UTF-8 output must be capped by byte length, not code-point
    length. The script prints 600 'ñ' characters — 1200 bytes after UTF-8
    encoding. With `max_stdout_bytes=200`, the returned stdout must fit
    inside the byte budget (with a small slack for the inline marker)
    rather than overshooting because the old `_truncate` measured code
    points after decode."""
    settings = SkillConfig(max_stdout_bytes=200, timeout_seconds=10.0)
    # Multi-byte char: ñ encodes to 2 bytes in UTF-8.
    script = "print('ñ' * 600)\n"
    result = await _sandbox.run_script(
        ctx=None,
        code=script,
        settings=settings,
        handler=_unused_handler,
        allowed=(),
    )
    assert result.status == "success", (
        f"expected success, got {result.status!r}; stderr={result.stderr!r}"
    )
    assert "truncated" in result.stdout, (
        f"expected truncation marker in stdout, got: {result.stdout!r}"
    )
    encoded = result.stdout.encode("utf-8")
    # Allow a small overshoot for the marker text itself, which is encoded
    # at insertion time; the byte slicing reserves 64 bytes for it.
    assert len(encoded) <= settings.max_stdout_bytes + 8, (
        f"stdout byte length exceeded cap: bytes={len(encoded)} cap="
        f"{settings.max_stdout_bytes}"
    )


@pytest.mark.asyncio
async def test_unbounded_stdout_does_not_exhaust_parent():
    """A script that prints far more than the cap (here 4 MB) must NOT
    cause the parent to buffer the full output. Bounded capture reads up
    to ~4x the cap and drains the rest. Final reported stdout must respect
    the byte budget."""
    settings = SkillConfig(max_stdout_bytes=50_000, timeout_seconds=15.0)
    # 4 MB output (4096 * 1024 bytes), far beyond any sane budget.
    script = (
        "import sys\n"
        "chunk = 'x' * 1024\n"
        "for _ in range(4096):\n"
        "    sys.stdout.write(chunk)\n"
        "sys.stdout.flush()\n"
    )
    result = await _sandbox.run_script(
        ctx=None,
        code=script,
        settings=settings,
        handler=_unused_handler,
        allowed=(),
    )
    assert result.status == "success", (
        f"expected success, got {result.status!r}; stderr={result.stderr!r}"
    )
    encoded = result.stdout.encode("utf-8")
    assert len(encoded) <= settings.max_stdout_bytes + 8, (
        f"stdout byte length exceeded cap: bytes={len(encoded)} cap="
        f"{settings.max_stdout_bytes}"
    )


@pytest.mark.asyncio
async def test_stderr_truncation():
    """A long uncaught exception produces a stderr much larger than the cap;
    the traceback alone is hundreds of bytes before the message. With
    `max_stderr_bytes=200` the marker must appear and the length must stay
    at or under the cap."""
    settings = SkillConfig(
        max_stderr_bytes=200, max_stdout_bytes=10_000, timeout_seconds=10.0
    )
    script = "raise RuntimeError('x' * 1000)\n"
    result = await _sandbox.run_script(
        ctx=None,
        code=script,
        settings=settings,
        handler=_unused_handler,
        allowed=(),
    )
    # Uncaught exception => non-zero exit => "error" status.
    assert result.status == "error", (
        f"expected error, got {result.status!r}"
    )
    assert "truncated" in result.stderr, (
        f"expected truncation marker in stderr, got: {result.stderr!r}"
    )
    assert len(result.stderr) <= settings.max_stderr_bytes, (
        f"stderr exceeded cap: len={len(result.stderr)} cap="
        f"{settings.max_stderr_bytes}"
    )


# ---------------------------------------------------------------------------
# memory cap (Linux-only)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="RLIMIT_AS only enforced on Linux — gated at module load in _sandbox",
)
@pytest.mark.asyncio
async def test_memory_cap_on_linux():
    """Under `memory_cap_bytes=64MB`, a bytearray of 128MB must fail. On
    Linux with RLIMIT_AS in effect the allocation raises MemoryError before
    the kernel OOM path, so we accept either trace evidence in stderr or
    just a non-zero exit code as a pass signal."""
    settings = SkillConfig(
        memory_cap_bytes=64 * 1024 * 1024, timeout_seconds=10.0
    )
    script = "x = bytearray(128 * 1024 * 1024)\n"
    result = await _sandbox.run_script(
        ctx=None,
        code=script,
        settings=settings,
        handler=_unused_handler,
        allowed=(),
    )
    assert result.status == "error", (
        f"expected error, got {result.status!r}; stderr={result.stderr!r}"
    )
    assert (
        "MemoryError" in result.stderr
        or (result.exit_code is not None and result.exit_code != 0)
    ), (
        f"expected MemoryError or non-zero exit; "
        f"exit_code={result.exit_code} stderr={result.stderr!r}"
    )


# ---------------------------------------------------------------------------
# progress events
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_slow_subscriber_does_not_block_dc_calls(dispatch_ctx):
    """A slow `tool_status` subscriber must not stretch the RPC roundtrip
    of each `dc.*` call. The progress publish is fire-and-forget so a
    100ms-per-event subscriber can't push a 3-call script's total wall
    clock toward 300ms. Asserts the total stays well under the serial
    bound — generous enough to absorb test-machine jitter without
    swallowing a real regression."""
    settings = SkillConfig(timeout_seconds=10.0)

    async def _slow_capture(event: dict) -> None:
        if event.get("type") == "tool_status":
            await asyncio.sleep(0.1)

    dispatch_ctx.event_bus.subscribe(_slow_capture)
    append_note(dispatch_ctx.config, dispatch_ctx.conv_id, "marker")

    script = (
        "from decafclaw_tools import dc\n"
        "for _ in range(3):\n"
        "    dc.notes_read()\n"
    )
    import time as _time
    t0 = _time.monotonic()
    result = await _sandbox.run_script(
        ctx=dispatch_ctx,
        code=script,
        settings=settings,
        handler=_make_tool_handler(dispatch_ctx),
        allowed=SANDBOX_ALLOWED_TOOLS,
        progress_publish=dispatch_ctx.publish,
    )
    elapsed = _time.monotonic() - t0

    assert result.status == "success", (
        f"expected success, got {result.status!r}; stderr={result.stderr!r}"
    )
    # Serial publish would be at least 3 * 0.1s = 0.3s on the critical
    # path. Fire-and-forget should keep wall-clock well below that — drain
    # at the end adds one 0.1s sleep (max of 3 parallel sleeps), giving us
    # a 0.25s headroom even on slow machines.
    assert elapsed < 0.5, (
        f"slow subscriber serialized into the RPC path: elapsed={elapsed:.3f}s"
    )


@pytest.mark.asyncio
async def test_progress_events_published(dispatch_ctx):
    """Three `dc.notes_read()` calls must publish three `tool_status`
    events on the event bus with `tool == "code_execution"`,
    `message == "dc.notes_read"`, and monotonically increasing
    `call_index` starting from 1."""
    settings = SkillConfig(timeout_seconds=10.0)
    captured: list[dict] = []

    async def _capture(event: dict) -> None:
        if event.get("type") == "tool_status":
            captured.append(event)

    dispatch_ctx.event_bus.subscribe(_capture)

    script = (
        "from decafclaw_tools import dc\n"
        "for _ in range(3):\n"
        "    dc.notes_read()\n"
    )
    result = await _sandbox.run_script(
        ctx=dispatch_ctx,
        code=script,
        settings=settings,
        handler=_make_tool_handler(dispatch_ctx),
        allowed=SANDBOX_ALLOWED_TOOLS,
        progress_publish=dispatch_ctx.publish,
    )

    assert result.status == "success", (
        f"expected success, got {result.status!r}; stderr={result.stderr!r}"
    )
    assert len(captured) == 3, (
        f"expected 3 tool_status events, got {len(captured)}: {captured!r}"
    )
    for ev in captured:
        assert ev["tool"] == "code_execution", ev
        assert ev["message"] == "dc.notes_read", ev
    assert [ev["call_index"] for ev in captured] == [1, 2, 3], (
        f"call_index sequence not monotonically increasing from 1: "
        f"{[ev['call_index'] for ev in captured]!r}"
    )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _unused_handler(tool: str, args: dict) -> dict:
    raise AssertionError(
        f"handler should not be invoked: tool={tool!r} args={args!r}"
    )
