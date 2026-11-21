"""Deterministic read-only classifier (#1641)."""

import pytest

from decafclaw.shell_readonly import is_read_only_command


@pytest.fixture
def sandbox(tmp_path):
    workspace = tmp_path / "workspace"
    (workspace / "sub").mkdir(parents=True)
    (workspace / "file.txt").write_text("x")
    (workspace / "sub" / "nested.txt").write_text("y")
    return workspace


def _read_only(command: str, sandbox, cwd=None) -> bool:
    return is_read_only_command(command, cwd=cwd or sandbox, workspace=sandbox)


@pytest.mark.parametrize(
    "command",
    [
        "ls -la",
        "ls -la .",
        "cat file.txt",
        "head -20 file.txt",
        "tail -f file.txt",
        "grep -rn TODO sub/",
        "egrep 'a|b' file.txt",
        "rg -n TODO sub/",
        "find . -name '*.py'",
        "pwd",
        "which python3",
        "jq '.x' file.txt",
        "wc -l file.txt",
        "du -sh .",
        "file file.txt",
        "stat file.txt",
        "echo hello",
        "diff file.txt file.txt",
        "cut -d: -f1 file.txt",
        "sort -u file.txt",
        "uniq file.txt",
        "tree sub",
        "git status --porcelain",
        "git log --oneline -5",
        "git diff HEAD~1",
        "git show HEAD:file.txt",
        "git branch -a",
        "git remote -v",
        "git config user.name",
        "git config --get user.name",
        "git stash list",
        "git worktree list",
        "gh pr view 1",
        "gh issue list",
        "gh api repos/x/y",
        "curl -I https://example.com",
        "ls | grep file",
        "git status && git log -1",
        "cat file.txt; wc -l file.txt",
        "grep 'a;b' file.txt",
        "git log --format='%h (%s)'",
        "echo '>not-a-redirect'",
        "cat sub/../file.txt",
    ],
)
def test_read_only_commands_are_allowed(command, sandbox):
    assert _read_only(command, sandbox) is True


@pytest.mark.parametrize(
    "command",
    [
        "cat file.txt > out.txt",
        "cat file.txt 2>/dev/null",
        "cat < file.txt",
        "ls; rm -rf x",
        "ls && rm -rf x",
        "ls || rm -rf x",
        "ls | sh",
        "ls & rm -rf x",
        "cat $(rm -rf x)",
        "echo `rm -rf x`",
        "ls (foo)",
        "cat /etc/passwd",
        "cat ../../secret",
        "cat ~/.ssh/id_rsa",
        "cat $HOME/file",
        "find . -exec rm {} ;",
        "find . -delete",
        "fd -x rm {}",
        "rg --pre 'cat' foo",
        "sort -o out.txt file.txt",
        "sort --compress-program=evil file.txt",
        "tree -o out.txt",
        "uniq file.txt out.txt",
        "file -C file.txt",
        "git diff --output=out.txt",
        "git -c core.pager=evil log",
        "git branch new-branch",
        "git remote add origin https://x",
        "git tag v1",
        "git stash",
        "git reflog expire --expire=now",
        "gh pr checkout 1",
        "gh pr view --web",
        "gh api -X POST repos/x",
        "gh api -f a=b repos/x",
        "curl -o out https://example.com",
        "curl -I file:///etc/passwd",
        "curl https://example.com",
        "sed -n 1p file.txt",
        "awk '{print}' file.txt",
        "python -c 'print(1)'",
        "sh -c 'ls'",
        "xargs ls",
    ],
)
def test_non_read_only_commands_are_rejected(command, sandbox):
    assert _read_only(command, sandbox) is False


def test_glob_of_contained_path_is_allowed(sandbox):
    assert _read_only("ls *.txt", sandbox) is True


def test_relative_path_staying_inside_workspace_is_allowed(sandbox):
    assert _read_only("cat ../file.txt", sandbox, cwd=sandbox / "sub") is True


def test_relative_escape_out_of_workspace_is_rejected(sandbox):
    assert _read_only("cat ../../outside.txt", sandbox, cwd=sandbox / "sub") is False


def test_symlink_escape_is_rejected(sandbox, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    (sandbox / "link.txt").symlink_to(outside)
    assert _read_only("cat link.txt", sandbox) is False


def test_literal_hyphen_argument_is_allowed(sandbox):
    assert _read_only("cat -", sandbox) is True
