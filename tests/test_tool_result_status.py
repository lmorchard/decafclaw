"""ToolResult.status: structured call outcome (#1331).

Success/error/cancelled is a first-class field, derived from the legacy
``[error: ...]`` / ``[cancelled...]`` text prefixes at construction, so
consumers read it instead of re-parsing text.
"""

import pytest

from decafclaw.media import ToolResult, ToolStatus, derive_tool_status
from decafclaw.tools import _to_tool_result

# -- derive_tool_status: text prefix -> status ---------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("hello", ToolStatus.SUCCESS),
        ("", ToolStatus.SUCCESS),  # empty result is a success
        (None, ToolStatus.SUCCESS),
        ("  [error: nope]", ToolStatus.ERROR),  # leading whitespace is stripped
        ("[error: nope]", ToolStatus.ERROR),
        ("[error executing foo: boom]", ToolStatus.ERROR),
        ("success: [error: found an error in the data]", ToolStatus.SUCCESS),  # prefix, not substring
        ("[cancelled]", ToolStatus.CANCELLED),
        ("[cancelled: shell]", ToolStatus.CANCELLED),
        ("[tool interrupted: agent turn cancelled]", ToolStatus.CANCELLED),
    ],
)
def test_derive_tool_status(text, expected):
    assert derive_tool_status(text) is expected


# -- ToolResult.status: derived at construction --------------------------------


def test_success_result_carries_success_status():
    assert ToolResult(text="done").status is ToolStatus.SUCCESS


def test_error_result_carries_error_status():
    assert ToolResult(text="[error: nope]").status is ToolStatus.ERROR


def test_cancelled_result_carries_cancelled_status():
    assert ToolResult(text="[cancelled]").status is ToolStatus.CANCELLED


def test_interrupted_result_carries_cancelled_status():
    assert ToolResult(text="[tool interrupted: agent turn cancelled]").status is ToolStatus.CANCELLED


def test_explicit_status_overrides_derivation():
    # A tool that knows its own outcome can set status explicitly; the
    # derivation must not clobber it.
    assert ToolResult(text="done", status=ToolStatus.ERROR).status is ToolStatus.ERROR


def test_from_text_derives_status():
    assert ToolResult.from_text("done").status is ToolStatus.SUCCESS


def test_status_is_string_valued():
    # StrEnum: compares equal to its string so JSON round-trips and existing
    # string comparisons keep working.
    assert ToolResult(text="[error: x]").status == "error"


# -- _to_tool_result: normalization populates status ---------------------------


def test_to_tool_result_string_is_success():
    assert _to_tool_result("done").status is ToolStatus.SUCCESS


def test_to_tool_result_none_is_success():
    assert _to_tool_result(None).status is ToolStatus.SUCCESS


def test_to_tool_result_non_string_is_success():
    assert _to_tool_result(42).status is ToolStatus.SUCCESS


def test_to_tool_result_preserves_error_toolresult():
    # A tool that returns an [error: ...] ToolResult keeps the error status.
    assert _to_tool_result(ToolResult(text="[error: nope]")).status is ToolStatus.ERROR


def test_to_tool_result_preserves_explicit_status():
    # An explicit status on the returned ToolResult is preserved, not re-derived.
    assert _to_tool_result(ToolResult(text="done", status=ToolStatus.ERROR)).status is ToolStatus.ERROR


# -- execute_tool: the execution path populates status -------------------------


@pytest.mark.asyncio
async def test_execute_tool_success_carries_success_status(ctx):
    from decafclaw.tools import execute_tool

    result = await execute_tool(ctx, "workspace_write", {"path": "status.txt", "content": "hi"})
    assert result.status is ToolStatus.SUCCESS


@pytest.mark.asyncio
async def test_execute_tool_unknown_tool_carries_error_status(ctx):
    from decafclaw.tools import execute_tool

    result = await execute_tool(ctx, "definitely_not_a_real_tool", {})
    assert result.status is ToolStatus.ERROR


@pytest.mark.asyncio
async def test_execute_tool_blocked_tool_carries_error_status(ctx):
    from decafclaw.tools import execute_tool

    ctx.tools.disallowed = {"workspace_write"}
    result = await execute_tool(ctx, "workspace_write", {"path": "x.txt", "content": "hi"})
    assert result.status is ToolStatus.ERROR
