"""Tests for the shared file-tool helpers in ``tools/_file_common.py`` (#1340).

6.4a moves the helpers duplicated between ``workspace_tools.py`` and
``admin_tools.py`` into one module; 6.4b adopts it in ``admin_tools``.
These tests pin the shared contract and assert that ``workspace_tools``
imports the shared objects rather than keeping private copies.
"""

import asyncio

from decafclaw.media import ToolResult
from decafclaw.tools import _file_common, workspace_tools


def test_workspace_tools_adopts_shared_helpers():
    """workspace_tools must use the shared module, not private copies."""
    assert workspace_tools._is_cancelled is _file_common._is_cancelled
    assert workspace_tools._file_error is _file_common._file_error
    assert workspace_tools._mini_diff is _file_common._mini_diff


def test_workspace_constants_match_shared_module():
    assert workspace_tools.MAX_READ_LINES == _file_common.MAX_READ_LINES
    assert workspace_tools.MAX_DIFF_WIDGET_CHARS == _file_common.MAX_DIFF_WIDGET_CHARS
    assert workspace_tools.MAX_DIFF_WIDGET_LINES == _file_common.MAX_DIFF_WIDGET_LINES


def test_shared_constant_values():
    assert _file_common.MAX_READ_LINES == 200
    assert _file_common.DIFF_CONTEXT_LINES == 3
    assert _file_common.MAX_DIFF_WIDGET_CHARS == 50_000
    assert _file_common.MAX_DIFF_WIDGET_LINES == 1_000


def test_is_cancelled_none_ctx():
    assert _file_common._is_cancelled(None) is False


def test_is_cancelled(ctx):
    assert _file_common._is_cancelled(ctx) is False  # no cancellation event seeded
    ctx.cancelled = asyncio.Event()
    assert _file_common._is_cancelled(ctx) is False  # event present but unset
    ctx.cancelled.set()
    assert _file_common._is_cancelled(ctx) is True


def test_file_error_variants():
    path = "notes/a.md"
    cases = [
        (FileNotFoundError("nope"), f"[error: file not found: {path}]"),
        (IsADirectoryError("dir"), f"[error: path is a directory, not a file: {path}]"),
        (PermissionError("denied"), f"[error: permission denied: {path}]"),
        (UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid"), f"[error: file is not valid UTF-8 text: {path}]"),
        (OSError("disk on fire"), f"[error: disk on fire: {path}]"),
    ]
    for exc, expected in cases:
        result = _file_common._file_error(exc, path)
        assert isinstance(result, ToolResult)
        assert result.text == expected


def test_mini_diff_identical_is_empty():
    assert _file_common._mini_diff("a\nb\n", "a\nb\n") == ""


def test_mini_diff_unified_with_path():
    diff = _file_common._mini_diff("a\nb\nc\n", "a\nB\nc\n", "notes/x.md")
    assert diff.startswith("--- a/notes/x.md")
    assert "+++ b/notes/x.md" in diff
    assert "-b\n" in diff
    assert "+B\n" in diff


def test_mini_diff_without_path_uses_before_after():
    diff = _file_common._mini_diff("a\n", "b\n")
    assert "--- before" in diff
    assert "+++ after" in diff


def test_mini_diff_uses_three_context_lines():
    old = "\n".join(f"line{i}" for i in range(10)) + "\n"
    new = old.replace("line5", "LINE5")
    diff = _file_common._mini_diff(old, new)
    # 3 context lines on each side of the change...
    assert "line2" in diff
    assert "line8" in diff
    # ...and nothing outside the window.
    assert "line1\n" not in diff
    assert "line9\n" not in diff
