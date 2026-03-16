"""Tests for workspace file tools — path sandboxing and file sharing."""

from decafclaw.tools.workspace_tools import (
    MAX_READ_LINES,
    _resolve_safe,
    tool_file_share,
    tool_workspace_append,
    tool_workspace_delete,
    tool_workspace_diff,
    tool_workspace_edit,
    tool_workspace_glob,
    tool_workspace_insert,
    tool_workspace_list,
    tool_workspace_move,
    tool_workspace_read,
    tool_workspace_replace_lines,
    tool_workspace_search,
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


# -- workspace_read large file guard --


def test_read_large_file_capped(ctx):
    lines = [f"line {i}" for i in range(1, 500)]
    tool_workspace_write(ctx, "big.txt", "\n".join(lines))
    result = tool_workspace_read(ctx, "big.txt")
    assert f"showing first {MAX_READ_LINES}" in result.lower()
    assert "line 1" in result
    assert f"line {MAX_READ_LINES}" in result
    assert f"line {MAX_READ_LINES + 1}" not in result


def test_read_large_file_with_range_not_capped(ctx):
    lines = [f"line {i}" for i in range(1, 500)]
    tool_workspace_write(ctx, "big.txt", "\n".join(lines))
    result = tool_workspace_read(ctx, "big.txt", start_line=1, end_line=300)
    assert "line 300" in result
    assert "showing first" not in result.lower()


# -- workspace_move tests --


def test_move_basic(ctx):
    tool_workspace_write(ctx, "old.txt", "content")
    result = tool_workspace_move(ctx, "old.txt", "new.txt")
    assert "moved" in result.lower()
    assert not ctx.config.workspace_path.joinpath("old.txt").exists()
    assert ctx.config.workspace_path.joinpath("new.txt").read_text() == "content"


def test_move_to_subdir(ctx):
    tool_workspace_write(ctx, "file.txt", "data")
    tool_workspace_move(ctx, "file.txt", "sub/dir/file.txt")
    assert ctx.config.workspace_path.joinpath("sub/dir/file.txt").read_text() == "data"


def test_move_not_found(ctx):
    result = tool_workspace_move(ctx, "nope.txt", "dest.txt")
    assert "error" in result.lower()
    assert "not found" in result.lower()


def test_move_destination_exists(ctx):
    tool_workspace_write(ctx, "a.txt", "aaa")
    tool_workspace_write(ctx, "b.txt", "bbb")
    result = tool_workspace_move(ctx, "a.txt", "b.txt")
    assert "error" in result.lower()
    assert "already exists" in result.lower()


def test_move_escape_blocked_src(ctx):
    result = tool_workspace_move(ctx, "../../evil.txt", "dest.txt")
    assert "outside" in result.lower()


def test_move_escape_blocked_dst(ctx):
    tool_workspace_write(ctx, "ok.txt", "data")
    result = tool_workspace_move(ctx, "ok.txt", "../../evil.txt")
    assert "outside" in result.lower()


# -- workspace_delete tests --


def test_delete_basic(ctx):
    tool_workspace_write(ctx, "doomed.txt", "bye")
    result = tool_workspace_delete(ctx, "doomed.txt")
    assert "deleted" in result.lower()
    assert not ctx.config.workspace_path.joinpath("doomed.txt").exists()


def test_delete_not_found(ctx):
    result = tool_workspace_delete(ctx, "nope.txt")
    assert "error" in result.lower()
    assert "not found" in result.lower()


def test_delete_directory_blocked(ctx):
    ctx.config.workspace_path.joinpath("mydir").mkdir(parents=True)
    result = tool_workspace_delete(ctx, "mydir")
    assert "error" in result.lower()
    assert "directory" in result.lower()


def test_delete_escape_blocked(ctx):
    result = tool_workspace_delete(ctx, "../../evil.txt")
    assert "outside" in result.lower()


# -- workspace_diff tests --


def test_diff_shows_changes(ctx):
    tool_workspace_write(ctx, "v1.txt", "line1\nline2\nline3\n")
    tool_workspace_write(ctx, "v2.txt", "line1\nLINE2\nline3\n")
    result = tool_workspace_diff(ctx, "v1.txt", "v2.txt")
    assert "-line2" in result
    assert "+LINE2" in result
    assert "v1.txt" in result
    assert "v2.txt" in result


def test_diff_identical(ctx):
    tool_workspace_write(ctx, "a.txt", "same\n")
    tool_workspace_write(ctx, "b.txt", "same\n")
    result = tool_workspace_diff(ctx, "a.txt", "b.txt")
    assert "identical" in result.lower()


def test_diff_not_found(ctx):
    tool_workspace_write(ctx, "exists.txt", "data\n")
    result = tool_workspace_diff(ctx, "exists.txt", "nope.txt")
    assert "error" in result.lower()
    assert "not found" in result.lower()


def test_diff_escape_blocked(ctx):
    result = tool_workspace_diff(ctx, "../../evil.txt", "other.txt")
    assert "outside" in result.lower()


# -- edit tools mini-diff output --


def test_edit_returns_diff(ctx):
    tool_workspace_write(ctx, "f.txt", "aaa\nbbb\nccc\n")
    result = tool_workspace_edit(ctx, "f.txt", "bbb", "BBB")
    assert "---" in result  # diff header
    assert "-bbb" in result
    assert "+BBB" in result


def test_insert_returns_diff(ctx):
    tool_workspace_write(ctx, "f.txt", "aaa\nccc\n")
    result = tool_workspace_insert(ctx, "f.txt", 2, "bbb")
    assert "---" in result
    assert "+bbb" in result


def test_replace_lines_returns_diff(ctx):
    tool_workspace_write(ctx, "f.txt", "aaa\nbbb\nccc\n")
    result = tool_workspace_replace_lines(ctx, "f.txt", 2, 2, "BBB")
    assert "---" in result
    assert "-bbb" in result
    assert "+BBB" in result


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
    assert "error" in result.lower()
    result = tool_workspace_insert(ctx, "f.txt", 100, "bad")
    assert "error" in result.lower()


def test_insert_multiline_content(ctx):
    tool_workspace_write(ctx, "f.txt", "aaa\nddd\n")
    tool_workspace_insert(ctx, "f.txt", 2, "bbb\nccc")
    content = ctx.config.workspace_path.joinpath("f.txt").read_text()
    lines = content.splitlines()
    assert lines == ["aaa", "bbb", "ccc", "ddd"]


def test_insert_escape_blocked(ctx):
    result = tool_workspace_insert(ctx, "../../evil.txt", 1, "bad")
    assert "outside" in result.lower()


# -- workspace_replace_lines tests --


def test_replace_lines_basic(ctx):
    tool_workspace_write(ctx, "f.txt", "aaa\nbbb\nccc\nddd\n")
    result = tool_workspace_replace_lines(ctx, "f.txt", 2, 3, "BBB\nCCC")
    assert "replaced" in result.lower()
    content = ctx.config.workspace_path.joinpath("f.txt").read_text()
    lines = content.splitlines()
    assert lines == ["aaa", "BBB", "CCC", "ddd"]


def test_replace_lines_delete(ctx):
    tool_workspace_write(ctx, "f.txt", "aaa\nbbb\nccc\nddd\n")
    result = tool_workspace_replace_lines(ctx, "f.txt", 2, 3)
    assert "deleted" in result.lower()
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
    assert "error" in result.lower()
    result = tool_workspace_replace_lines(ctx, "f.txt", 0, 2, "x")
    assert "error" in result.lower()
    result = tool_workspace_replace_lines(ctx, "f.txt", 1, 100, "x")
    assert "error" in result.lower()


def test_replace_lines_escape_blocked(ctx):
    result = tool_workspace_replace_lines(ctx, "../../evil.txt", 1, 2, "x")
    assert "outside" in result.lower()


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


# -- workspace_search tests --


def test_search_basic(ctx):
    tool_workspace_write(ctx, "hello.txt", "hello world\ngoodbye world\n")
    result = tool_workspace_search(ctx, "hello")
    assert "hello.txt" in result
    assert "hello world" in result


def test_search_regex(ctx):
    tool_workspace_write(ctx, "data.txt", "count: 42\nname: alice\ncount: 7\n")
    result = tool_workspace_search(ctx, r"\d+")
    assert "42" in result
    assert "7" in result


def test_search_multiple_files(ctx):
    tool_workspace_write(ctx, "a.txt", "needle here\n")
    tool_workspace_write(ctx, "b.txt", "needle there\n")
    result = tool_workspace_search(ctx, "needle")
    assert "a.txt" in result
    assert "b.txt" in result


def test_search_glob_filter(ctx):
    tool_workspace_write(ctx, "code.py", "needle in python\n")
    tool_workspace_write(ctx, "doc.md", "needle in markdown\n")
    result = tool_workspace_search(ctx, "needle", glob="*.py")
    assert "code.py" in result
    assert "doc.md" not in result


def test_search_context_lines(ctx):
    tool_workspace_write(ctx, "f.txt", "aaa\nbbb\nccc\nddd\neee\n")
    result = tool_workspace_search(ctx, "ccc", context_lines=1)
    assert "bbb" in result
    assert "ddd" in result
    # aaa should not appear with only 1 line of context
    assert "aaa" not in result


def test_search_single_file(ctx):
    tool_workspace_write(ctx, "target.txt", "find me\n")
    tool_workspace_write(ctx, "other.txt", "find me too\n")
    result = tool_workspace_search(ctx, "find", path="target.txt")
    assert "target.txt" in result
    assert "other.txt" not in result


def test_search_no_matches(ctx):
    tool_workspace_write(ctx, "f.txt", "nothing here\n")
    result = tool_workspace_search(ctx, "zzzzz")
    assert result == "(no matches)"


def test_search_invalid_regex(ctx):
    ctx.config.workspace_path.mkdir(parents=True, exist_ok=True)
    result = tool_workspace_search(ctx, "[invalid")
    assert "error" in result.lower()
    assert "regex" in result.lower()


def test_search_escape_blocked(ctx):
    result = tool_workspace_search(ctx, "test", path="../../etc")
    assert "outside" in result.lower()


# -- workspace_glob tests --


def test_glob_basic(ctx):
    tool_workspace_write(ctx, "a.py", "python\n")
    tool_workspace_write(ctx, "b.py", "python\n")
    tool_workspace_write(ctx, "c.txt", "text\n")
    result = tool_workspace_glob(ctx, "*.py")
    assert "a.py" in result
    assert "b.py" in result
    assert "c.txt" not in result


def test_glob_nested(ctx):
    tool_workspace_write(ctx, "sub/deep/file.py", "nested\n")
    result = tool_workspace_glob(ctx, "*.py")
    assert "sub/deep/file.py" in result


def test_glob_specific_name(ctx):
    tool_workspace_write(ctx, "config.yaml", "key: val\n")
    tool_workspace_write(ctx, "other.yaml", "key: val\n")
    result = tool_workspace_glob(ctx, "config.yaml")
    assert "config.yaml" in result
    assert "other.yaml" not in result


def test_glob_no_matches(ctx):
    tool_workspace_write(ctx, "a.txt", "text\n")
    result = tool_workspace_glob(ctx, "*.zzz")
    assert result == "(no matches)"


def test_glob_with_subpath(ctx):
    tool_workspace_write(ctx, "src/a.py", "code\n")
    tool_workspace_write(ctx, "tests/b.py", "test\n")
    result = tool_workspace_glob(ctx, "*.py", path="src")
    assert "src/a.py" in result
    assert "tests/b.py" not in result


def test_glob_escape_blocked(ctx):
    result = tool_workspace_glob(ctx, "*.py", path="../../etc")
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
