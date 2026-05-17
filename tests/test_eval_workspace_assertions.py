"""Unit tests for `_check_workspace_assertions` — post-turn workspace state."""

from pathlib import Path

import pytest

from decafclaw.eval.runner import _check_workspace_assertions


def _seed(tmp_path: Path, files: dict[str, str]) -> Path:
    for rel, content in files.items():
        dest = tmp_path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
    return tmp_path


def test_no_expect_workspace_is_a_pass(tmp_path: Path):
    passed, _ = _check_workspace_assertions({}, tmp_path)
    assert passed


def test_workspace_files_substring_match_passes(tmp_path: Path):
    _seed(tmp_path, {"notes.md": "Today we shipped the new auth system."})
    test_case = {"expect_workspace": {"workspace_files": {"notes.md": "auth system"}}}
    passed, _ = _check_workspace_assertions(test_case, tmp_path)
    assert passed


def test_workspace_files_substring_is_case_insensitive(tmp_path: Path):
    _seed(tmp_path, {"notes.md": "AUTH"})
    test_case = {"expect_workspace": {"workspace_files": {"notes.md": "auth"}}}
    passed, _ = _check_workspace_assertions(test_case, tmp_path)
    assert passed


def test_workspace_files_substring_miss_fails_with_reason(tmp_path: Path):
    _seed(tmp_path, {"notes.md": "hello world"})
    test_case = {"expect_workspace": {"workspace_files": {"notes.md": "goodbye"}}}
    passed, reason = _check_workspace_assertions(test_case, tmp_path)
    assert not passed
    assert "notes.md" in reason
    assert "goodbye" in reason


def test_workspace_files_regex_match_passes(tmp_path: Path):
    _seed(tmp_path, {"page.md": "---\nsummary: about cats\n---\n\nBody"})
    test_case = {
        "expect_workspace": {
            "workspace_files": {"page.md": "re:^---\\nsummary: about cats"}
        }
    }
    passed, _ = _check_workspace_assertions(test_case, tmp_path)
    assert passed


def test_workspace_files_regex_supports_dotall(tmp_path: Path):
    """re.DOTALL lets `.+` match newlines — important for multi-section files."""
    _seed(tmp_path, {"page.md": "## A\n\nfoo\n\n## B\n\nbar\n"})
    test_case = {
        "expect_workspace": {
            "workspace_files": {"page.md": "re:## A.+## B"}
        }
    }
    passed, _ = _check_workspace_assertions(test_case, tmp_path)
    assert passed


def test_workspace_files_regex_miss_fails_with_pattern_in_reason(tmp_path: Path):
    _seed(tmp_path, {"page.md": "no match here"})
    test_case = {
        "expect_workspace": {"workspace_files": {"page.md": "re:^---\\nsummary"}}
    }
    passed, reason = _check_workspace_assertions(test_case, tmp_path)
    assert not passed
    assert "page.md" in reason
    assert "pattern" in reason


def test_workspace_files_missing_file_fails(tmp_path: Path):
    test_case = {
        "expect_workspace": {"workspace_files": {"never-existed.md": "anything"}}
    }
    passed, reason = _check_workspace_assertions(test_case, tmp_path)
    assert not passed
    assert "never-existed.md" in reason
    assert "exist" in reason


def test_workspace_file_exists_passes(tmp_path: Path):
    _seed(tmp_path, {"a.txt": "", "sub/b.txt": ""})
    test_case = {
        "expect_workspace": {"workspace_file_exists": ["a.txt", "sub/b.txt"]}
    }
    passed, _ = _check_workspace_assertions(test_case, tmp_path)
    assert passed


def test_workspace_file_exists_fails_on_missing(tmp_path: Path):
    _seed(tmp_path, {"a.txt": ""})
    test_case = {
        "expect_workspace": {"workspace_file_exists": ["a.txt", "missing.txt"]}
    }
    passed, reason = _check_workspace_assertions(test_case, tmp_path)
    assert not passed
    assert "missing.txt" in reason


def test_workspace_file_absent_passes(tmp_path: Path):
    test_case = {
        "expect_workspace": {"workspace_file_absent": ["nope.txt", "also/nope.txt"]}
    }
    passed, _ = _check_workspace_assertions(test_case, tmp_path)
    assert passed


def test_workspace_file_absent_fails_when_present(tmp_path: Path):
    _seed(tmp_path, {"hello.txt": "x"})
    test_case = {"expect_workspace": {"workspace_file_absent": ["hello.txt"]}}
    passed, reason = _check_workspace_assertions(test_case, tmp_path)
    assert not passed
    assert "hello.txt" in reason
    assert "unexpectedly" in reason


def test_combining_all_three_fields(tmp_path: Path):
    _seed(tmp_path, {"keep.md": "kept", "also-keep.md": "also"})
    test_case = {
        "expect_workspace": {
            "workspace_files": {"keep.md": "kept"},
            "workspace_file_exists": ["also-keep.md"],
            "workspace_file_absent": ["gone.md"],
        }
    }
    passed, _ = _check_workspace_assertions(test_case, tmp_path)
    assert passed


def test_absolute_path_rejected(tmp_path: Path):
    test_case = {
        "expect_workspace": {"workspace_file_exists": ["/etc/passwd"]}
    }
    with pytest.raises(ValueError, match="relative"):
        _check_workspace_assertions(test_case, tmp_path)


def test_parent_dir_escape_rejected(tmp_path: Path):
    test_case = {
        "expect_workspace": {"workspace_file_exists": ["../escape.txt"]}
    }
    with pytest.raises(ValueError, match="escapes"):
        _check_workspace_assertions(test_case, tmp_path)


def test_parent_dir_escape_via_workspace_files_rejected(tmp_path: Path):
    """Sandbox check applies symmetrically across all three fields."""
    test_case = {
        "expect_workspace": {"workspace_files": {"../escape.txt": "anything"}}
    }
    with pytest.raises(ValueError, match="escapes"):
        _check_workspace_assertions(test_case, tmp_path)
