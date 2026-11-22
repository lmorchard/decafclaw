"""Stable read references for saved tool output (#1128).

Covers the design's promises: a conversation-scoped handle that survives
compaction, reload, and stubbing; paged retrieval; the five retrieval states;
deletion; and the handle the truncation note hands the model.
"""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from decafclaw.context import Context
from decafclaw.conversation_paths import conversation_dir
from decafclaw.events import EventBus
from decafclaw.media import ToolResult
from decafclaw.tool_execution import execute_single_tool
from decafclaw.tool_outputs import (
    CAPPED,
    COMPLETE,
    DENIED,
    FAILED,
    MISSING,
    capture,
    delete,
    format_handle,
    meta_path,
    parse_handle,
    read_page,
)
from decafclaw.tools.tool_output_tools import _page_result, tool_output_read


@pytest.fixture
def outputs(ctx):
    return conversation_dir(ctx.config, ctx.conv_id, create=True) / "tool_outputs"


# -- Handles ------------------------------------------------------------------


def test_handle_round_trip():
    handle = format_handle("conv1", "call_2.txt")
    assert handle == "tool_output://conv1/call_2.txt"
    assert parse_handle(handle) == ("conv1", "call_2.txt")


@pytest.mark.parametrize(
    "bad",
    [
        "conv1/call.txt",
        "tool_output://",
        "tool_output://conv1",
        "tool_output://conv1/",
        "tool_output://conv1/../etc/passwd",
        "tool_output://conv1/sub/call.txt",
        "tool_output://conv1/call\\x.txt",
        "tool_output://../call.txt",
        "",
    ],
)
def test_malformed_handles_are_rejected(bad):
    assert parse_handle(bad) is None


def test_resolve_raises_on_malformed_handle(ctx):
    with pytest.raises(ValueError):
        read_page(ctx.config, ctx.conv_id, "not-a-handle")


# -- Capture ------------------------------------------------------------------


def test_capture_stores_complete_body_and_metadata(ctx, outputs):
    body = "hello world".encode()
    result = capture(ctx.config, ctx.conv_id, "call_1", body, tool="test_tool")

    assert result.state == COMPLETE
    assert result.stored_bytes == result.total_bytes == len(body)
    assert result.path == outputs / "call_1.txt"
    assert result.path.read_bytes() == body
    meta = json.loads(meta_path(result.path).read_text())
    assert meta["state"] == COMPLETE
    assert meta["tool"] == "test_tool"
    assert meta["tool_call_id"] == "call_1"


def test_capture_with_a_cap_stores_only_the_prefix(ctx):
    body = b"x" * 100
    result = capture(ctx.config, ctx.conv_id, "call_cap", body, max_bytes=10)

    assert result.state == CAPPED
    assert result.stored_bytes == 10
    assert result.total_bytes == 100
    assert result.path.read_bytes() == b"x" * 10


def test_capture_failure_is_recorded_not_raised(ctx, monkeypatch):
    def _boom(self, data):
        raise OSError("disk full")

    monkeypatch.setattr("pathlib.Path.write_bytes", _boom)
    result = capture(ctx.config, ctx.conv_id, "call_fail", b"data")

    assert result.state == FAILED
    assert result.path is None
    assert "disk full" in result.message

    # The reference still reports what happened instead of pretending the
    # artifact is missing.
    page = read_page(ctx.config, ctx.conv_id, result.handle)
    assert page.state == FAILED
    assert "disk full" in page.message


# -- Retrieval ----------------------------------------------------------------


def test_read_page_serves_the_whole_complete_body(ctx):
    body = b"abcdefghij"
    capture(ctx.config, ctx.conv_id, "call_complete", body)

    page = read_page(ctx.config, ctx.conv_id, format_handle(ctx.conv_id, "call_complete.txt"))

    assert page.state == COMPLETE
    assert page.content == "abcdefghij"
    assert page.next_offset is None
    assert page.total_bytes == 10


def test_read_page_pages_forward_and_respects_the_cap(ctx):
    body = b"x" * 25
    capture(ctx.config, ctx.conv_id, "call_pages", body, max_bytes=10)
    handle = format_handle(ctx.conv_id, "call_pages.txt")

    first = read_page(ctx.config, ctx.conv_id, handle, offset=0, limit=10)
    assert first.state == CAPPED
    assert first.content == "x" * 10
    assert first.next_offset is None, "only the stored prefix is addressable"
    assert first.total_bytes == 25, "the reader learns the body was capped"


def test_read_page_offset_past_the_end_reports_it(ctx):
    capture(ctx.config, ctx.conv_id, "call_end", b"short")
    page = read_page(ctx.config, ctx.conv_id, format_handle(ctx.conv_id, "call_end.txt"), offset=99)

    assert page.state == COMPLETE
    assert page.content == ""
    assert page.next_offset is None
    assert "past the end" in page.message


def test_read_page_never_splits_a_multibyte_character(ctx):
    body = "aébc".encode()  # 'é' is two bytes
    capture(ctx.config, ctx.conv_id, "call_utf8", body)
    handle = format_handle(ctx.conv_id, "call_utf8.txt")

    # A limit that lands between the two bytes of 'é'.
    first = read_page(ctx.config, ctx.conv_id, handle, offset=0, limit=2)
    assert first.content == "a"
    assert first.next_offset == 1

    rest = read_page(ctx.config, ctx.conv_id, handle, offset=first.next_offset)
    assert rest.content == "ébc"


def test_read_page_reports_missing_when_the_file_is_gone(ctx):
    capture(ctx.config, ctx.conv_id, "call_gone", b"data")
    handle = format_handle(ctx.conv_id, "call_gone.txt")
    delete(ctx.config, ctx.conv_id, handle)

    page = read_page(ctx.config, ctx.conv_id, handle)
    assert page.state == MISSING


def test_read_page_denies_another_conversations_handle(ctx):
    other = Context(config=ctx.config, event_bus=EventBus())
    other.conv_id = "other-conv"
    capture(ctx.config, "other-conv", "call_theirs", b"secret")

    page = read_page(ctx.config, ctx.conv_id, format_handle("other-conv", "call_theirs.txt"))

    assert page.state == DENIED
    assert page.content == ""


def test_legacy_artifact_without_metadata_reads_as_complete(ctx, outputs):
    outputs.mkdir(parents=True, exist_ok=True)
    (outputs / "legacy.txt").write_text("old body")

    page = read_page(ctx.config, ctx.conv_id, format_handle(ctx.conv_id, "legacy.txt"))

    assert page.state == COMPLETE
    assert page.content == "old body"


def test_two_conversations_with_the_same_local_id_resolve_separately(ctx):
    other = Context(config=ctx.config, event_bus=EventBus())
    other.conv_id = "second-conv"
    capture(ctx.config, ctx.conv_id, "same_id", b"first body")
    capture(ctx.config, "second-conv", "same_id", b"second body")

    mine = read_page(ctx.config, ctx.conv_id, format_handle(ctx.conv_id, "same_id.txt"))
    theirs = read_page(ctx.config, "second-conv", format_handle("second-conv", "same_id.txt"))

    assert mine.content == "first body"
    assert theirs.content == "second body"


def test_a_new_context_reads_the_same_handle_after_reload(ctx):
    """A restart rebuilds the context; the handle still addresses the artifact."""
    capture(ctx.config, ctx.conv_id, "call_reload", b"durable body")

    restarted = Context(config=ctx.config, event_bus=EventBus())
    restarted.conv_id = ctx.conv_id
    page = read_page(restarted.config, restarted.conv_id, format_handle(ctx.conv_id, "call_reload.txt"))

    assert page.content == "durable body"


def test_delete_removes_the_artifact_and_metadata(ctx):
    captured = capture(ctx.config, ctx.conv_id, "call_del", b"body")
    assert captured.path is not None and captured.path.exists()

    assert delete(ctx.config, ctx.conv_id, captured.handle) == "deleted"
    assert not captured.path.exists()
    assert not meta_path(captured.path).exists()
    assert delete(ctx.config, ctx.conv_id, captured.handle) == "missing"
    assert delete(ctx.config, "other-conv", captured.handle) == DENIED


# -- Truncation flow ---------------------------------------------------------


@pytest.mark.asyncio
async def test_truncated_tool_result_carries_a_handle_that_reads_back(ctx):
    ctx.config.max_tool_output_bytes = 100
    long_text = "A" * 250
    tc = {"id": "call_flow", "function": {"name": "test_tool", "arguments": "{}"}}
    semaphore = asyncio.Semaphore(1)

    with patch(
        "decafclaw.tool_execution.execute_tool", new_callable=AsyncMock, return_value=ToolResult(text=long_text)
    ):
        tool_msg, _ = await execute_single_tool(ctx, tc, semaphore)

    content = tool_msg["content"]
    handle = format_handle(ctx.conv_id, "call_flow.txt")
    assert handle in content
    assert "tool_output_read" in content

    result = await tool_output_read(ctx, handle, limit=100)
    assert result.text.startswith("A" * 100)
    assert result.data["state"] == COMPLETE
    assert result.data["total_bytes"] == 250


@pytest.mark.asyncio
async def test_tool_output_read_reports_missing_and_denied(ctx):
    missing = await tool_output_read(ctx, format_handle(ctx.conv_id, "nope.txt"))
    assert missing.text.startswith("[error: no saved output")

    other = Context(config=ctx.config, event_bus=EventBus())
    other.conv_id = "theirs"
    capture(ctx.config, "theirs", "call_x", b"secret")
    denied = await tool_output_read(ctx, format_handle("theirs", "call_x.txt"))
    assert denied.text.startswith("[error:")
    assert "another conversation" in denied.text


def test_page_result_is_a_tool_result_for_errors(ctx):
    """The paged-read helper returns a ToolResult for error states, never a bare string."""
    missing_handle = format_handle(ctx.conv_id, "nope.txt")
    missing = _page_result(missing_handle, read_page(ctx.config, ctx.conv_id, missing_handle))
    assert isinstance(missing, ToolResult)

    other = Context(config=ctx.config, event_bus=EventBus())
    other.conv_id = "theirs"
    capture(ctx.config, "theirs", "call_x", b"secret")
    denied_handle = format_handle("theirs", "call_x.txt")
    denied = _page_result(denied_handle, read_page(ctx.config, ctx.conv_id, denied_handle))
    assert isinstance(denied, ToolResult)


@pytest.mark.asyncio
async def test_tool_output_read_rejects_a_malformed_handle(ctx):
    result = await tool_output_read(ctx, "call_123.txt")
    assert result.text.startswith("[error: invalid tool-output handle")


@pytest.mark.asyncio
async def test_tool_output_read_bounds_the_page_size(ctx):
    capture(ctx.config, ctx.conv_id, "call_big", b"z" * 1000)
    result = await tool_output_read(ctx, format_handle(ctx.conv_id, "call_big.txt"), limit=10**9)

    # The tool clamps to MAX_PAGE_BYTES; a small body is served whole.
    assert result.data["stored_bytes"] == 1000
    assert len(result.text) >= 1000
