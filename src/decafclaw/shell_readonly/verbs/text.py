"""Text-processing verbs (#1641).

Contract: these verbs transform or compare text and print it. The escape
hatches are writing a file (``sort -o``/``--output``, ``uniq`` with a second
positional, ``tree`` lives in filesystem), executing a program (``sort
--compress-program``), following symlinks recursively (``diff -r``), and
choosing a temporary directory (``sort -T``). Every one is enumerated here; a
path argument still has to resolve inside the workspace.
"""

from pathlib import Path

from ..containment import paths_contained
from ._common import flag_name, short_flag_present

VERBS = frozenset({"diff", "cut", "sort", "uniq", "basename", "dirname", "realpath", "readlink"})

_SORT_WRITE_FLAGS = frozenset({"-o", "--output", "--compress-program"})


def _sort_allows(args: list[str]) -> bool:
    if short_flag_present(args, "T") or any(flag_name(token) == "--temporary-directory" for token in args):
        return False
    for token in args:
        if flag_name(token) in _SORT_WRITE_FLAGS:
            return False
        if token.startswith("-o") and token != "-o":
            return False
    return True


def allows(verb: str, args: list[str], cwd: Path, workspace: Path) -> bool:
    if verb == "diff" and (short_flag_present(args, "r") or "--recursive" in args):
        return False
    if verb == "sort" and not _sort_allows(args):
        return False
    if verb == "uniq" and sum(1 for token in args if not token.startswith("-")) > 1:
        return False
    return paths_contained(args, cwd, workspace)
