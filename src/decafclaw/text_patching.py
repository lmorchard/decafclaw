"""Shared text-patching engine for the workspace, admin, and vault file tools.

One implementation of the mechanics every file suite needs (#1771):

- exact-match replacement (``patch_exact_match``), with line-ending preservation
- physical-line range replacement (``patch_line_range``)
- content version tokens for read/compare flows (``content_version``)
- compact unified diffs (``unified_diff``)
- the "text not found" hint for failed exact matches (``edit_not_found_hint``)

The callers own their storage domain: path resolution, security gates,
confirmation, event emission, and indexing. This module never touches disk.
"""

from __future__ import annotations

import difflib
import hashlib
import re

# Context lines shown in unified diffs
DIFF_CONTEXT_LINES = 3

_PHYSICAL_LINE_SPLIT_RE = re.compile(r"(\r\n|\r|\n)")


class PatchError(ValueError):
    """A patch could not be applied. The message is agent-facing."""


class TextNotFound(PatchError):
    """``old_text`` does not occur in the content."""


class AmbiguousMatch(PatchError):
    """``old_text`` occurs more than once and ``replace_all`` is false."""

    def __init__(self, count: int):
        self.count = count
        super().__init__(f"found {count} matches")


class InvalidLineRange(PatchError):
    """The requested line range is outside the file or inverted."""


def split_physical_lines(text: str, keepends: bool = False) -> list[str]:
    """Split text on physical newlines (\\r\\n, \\r, \\n) only, preserving 1-based source line numbering."""
    if not text:
        return []
    parts = _PHYSICAL_LINE_SPLIT_RE.split(text)
    result: list[str] = []
    for i in range(0, len(parts) - 1, 2):
        result.append(parts[i] + parts[i + 1] if keepends else parts[i])
    if len(parts) % 2 == 1 and parts[-1]:
        result.append(parts[-1])
    return result


def content_version(content: str) -> str:
    """Content version token for read/compare flows (#1126).

    Hashes the LF-normalized content so CRLF and LF files with identical
    logical text share one version, keeping version comparisons consistent
    across line-ending styles. Read results expose this version; edits accept
    an ``expected_version`` and reject when it no longer matches.
    """
    normalized = content.replace("\r\n", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def unified_diff(old_text: str, new_text: str, path: str = "") -> str:
    """Generate a compact unified diff for file-tool output."""
    old_lines = old_text.splitlines(keepends=True)
    new_lines = new_text.splitlines(keepends=True)
    diff = list(
        difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=f"a/{path}" if path else "before",
            tofile=f"b/{path}" if path else "after",
            n=DIFF_CONTEXT_LINES,
        )
    )
    if not diff:
        return ""
    return "".join(diff)


def edit_not_found_hint(content: str, old_text: str) -> str:
    """Build a hint for a failed exact match, or "" if nothing is close enough.

    Two tiers, checked in order: a whitespace-insensitive match (the text is
    in the file but whitespace runs differ), then a near match of old_text's
    first non-blank line against the file's lines. Hints only — never writes.
    """
    if old_text.strip():
        # Escape each non-whitespace segment and join with \s+ so the match
        # succeeds iff the non-whitespace content is identical, regardless of
        # whitespace runs; m.start() maps back to a 1-based line in the file.
        # (Subbing over re.escape(old_text) won't do: it escapes whitespace
        # too, so the sub would bake literal backslashes into the pattern.)
        pattern = r"\s+".join(re.escape(p) for p in re.split(r"\s+", old_text) if p)
        match = re.search(pattern, content)
        if match:
            line = content.count("\n", 0, match.start()) + 1
            return f"It matches when whitespace differences are ignored — the match starts at line {line}."
    lines = content.splitlines()
    for candidate in old_text.splitlines():
        if not candidate.strip():
            continue
        close = difflib.get_close_matches(candidate, lines, n=1, cutoff=0.8)
        if close:
            return f"A similar line exists at line {lines.index(close[0]) + 1}: {close[0]}"
        break
    return ""


def patch_exact_match(
    content: str,
    old_text: str,
    new_text: str,
    replace_all: bool = False,
) -> tuple[str, str]:
    """Replace exact text in ``content`` and return ``(new_content, unified_diff)``.

    Matching runs on LF-normalized text so an LF ``old_text`` matches a CRLF
    file (and vice versa); the returned content keeps the file's original line
    ending. The diff is generated from the normalized text.

    Raises ``TextNotFound`` when ``old_text`` is absent and ``AmbiguousMatch``
    when it occurs more than once and ``replace_all`` is false.
    """
    if not old_text:
        raise PatchError("old_text must not be empty")
    eol = "\r\n" if "\r\n" in content else "\n"
    normalized = content.replace("\r\n", "\n")
    old = old_text.replace("\r\n", "\n")
    new = new_text.replace("\r\n", "\n")

    count = normalized.count(old)
    if count == 0:
        raise TextNotFound("text not found")
    if count > 1 and not replace_all:
        raise AmbiguousMatch(count)

    replaced = normalized.replace(old, new) if replace_all else normalized.replace(old, new, 1)
    return replaced.replace("\n", eol), unified_diff(normalized, replaced)


def patch_line_range(
    content: str,
    start_line: int,
    end_line: int,
    new_content: str = "",
) -> tuple[str, str]:
    """Replace 1-based inclusive lines ``start_line``–``end_line`` in ``content``.

    An empty ``new_content`` deletes the range. Returns
    ``(new_content, unified_diff)``; raises ``InvalidLineRange`` when the
    range is inverted or outside the file.
    """
    lines = split_physical_lines(content, keepends=True)
    if start_line < 1 or end_line < start_line or end_line > len(lines):
        raise InvalidLineRange(f"invalid line range {start_line}-{end_line}. File has {len(lines)} lines.")

    if new_content:
        if not new_content.endswith("\n"):
            new_content += "\n"
        replacement = split_physical_lines(new_content, keepends=True)
    else:
        replacement = []

    new_lines = list(lines)
    new_lines[start_line - 1 : end_line] = replacement
    updated = "".join(new_lines)
    return updated, unified_diff(content, updated)
