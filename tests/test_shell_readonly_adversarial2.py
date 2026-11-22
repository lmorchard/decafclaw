"""Second adversarial pass: exotic escape hatches (#1641 review).

Suspicions worth testing:
- jq's `$ENV` / `env` builtin has many spellings; the guard regex may miss some
- jq flag forms that read files
- git/gh/curl flag forms not covered by the first pass
- TOCTOU-ish path resolution
"""

import pytest

from decafclaw.shell_readonly import is_read_only_command


@pytest.fixture
def ws(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "afile.txt").write_text("hello\n", encoding="utf-8")
    (tmp_path / "filter.jq").write_text(".", encoding="utf-8")
    return tmp_path


# jq can print the process environment (which holds API keys) via a builtin.
# The guard is a regex; regexes on a shell-ish expression language are leaky.
JQ_ENV_LEAKS = [
    "jq -n env",
    "jq -n $ENV",
    "jq -n env|keys",
    "jq -n env[]",
    "jq -n '$ENV[]'",
    "jq -n 'env[\"HOME\"]'",
    "jq -n 'env|to_entries'",
    "jq -n '$ENV.HOME'",
    "jq -n '. + env'",
]


@pytest.mark.parametrize("command", JQ_ENV_LEAKS)
def test_jq_cannot_print_the_environment(ws, command):
    assert is_read_only_command(command, cwd=ws, workspace=ws) is False, (
        f"SECURITY: {command!r} classified read-only but can print the process environment, which holds credentials"
    )


# jq flags that read arbitrary files
JQ_FILE_READS = [
    "jq --rawfile x /etc/passwd .",
    "jq -f /etc/passwd .",
    "jq --from-file /etc/passwd",
    "jq --slurpfile x /etc/passwd .",
]


@pytest.mark.parametrize("command", JQ_FILE_READS)
def test_jq_cannot_read_outside_the_workspace(ws, command):
    assert is_read_only_command(command, cwd=ws, workspace=ws) is False, (
        f"SECURITY: {command!r} classified read-only but reads a file outside the workspace"
    )


# A contained jq filter file is legitimate; keep the tier useful.
def test_jq_contained_filter_file_is_allowed(ws):
    assert is_read_only_command("jq -f filter.jq afile.txt", cwd=ws, workspace=ws) is True


# Wrapper verbs that execute their arguments.
WRAPPERS = [
    "env rm -rf sub",
    "nice rm -rf sub",
    "nohup rm -rf sub",
    "time rm -rf sub",
    "command rm -rf sub",
    "builtin rm -rf sub",
    "exec rm -rf sub",
    "sudo rm -rf sub",
    "watch rm -rf sub",
    "strace rm -rf sub",
]


@pytest.mark.parametrize("command", WRAPPERS)
def test_wrapper_verbs_are_rejected(ws, command):
    assert is_read_only_command(command, cwd=ws, workspace=ws) is False, (
        f"SECURITY: {command!r} classified read-only but wraps an executing verb"
    )


# git flag forms not in the explicit exec-flag list.
GIT_EXTRA = [
    "git --config-env=core.pager=EVIL log",
    "git --exec-path=/tmp log",
    "git -c core.fsmonitor=sh ls-files",
    "git log --output=out.txt",
    "git diff --output=out.txt",
    "git stash list --format=%H > afile.txt",
]


@pytest.mark.parametrize("command", GIT_EXTRA)
def test_git_extra_escape_hatches_are_rejected(ws, command):
    assert is_read_only_command(command, cwd=ws, workspace=ws) is False, (
        f"SECURITY: {command!r} classified read-only but can execute or write"
    )


# gh flag forms
GH_EXTRA = [
    "gh api --input body.json /x",
    "gh api /x --input body.json",
    "gh api -XPOST /x",
    "gh api --method POST /x",
    "gh auth token",
    "gh config set editor vim",
    "gh release create v1",
    "gh repo clone x/y",
]


@pytest.mark.parametrize("command", GH_EXTRA)
def test_gh_extra_escape_hatches_are_rejected(ws, command):
    assert is_read_only_command(command, cwd=ws, workspace=ws) is False, (
        f"SECURITY: {command!r} classified read-only but writes or leaks a credential"
    )
