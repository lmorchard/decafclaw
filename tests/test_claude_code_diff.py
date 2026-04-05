"""Tests for git diff capture helpers — _get_git_head and _capture_git_diff."""

import subprocess

import pytest

from decafclaw.skills.claude_code.tools import _capture_git_diff, _get_git_head


def _git(cwd, *args):
    """Run a git command in the given directory."""
    subprocess.run(["git", "-C", str(cwd)] + list(args),
                   check=True, capture_output=True)


def _init_repo(tmp_path):
    """Create a git repo with one initial commit."""
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "test@test.com")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "initial.txt").write_text("hello\n")
    _git(tmp_path, "add", "initial.txt")
    _git(tmp_path, "commit", "-m", "initial")


@pytest.mark.asyncio
async def test_get_git_head_returns_hash(tmp_path):
    """Returns a 40-char hex hash for a repo with commits."""
    _init_repo(tmp_path)
    head = await _get_git_head(str(tmp_path))
    assert head is not None
    assert len(head) == 40
    assert all(c in "0123456789abcdef" for c in head)


@pytest.mark.asyncio
async def test_get_git_head_not_a_repo(tmp_path):
    """Returns None for a plain directory."""
    head = await _get_git_head(str(tmp_path))
    assert head is None


@pytest.mark.asyncio
async def test_get_git_head_empty_repo(tmp_path):
    """Returns None for a repo with no commits."""
    _git(tmp_path, "init")
    head = await _get_git_head(str(tmp_path))
    assert head is None


@pytest.mark.asyncio
async def test_capture_diff_modified_file(tmp_path):
    """Captures diff for a modified tracked file."""
    _init_repo(tmp_path)
    head = await _get_git_head(str(tmp_path))

    (tmp_path / "initial.txt").write_text("changed\n")

    diff = await _capture_git_diff(str(tmp_path), head)
    assert diff is not None
    assert "hello" in diff
    assert "changed" in diff


@pytest.mark.asyncio
async def test_capture_diff_new_untracked_file(tmp_path):
    """Lists new untracked files."""
    _init_repo(tmp_path)
    head = await _get_git_head(str(tmp_path))

    (tmp_path / "newfile.py").write_text("print('hi')\n")

    diff = await _capture_git_diff(str(tmp_path), head)
    assert diff is not None
    assert "New untracked files:" in diff
    assert "newfile.py" in diff


@pytest.mark.asyncio
async def test_capture_diff_committed_change(tmp_path):
    """Captures diff for changes committed after baseline."""
    _init_repo(tmp_path)
    head = await _get_git_head(str(tmp_path))

    # Make a new commit after baseline
    (tmp_path / "new.txt").write_text("new content\n")
    _git(tmp_path, "add", "new.txt")
    _git(tmp_path, "commit", "-m", "add new file")

    diff = await _capture_git_diff(str(tmp_path), head)
    assert diff is not None
    assert "new content" in diff


@pytest.mark.asyncio
async def test_capture_diff_no_baseline():
    """Returns None when baseline_ref is None."""
    diff = await _capture_git_diff("/tmp", None)
    assert diff is None


@pytest.mark.asyncio
async def test_capture_diff_no_changes(tmp_path):
    """Returns empty string when nothing changed since baseline."""
    _init_repo(tmp_path)
    head = await _get_git_head(str(tmp_path))

    diff = await _capture_git_diff(str(tmp_path), head)
    assert diff == ""


@pytest.mark.asyncio
async def test_capture_diff_committed_plus_unstaged(tmp_path):
    """Committed change + unstaged edit shows both without duplication."""
    _init_repo(tmp_path)
    head = await _get_git_head(str(tmp_path))

    # Make a commit after baseline
    (tmp_path / "committed.txt").write_text("committed content\n")
    _git(tmp_path, "add", "committed.txt")
    _git(tmp_path, "commit", "-m", "add committed file")

    # Also make an unstaged edit to the original file
    (tmp_path / "initial.txt").write_text("unstaged edit\n")

    diff = await _capture_git_diff(str(tmp_path), head)
    assert diff is not None
    # Both changes should appear
    assert "committed content" in diff
    assert "unstaged edit" in diff
    # The committed content should appear exactly once (no duplication)
    assert diff.count("committed content") == 1
