"""``gh`` read-only contract (#1641).

Contract: only the inspection groups/actions, only ``gh api`` with a GET
method, and never ``--web`` (opens a browser) or a field flag (implies POST).
Anything that writes, clones, or prints a token is rejected. ``gh api`` can
mutate despite the read-only tier, so its method and field forms are
enumerated.
"""

from pathlib import Path

from ..containment import paths_contained

VERBS = frozenset({"gh"})

_GH_GROUPS = frozenset({"issue", "pr", "run", "repo", "release", "label", "search", "auth", "workflow", "api"})
_GH_READ_ACTIONS = frozenset({"list", "view", "diff", "checks", "status", "search"})
_GH_API_WRITE_FLAGS = frozenset({"-f", "-F", "--field", "--raw-field", "--input"})


def _gh_api_allows(tail: list[str]) -> bool:
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


def _gh_read_only(args: list[str]) -> bool:
    if not args or any(token == "--web" for token in args):
        return False
    group, rest = args[0], args[1:]
    if group == "api":
        return _gh_api_allows(args[1:])
    if group not in _GH_GROUPS or not rest:
        return False
    if group == "auth":
        return rest[0] == "status"
    return rest[0] in _GH_READ_ACTIONS


def allows(verb: str, args: list[str], cwd: Path, workspace: Path) -> bool:
    if not _gh_read_only(args):
        return False
    return paths_contained(args, cwd, workspace)
