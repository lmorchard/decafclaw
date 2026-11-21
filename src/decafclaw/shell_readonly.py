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

import os
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

_GIT_READ_ONLY_SUBCOMMANDS = frozenset(
    {
        "status",
        "log",
        "diff",
        "show",
        "fetch",
        "blame",
        "describe",
        "rev-parse",
        "rev-list",
        "ls-files",
        "ls-tree",
        "ls-remote",
        "cat-file",
        "shortlog",
        "grep",
        "merge-base",
        "count-objects",
        "for-each-ref",
        "name-rev",
        "show-ref",
        "diff-tree",
        "reflog",
    }
)

_GIT_WRITE_OR_EXEC_FLAGS = (
    "--output",
    "--exec-path",
    "--git-dir",
    "--work-tree",
    "--textconv",
    "--ext-diff",
)

_GIT_TAG_WRITE_FLAGS = frozenset(
    {"-d", "--delete", "-a", "--annotate", "-s", "--sign", "-f", "--force", "-m", "--message"}
)

_GIT_CONFIG_READ_ACTIONS = frozenset({"--get", "--get-all", "--list", "-l", "get"})

_GH_GROUPS = frozenset({"issue", "pr", "run", "repo", "release", "label", "search", "auth", "workflow", "api"})
_GH_READ_ACTIONS = frozenset({"list", "view", "diff", "checks", "status", "search"})
_GH_API_WRITE_FLAGS = frozenset({"-f", "--field", "--raw-field", "--input"})

_CURL_WRITE_SHORT_FLAGS = frozenset("oODcTdFKX")
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


def _git_read_only(args: list[str]) -> bool:
    if not args:
        return False
    if any(token == "-c" or token.startswith(_GIT_WRITE_OR_EXEC_FLAGS) for token in args):
        return False
    subcommand, rest = args[0], args[1:]
    if subcommand in _GIT_READ_ONLY_SUBCOMMANDS:
        if subcommand == "reflog":
            return not rest or rest[0] == "show" or rest[0].startswith("-")
        return True
    if subcommand == "branch":
        return all(token.startswith("-") for token in rest)
    if subcommand == "remote":
        return not rest or all(token.startswith("-") for token in rest) or rest[0] in ("show", "get-url")
    if subcommand == "tag":
        if any(token in _GIT_TAG_WRITE_FLAGS for token in rest):
            return False
        return not rest or any(token in ("-l", "--list") for token in rest)
    if subcommand == "stash":
        return bool(rest) and rest[0] in ("list", "show")
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
        for index, token in enumerate(tail):
            if token in ("-X", "--method"):
                if index + 2 >= len(args) or args[index + 2].upper() != "GET":
                    return False
        if any(
            token in _GH_API_WRITE_FLAGS
            or token.startswith("--field=")
            or token.startswith("--raw-field=")
            or token.startswith("--input=")
            for token in tail
        ):
            return False
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


def _find_read_only(args: list[str], verb: str) -> bool:
    if verb == "fd":
        return not any(token in _FD_EXEC_FLAGS for token in args)
    for token in args:
        if token in _FIND_EXEC_FLAGS:
            return False
        if any(token.startswith(prefix) for prefix in _FIND_EXEC_PREFIXES):
            return False
    return True


def _rg_read_only(args: list[str]) -> bool:
    return not any(token.split("=", 1)[0] in _RG_EXEC_FLAGS for token in args)


def _sort_or_tree_read_only(args: list[str], verb: str) -> bool:
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
    verb = os.path.basename(tokens[0])
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
    if verb in ("sort", "tree") and not _sort_or_tree_read_only(args, verb):
        return False
    if verb == "uniq" and sum(1 for token in args if not token.startswith("-")) > 1:
        return False
    if verb == "file" and any(token in ("-C", "--compile") for token in args):
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
