"""Deterministic read-only shell classifier (#1641).

The aux approval judge used to spend a model call deciding whether ``ls``,
``git status``, or ``grep`` needed a human. It declined many of them — not for
risk, but because a native tool could do the job. This module decides the
read-only case deterministically instead: a command is read-only only when
*every* pipeline/sequence segment is a known read-only verb, every path
argument resolves inside the workspace, and the command carries no shell
feature that could redirect, execute, or escape:

- redirection (``>``, ``<``), command substitution (``$(...)``, backticks),
  subshells (``(``, ``)``), environment/tilde expansion, and bare ``&``
  backgrounding all disqualify a command;
- ``;``, ``&&``, ``||``, newline, and ``|`` split it into segments, and every
  segment must qualify on its own;
- verbs with write/execute escape hatches (``find -exec``, ``git diff
  --output``, ``curl -o``, ``sort -o``) are rejected per verb.

Anything this cannot prove read-only falls through to the existing approval
pipeline unchanged. False negatives cost a judge call or a prompt; false
positives would silently run something unsafe, so the rules are conservative.
"""

import re
import shlex
from pathlib import Path

# Verbs that only read. Verbs with an execute/write escape hatch are handled
# explicitly below.
_READ_ONLY_VERBS = frozenset(
    {
        "ls",
        "cat",
        "head",
        "tail",
        "grep",
        "egrep",
        "fgrep",
        "rg",
        "find",
        "fd",
        "tree",
        "pwd",
        "which",
        "jq",
        "wc",
        "du",
        "file",
        "stat",
        "echo",
        "diff",
        "cut",
        "sort",
        "uniq",
        "basename",
        "dirname",
        "realpath",
        "readlink",
        "git",
        "gh",
        "curl",
    }
)

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

_GH_GROUPS = frozenset({"issue", "pr", "run", "repo", "release", "label", "search", "auth", "workflow", "api"})
_GH_READ_ACTIONS = frozenset({"list", "view", "diff", "checks", "status", "search"})
_GH_API_WRITE_FLAGS = frozenset({"-f", "-F", "--field", "--raw-field", "--input"})

_CURL_WRITE_SHORT_FLAGS = frozenset("oODcTdFKXnE")
_CURL_WRITE_LONG_FLAGS = (
    "--output",
    "--output-dir",
    "--remote-name",
    "--remote-header-name",
    "--dump-header",
    "--cookie-jar",
    "--upload-file",
    "--data",
    "--data-ascii",
    "--data-binary",
    "--data-raw",
    "--data-urlencode",
    "--form",
    "--form-string",
    "--config",
    "--request",
    "--json",
    "--trace",
    "--trace-ascii",
    "--stderr",
    "--libcurl",
    "--etag-save",
    "--hsts",
    "--alt-svc",
    # Sends ~/.netrc credentials to the URL; a silent HEAD must not.
    "--netrc",
    "--netrc-optional",
    "--sslkeylogfile",
    "--ssl-keylogfile",
    # Read a local credential/material file and send it (or load code).
    "--cert",
    "--key",
    "--cacert",
    "--capath",
    "--crlfile",
    "--netrc-file",
    "--proxy-cert",
    "--proxy-key",
    "--proxy-cacert",
    "--engine",
)

_FIND_EXEC_FLAGS = frozenset({"-exec", "-execdir", "-ok", "-okdir", "-delete", "-fls"})
_FIND_EXEC_PREFIXES = ("-fprint",)
_FD_EXEC_FLAGS = frozenset({"-x", "--exec", "-X", "--exec-batch"})
_RG_EXEC_FLAGS = ("--pre", "--pre-glob")
_SORT_WRITE_FLAGS = ("-o", "--output", "--compress-program")
_TREE_WRITE_FLAGS = ("-o", "--output")

_URL_SCHEME_RE = re.compile(r"^https?://", re.IGNORECASE)
_ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_ATTACHED_PATH_RE = re.compile(r"^-([A-Za-z]+)(.+)$")
# `jq -n env` prints the process environment, which can hold credentials. The
# builtin has many spellings — `env`, `$ENV`, `env[]`, `env["HOME"]`,
# `env|to_entries`, `$ENV.HOME` — and enumerating them by regex is a losing
# game, so the check is inverted: if a non-flag argument mentions the env
# builtin in any form (bare `env`, or `$ENV`, or `env`/`ENV` used as a value),
# refuse. A field literally named `.env` is still allowed, so a contained
# `jq .env file.json` keeps working.
_JQ_ENV_RE = re.compile(r"(?:^|[^A-Za-z0-9_.])env(?![A-Za-z0-9_])|\$ENV(?![A-Za-z0-9_])", re.IGNORECASE)


def _split_segments(command: str) -> list[str] | None:
    """Split a command into quote-aware top-level segments.

    Returns ``None`` when the command contains a construct this classifier
    cannot prove read-only: unbalanced quotes, redirection, command or process
    substitution, subshell parentheses, expansion, or bare backgrounding.
    """
    segments: list[str] = []
    buffer: list[str] = []
    quote: str | None = None
    index = 0
    length = len(command)
    while index < length:
        char = command[index]
        if quote == "'":
            # Single quotes are entirely literal to the shell.
            buffer.append(char)
            if char == "'":
                quote = None
            index += 1
            continue
        if quote == '"':
            if char == "\\" and index + 1 < length:
                buffer.append(char)
                buffer.append(command[index + 1])
                index += 2
                continue
            if char in "$`":
                return None
            buffer.append(char)
            if char == '"':
                quote = None
            index += 1
            continue
        if char in "'\"":
            quote = char
            buffer.append(char)
            index += 1
            continue
        if char == "\\":
            if index + 1 >= length:
                return None
            buffer.append(char)
            buffer.append(command[index + 1])
            index += 2
            continue
        if char in "$`":
            return None
        if char == "~" and (not buffer or buffer[-1] in " \t"):
            return None
        if char in "><()":
            return None
        if char == "&":
            if command.startswith("&&", index):
                segments.append("".join(buffer))
                buffer = []
                index += 2
                continue
            return None
        if char == "|":
            if command.startswith("||", index):
                segments.append("".join(buffer))
                buffer = []
                index += 2
                continue
            segments.append("".join(buffer))
            buffer = []
            index += 1
            continue
        if char in ";\n":
            segments.append("".join(buffer))
            buffer = []
            index += 1
            continue
        buffer.append(char)
        index += 1
    if quote is not None:
        return None
    segments.append("".join(buffer))
    result = [segment.strip() for segment in segments if segment.strip()]
    return result or None


def _path_values(token: str) -> list[str]:
    """Values in one token that could name a filesystem path."""
    if not token.startswith("-"):
        return [token]
    if "=" in token:
        value = token.split("=", 1)[1]
        return [value] if value else []
    if token.startswith("--"):
        return []
    attached = _ATTACHED_PATH_RE.match(token)
    if attached and attached.group(2)[:1] in "/~.":
        return [attached.group(2)]
    return []


def _contained(value: str, cwd: Path, workspace: Path) -> bool:
    if value in ("", "-"):
        return True
    if value.startswith("~") or "$" in value or "`" in value:
        return False
    try:
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = cwd / candidate
        resolved = candidate.resolve()
    except (OSError, RuntimeError, ValueError):
        return False
    return resolved.is_relative_to(workspace)


def _paths_contained(args: list[str], cwd: Path, workspace: Path) -> bool:
    after_double_dash = False
    for token in args:
        if token == "--":
            after_double_dash = True
            continue
        values = [token] if after_double_dash else _path_values(token)
        for value in values:
            if not _contained(value, cwd, workspace):
                return False
    return True


def _short_flag_present(args: list[str], letters: str) -> bool:
    """True when a short-option cluster contains any of ``letters``."""
    for token in args:
        if token.startswith("-") and not token.startswith("--") and len(token) > 1:
            if any(letter in token[1:] for letter in letters):
                return True
    return False


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


def _gh_read_only(args: list[str]) -> bool:
    if not args or any(token == "--web" for token in args):
        return False
    group, rest = args[0], args[1:]
    if group == "api":
        tail = args[1:]
        index = 0
        while index < len(tail):
            token = tail[index]
            if token in ("-X", "--method"):
                if index + 1 >= len(tail) or tail[index + 1].upper() != "GET":
                    return False
                index += 2
                continue
            if token.startswith("--method="):
                if token.split("=", 1)[1].upper() != "GET":
                    return False
            elif token.startswith("-X"):
                if token[2:].upper() != "GET":
                    return False
            if (
                token in _GH_API_WRITE_FLAGS
                or token.startswith("--field=")
                or token.startswith("--raw-field=")
                or token.startswith("--input=")
            ):
                return False
            index += 1
        return True
    if group not in _GH_GROUPS or not rest:
        return False
    if group == "auth":
        return rest[0] == "status"
    return rest[0] in _GH_READ_ACTIONS


def _curl_read_only(args: list[str]) -> bool:
    head_request = False
    urls: list[str] = []
    for token in args:
        # `-H @file`, `-b @file`, and friends read a local file into the
        # request. That can send workspace or system data outbound, so the
        # read-only tier refuses @-prefixed values entirely.
        if token.startswith("@") or "=@" in token:
            return False
        if token in ("-I", "--head"):
            head_request = True
            continue
        if token.startswith("--"):
            name, _, value = token.partition("=")
            if name in _CURL_WRITE_LONG_FLAGS:
                return False
            if value and "://" in value:
                urls.append(value)
            continue
        if token.startswith("-") and token != "-":
            if any(flag in _CURL_WRITE_SHORT_FLAGS for flag in token[1:]):
                return False
            continue
        if "://" in token:
            urls.append(token)
    if not head_request or not urls:
        return False
    return all(_URL_SCHEME_RE.match(url) for url in urls)


def _jq_read_only(args: list[str]) -> bool:
    return not any(_JQ_ENV_RE.search(token) for token in args if not token.startswith("-"))


def _find_read_only(args: list[str], verb: str) -> bool:
    if verb == "fd":
        return not (
            any(token in _FD_EXEC_FLAGS for token in args) or _short_flag_present(args, "L") or "--follow" in args
        )
    if any(token in ("-L", "-follow") for token in args):
        return False
    for token in args:
        if token in _FIND_EXEC_FLAGS:
            return False
        if any(token.startswith(prefix) for prefix in _FIND_EXEC_PREFIXES):
            return False
    return True


def _rg_read_only(args: list[str]) -> bool:
    return not (
        any(token.split("=", 1)[0] in _RG_EXEC_FLAGS for token in args)
        or _short_flag_present(args, "L")
        or "--follow" in args
    )


def _sort_or_tree_read_only(args: list[str], verb: str) -> bool:
    if verb == "tree":
        if _short_flag_present(args, "l") or "--follow-links" in args:
            return False
    else:
        if _short_flag_present(args, "T") or any(token.split("=", 1)[0] == "--temporary-directory" for token in args):
            return False
    flags = _TREE_WRITE_FLAGS if verb == "tree" else _SORT_WRITE_FLAGS
    for token in args:
        name = token.split("=", 1)[0]
        if name in flags:
            return False
        if verb == "sort" and token.startswith("-o") and token != "-o":
            return False
    return True


def _is_read_only_segment(segment: str, cwd: Path, workspace: Path) -> bool:
    try:
        tokens = shlex.split(segment, posix=True)
    except ValueError:
        return False
    if not tokens:
        return False
    if _ENV_ASSIGN_RE.match(tokens[0]):
        return False
    # Only trust verbs resolved from PATH. A path-qualified binary can be an
    # agent-written script (`./tools/ls`) or an outside executable posing as a
    # read-only verb.
    if "/" in tokens[0] or "\\" in tokens[0]:
        return False
    verb = tokens[0]
    args = tokens[1:]
    if verb not in _READ_ONLY_VERBS:
        return False
    if verb == "git" and not _git_read_only(args):
        return False
    if verb == "gh" and not _gh_read_only(args):
        return False
    if verb == "curl" and not _curl_read_only(args):
        return False
    if verb in ("find", "fd") and not _find_read_only(args, verb):
        return False
    if verb == "rg" and not _rg_read_only(args):
        return False
    if verb == "jq" and not _jq_read_only(args):
        return False
    if verb == "grep" and (_short_flag_present(args, "R") or "--dereference-recursive" in args):
        return False
    if verb in ("du", "file", "stat", "ls") and (_short_flag_present(args, "L") or "--dereference" in args):
        return False
    if verb == "diff" and (_short_flag_present(args, "r") or "--recursive" in args):
        return False
    if verb in ("sort", "tree") and not _sort_or_tree_read_only(args, verb):
        return False
    if verb == "uniq" and sum(1 for token in args if not token.startswith("-")) > 1:
        return False
    if verb == "file" and any(token in ("-C", "--compile") for token in args):
        return False
    # File-list indirection: the list file is contained, but the paths inside
    # it are not checked. `find`/`du`/`wc` could reach outside; `file` reads
    # the listed targets.
    if verb in ("find", "du", "wc") and any(
        token == "--files0-from" or token.startswith("--files0-from=") for token in args
    ):
        return False
    if verb == "file" and (
        _short_flag_present(args, "f")
        or any(token == "--files-from" or token.startswith("--files-from=") for token in args)
    ):
        return False
    return _paths_contained(args, cwd, workspace)


def is_read_only_command(command: str, *, cwd: Path, workspace: Path) -> bool:
    """True only when every segment is a provably read-only, contained command."""
    try:
        resolved_cwd = cwd.resolve()
        resolved_workspace = workspace.resolve()
    except (OSError, RuntimeError):
        return False
    segments = _split_segments(command or "")
    if segments is None:
        return False
    return all(_is_read_only_segment(segment, resolved_cwd, resolved_workspace) for segment in segments)
