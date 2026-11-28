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
        # Legacy phrasings that do not start with "[cancelled": the agent loop /
        # tool_execution cancel marker and subagent_cancel's result.
        ("[Agent turn cancelled by user]", ToolStatus.CANCELLED),
        ("[subagent cancelled: conv-abc123]", ToolStatus.CANCELLED),
        # The error marker is case-insensitive: extensions/mentions capitalizes it.
        ("[Error reading workspace file 'notes.md': boom]", ToolStatus.ERROR),
        ("[Error: reading MCP resource 'x' timed out after 30s]", ToolStatus.ERROR),
        ("[ERROR: nope]", ToolStatus.ERROR),
        # Non-outcome markers stay successes (guards against over-matching).
        ("[no context available]", ToolStatus.SUCCESS),
        ("[awaiting user response: approve?]", ToolStatus.SUCCESS),
    ],
)
def test_derive_tool_status(text, expected):
    assert derive_tool_status(text) is expected


def test_derive_tool_status_ignores_marker_case():
    # The marker's case must not change the outcome: a capitalized [Error ...]
    # is an error, exactly like its lowercase form.
    assert derive_tool_status("[Error: nope]") is ToolStatus.ERROR
    assert derive_tool_status("[error: nope]") is ToolStatus.ERROR


def test_derive_tool_status_long_text_still_classifies_by_prefix():
    # Large outputs (e.g. shell results) must classify by their leading marker
    # without the prefix being lost to truncation or case folding.
    assert derive_tool_status("[error: boom]\n" + "x" * 10_000) is ToolStatus.ERROR
    assert derive_tool_status("[Agent turn cancelled by user]\n" + "y" * 10_000) is ToolStatus.CANCELLED
    assert derive_tool_status("ok\n" + "z" * 10_000) is ToolStatus.SUCCESS


# -- ToolResult.status: derived at construction --------------------------------


def test_success_result_carries_success_status():
    assert ToolResult(text="done").status is ToolStatus.SUCCESS


def test_error_result_carries_error_status():
    assert ToolResult(text="[error: nope]").status is ToolStatus.ERROR


def test_cancelled_result_carries_cancelled_status():
    assert ToolResult(text="[cancelled]").status is ToolStatus.CANCELLED


def test_interrupted_result_carries_cancelled_status():
    assert ToolResult(text="[tool interrupted: agent turn cancelled]").status is ToolStatus.CANCELLED


def test_agent_cancel_marker_carries_cancelled_status():
    # agent._check_cancelled / tool_execution._check_cancelled produce this exact
    # phrasing (with termination_reason=CANCELLED); it must not derive SUCCESS.
    from decafclaw.media import TerminationReason

    result = ToolResult(text="[Agent turn cancelled by user]", termination_reason=TerminationReason.CANCELLED)
    assert result.status is ToolStatus.CANCELLED


def test_subagent_cancel_result_carries_cancelled_status():
    # subagent_tools.subagent_cancel's success phrasing is a cancellation of the
    # child turn, not an error — and it must not derive SUCCESS either.
    assert ToolResult(text="[subagent cancelled: conv-abc123]").status is ToolStatus.CANCELLED


def test_capitalized_error_marker_carries_error_status():
    # extensions/mentions capitalizes the error marker; case must not matter.
    assert ToolResult(text="[Error reading workspace file 'notes.md': boom]").status is ToolStatus.ERROR
    assert ToolResult(text="[Error: reading MCP resource 'x' timed out after 30s]").status is ToolStatus.ERROR


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
