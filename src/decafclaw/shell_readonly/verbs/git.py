"""``git`` read-only contract (#1641).

Contract: only subcommands that read the repository and print, with no flag
that runs a configured program, writes a file, or touches another repository.
Repository config can run programs, so ``git status`` (fsmonitor), ``git
diff``/``show`` (textconv), ``fetch``/``ls-remote`` (upload-pack/transport),
and patch-producing ``git log`` are deliberately excluded; they fall through
to the judge, which can still allow them. Every escape flag is enumerated.
"""

from pathlib import Path

from ..containment import paths_contained

VERBS = frozenset({"git"})

# A silent read-only tier must not run programs chosen by repository config.
# `git status` runs `core.fsmonitor`, `git diff`/`log -p`/`show` run
# `diff.*.textconv`, `git fetch`/`ls-remote` run `--upload-pack` or transport
# helpers, and `gpg.program` runs for signature output. Those subcommands stay
# out of this set; they fall through to the judge, which can still allow them.
_GIT_READ_ONLY_SUBCOMMANDS = frozenset(
    {
        "blame",
        "describe",
        "rev-parse",
        "rev-list",
        "ls-files",
        "ls-tree",
        "cat-file",
        "shortlog",
        "grep",
        "merge-base",
        "count-objects",
        "for-each-ref",
        "name-rev",
        "show-ref",
        "reflog",
    }
)

# Flags that make an otherwise-safe git subcommand execute a configured
# program, write a file, or read from an unexpected repository.
_GIT_EXEC_FLAGS = (
    "--output",
    "--exec-path",
    "--git-dir",
    "--work-tree",
    "--textconv",
    "--ext-diff",
    "--show-signature",
    "--open-files-in-pager",
    "--filters",
    "--upload-pack",
    "--remerge-diff",
    "--batch-command",
)

# `git log` is safe while it produces no patch: `--stat` does not run
# textconv (verified), but patch output does. Reject every diff-producing,
# signature-verifying, or line-log mode.
_GIT_LOG_UNSAFE_FLAGS = (
    "-p",
    "--patch",
    "-u",
    "--unified",
    "-U",
    "--cc",
    "-m",
    "-c",
    "--combined",
    "-L",
    "--line-log",
    "--submodule",
    "--remerge-diff",
    "--show-signature",
    "--ext-diff",
    "--textconv",
    "--diff-merges",
    "--full-diff",
    "--word-diff",
    "--color-words",
    "--binary",
    "--function-context",
)

_GIT_TAG_WRITE_FLAGS = frozenset(
    {"-d", "--delete", "-a", "--annotate", "-s", "--sign", "-f", "--force", "-m", "--message"}
)

_GIT_BRANCH_WRITE_PREFIXES = (
    "-d",
    "-D",
    "-m",
    "-M",
    "-c",
    "-C",
    "-f",
    "-u",
    "-e",
    "--delete",
    "--move",
    "--copy",
    "--force",
    "--set-upstream-to",
    "--unset-upstream",
    "--track",
    "--no-track",
    "--edit-description",
    "--create-reflog",
    "--no-create-reflog",
)

_GIT_CONFIG_READ_ACTIONS = frozenset({"--get", "--get-all", "--list", "-l", "get"})


def _git_log_read_only(args: list[str]) -> bool:
    """`git log` is safe only while it produces no patch and verifies nothing."""
    for token in args:
        if any(token.startswith(flag) for flag in _GIT_LOG_UNSAFE_FLAGS if flag.startswith("--")):
            return False
        if token in ("-p", "-u", "-m", "-c", "--cc", "--combined", "-W", "--function-context"):
            return False
        if token.startswith(("-p", "-u", "-m", "-U", "-L")):
            return False
        if "%G" in token:
            return False
    return True


def _git_read_only(args: list[str]) -> bool:
    if not args:
        return False
    if any(token == "-c" or token.startswith(_GIT_EXEC_FLAGS) for token in args):
        return False
    subcommand, rest = args[0], args[1:]
    if subcommand == "log":
        return _git_log_read_only(rest)
    if subcommand in _GIT_READ_ONLY_SUBCOMMANDS:
        if subcommand == "reflog":
            return not rest or rest[0] == "show" or rest[0].startswith("-")
        if subcommand == "describe":
            return not any(token == "--dirty" or token.startswith("--dirty=") for token in rest)
        return True
    if subcommand == "branch":
        if any(token.startswith(_GIT_BRANCH_WRITE_PREFIXES) for token in rest):
            return False
        return all(token.startswith("-") for token in rest)
    if subcommand == "remote":
        # Listing only: `remote show`/`get-url` contact or interpret a remote.
        return not rest or all(token.startswith("-") for token in rest)
    if subcommand == "tag":
        if any(token in _GIT_TAG_WRITE_FLAGS for token in rest):
            return False
        return not rest or any(token in ("-l", "--list") for token in rest)
    if subcommand == "stash":
        return bool(rest) and rest[0] == "list"
    if subcommand == "worktree":
        return bool(rest) and rest[0] == "list"
    if subcommand == "config":
        if not rest:
            return False
        if rest[0] in _GIT_CONFIG_READ_ACTIONS or any(token in _GIT_CONFIG_READ_ACTIONS for token in rest):
            return True
        # `git config <key>` prints one value; a second positional writes it.
        return len(rest) == 1 and not rest[0].startswith("-")
    return False


def allows(verb: str, args: list[str], cwd: Path, workspace: Path) -> bool:
    if not _git_read_only(args):
        return False
    return paths_contained(args, cwd, workspace)
