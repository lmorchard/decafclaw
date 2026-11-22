"""Filesystem inspection verbs (#1641).

Contract: these verbs only read the paths they are given. The only ways out of
that contract are following symlinks (``-L``/``--dereference``, ``tree -l``,
``--follow-links``), writing a file (``tree -o``), compiling a magic database
(``file -C``), or reading targets from a list the classifier does not check
(``--files0-from``, ``file -f``/``--files-from``). Every flag that can do one
of those is enumerated below; a path argument still has to resolve inside the
workspace.
"""

from pathlib import Path

from ..containment import paths_contained
from ._common import flag_name, has_files0_from, has_files_from, short_flag_present

VERBS = frozenset(
    {
        "ls",
        "cat",
        "head",
        "tail",
        "pwd",
        "which",
        "wc",
        "du",
        "file",
        "stat",
        "echo",
        "tree",
    }
)

_TREE_WRITE_FLAGS = frozenset({"-o", "--output"})


def allows(verb: str, args: list[str], cwd: Path, workspace: Path) -> bool:
    if verb in ("du", "file", "stat", "ls") and (short_flag_present(args, "L") or "--dereference" in args):
        return False
    if verb == "tree":
        if short_flag_present(args, "l") or "--follow-links" in args:
            return False
        if any(flag_name(token) in _TREE_WRITE_FLAGS for token in args):
            return False
    if verb == "file" and any(token in ("-C", "--compile") for token in args):
        return False
    if verb in ("du", "wc") and has_files0_from(args):
        return False
    if verb == "file" and (short_flag_present(args, "f") or has_files_from(args)):
        return False
    return paths_contained(args, cwd, workspace)
