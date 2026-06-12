"""Tests for WorkflowHandle.tool_call (Phase 3)."""
import pytest

from decafclaw.media import ToolResult
from decafclaw.workflow.errors import WorkflowToolNotAllowed
from decafclaw.workflow.handle import WorkflowHandle
from decafclaw.workflow.journal import Journal, fingerprint


@pytest.mark.asyncio
async def test_tool_call_live_path(ctx):
    """A live tool_call invokes the tool, journals the serialized result, and
    a second handle replays from the journal without re-invoking."""
    calls: list[dict] = []

    def echo_tool(ctx, **kwargs):
        calls.append(kwargs)
        return ToolResult(text=f"echoed: {kwargs.get('message', '')}",
                          data={"received": kwargs})

    ctx.tools.extra = {"echo": echo_tool}
    ctx.tools.allowed = {"echo"}

    j = Journal(workflow_name="t")
    h = WorkflowHandle(ctx, j)
    out = await h.tool_call("echo", message="hi")
    assert out == {"text": "echoed: hi", "data": {"received": {"message": "hi"}}}
    entry = j.get((0,))
    assert entry is not None
    assert entry.kind == "tool_call"
    expected_fp = fingerprint("tool_call",
                              {"name": "echo", "args": {"message": "hi"}})
    assert entry.args_fingerprint == expected_fp
    assert len(calls) == 1

    # Fresh handle, same journal → replays without invoking the tool again.
    h2 = WorkflowHandle(ctx, j)
    out2 = await h2.tool_call("echo", message="hi")
    assert out2 == out
    assert len(calls) == 1  # tool NOT re-invoked


@pytest.mark.asyncio
async def test_tool_call_replay_path(ctx):
    """A pre-populated journal entry is returned verbatim; the live tool
    function MUST NOT be called during replay."""
    def boom_tool(ctx, **kwargs):
        raise AssertionError("live tool MUST NOT run during replay")

    ctx.tools.extra = {"boom": boom_tool}
    ctx.tools.allowed = {"boom"}

    j = Journal(workflow_name="t")
    fp = fingerprint("tool_call", {"name": "boom", "args": {"x": 1}})
    cached = {"text": "cached-text", "data": {"k": "v"}}
    j.append((0,), "tool_call", fp, cached)

    h = WorkflowHandle(ctx, j)
    out = await h.tool_call("boom", x=1)
    assert out == cached


@pytest.mark.asyncio
async def test_tool_call_rejects_disallowed_tool(ctx):
    """A tool not in ctx.tools.allowed raises WorkflowToolNotAllowed, and
    nothing is written to the journal."""
    ctx.tools.allowed = {"some_other_tool"}

    j = Journal(workflow_name="t")
    h = WorkflowHandle(ctx, j)
    with pytest.raises(WorkflowToolNotAllowed):
        await h.tool_call("shell_exec", cmd="ls")
    # Nothing journaled.
    assert j.get((0,)) is None
    assert j.entries == {}


@pytest.mark.asyncio
async def test_tool_call_disallowed_does_not_advance_cursor(ctx):
    """The allowlist gate fires BEFORE the cursor is consumed. If an
    orchestrator catches WorkflowToolNotAllowed and continues, the next
    journaled call must land at (0,), not (1,)."""
    ctx.tools.allowed = {"some_other_tool"}
    j = Journal(workflow_name="t")
    h = WorkflowHandle(ctx, j)

    with pytest.raises(WorkflowToolNotAllowed):
        await h.tool_call("disallowed", x=1)
    # Cursor must still be at 0 — no journal slot was burned.
    assert h._cursor == 0
    assert j.entries == {}


@pytest.mark.asyncio
async def test_tool_call_with_no_allowlist_allows_any_registered_tool(ctx):
    """`ctx.tools.allowed is None` means "no restriction" — mirrors
    `execute_tool`'s semantics. Any registered tool is invocable.
    Guards against a regression to "None collapses to empty set =
    nothing allowed."
    """
    def fake_tool(ctx, **kwargs):
        return ToolResult(text="fake-ok", data={"args": kwargs})

    ctx.tools.extra = {"the_fake_tool": fake_tool}
    ctx.tools.allowed = None  # explicit: no restriction

    j = Journal(workflow_name="t")
    h = WorkflowHandle(ctx, j)
    out = await h.tool_call("the_fake_tool", x=1)
    assert out == {"text": "fake-ok", "data": {"args": {"x": 1}}}
    entry = j.get((0,))
    assert entry is not None
    assert entry.kind == "tool_call"


@pytest.mark.asyncio
async def test_tool_call_strips_media_from_journal(ctx):
    """Media attachments on the ToolResult are not stored in the journal —
    only text + data make the trip."""
    def media_tool(ctx, **kwargs):
        return ToolResult(
            text="ok",
            data={"k": 1},
            media=[{"type": "image", "bytes": b"xxxx"}],
        )

    ctx.tools.extra = {"with_media": media_tool}
    ctx.tools.allowed = {"with_media"}

    j = Journal(workflow_name="t")
    h = WorkflowHandle(ctx, j)
    out = await h.tool_call("with_media")
    assert out == {"text": "ok", "data": {"k": 1}}
    entry = j.get((0,))
    assert entry is not None
    assert entry.result == {"text": "ok", "data": {"k": 1}}
    # No `media` key smuggled into the journal-stored result.
    assert "media" not in entry.result


@pytest.mark.asyncio
async def test_tool_call_fingerprint_includes_name_and_args(ctx):
    """Different name OR different args yield different fingerprints."""
    def t(ctx, **kwargs):
        return ToolResult(text="ok")

    ctx.tools.extra = {"foo": t, "bar": t}
    ctx.tools.allowed = {"foo", "bar"}

    # Two args values for same name.
    j1 = Journal(workflow_name="t")
    h1 = WorkflowHandle(ctx, j1)
    await h1.tool_call("foo", x=1)
    fp_foo_1 = j1.get((0,)).args_fingerprint

    j2 = Journal(workflow_name="t")
    h2 = WorkflowHandle(ctx, j2)
    await h2.tool_call("foo", x=2)
    fp_foo_2 = j2.get((0,)).args_fingerprint

    assert fp_foo_1 != fp_foo_2

    # Two names with same args.
    j3 = Journal(workflow_name="t")
    h3 = WorkflowHandle(ctx, j3)
    await h3.tool_call("bar", x=1)
    fp_bar_1 = j3.get((0,)).args_fingerprint

    assert fp_foo_1 != fp_bar_1


@pytest.mark.asyncio
async def test_tool_call_uses_sub_handle_key_prefix(ctx):
    """A sub-handle at prefix (7,) journals its tool_call at seq (7, 0)."""
    def t(ctx, **kwargs):
        return ToolResult(text="ok", data=None)

    ctx.tools.extra = {"echo": t}
    ctx.tools.allowed = {"echo"}

    j = Journal(workflow_name="t")
    sub = WorkflowHandle(ctx, j, _key_prefix=(7,))
    out = await sub.tool_call("echo", q="x")
    assert out == {"text": "ok", "data": None}
    entry = j.get((7, 0))
    assert entry is not None
    assert entry.kind == "tool_call"
