"""Deterministic read-only shell classifier (#1641).

The aux approval judge used to spend a model call deciding whether ``ls``,
``git status``, or ``grep`` needed a human. It declined many of them — not for
risk, but because a native tool could do the job. This package decides the
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

Layout:
- ``segment.py`` — quote-aware splitting and argv tokenization (shared);
- ``containment.py`` — workspace boundary checks (shared);
- ``verbs/`` — one module per verb family, each with its complete contract;
- this module — dispatch, and the public ``is_read_only_command`` predicate.
"""

from pathlib import Path

from .segment import split_segments, tokenize_segment
from .verbs import READ_ONLY_VERBS, VERB_OWNERS
from .verbs import allows as _verb_allows

__all__ = ["READ_ONLY_VERBS", "VERB_OWNERS", "is_read_only_command"]


def _is_read_only_segment(segment: str, cwd: Path, workspace: Path) -> bool:
    tokens = tokenize_segment(segment)
    if tokens is None:
        return False
    return _verb_allows(tokens[0], tokens[1:], cwd, workspace)


def is_read_only_command(command: str, *, cwd: Path, workspace: Path) -> bool:
    """True only when every segment is a provably read-only, contained command."""
    try:
        resolved_cwd = cwd.resolve()
        resolved_workspace = workspace.resolve()
    except (OSError, RuntimeError):
        return False
    segments = split_segments(command or "")
    if segments is None:
        return False
    return all(_is_read_only_segment(segment, resolved_cwd, resolved_workspace) for segment in segments)
