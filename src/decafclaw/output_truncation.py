"""Shared truncation utilities for tool outputs (#922).

Pure functions, no I/O. Truncation is based on two independent limits —
whichever is hit first wins:

- Line limit (``max_lines``)
- Byte limit (``max_bytes``, measured as UTF-8 bytes of the decoded text)

Because we operate on already-decoded ``str``, character boundaries are never
split — a multibyte character can never be mangled into U+FFFD. Line limits
count ``splitlines()`` lines; we never return partial lines, except a single
line that alone exceeds the byte limit (cut at a character boundary).

Head retention keeps the beginning of the text (documents); tail retention
keeps the end (command diagnostics — errors and final status live at the end).
The per-tool choice is ``retention_for_tool``.

Borrowed from pi's ``truncate.ts`` shape (line+byte limits, head/tail
retention, rich metadata). See ``docs/dev-sessions/`` for the #922 spec.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class TruncationResult:
    """The outcome of truncating a text to line/byte limits."""

    content: str
    truncated: bool
    truncated_by: str | None  # "lines" | "bytes" | None
    total_lines: int
    total_bytes: int
    output_lines: int
    output_bytes: int


def _utf8_len(s: str) -> int:
    return len(s.encode("utf-8"))


def _head_cut_to_bytes(s: str, max_bytes: int) -> str:
    """Longest prefix of ``s`` whose UTF-8 encoding is at most ``max_bytes``.

    Never splits a character: the result is a true prefix of ``s`` that
    encodes to ≤ max_bytes. Binary search over the character index — each step
    encodes a prefix at C speed (a per-character Python loop would be too slow
    for multi-MB lines).
    """
    if _utf8_len(s) <= max_bytes:
        return s
    lo, hi = 0, len(s) + 1  # answer in [lo, hi); s[:0] always fits
    while lo + 1 < hi:
        mid = (lo + hi) // 2
        if _utf8_len(s[:mid]) <= max_bytes:
            lo = mid
        else:
            hi = mid
    return s[:lo]


def _tail_cut_to_bytes(s: str, max_bytes: int) -> str:
    """Longest suffix of ``s`` whose UTF-8 encoding is at most ``max_bytes``.

    Mirror image of :func:`_head_cut_to_bytes` — keeps the END of an
    over-long line, which is what tail retention is for.
    """
    if _utf8_len(s) <= max_bytes:
        return s
    lo, hi = 0, len(s)  # answer in [lo, hi]; s[len(s):] = "" always fits
    while lo < hi:
        mid = (lo + hi) // 2
        if _utf8_len(s[mid:]) <= max_bytes:
            hi = mid
        else:
            lo = mid + 1
    return s[lo:]


def truncate_head(text: str, *, max_lines: int, max_bytes: int) -> TruncationResult:
    """Keep the first ``max_lines`` lines within ``max_bytes`` UTF-8 bytes.

    The line limit is applied first; if the kept block still exceeds the byte
    limit, lines are dropped from the END. If a single line alone exceeds the
    byte limit it is cut to fit (its prefix), so even one enormous line is
    bounded. Line endings are normalized to ``\\n`` in the output.
    """
    total_lines = len(text.splitlines())
    total_bytes = _utf8_len(text)
    if not text:
        return TruncationResult("", False, None, 0, 0, 0, 0)

    lines = text.splitlines()
    truncated_by: str | None = None
    kept = lines[:max_lines]  # [:0] == [] — a zero line limit keeps nothing
    if len(kept) < total_lines:
        truncated_by = "lines"

    size = _utf8_len("\n".join(kept))
    while kept and size > max_bytes:
        if len(kept) == 1:
            # A single line alone exceeds the limit: cut it to fit.
            kept[0] = _head_cut_to_bytes(kept[0], max_bytes)
            size = _utf8_len(kept[0])  # now ≤ max_bytes; loop exits
        else:
            # Drop the last line (and its preceding newline).
            size -= _utf8_len(kept[-1]) + 1
            kept.pop()
        if truncated_by is None:
            truncated_by = "bytes"

    content = "\n".join(kept)
    return TruncationResult(
        content=content,
        truncated=truncated_by is not None,
        truncated_by=truncated_by,
        total_lines=total_lines,
        total_bytes=total_bytes,
        output_lines=len(kept),
        output_bytes=_utf8_len(content),
    )


def truncate_tail(text: str, *, max_lines: int, max_bytes: int) -> TruncationResult:
    """Keep the last ``max_lines`` lines within ``max_bytes`` UTF-8 bytes.

    Mirror image of :func:`truncate_head`: the line limit is applied first; if
    the kept block still exceeds the byte limit, lines are dropped from the
    FRONT. If a single line alone exceeds the byte limit it is cut to fit (its
    suffix — the end of the line is what tail retention exists for). Line
    endings are normalized to ``\\n`` in the output.
    """
    total_lines = len(text.splitlines())
    total_bytes = _utf8_len(text)
    if not text:
        return TruncationResult("", False, None, 0, 0, 0, 0)

    lines = text.splitlines()
    truncated_by: str | None = None
    if total_lines > max_lines:
        # lines[-0:] would keep everything, so guard the zero limit explicitly.
        kept = lines[-max_lines:] if max_lines > 0 else []
    else:
        kept = list(lines)
    if len(kept) < total_lines:
        truncated_by = "lines"

    size = _utf8_len("\n".join(kept))
    while kept and size > max_bytes:
        if len(kept) == 1:
            # A single line alone exceeds the limit: cut it to fit (keep its end).
            kept[0] = _tail_cut_to_bytes(kept[0], max_bytes)
            size = _utf8_len(kept[0])  # now ≤ max_bytes; loop exits
        else:
            # Drop the first line (and its following newline).
            size -= _utf8_len(kept[0]) + 1
            kept.pop(0)
        if truncated_by is None:
            truncated_by = "bytes"

    content = "\n".join(kept)
    return TruncationResult(
        content=content,
        truncated=truncated_by is not None,
        truncated_by=truncated_by,
        total_lines=total_lines,
        total_bytes=total_bytes,
        output_lines=len(kept),
        output_bytes=_utf8_len(content),
    )


# Per-tool preview retention (#922). Default "head" preserves today's
# byte-prefix behavior for every tool we are not changing; command diagnostics
# (errors, final status) live at the end, so shell keeps its tail.
_TAIL_TOOLS = frozenset({"shell"})


def retention_for_tool(tool_name: str) -> str:
    """Return the preview retention mode for a tool: ``"head"`` or ``"tail"``."""
    return "tail" if tool_name in _TAIL_TOOLS else "head"


def truncation_note(filepath: str) -> str:
    """The standard note appended to a truncated preview (stable wording —
    the LLM has learned this phrasing; keep it identical across call sites)."""
    return f"\n\n[Output truncated. Full output saved to {filepath}. Use the 'read' tool to inspect it.]"


def format_size(n: int) -> str:
    """Human-readable byte size: ``512B``, ``6.0KB``, ``1.2MB``."""
    if n < 1024:
        return f"{n}B"
    for unit, threshold in (("KB", 1024), ("MB", 1024**2)):
        if n < threshold * 1024:
            return f"{n / threshold:.1f}{unit}"
    return f"{n / 1024**3:.1f}GB"
