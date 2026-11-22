"""Search verbs: grep/find families (#1641).

Contract: these verbs search and print. The escape hatches are executing a
command (``find -exec``/``-ok``/``-delete``, ``fd -x``/``-X``, ``rg --pre``),
writing a file (``find -fprint``/``-fls``), following symlinks out of the
workspace, and reading targets from an unchecked list (``--files0-from``).
Every one is enumerated here; a path argument still has to resolve inside the
workspace.

``egrep``/``fgrep`` are deliberately *not* given grep's ``-R`` guard: that
matches the pre-refactor classifier. Adding it is a classification change for
a separate issue, not a behavior-preserving refactor.
"""

from pathlib import Path

from ..containment import paths_contained
from ._common import flag_name, has_files0_from, short_flag_present

VERBS = frozenset({"grep", "egrep", "fgrep", "rg", "find", "fd"})

_FIND_EXEC_FLAGS = frozenset({"-exec", "-execdir", "-ok", "-okdir", "-delete", "-fls"})
_FIND_EXEC_PREFIXES = ("-fprint",)
_FD_EXEC_FLAGS = frozenset({"-x", "--exec", "-X", "--exec-batch"})
_RG_EXEC_FLAGS = ("--pre", "--pre-glob")


def _find_allows(args: list[str]) -> bool:
    if any(token in ("-L", "-follow") for token in args):
        return False
    for token in args:
        if token in _FIND_EXEC_FLAGS:
            return False
        if any(token.startswith(prefix) for prefix in _FIND_EXEC_PREFIXES):
            return False
    if has_files0_from(args):
        return False
    return True


def _fd_allows(args: list[str]) -> bool:
    return not (any(token in _FD_EXEC_FLAGS for token in args) or short_flag_present(args, "L") or "--follow" in args)


def _rg_allows(args: list[str]) -> bool:
    return not (
        any(flag_name(token) in _RG_EXEC_FLAGS for token in args) or short_flag_present(args, "L") or "--follow" in args
    )


def allows(verb: str, args: list[str], cwd: Path, workspace: Path) -> bool:
    if verb == "fd":
        if not _fd_allows(args):
            return False
    elif verb == "find":
        if not _find_allows(args):
            return False
    elif verb == "rg":
        if not _rg_allows(args):
            return False
    elif verb == "grep" and (short_flag_present(args, "R") or "--dereference-recursive" in args):
        return False
    return paths_contained(args, cwd, workspace)
