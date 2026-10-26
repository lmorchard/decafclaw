"""Tests for workspace file tools — path sandboxing and file sharing."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from decafclaw.media import ToolResult, WidgetRequest
from decafclaw.tools.workspace_tools import (
    MAX_READ_LINES,
    _resolve_safe,
    tool_file_share,
    tool_workspace_append,
    tool_workspace_copy,
    tool_workspace_delete,
    tool_workspace_diff,
    tool_workspace_edit,
    tool_workspace_glob,
    tool_workspace_insert,
    tool_workspace_list,
    tool_workspace_mkdir,
    tool_workspace_move,
    tool_workspace_outline,
    tool_workspace_preview_markdown,
    tool_workspace_read,
    tool_workspace_replace_lines,
    tool_workspace_search,
    tool_workspace_write,
)


def _text(result):
    """Extract text from str or ToolResult."""
    return result.text if isinstance(result, ToolResult) else result


def test_resolve_safe_normal(config):
    result = _resolve_safe(config, "test.txt")
    assert result is not None
    assert str(config.workspace_path) in str(result)


def test_resolve_safe_rejects_escape(config):
    result = _resolve_safe(config, "../../etc/passwd")
    assert result is None


def test_resolve_safe_rejects_absolute(config):
    result = _resolve_safe(config, "/etc/passwd")
    assert result is None


def test_resolve_safe_rejects_prefix_attack(config, tmp_data):
    """Workspace '/tmp/ws' should not allow access to '/tmp/ws2/...'."""
    # Create a sibling directory whose name starts with the workspace path
    sibling = tmp_data / config.agent.id / "workspace2"
    sibling.mkdir(parents=True, exist_ok=True)
    (sibling / "secret.txt").write_text("secret")
    # The attack: workspace is '.../workspace', try to reach '.../workspace2'
    # by using a relative path that resolves to the sibling
    result = _resolve_safe(config, "../workspace2/secret.txt")
    assert result is None


def test_write_and_read(ctx):
    tool_workspace_write(ctx, "test.txt", "hello world")
    assert "hello world" in _text(tool_workspace_read(ctx, "test.txt"))


def test_write_creates_dirs(ctx):
    tool_workspace_write(ctx, "subdir/nested/file.txt", "content")
    assert "content" in _text(tool_workspace_read(ctx, "subdir/nested/file.txt"))


def test_read_nonexistent(ctx):
    result = tool_workspace_read(ctx, "nope.txt")
    assert "error" in _text(result).lower()


def test_read_misspelled_path_suggests_close_match(ctx):
    tool_workspace_write(ctx, "config.py", "SETTING = 1")
    text = _text(tool_workspace_read(ctx, "cnfg.py"))
    assert text == "[error: file not found: cnfg.py. Did you mean: config.py?]"


def test_read_exact_path_returns_contents(ctx):
    tool_workspace_write(ctx, "config.py", "SETTING = 1")
    text = _text(tool_workspace_read(ctx, "config.py"))
    assert "SETTING = 1" in text
    assert "error" not in text.lower()


def test_read_ambiguous_path_suggests_all_close_matches(ctx):
    tool_workspace_write(ctx, "test_alpha.py", "a")
    tool_workspace_write(ctx, "test_alphe.py", "e")
    text = _text(tool_workspace_read(ctx, "test_alphx.py"))
    assert "Did you mean:" in text
    assert "test_alpha.py" in text
    assert "test_alphe.py" in text


def test_read_suggestions_limited_to_three(ctx):
    for suffix in "abcde":
        tool_workspace_write(ctx, f"report_{suffix}.md", "x")
    text = _text(tool_workspace_read(ctx, "report_z.md"))
    suggestions = text.split("Did you mean: ", 1)[1].rstrip("?]").split(", ")
    assert len(suggestions) == 3


def test_read_suggests_nested_path(ctx):
    tool_workspace_write(ctx, "notes/meeting.md", "x")
    text = _text(tool_workspace_read(ctx, "notes/meetng.md"))
    assert "Did you mean: notes/meeting.md?" in text


def test_read_without_close_match_has_no_suggestions(ctx):
    tool_workspace_write(ctx, "config.py", "x")
    text = _text(tool_workspace_read(ctx, "zzzzzzzz.txt"))
    assert text == "[error: file not found: zzzzzzzz.txt]"


def test_read_suggestions_skip_hidden_files(ctx):
    tool_workspace_write(ctx, ".secret.env", "x")
    text = _text(tool_workspace_read(ctx, "secret.env"))
    assert "Did you mean" not in text


def test_write_does_not_redirect_to_fuzzy_match(ctx):
    tool_workspace_write(ctx, "notes.txt", "original")
    tool_workspace_write(ctx, "notez.txt", "new text")
    assert (ctx.config.workspace_path / "notes.txt").read_text() == "original"
    assert (ctx.config.workspace_path / "notez.txt").read_text() == "new text"


def test_edit_does_not_redirect_to_fuzzy_match(ctx):
    tool_workspace_write(ctx, "notes.txt", "original")
    text = _text(tool_workspace_edit(ctx, "notez.txt", "original", "changed"))
    assert "not found" in text
    assert (ctx.config.workspace_path / "notes.txt").read_text() == "original"
    assert not (ctx.config.workspace_path / "notez.txt").exists()


def test_read_escape_blocked(ctx):
    result = tool_workspace_read(ctx, "../../etc/passwd")
    assert "outside" in _text(result).lower()


def test_write_escape_blocked(ctx):
    result = tool_workspace_write(ctx, "../../evil.txt", "pwned")
    assert "outside" in _text(result).lower()


def test_list_workspace(ctx):
    tool_workspace_write(ctx, "a.txt", "aaa")
    tool_workspace_write(ctx, "b.txt", "bbb")
    result = tool_workspace_list(ctx)
    assert "a.txt" in _text(result)
    assert "b.txt" in _text(result)


def test_list_empty(ctx):
    # Ensure workspace dir exists
    ctx.config.workspace_path.mkdir(parents=True, exist_ok=True)
    result = tool_workspace_list(ctx)
    assert "error" not in _text(result).lower()


# -- workspace_read line number tests --


def test_read_with_line_numbers(ctx):
    tool_workspace_write(ctx, "numbered.txt", "alpha\nbeta\ngamma")
    result = tool_workspace_read(ctx, "numbered.txt")
    assert "1| alpha" in _text(result)
    assert "2| beta" in _text(result)
    assert "3| gamma" in _text(result)


def test_read_start_line(ctx):
    tool_workspace_write(ctx, "lines.txt", "a\nb\nc\nd\ne")
    result = tool_workspace_read(ctx, "lines.txt", start_line=3)
    assert "Lines 3-5 of 5:" in _text(result)
    assert "c" in _text(result)
    assert "d" in _text(result)
    assert "e" in _text(result)
    assert "a" not in _text(result)


def test_read_end_line(ctx):
    tool_workspace_write(ctx, "lines.txt", "a\nb\nc\nd\ne")
    result = tool_workspace_read(ctx, "lines.txt", end_line=2)
    assert "Lines 1-2 of 5:" in _text(result)
    assert "a" in _text(result)
    assert "b" in _text(result)
    assert "c" not in _text(result)


def test_read_line_range(ctx):
    tool_workspace_write(ctx, "lines.txt", "aaa\nbbb\nccc\nddd\neee")
    result = tool_workspace_read(ctx, "lines.txt", start_line=2, end_line=4)
    assert "Lines 2-4 of 5:" in _text(result)
    assert "bbb" in _text(result)
    assert "ccc" in _text(result)
    assert "ddd" in _text(result)
    assert "aaa" not in _text(result)
    assert "eee" not in _text(result)


def test_read_out_of_range(ctx):
    tool_workspace_write(ctx, "short.txt", "one\ntwo\nthree")
    result = tool_workspace_read(ctx, "short.txt", end_line=100)
    # Should just return to end, no error
    assert "error" not in _text(result).lower()
    assert "three" in _text(result)


# -- workspace_read large file guard --


def test_read_large_file_capped(ctx):
    lines = [f"line {i}" for i in range(1, 500)]
    tool_workspace_write(ctx, "big.txt", "\n".join(lines))
    result = tool_workspace_read(ctx, "big.txt")
    assert f"showing first {MAX_READ_LINES}" in _text(result).lower()
    assert "line 1" in _text(result)
    assert f"line {MAX_READ_LINES}" in _text(result)
    assert f"line {MAX_READ_LINES + 1}" not in _text(result)


def test_read_large_file_with_range_not_capped(ctx):
    lines = [f"line {i}" for i in range(1, 500)]
    tool_workspace_write(ctx, "big.txt", "\n".join(lines))
    result = tool_workspace_read(ctx, "big.txt", start_line=1, end_line=300)
    assert "line 300" in _text(result)
    assert "showing first" not in _text(result).lower()


# -- workspace_mkdir tests --


def test_workspace_mkdir(ctx):
    result = tool_workspace_mkdir(ctx, "new_dir/nested")
    assert "Created directory" in _text(result)
    assert ctx.config.workspace_path.joinpath("new_dir/nested").is_dir()


def test_workspace_mkdir_exists(ctx):
    ctx.config.workspace_path.joinpath("existing_dir").mkdir(parents=True)
    result = tool_workspace_mkdir(ctx, "existing_dir")
    assert "already exists" in _text(result).lower()


def test_workspace_mkdir_escape_blocked(ctx):
    result = tool_workspace_mkdir(ctx, "../../evil_dir")
    assert "outside" in _text(result).lower()


# -- workspace_copy tests --


def test_workspace_copy(ctx):
    tool_workspace_write(ctx, "src.txt", "copy me")
    result = tool_workspace_copy(ctx, "src.txt", "dest.txt")
    assert "Copied" in _text(result)
    assert ctx.config.workspace_path.joinpath("src.txt").read_text() == "copy me"
    assert ctx.config.workspace_path.joinpath("dest.txt").read_text() == "copy me"


def test_workspace_copy_not_found(ctx):
    result = tool_workspace_copy(ctx, "nope.txt", "dest.txt")
    assert "error" in _text(result).lower()
    assert "not found" in _text(result).lower()


def test_workspace_copy_destination_exists(ctx):
    tool_workspace_write(ctx, "src.txt", "src")
    tool_workspace_write(ctx, "dest.txt", "dest")
    result = tool_workspace_copy(ctx, "src.txt", "dest.txt")
    assert "error" in _text(result).lower()
    assert "already exists" in _text(result).lower()


def test_workspace_copy_directory_blocked(ctx):
    ctx.config.workspace_path.joinpath("srcdir").mkdir(parents=True, exist_ok=True)
    result = tool_workspace_copy(ctx, "srcdir", "destdir")
    assert "error" in _text(result).lower()
    assert "is a directory" in _text(result).lower()


def test_workspace_copy_escape_blocked_src(ctx):
    result = tool_workspace_copy(ctx, "../../evil.txt", "dest.txt")
    assert "outside" in _text(result).lower()


def test_workspace_copy_escape_blocked_dst(ctx):
    tool_workspace_write(ctx, "ok.txt", "data")
    result = tool_workspace_copy(ctx, "ok.txt", "../../evil.txt")
    assert "outside" in _text(result).lower()


# -- workspace_move tests --


def test_move_basic(ctx):
    tool_workspace_write(ctx, "old.txt", "content")
    result = tool_workspace_move(ctx, "old.txt", "new.txt")
    assert "moved" in _text(result).lower()
    assert not ctx.config.workspace_path.joinpath("old.txt").exists()
    assert ctx.config.workspace_path.joinpath("new.txt").read_text() == "content"


def test_move_to_subdir(ctx):
    tool_workspace_write(ctx, "file.txt", "data")
    tool_workspace_move(ctx, "file.txt", "sub/dir/file.txt")
    assert ctx.config.workspace_path.joinpath("sub/dir/file.txt").read_text() == "data"


def test_move_not_found(ctx):
    result = tool_workspace_move(ctx, "nope.txt", "dest.txt")
    assert "error" in _text(result).lower()
    assert "not found" in _text(result).lower()


def test_move_destination_exists(ctx):
    tool_workspace_write(ctx, "a.txt", "aaa")
    tool_workspace_write(ctx, "b.txt", "bbb")
    result = tool_workspace_move(ctx, "a.txt", "b.txt")
    assert "error" in _text(result).lower()
    assert "already exists" in _text(result).lower()


def test_move_escape_blocked_src(ctx):
    result = tool_workspace_move(ctx, "../../evil.txt", "dest.txt")
    assert "outside" in _text(result).lower()


def test_move_escape_blocked_dst(ctx):
    tool_workspace_write(ctx, "ok.txt", "data")
    result = tool_workspace_move(ctx, "ok.txt", "../../evil.txt")
    assert "outside" in _text(result).lower()


# -- workspace_delete tests --


def test_delete_basic(ctx):
    tool_workspace_write(ctx, "doomed.txt", "bye")
    result = tool_workspace_delete(ctx, "doomed.txt")
    assert "deleted" in _text(result).lower()
    assert not ctx.config.workspace_path.joinpath("doomed.txt").exists()


def test_delete_not_found(ctx):
    result = tool_workspace_delete(ctx, "nope.txt")
    assert "error" in _text(result).lower()
    assert "not found" in _text(result).lower()


def test_workspace_delete_directory(ctx):
    # Test deleting non-empty directory without recursive flag fails
    dir_path = ctx.config.workspace_path.joinpath("mydir")
    dir_path.mkdir(parents=True)
    (dir_path / "file.txt").write_text("data")

    result = tool_workspace_delete(ctx, "mydir", recursive=False)
    assert "error" in _text(result).lower()
    assert "directory" in _text(result).lower()
    assert "recursive=true" in _text(result).lower()
    assert dir_path.exists()

    # Test deleting non-empty directory with recursive flag succeeds
    result = tool_workspace_delete(ctx, "mydir", recursive=True)
    assert "Deleted directory" in _text(result)
    assert not dir_path.exists()


def test_delete_empty_directory_succeeds(ctx):
    dir_path = ctx.config.workspace_path.joinpath("emptydir")
    dir_path.mkdir(parents=True)
    result = tool_workspace_delete(ctx, "emptydir", recursive=False)
    assert "Deleted directory" in _text(result)
    assert not dir_path.exists()


def test_delete_escape_blocked(ctx):
    result = tool_workspace_delete(ctx, "../../evil.txt")
    assert "outside" in _text(result).lower()


# -- workspace_diff tests --


def test_diff_shows_changes(ctx):
    tool_workspace_write(ctx, "v1.txt", "line1\nline2\nline3\n")
    tool_workspace_write(ctx, "v2.txt", "line1\nLINE2\nline3\n")
    result = tool_workspace_diff(ctx, "v1.txt", "v2.txt")
    assert "-line2" in _text(result)
    assert "+LINE2" in _text(result)
    assert "v1.txt" in _text(result)
    assert "v2.txt" in _text(result)


def test_diff_identical(ctx):
    tool_workspace_write(ctx, "a.txt", "same\n")
    tool_workspace_write(ctx, "b.txt", "same\n")
    result = tool_workspace_diff(ctx, "a.txt", "b.txt")
    assert "identical" in _text(result).lower()


def test_diff_not_found(ctx):
    tool_workspace_write(ctx, "exists.txt", "data\n")
    result = tool_workspace_diff(ctx, "exists.txt", "nope.txt")
    assert "error" in _text(result).lower()
    assert "not found" in _text(result).lower()


def test_diff_escape_blocked(ctx):
    result = tool_workspace_diff(ctx, "../../evil.txt", "other.txt")
    assert "outside" in _text(result).lower()


# -- edit tools mini-diff output --


def test_edit_returns_diff(ctx):
    tool_workspace_write(ctx, "f.txt", "aaa\nbbb\nccc\n")
    result = tool_workspace_edit(ctx, "f.txt", "bbb", "BBB")
    assert "---" in _text(result)  # diff header
    assert "-bbb" in _text(result)
    assert "+BBB" in _text(result)


def test_insert_returns_diff(ctx):
    tool_workspace_write(ctx, "f.txt", "aaa\nccc\n")
    result = tool_workspace_insert(ctx, "f.txt", 2, "bbb")
    assert "---" in _text(result)
    assert "+bbb" in _text(result)


def test_replace_lines_returns_diff(ctx):
    tool_workspace_write(ctx, "f.txt", "aaa\nbbb\nccc\n")
    result = tool_workspace_replace_lines(ctx, "f.txt", 2, 2, "BBB")
    assert "---" in _text(result)
    assert "-bbb" in _text(result)
    assert "+BBB" in _text(result)


# -- workspace_edit tests --


def test_edit_simple(ctx):
    tool_workspace_write(ctx, "code.py", "def hello():\n    print('greet')\n")
    result = tool_workspace_edit(ctx, "code.py", "hello", "world")
    assert "replaced 1" in _text(result).lower()
    content = ctx.config.workspace_path.joinpath("code.py").read_text()
    assert "def world():" in content
    assert "print('greet')" in content


def test_edit_not_found(ctx):
    tool_workspace_write(ctx, "code.py", "def hello():\n    pass\n")
    result = tool_workspace_edit(ctx, "code.py", "nonexistent", "replacement")
    assert "error" in _text(result).lower()
    assert "not found" in _text(result).lower()


def test_edit_ambiguous(ctx):
    tool_workspace_write(ctx, "code.py", "foo = 1\nbar = foo\nbaz = foo\n")
    result = tool_workspace_edit(ctx, "code.py", "foo", "qux")
    assert "error" in _text(result).lower()
    assert "3 matches" in _text(result)


def test_edit_replace_all(ctx):
    tool_workspace_write(ctx, "code.py", "foo = 1\nbar = foo\nbaz = foo\n")
    result = tool_workspace_edit(ctx, "code.py", "foo", "qux", replace_all=True)
    assert "replaced 3" in _text(result).lower()
    content = ctx.config.workspace_path.joinpath("code.py").read_text()
    assert "foo" not in content
    assert content.count("qux") == 3


def test_edit_preserves_rest(ctx):
    tool_workspace_write(ctx, "code.py", "alpha\nbeta\ngamma\n")
    tool_workspace_edit(ctx, "code.py", "beta", "BETA")
    content = ctx.config.workspace_path.joinpath("code.py").read_text()
    assert content == "alpha\nBETA\ngamma\n"


def test_edit_multiline(ctx):
    tool_workspace_write(ctx, "code.py", "def old():\n    pass\n\ndef other():\n    pass\n")
    tool_workspace_edit(ctx, "code.py", "def old():\n    pass", "def new():\n    return 42")
    content = ctx.config.workspace_path.joinpath("code.py").read_text()
    assert "def new():" in content
    assert "return 42" in content
    assert "def old()" not in content


def test_edit_escape_blocked(ctx):
    result = tool_workspace_edit(ctx, "../../evil.py", "a", "b")
    assert "outside" in _text(result).lower()


def test_edit_nonexistent_file(ctx):
    result = tool_workspace_edit(ctx, "nope.py", "a", "b")
    assert "error" in _text(result).lower()
    assert "not found" in _text(result).lower()


# -- workspace_edit line-ending preservation (issue #997) --


def _write_bytes(ctx, name: str, data: bytes) -> Path:
    """Write raw bytes into the workspace, returning the workspace path."""
    ws = ctx.config.workspace_path
    ws.mkdir(parents=True, exist_ok=True)
    (ws / name).write_bytes(data)
    return ws


def test_edit_crlf_file_with_lf_old_text_preserves_crlf(ctx):
    ws = _write_bytes(ctx, "win.txt", b"alpha\r\nbeta\r\ngamma\r\n")
    result = tool_workspace_edit(ctx, "win.txt", "beta", "BETA")
    assert "replaced 1" in _text(result).lower()
    assert (ws / "win.txt").read_bytes() == b"alpha\r\nBETA\r\ngamma\r\n"


def test_edit_crlf_file_with_crlf_old_text(ctx):
    ws = _write_bytes(ctx, "win.txt", b"alpha\r\nbeta\r\ngamma\r\n")
    result = tool_workspace_edit(ctx, "win.txt", "beta\r\n", "BETA\r\n")
    assert "replaced 1" in _text(result).lower()
    assert (ws / "win.txt").read_bytes() == b"alpha\r\nBETA\r\ngamma\r\n"


def test_edit_lf_file_with_crlf_old_text_keeps_lf(ctx):
    ws = _write_bytes(ctx, "unix.txt", b"alpha\nbeta\ngamma\n")
    result = tool_workspace_edit(ctx, "unix.txt", "beta\r\n", "BETA\r\n")
    assert "replaced 1" in _text(result).lower()
    assert (ws / "unix.txt").read_bytes() == b"alpha\nBETA\ngamma\n"


def test_edit_lf_file_stays_unchanged_in_behavior(ctx):
    ws = _write_bytes(ctx, "unix.txt", b"alpha\nbeta\ngamma\n")
    result = tool_workspace_edit(ctx, "unix.txt", "beta\n", "BETA\n")
    assert "replaced 1" in _text(result).lower()
    assert (ws / "unix.txt").read_bytes() == b"alpha\nBETA\ngamma\n"


# -- workspace_edit not-found hints (issue #997) --


# The tier-1 marker is unique to the whitespace hint — the base error text
# already says "whitespace and indentation", so bare "whitespace" proves nothing.
_TIER1_MARKER = "whitespace differences are ignored"


def test_edit_whitespace_only_mismatch_gets_line_hint(ctx):
    ws = _write_bytes(ctx, "code.py", b"def hello():\n    print('hi')\n")
    before = (ws / "code.py").read_bytes()
    result = tool_workspace_edit(ctx, "code.py", "def hello():\n  print('hi')", "x")
    text = _text(result).lower()
    assert "error" in text
    assert "not found" in text
    assert _TIER1_MARKER in text
    assert "line 1" in text
    assert (ws / "code.py").read_bytes() == before


def test_edit_whitespace_hint_line_number_on_later_line(ctx):
    # Genuine whitespace-run difference (two spaces vs one) on line 2, so
    # only the tier-1 whitespace hint can fire here.
    ws = _write_bytes(ctx, "notes.txt", b"first\nline  two\nthird\n")
    before = (ws / "notes.txt").read_bytes()
    result = tool_workspace_edit(ctx, "notes.txt", "line two", "x")
    text = _text(result).lower()
    assert "error" in text
    assert "not found" in text
    assert _TIER1_MARKER in text
    assert "line 2" in text
    assert (ws / "notes.txt").read_bytes() == before


def test_edit_near_match_hint_names_line(ctx):
    ws = _write_bytes(ctx, "notes.txt", b"first line\nsecond line\nthird line\n")
    before = (ws / "notes.txt").read_bytes()
    result = tool_workspace_edit(ctx, "notes.txt", "secend line", "x")
    text = _text(result).lower()
    assert "error" in text
    assert "not found" in text
    assert "line 2" in text
    assert "second line" in text
    assert (ws / "notes.txt").read_bytes() == before


def test_edit_no_hint_when_nothing_is_close(ctx):
    ws = _write_bytes(ctx, "notes.txt", b"first line\nsecond line\nthird line\n")
    before = (ws / "notes.txt").read_bytes()
    result = tool_workspace_edit(ctx, "notes.txt", "zzzzz", "x")
    text = _text(result).lower()
    assert "error" in text
    assert "not found" in text
    # Markers that only appear in the hint variants (the base error itself
    # mentions "whitespace and indentation").
    assert "differences are ignored" not in text
    assert "similar line exists" not in text
    assert (ws / "notes.txt").read_bytes() == before


# -- workspace_insert tests --


def test_insert_at_beginning(ctx):
    tool_workspace_write(ctx, "f.txt", "line1\nline2\n")
    tool_workspace_insert(ctx, "f.txt", 1, "new_first")
    content = ctx.config.workspace_path.joinpath("f.txt").read_text()
    assert content.startswith("new_first\n")
    assert "line1\n" in content


def test_insert_at_middle(ctx):
    tool_workspace_write(ctx, "f.txt", "aaa\nccc\n")
    tool_workspace_insert(ctx, "f.txt", 2, "bbb")
    content = ctx.config.workspace_path.joinpath("f.txt").read_text()
    lines = content.splitlines()
    assert lines == ["aaa", "bbb", "ccc"]


def test_insert_at_end(ctx):
    tool_workspace_write(ctx, "f.txt", "aaa\nbbb\n")
    tool_workspace_insert(ctx, "f.txt", 3, "ccc")
    content = ctx.config.workspace_path.joinpath("f.txt").read_text()
    assert "ccc" in content
    assert content.splitlines()[-1] == "ccc"


def test_insert_invalid_line(ctx):
    tool_workspace_write(ctx, "f.txt", "one\ntwo\n")
    result = tool_workspace_insert(ctx, "f.txt", 0, "bad")
    assert "error" in _text(result).lower()
    result = tool_workspace_insert(ctx, "f.txt", 100, "bad")
    assert "error" in _text(result).lower()


def test_insert_multiline_content(ctx):
    tool_workspace_write(ctx, "f.txt", "aaa\nddd\n")
    tool_workspace_insert(ctx, "f.txt", 2, "bbb\nccc")
    content = ctx.config.workspace_path.joinpath("f.txt").read_text()
    lines = content.splitlines()
    assert lines == ["aaa", "bbb", "ccc", "ddd"]


def test_insert_escape_blocked(ctx):
    result = tool_workspace_insert(ctx, "../../evil.txt", 1, "bad")
    assert "outside" in _text(result).lower()


# -- workspace_replace_lines tests --


def test_replace_lines_basic(ctx):
    tool_workspace_write(ctx, "f.txt", "aaa\nbbb\nccc\nddd\n")
    result = tool_workspace_replace_lines(ctx, "f.txt", 2, 3, "BBB\nCCC")
    assert "replaced" in _text(result).lower()
    content = ctx.config.workspace_path.joinpath("f.txt").read_text()
    lines = content.splitlines()
    assert lines == ["aaa", "BBB", "CCC", "ddd"]


def test_replace_lines_delete(ctx):
    tool_workspace_write(ctx, "f.txt", "aaa\nbbb\nccc\nddd\n")
    result = tool_workspace_replace_lines(ctx, "f.txt", 2, 3)
    assert "deleted" in _text(result).lower()
    content = ctx.config.workspace_path.joinpath("f.txt").read_text()
    lines = content.splitlines()
    assert lines == ["aaa", "ddd"]


def test_replace_lines_expand(ctx):
    tool_workspace_write(ctx, "f.txt", "aaa\nbbb\nccc\n")
    tool_workspace_replace_lines(ctx, "f.txt", 2, 2, "x1\nx2\nx3\nx4\nx5")
    content = ctx.config.workspace_path.joinpath("f.txt").read_text()
    lines = content.splitlines()
    assert lines == ["aaa", "x1", "x2", "x3", "x4", "x5", "ccc"]


def test_replace_lines_shrink(ctx):
    tool_workspace_write(ctx, "f.txt", "aaa\nb1\nb2\nb3\nb4\nb5\nccc\n")
    tool_workspace_replace_lines(ctx, "f.txt", 2, 6, "bbb")
    content = ctx.config.workspace_path.joinpath("f.txt").read_text()
    lines = content.splitlines()
    assert lines == ["aaa", "bbb", "ccc"]


def test_replace_lines_invalid_range(ctx):
    tool_workspace_write(ctx, "f.txt", "aaa\nbbb\nccc\n")
    result = tool_workspace_replace_lines(ctx, "f.txt", 3, 2, "x")
    assert "error" in _text(result).lower()
    result = tool_workspace_replace_lines(ctx, "f.txt", 0, 2, "x")
    assert "error" in _text(result).lower()
    result = tool_workspace_replace_lines(ctx, "f.txt", 1, 100, "x")
    assert "error" in _text(result).lower()


def test_replace_lines_escape_blocked(ctx):
    result = tool_workspace_replace_lines(ctx, "../../evil.txt", 1, 2, "x")
    assert "outside" in _text(result).lower()


# -- workspace_append tests --


def test_append_creates_file(ctx):
    result = tool_workspace_append(ctx, "new.txt", "hello")
    assert "Appended" in _text(result)
    content = ctx.config.workspace_path.joinpath("new.txt").read_text()
    assert content == "hello"


def test_append_to_existing(ctx):
    tool_workspace_write(ctx, "log.txt", "line1\n")
    tool_workspace_append(ctx, "log.txt", "line2\n")
    content = ctx.config.workspace_path.joinpath("log.txt").read_text()
    assert content == "line1\nline2\n"


def test_append_adds_newline(ctx):
    tool_workspace_write(ctx, "no_newline.txt", "first")
    tool_workspace_append(ctx, "no_newline.txt", "second")
    content = ctx.config.workspace_path.joinpath("no_newline.txt").read_text()
    assert content == "first\nsecond"


def test_append_escape_blocked(ctx):
    result = tool_workspace_append(ctx, "../../evil.txt", "bad")
    assert "outside" in _text(result).lower()


# -- workspace_search tests --


def test_search_basic(ctx):
    tool_workspace_write(ctx, "hello.txt", "hello world\ngoodbye world\n")
    result = tool_workspace_search(ctx, "hello")
    assert "hello.txt" in _text(result)
    assert "hello world" in _text(result)


def test_search_regex(ctx):
    tool_workspace_write(ctx, "data.txt", "count: 42\nname: alice\ncount: 7\n")
    result = tool_workspace_search(ctx, r"\d+")
    assert "42" in _text(result)
    assert "7" in _text(result)


def test_search_multiple_files(ctx):
    tool_workspace_write(ctx, "a.txt", "needle here\n")
    tool_workspace_write(ctx, "b.txt", "needle there\n")
    result = tool_workspace_search(ctx, "needle")
    assert "a.txt" in _text(result)
    assert "b.txt" in _text(result)


def test_search_glob_filter(ctx):
    tool_workspace_write(ctx, "code.py", "needle in python\n")
    tool_workspace_write(ctx, "doc.md", "needle in markdown\n")
    result = tool_workspace_search(ctx, "needle", glob="*.py")
    assert "code.py" in _text(result)
    assert "doc.md" not in _text(result)


def test_search_context_lines(ctx):
    tool_workspace_write(ctx, "f.txt", "aaa\nbbb\nccc\nddd\neee\n")
    result = tool_workspace_search(ctx, "ccc", context_lines=1)
    assert "bbb" in _text(result)
    assert "ddd" in _text(result)
    # aaa should not appear with only 1 line of context
    assert "aaa" not in _text(result)


def test_search_single_file(ctx):
    tool_workspace_write(ctx, "target.txt", "find me\n")
    tool_workspace_write(ctx, "other.txt", "find me too\n")
    result = tool_workspace_search(ctx, "find", path="target.txt")
    assert "target.txt" in _text(result)
    assert "other.txt" not in _text(result)


def test_search_no_matches(ctx):
    tool_workspace_write(ctx, "f.txt", "nothing here\n")
    result = tool_workspace_search(ctx, "zzzzz")
    assert _text(result) == "(no matches)"


def test_search_invalid_regex(ctx):
    ctx.config.workspace_path.mkdir(parents=True, exist_ok=True)
    result = tool_workspace_search(ctx, "[invalid")
    assert "error" in _text(result).lower()
    assert "regex" in _text(result).lower()


def test_search_escape_blocked(ctx):
    result = tool_workspace_search(ctx, "test", path="../../etc")
    assert "outside" in _text(result).lower()


# -- workspace_glob tests --


def test_glob_basic(ctx):
    tool_workspace_write(ctx, "a.py", "python\n")
    tool_workspace_write(ctx, "b.py", "python\n")
    tool_workspace_write(ctx, "c.txt", "text\n")
    result = tool_workspace_glob(ctx, "*.py")
    assert "a.py" in _text(result)
    assert "b.py" in _text(result)
    assert "c.txt" not in _text(result)


def test_glob_nested(ctx):
    tool_workspace_write(ctx, "sub/deep/file.py", "nested\n")
    result = tool_workspace_glob(ctx, "*.py")
    assert "sub/deep/file.py" in _text(result)


def test_glob_specific_name(ctx):
    tool_workspace_write(ctx, "config.yaml", "key: val\n")
    tool_workspace_write(ctx, "other.yaml", "key: val\n")
    result = tool_workspace_glob(ctx, "config.yaml")
    assert "config.yaml" in _text(result)
    assert "other.yaml" not in _text(result)


def test_glob_no_matches(ctx):
    tool_workspace_write(ctx, "a.txt", "text\n")
    result = tool_workspace_glob(ctx, "*.zzz")
    assert _text(result) == "(no matches)"


def test_glob_with_subpath(ctx):
    tool_workspace_write(ctx, "src/a.py", "code\n")
    tool_workspace_write(ctx, "tests/b.py", "test\n")
    result = tool_workspace_glob(ctx, "*.py", path="src")
    assert "src/a.py" in _text(result)
    assert "tests/b.py" not in _text(result)


def test_glob_escape_blocked(ctx):
    result = tool_workspace_glob(ctx, "*.py", path="../../etc")
    assert "outside" in _text(result).lower()


# -- file_share tests --


def test_file_share(ctx):
    tool_workspace_write(ctx, "report.txt", "some data")
    result = tool_file_share(ctx, "report.txt")
    assert len(result.media) == 1
    assert result.media[0]["filename"] == "report.txt"
    assert result.media[0]["data"] == b"some data"


def test_file_share_with_message(ctx):
    tool_workspace_write(ctx, "data.json", '{"key": "value"}')
    result = tool_file_share(ctx, "data.json", message="Here's the data")
    assert result.text == "Here's the data"
    assert result.media[0]["content_type"] == "application/json"


def test_file_share_escape_blocked(ctx):
    result = tool_file_share(ctx, "../../etc/passwd")
    assert "outside" in _text(result).lower()
    assert result.media == []


def test_file_share_not_found(ctx):
    result = tool_file_share(ctx, "nonexistent.txt")
    assert "not found" in _text(result).lower()
    assert result.media == []


# ---------------------------------------------------------------------------
# workspace_preview_markdown tests
# ---------------------------------------------------------------------------


def _make_preview_ctx(config):
    ctx = MagicMock()
    ctx.config = config
    return ctx


@pytest.fixture
def workspace_with_md(config):
    """Create a workspace dir with a sample markdown file."""
    workspace = config.workspace_path
    workspace.mkdir(parents=True, exist_ok=True)
    md_file = workspace / "sample.md"
    md_file.write_text("# Hello\n\nThis is a test.\n")
    nonmd_file = workspace / "data.txt"
    nonmd_file.write_text("plain text\n")
    return workspace


def test_workspace_preview_markdown_happy_path(config, workspace_with_md):
    ctx = _make_preview_ctx(config)
    result = tool_workspace_preview_markdown(ctx, "sample.md")
    assert isinstance(result, ToolResult)
    assert "# Hello" in result.text
    assert isinstance(result.widget, WidgetRequest)
    assert result.widget.widget_type == "markdown_document"
    assert result.widget.target == "inline"
    # Trailing newline is normalized away by the line-by-line read; both
    # variants are semantically the same markdown.
    assert result.widget.data["content"].rstrip("\n") == "# Hello\n\nThis is a test."


def test_workspace_preview_markdown_rejects_non_markdown(config, workspace_with_md):
    ctx = _make_preview_ctx(config)
    result = tool_workspace_preview_markdown(ctx, "data.txt")
    assert isinstance(result, ToolResult)
    assert result.text.startswith("[error: ")
    assert "markdown" in result.text.lower()
    assert result.widget is None


def test_workspace_preview_markdown_missing_file(config, workspace_with_md):
    ctx = _make_preview_ctx(config)
    result = tool_workspace_preview_markdown(ctx, "nope.md")
    assert isinstance(result, ToolResult)
    assert result.text.startswith("[error: ")
    assert result.widget is None


def test_workspace_preview_markdown_path_traversal(config, workspace_with_md):
    ctx = _make_preview_ctx(config)
    result = tool_workspace_preview_markdown(ctx, "../etc/passwd.md")
    assert isinstance(result, ToolResult)
    assert result.text.startswith("[error: ")
    assert result.widget is None


def test_workspace_preview_markdown_accepts_markdown_extension(config, workspace_with_md):
    """Both .md and .markdown should work."""
    workspace = config.workspace_path
    (workspace / "longext.markdown").write_text("# Long ext\n")
    ctx = _make_preview_ctx(config)
    result = tool_workspace_preview_markdown(ctx, "longext.markdown")
    assert result.widget is not None
    assert result.widget.widget_type == "markdown_document"


def test_workspace_preview_markdown_caps_large_files(config, workspace_with_md):
    """Files over MAX_READ_LINES get truncated; widget shows notice + first N lines."""
    from decafclaw.tools.workspace_tools import MAX_READ_LINES

    workspace = config.workspace_path
    big_lines = [f"line {i}" for i in range(MAX_READ_LINES * 3)]
    (workspace / "big.md").write_text("# Big\n" + "\n".join(big_lines) + "\n")
    ctx = _make_preview_ctx(config)
    result = tool_workspace_preview_markdown(ctx, "big.md")
    assert result.widget is not None
    widget_lines = result.widget.data["content"].splitlines()
    # First MAX_READ_LINES + a couple of notice lines (blank + italic)
    assert len(widget_lines) <= MAX_READ_LINES + 4
    assert "showing first" in result.widget.data["content"]
    # Tool text starts with truncation notice for the LLM
    assert result.text.startswith("[file truncated:")


# -- redundant workspace/ prefix note --
#
# workspace_write paths are already workspace-relative, so a 'workspace/'
# prefix silently writes one level too deep. The prefix is NOT stripped —
# 'workspace' is a legal directory name inside a workspace, so stripping it
# would make a legitimate path unreachable — but the write says so, which is
# the earliest point the mistake can be caught.


def test_write_notes_a_redundant_workspace_prefix(ctx):
    from decafclaw.tools.workspace_tools import tool_workspace_write

    result = tool_workspace_write(ctx, path="workspace/skills/foo/SKILL.md", content="x")
    text = result if isinstance(result, str) else result.text
    # "skills/foo/SKILL.md" is a substring of the path itself, so assert on
    # the note and the suggested path specifically.
    assert "did you mean" in text.lower()
    assert "'skills/foo/SKILL.md'" in text
    # The write still happened where it was asked to — nothing is stripped.
    assert (ctx.config.workspace_path / "workspace/skills/foo/SKILL.md").exists()


def test_write_does_not_note_ordinary_paths(ctx):
    from decafclaw.tools.workspace_tools import tool_workspace_write

    result = tool_workspace_write(ctx, path="skills/foo/SKILL.md", content="x")
    text = result if isinstance(result, str) else result.text
    assert "did you mean" not in text.lower()


def test_write_does_not_note_a_nested_workspace_dir(ctx):
    """Only a *leading* 'workspace/' is suspicious."""
    from decafclaw.tools.workspace_tools import tool_workspace_write

    result = tool_workspace_write(ctx, path="notes/workspace/plan.md", content="x")
    text = result if isinstance(result, str) else result.text
    assert "did you mean" not in text.lower()


def test_workspace_edit_attaches_diff_view_widget(ctx):
    tool_workspace_write(ctx, "hello.py", "def greet():\n    print('hello')\n")
    result = tool_workspace_edit(ctx, "hello.py", "hello", "world")
    assert isinstance(result, ToolResult)
    assert "Edited hello.py: replaced 1 occurrence(s)" in result.text
    assert isinstance(result.widget, WidgetRequest)
    assert result.widget.widget_type == "diff_view"
    assert result.widget.target == "inline"
    assert result.widget.data["filename"] == "hello.py"
    assert result.widget.data["before"] == "def greet():\n    print('hello')\n"
    assert result.widget.data["after"] == "def greet():\n    print('world')\n"
    assert result.widget.data["view"] == "unified"


def test_workspace_diff_attaches_diff_view_widget(ctx):
    tool_workspace_write(ctx, "a.py", "x = 1\n")
    tool_workspace_write(ctx, "b.py", "x = 2\n")
    result = tool_workspace_diff(ctx, "a.py", "b.py")
    assert isinstance(result, ToolResult)
    assert "-x = 1" in result.text
    assert "+x = 2" in result.text
    assert isinstance(result.widget, WidgetRequest)
    assert result.widget.widget_type == "diff_view"
    assert result.widget.target == "inline"
    assert result.widget.data["filename"] == "a.py ↔ b.py"
    assert result.widget.data["before"] == "x = 1\n"
    assert result.widget.data["after"] == "x = 2\n"
    assert result.widget.data["view"] == "unified"


def test_workspace_diff_identical_files_has_no_widget(ctx):
    tool_workspace_write(ctx, "same1.py", "x = 1\n")
    tool_workspace_write(ctx, "same2.py", "x = 1\n")
    result = tool_workspace_diff(ctx, "same1.py", "same2.py")
    assert "identical" in _text(result).lower()
    if isinstance(result, ToolResult):
        assert result.widget is None


def test_workspace_list_attaches_data_table_widget(ctx):
    tool_workspace_write(ctx, "dir1/file1.txt", "content")
    result = tool_workspace_list(ctx, "dir1")
    assert isinstance(result, ToolResult)
    assert "file1.txt" in result.text
    assert isinstance(result.widget, WidgetRequest)
    assert result.widget.widget_type == "data_table"
    assert result.widget.target == "inline"
    assert "dir1" in result.widget.data["caption"]
    assert result.widget.data["columns"] == [
        {"key": "name", "label": "Name"},
        {"key": "type", "label": "Type"},
        {"key": "size", "label": "Size (bytes)"},
    ]
    assert len(result.widget.data["rows"]) == 1
    assert result.widget.data["rows"][0]["name"] == "file1.txt"
    assert result.widget.data["rows"][0]["type"] == "file"
    assert result.widget.data["rows"][0]["size"] == len("content")


def test_workspace_list_empty_directory_has_no_widget(ctx):
    ctx.config.workspace_path.mkdir(parents=True, exist_ok=True)
    (ctx.config.workspace_path / "emptydir").mkdir()
    result = tool_workspace_list(ctx, "emptydir")
    assert isinstance(result, ToolResult)
    assert result.text == "(empty directory)"
    assert result.widget is None


def test_workspace_edit_oversized_diff_has_no_widget(ctx):
    # Create file exceeding MAX_DIFF_WIDGET_CHARS (50_000)
    large_content = "x = 1\n" + ("# padding line\n" * 3500)
    tool_workspace_write(ctx, "large.py", large_content)
    result = tool_workspace_edit(ctx, "large.py", "x = 1", "x = 2")
    assert isinstance(result, ToolResult)
    assert "Edited large.py: replaced 1 occurrence(s)" in result.text
    assert "+x = 2" in result.text
    assert result.widget is None


def test_workspace_diff_oversized_has_no_widget(ctx):
    large_content_1 = "x = 1\n" + ("# padding line\n" * 3500)
    large_content_2 = "x = 2\n" + ("# padding line\n" * 3500)
    tool_workspace_write(ctx, "large1.py", large_content_1)
    tool_workspace_write(ctx, "large2.py", large_content_2)
    result = tool_workspace_diff(ctx, "large1.py", "large2.py")
    assert isinstance(result, ToolResult)
    assert "-x = 1" in result.text
    assert "+x = 2" in result.text
    assert result.widget is None


class TestWorkspaceToolPromptGuidelines:
    def test_workspace_tools_define_prompt_guidelines(self):
        from decafclaw.tools.workspace_tools import WORKSPACE_TOOL_DEFINITIONS

        expected_tools = {
            "workspace_read",
            "workspace_search",
            "workspace_glob",
            "workspace_write",
            "workspace_append",
            "workspace_edit",
            "workspace_insert",
            "workspace_replace_lines",
            "workspace_move",
            "workspace_delete",
            "workspace_diff",
        }
        defs_by_name = {td["function"]["name"]: td for td in WORKSPACE_TOOL_DEFINITIONS}
        for name in expected_tools:
            td = defs_by_name.get(name)
            assert td is not None, f"Missing definition for {name}"
            guidelines = td.get("prompt_guidelines")
            assert guidelines, f"Tool {name} must declare prompt_guidelines"
            assert isinstance(guidelines, list)

    def test_guidelines_rendered_and_deduplicated(self):
        from decafclaw.tools.tool_registry import build_tool_guidance_text
        from decafclaw.tools.workspace_tools import WORKSPACE_TOOL_DEFINITIONS

        trusted = {td["function"]["name"]: td.get("prompt_guidelines") or [] for td in WORKSPACE_TOOL_DEFINITIONS}
        text = build_tool_guidance_text(WORKSPACE_TOOL_DEFINITIONS, trusted)
        assert text is not None
        assert text.startswith("<tool_guidance>\n")
        assert text.endswith("\n</tool_guidance>")
        # Check deduplication: search and glob share the same line
        assert text.count("Use workspace_search and workspace_glob to find files first.") == 1
        # Check deduplication: move and delete share the same line
        assert text.count("Use workspace_move and workspace_delete to rename or remove files.") == 1

    def test_agent_md_no_longer_contains_workspace_selection_bullets(self):
        # Read the raw AGENT.md text
        agent_md = (Path(__file__).parents[1] / "src" / "decafclaw" / "prompts" / "AGENT.md").read_text()
        assert "workspace_replace_lines / workspace_insert" not in agent_md
        assert "workspace_search / workspace_glob" not in agent_md
        assert "Prefer surgical edits to full rewrites." in agent_md


class TestWorkspaceOutline:
    def test_python_classes_functions_and_signatures(self, ctx):
        code = (
            "class Greeter[T](Base, metaclass=Meta):\n"
            "    '''Docstring.'''\n"
            "    def __init__(self, greeting: str = 'hello'):\n"
            "        self.greeting = greeting\n"
            "\n"
            "    @property\n"
            "    def ready(self) -> bool:\n"
            "        return True\n"
            "\n"
            "    async def greet(self, name: str) -> str:\n"
            "        return f'{self.greeting}, {name}'\n"
            "\n"
            "def standalone_func[T](x: T, y: int = 0) -> T:\n"
            "    return x\n"
        )
        tool_workspace_write(ctx, "greeter.py", code)
        res = tool_workspace_outline(ctx, "greeter.py")
        assert isinstance(res, ToolResult)
        assert res.data["symbols"] == 5
        assert "1-11: class Greeter[T](Base, metaclass=Meta): ..." in res.text
        assert "3-4: def __init__(self, greeting: str = 'hello'): ..." in res.text
        assert "6-8: @property def ready(self) -> bool: ..." in res.text
        assert "10-11: async def greet(self, name: str) -> str: ..." in res.text
        assert "13-14: def standalone_func[T](x: T, y: int = 0) -> T: ..." in res.text

    def test_python_nested_definitions(self, ctx):
        code = (
            "class Outer:\n"
            "    class InnerClass:\n"
            "        def inner_method(self):\n"
            "            pass\n"
            "\n"
            "    def outer_method(self):\n"
            "        def helper():\n"
            "            return 1\n"
            "        return helper()\n"
        )
        tool_workspace_write(ctx, "nested.py", code)
        res = tool_workspace_outline(ctx, "nested.py")
        assert isinstance(res, ToolResult)
        assert "1-9: class Outer: ..." in res.text
        assert "  2-4: class InnerClass: ..." in res.text
        assert "    3-4: def inner_method(self): ..." in res.text
        assert "  6-9: def outer_method(self): ..." in res.text
        assert "    7-8: def helper(): ..." in res.text

    def test_markdown_headings_and_fences(self, ctx):
        doc = (
            "# Main Document\n"
            "Intro text.\n"
            "\n"
            "```python\n"
            "# This is a python comment, not a heading\n"
            "def foo():\n"
            "    pass\n"
            "```\n"
            "\n"
            "## First Section\n"
            "Section body.\n"
            "\n"
            "### Subsection 1.1\n"
            "Subsection body.\n"
            "\n"
            "## Second Section\n"
            "Final section.\n"
        )
        tool_workspace_write(ctx, "doc.md", doc)
        res = tool_workspace_outline(ctx, "doc.md")
        assert isinstance(res, ToolResult)
        assert "not a heading" not in res.text
        assert "1-17: # Main Document ..." in res.text
        assert "  10-15: ## First Section ..." in res.text
        assert "    13-15: ### Subsection 1.1 ..." in res.text
        assert "  16-17: ## Second Section ..." in res.text

    def test_unicode_support(self, ctx):
        code = (
            "# Unicode in code and markdown\n"
            "class Données:\n"
            "    def résumé(self, texte: str) -> str:\n"
            "        return texte[:10]\n"
            "\n"
            "def 计算(x: int) -> int:\n"
            "    return x * 2\n"
        )
        tool_workspace_write(ctx, "unicode_test.py", code)
        res = tool_workspace_outline(ctx, "unicode_test.py")
        assert "2-4: class Données: ..." in res.text
        assert "3-4: def résumé(self, texte: str) -> str: ..." in res.text
        assert "6-7: def 计算(x: int) -> int: ..." in res.text

        md = "# 🚀 Features Overview\nDescription.\n## 概要\nDetails.\n"
        tool_workspace_write(ctx, "unicode_doc.md", md)
        res_md = tool_workspace_outline(ctx, "unicode_doc.md")
        assert "# 🚀 Features Overview" in res_md.text
        assert "## 概要" in res_md.text

    def test_targeted_section_retrieval_python(self, ctx):
        code = (
            "class Service:\n"
            "    def start(self):\n"
            "        self.running = True\n"
            "\n"
            "    def stop(self):\n"
            "        self.running = False\n"
        )
        tool_workspace_write(ctx, "service.py", code)
        # By full method path
        res_full = tool_workspace_outline(ctx, "service.py", section="Service.stop")
        assert isinstance(res_full, ToolResult)
        assert "Lines 5-6 of 6 (section 'stop'):" in res_full.text
        assert "5|     def stop(self):" in res_full.text
        assert "6|         self.running = False" in res_full.text

        # By suffix
        res_suffix = tool_workspace_outline(ctx, "service.py", section="start")
        assert "Lines 2-3 of 6 (section 'start'):" in res_suffix.text
        assert "2|     def start(self):" in res_suffix.text

        # By class
        res_class = tool_workspace_outline(ctx, "service.py", section="Service")
        assert "Lines 1-6 of 6 (section 'Service'):" in res_class.text
        assert "1| class Service:" in res_class.text

    def test_targeted_section_retrieval_markdown(self, ctx):
        doc = "# Project\nOverview\n## Setup\nRun pip install.\n## Usage\nRun python main.py.\n"
        tool_workspace_write(ctx, "readme.md", doc)
        res = tool_workspace_outline(ctx, "readme.md", section="Setup")
        assert "Lines 3-4 of 6 (section 'Setup'):" in res.text
        assert "3| ## Setup" in res.text
        assert "4| Run pip install." in res.text

    def test_targeted_section_not_found_lists_candidates(self, ctx):
        code = "class Worker:\n    def do_work(self):\n        pass\n"
        tool_workspace_write(ctx, "worker.py", code)
        res = tool_workspace_outline(ctx, "worker.py", section="nonexistent")
        assert "[error: section 'nonexistent' not found in 'worker.py'. Known sections:" in res.text
        assert "Worker" in res.text
        assert "Worker.do_work" in res.text

    def test_targeted_section_ambiguous(self, ctx):
        code = "class WorkerA:\n    def run(self):\n        pass\n\nclass WorkerB:\n    def run(self):\n        pass\n"
        tool_workspace_write(ctx, "ambiguous.py", code)
        res = tool_workspace_outline(ctx, "ambiguous.py", section="run")
        assert "[error: section 'run' is ambiguous in 'ambiguous.py'. Matching candidates:" in res.text
        assert "WorkerA.run" in res.text
        assert "WorkerB.run" in res.text

    def test_malformed_python_syntax_error_fallback(self, ctx):
        bad_code = "def broken(\n    print('missing close paren')\n"
        tool_workspace_write(ctx, "bad.py", bad_code)
        res = tool_workspace_outline(ctx, "bad.py")
        assert isinstance(res, ToolResult)
        assert "[Could not parse outline: line" in res.text
        assert "was never closed" in res.text
        assert "1| def broken(" in res.text
        assert "2|     print('missing close paren')" in res.text

        # Trying to retrieve section from malformed file gives explicit error
        sec_res = tool_workspace_outline(ctx, "bad.py", section="broken")
        assert "[error: cannot retrieve section 'broken': file contains syntax error" in sec_res.text

    def test_unsupported_file_fallback(self, ctx):
        json_content = '{\n  "name": "decafclaw",\n  "version": "1.0.0"\n}\n'
        tool_workspace_write(ctx, "package.json", json_content)
        res = tool_workspace_outline(ctx, "package.json")
        assert isinstance(res, ToolResult)
        assert "[File type '.json' does not have a structural outline parser. Showing first 4 of 4 lines:]" in res.text
        assert "1| {" in res.text
        assert '2|   "name": "decafclaw",' in res.text

        # Trying to retrieve section from unsupported file gives explicit error
        sec_res = tool_workspace_outline(ctx, "package.json", section="version")
        assert (
            "[error: cannot retrieve section 'version': file does not have a structural outline (unsupported type '.json')]"
            in sec_res.text
        )

    def test_empty_file(self, ctx):
        tool_workspace_write(ctx, "empty.py", "")
        res = tool_workspace_outline(ctx, "empty.py")
        assert res.text == "(empty file)"

    def test_no_symbols_fallback(self, ctx):
        code = "a = 1\nb = 2\nc = a + b\n"
        tool_workspace_write(ctx, "vars.py", code)
        res = tool_workspace_outline(ctx, "vars.py")
        assert "[No classes, functions, or headings found in 'vars.py'. Showing 3 of 3 lines:]" in res.text
        assert "1| a = 1" in res.text

    def test_path_boundary_enforcement(self, ctx):
        res = tool_workspace_outline(ctx, "../secret.txt")
        assert "[error: path '../secret.txt' is outside the workspace]" in res.text

    def test_directory_error(self, ctx):
        tool_workspace_mkdir(ctx, "mydir")
        res = tool_workspace_outline(ctx, "mydir")
        assert "[error: 'mydir' is a directory, not a file]" in res.text

    def test_bounded_output(self, ctx):
        # Generate file with 250 functions
        lines = [f"def func_{i}():\n    pass\n" for i in range(250)]
        tool_workspace_write(ctx, "large_funcs.py", "".join(lines))
        res = tool_workspace_outline(ctx, "large_funcs.py")
        assert res.data["truncated"] is True
        assert "[Outline truncated: showing first 200 of 250 items." in res.text

    def test_workspace_read_with_section(self, ctx):
        code = (
            "class Greeter:\n"
            "    def hello(self):\n"
            "        return 'hello'\n"
            "\n"
            "    def goodbye(self):\n"
            "        return 'goodbye'\n"
        )
        tool_workspace_write(ctx, "greeter.py", code)
        res = tool_workspace_read(ctx, "greeter.py", section="Greeter.hello")
        assert isinstance(res, ToolResult)
        assert "Lines 2-3 of 6 (section 'hello'):" in res.text
        assert "2|     def hello(self):" in res.text
        assert "3|         return 'hello'" in res.text

    def test_workspace_read_section_mutually_exclusive_with_line_numbers(self, ctx):
        tool_workspace_write(ctx, "foo.py", "def foo():\n    pass\n")
        res = tool_workspace_read(ctx, "foo.py", start_line=1, section="foo")
        assert "[error: cannot specify both 'section' and 'start_line'/'end_line']" in res.text

    def test_workspace_read_section_large_bounded(self, ctx):
        body = "\n".join(f"        x = {i}" for i in range(250))
        code = f"def giant_func():\n{body}\n"
        tool_workspace_write(ctx, "giant.py", code)
        res = tool_workspace_read(ctx, "giant.py", section="giant_func")
        assert res.data["truncated"] is True
        assert "showing first 200. Use start_line/end_line to read specific ranges." in res.text

    def test_section_not_found_not_misclassified_as_ambiguous(self, ctx):
        code = "class Worker:\n    pass\n\ndef do_work():\n    pass\n\ndef cleanup():\n    pass\n"
        tool_workspace_write(ctx, "tasks.py", code)
        res = tool_workspace_outline(ctx, "tasks.py", section="work")
        assert "[error: section 'work' not found in 'tasks.py'. Known sections:" in res.text
        assert "ambiguous" not in res.text
        assert "Worker" in res.text
        assert "do_work" in res.text
        assert "cleanup" in res.text

    def test_section_markdown_heading_lookup_with_hash_prefix(self, ctx):
        doc = "# Main Title\nBody 1\n## Sub Title\nBody 2\n"
        tool_workspace_write(ctx, "doc.md", doc)
        res1 = tool_workspace_outline(ctx, "doc.md", section="# Main Title")
        assert "Lines 1-4 of 4 (section 'Main Title'):" in res1.text
        assert "1| # Main Title" in res1.text

        res2 = tool_workspace_outline(ctx, "doc.md", section="## Sub Title")
        assert "Lines 3-4 of 4 (section 'Sub Title'):" in res2.text
        assert "3| ## Sub Title" in res2.text

    def test_outline_python_compound_statement_containers(self, ctx):
        code = (
            "if True:\n"
            "    def conditional_worker():\n"
            "        pass\n"
            "else:\n"
            "    def fallback_worker():\n"
            "        pass\n"
            "\n"
            "try:\n"
            "    class SafeManager:\n"
            "        def manage(self):\n"
            "            pass\n"
            "except Exception:\n"
            "    def error_handler():\n"
            "        pass\n"
        )
        tool_workspace_write(ctx, "guarded.py", code)
        res = tool_workspace_outline(ctx, "guarded.py")
        assert "conditional_worker" in res.text
        assert "fallback_worker" in res.text
        assert "SafeManager" in res.text
        assert "error_handler" in res.text
        assert "manage" in res.text

    def test_markdown_embedded_code_fences(self, ctx):
        doc = (
            "# Outer Title\n"
            "\n"
            "````markdown\n"
            "# Not a heading inside 4-backtick\n"
            "```python\n"
            "# Still not a heading\n"
            "```\n"
            "````\n"
            "\n"
            "## Real Heading\n"
            "Content.\n"
        )
        tool_workspace_write(ctx, "fences.md", doc)
        res = tool_workspace_outline(ctx, "fences.md")
        assert "Not a heading" not in res.text
        assert "Still not a heading" not in res.text
        assert "## Real Heading" in res.text
