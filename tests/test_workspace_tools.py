"""Tests for workspace file tools — path sandboxing and file sharing."""

from decafclaw.tools.workspace_tools import (
    _resolve_safe,
    tool_file_share,
    tool_workspace_append,
    tool_workspace_edit,
    tool_workspace_list,
    tool_workspace_read,
    tool_workspace_write,
)


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


def test_write_and_read(ctx):
    tool_workspace_write(ctx, "test.txt", "hello world")
    assert "hello world" in tool_workspace_read(ctx, "test.txt")


def test_write_creates_dirs(ctx):
    tool_workspace_write(ctx, "subdir/nested/file.txt", "content")
    assert "content" in tool_workspace_read(ctx, "subdir/nested/file.txt")


def test_read_nonexistent(ctx):
    result = tool_workspace_read(ctx, "nope.txt")
    assert "error" in result.lower()


def test_read_escape_blocked(ctx):
    result = tool_workspace_read(ctx, "../../etc/passwd")
    assert "outside" in result.lower()


def test_write_escape_blocked(ctx):
    result = tool_workspace_write(ctx, "../../evil.txt", "pwned")
    assert "outside" in result.lower()


def test_list_workspace(ctx):
    tool_workspace_write(ctx, "a.txt", "aaa")
    tool_workspace_write(ctx, "b.txt", "bbb")
    result = tool_workspace_list(ctx)
    assert "a.txt" in result
    assert "b.txt" in result


def test_list_empty(ctx):
    # Ensure workspace dir exists
    ctx.config.workspace_path.mkdir(parents=True, exist_ok=True)
    result = tool_workspace_list(ctx)
    assert "error" not in result.lower()


# -- workspace_read line number tests --


def test_read_with_line_numbers(ctx):
    tool_workspace_write(ctx, "numbered.txt", "alpha\nbeta\ngamma")
    result = tool_workspace_read(ctx, "numbered.txt")
    assert "1| alpha" in result
    assert "2| beta" in result
    assert "3| gamma" in result


def test_read_start_line(ctx):
    tool_workspace_write(ctx, "lines.txt", "a\nb\nc\nd\ne")
    result = tool_workspace_read(ctx, "lines.txt", start_line=3)
    assert "Lines 3-5 of 5:" in result
    assert "c" in result
    assert "d" in result
    assert "e" in result
    assert "a" not in result


def test_read_end_line(ctx):
    tool_workspace_write(ctx, "lines.txt", "a\nb\nc\nd\ne")
    result = tool_workspace_read(ctx, "lines.txt", end_line=2)
    assert "Lines 1-2 of 5:" in result
    assert "a" in result
    assert "b" in result
    assert "c" not in result


def test_read_line_range(ctx):
    tool_workspace_write(ctx, "lines.txt", "aaa\nbbb\nccc\nddd\neee")
    result = tool_workspace_read(ctx, "lines.txt", start_line=2, end_line=4)
    assert "Lines 2-4 of 5:" in result
    assert "bbb" in result
    assert "ccc" in result
    assert "ddd" in result
    assert "aaa" not in result
    assert "eee" not in result


def test_read_out_of_range(ctx):
    tool_workspace_write(ctx, "short.txt", "one\ntwo\nthree")
    result = tool_workspace_read(ctx, "short.txt", end_line=100)
    # Should just return to end, no error
    assert "error" not in result.lower()
    assert "three" in result


# -- workspace_edit tests --


def test_edit_simple(ctx):
    tool_workspace_write(ctx, "code.py", "def hello():\n    print('greet')\n")
    result = tool_workspace_edit(ctx, "code.py", "hello", "world")
    assert "replaced 1" in result.lower()
    content = ctx.config.workspace_path.joinpath("code.py").read_text()
    assert "def world():" in content
    assert "print('greet')" in content


def test_edit_not_found(ctx):
    tool_workspace_write(ctx, "code.py", "def hello():\n    pass\n")
    result = tool_workspace_edit(ctx, "code.py", "nonexistent", "replacement")
    assert "error" in result.lower()
    assert "not found" in result.lower()


def test_edit_ambiguous(ctx):
    tool_workspace_write(ctx, "code.py", "foo = 1\nbar = foo\nbaz = foo\n")
    result = tool_workspace_edit(ctx, "code.py", "foo", "qux")
    assert "error" in result.lower()
    assert "3 matches" in result


def test_edit_replace_all(ctx):
    tool_workspace_write(ctx, "code.py", "foo = 1\nbar = foo\nbaz = foo\n")
    result = tool_workspace_edit(ctx, "code.py", "foo", "qux", replace_all=True)
    assert "replaced 3" in result.lower()
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
    assert "outside" in result.lower()


def test_edit_nonexistent_file(ctx):
    result = tool_workspace_edit(ctx, "nope.py", "a", "b")
    assert "error" in result.lower()
    assert "not found" in result.lower()


# -- workspace_append tests --


def test_append_creates_file(ctx):
    result = tool_workspace_append(ctx, "new.txt", "hello")
    assert "Appended" in result
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
    assert "outside" in result.lower()


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
    assert "outside" in result.text.lower()
    assert result.media == []


def test_file_share_not_found(ctx):
    result = tool_file_share(ctx, "nonexistent.txt")
    assert "not found" in result.text.lower()
    assert result.media == []
