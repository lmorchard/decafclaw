"""Tests for the shared file-tool helpers in ``tools/_file_common.py`` (#1340).

6.4a moved the helpers duplicated between ``workspace_tools.py`` and
``admin_tools.py`` into one module; both suites now import them. These tests
pin the shared contract. Text patching and unified diffs live in
``decafclaw.text_patching`` (#1771) and are tested in ``test_text_patching.py``.
"""

import asyncio

from decafclaw.media import ToolResult
from decafclaw.tools import _file_common, admin_tools, workspace_tools


def test_file_tools_adopt_shared_helpers():
    """workspace_tools and admin_tools must use the shared module, not private copies."""
    assert workspace_tools._is_cancelled is _file_common._is_cancelled
    assert workspace_tools._file_error is _file_common._file_error
    assert admin_tools._is_cancelled is _file_common._is_cancelled
    assert admin_tools._file_error is _file_common._file_error


def test_workspace_constants_match_shared_module():
    assert workspace_tools.MAX_READ_LINES == _file_common.MAX_READ_LINES
    assert workspace_tools.MAX_DIFF_WIDGET_CHARS == _file_common.MAX_DIFF_WIDGET_CHARS
    assert workspace_tools.MAX_DIFF_WIDGET_LINES == _file_common.MAX_DIFF_WIDGET_LINES


def test_shared_constant_values():
    assert _file_common.MAX_READ_LINES == 200
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
