"""Structured decision slice threaded through compaction.

Compaction's prose summary is lossy for high-signal facts —
architectural decisions, unresolved questions, artifacts produced.
This module owns the structured slice that runs alongside the prose
summary: extract from the LLM's compaction response, dedup against
the existing slice, persist to a per-conversation sidecar JSON file,
and render compactly into the rebuilt history.

See ``docs/context-composer.md`` and #302.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

CATEGORIES = ("decisions", "open_questions", "artifacts")
_FENCED_JSON_RE = re.compile(
    r"```json\s*\n(?P<body>.+?)\n```",
    re.DOTALL,
)


@dataclass(frozen=True)
class DecisionEntry:
    """One item in a slice category.

    ``frozen=True`` provides immutability — entries are not mutated
    after creation; the merge layer constructs new instances rather
    than editing existing ones. (Note: dataclass equality includes
    both fields, so dedup-by-text is handled explicitly in
    ``merge_slice``, not via set/dict semantics on ``DecisionEntry``
    itself.)
    """
    text: str
    created_at: str  # ISO-8601 UTC


@dataclass
class DecisionSlice:
    """The forward-threaded structured slice for one conversation."""
    decisions: list[DecisionEntry] = field(default_factory=list)
    open_questions: list[DecisionEntry] = field(default_factory=list)
    artifacts: list[DecisionEntry] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.decisions or self.open_questions or self.artifacts)

    def to_dict(self) -> dict:
        return {cat: [asdict(e) for e in getattr(self, cat)] for cat in CATEGORIES}

    @classmethod
    def from_dict(cls, raw: dict) -> DecisionSlice:
        if not isinstance(raw, dict):
            return cls()
        kwargs: dict[str, list[DecisionEntry]] = {}
        for cat in CATEGORIES:
            entries: list[DecisionEntry] = []
            for item in raw.get(cat, []) or []:
                if not isinstance(item, dict):
                    continue
                text = item.get("text")
                created_at = item.get("created_at")
                if isinstance(text, str) and isinstance(created_at, str):
                    entries.append(DecisionEntry(text=text, created_at=created_at))
            kwargs[cat] = entries
        return cls(**kwargs)


# -- Persistence --------------------------------------------------------------


def _slice_path(config, conv_id: str) -> Path:
    """Resolve the sidecar path for a given conv_id, sandboxed to the
    conversations directory.

    Mirrors ``_context_sidecar_path`` in ``context_composer.py``:
    strips path traversal characters, falls back to a sentinel name
    if the result is empty, and returns the sentinel if the resolved
    path escapes the conversations directory. This is defense-in-depth
    since ``conv_id`` originates from user-controlled web routes.
    """
    base_dir = (config.workspace_path / "conversations").resolve()
    safe_name = conv_id.replace("/", "").replace("\\", "").replace("..", "")
    if not safe_name:
        return base_dir / "_invalid.decisions.json"
    path = (base_dir / f"{safe_name}.decisions.json").resolve()
    if not path.is_relative_to(base_dir):
        return base_dir / "_invalid.decisions.json"
    return path


def load_slice(config, conv_id: str) -> DecisionSlice:
    """Read the slice from sidecar. Missing/invalid → empty slice."""
    try:
        path = _slice_path(config, conv_id)
        if not path.exists():
            return DecisionSlice()
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Failed to load decision slice for %s: %s", conv_id, exc)
        return DecisionSlice()
    return DecisionSlice.from_dict(raw)


def save_slice(config, conv_id: str, slice_: DecisionSlice) -> None:
    """Write the slice atomically. Fail-open on errors."""
    try:
        path = _slice_path(config, conv_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(slice_.to_dict(), indent=2))
        os.replace(tmp, path)
    except OSError as exc:
        log.warning("Failed to save decision slice for %s: %s", conv_id, exc)


# -- Parse from LLM response ---------------------------------------------------


def parse_slice_from_response(text: str) -> dict[str, list[str]] | None:
    """Try to extract the structured slice from a compaction LLM response.

    Looks for a fenced ```json block; parses it; validates the three-key
    object-of-string-lists shape. Returns the validated dict on success
    or None on any failure (silent — prose-only fallback is intended).
    """
    if not text:
        return None
    match = _FENCED_JSON_RE.search(text)
    if not match:
        return None
    body = match.group("body").strip()
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    out: dict[str, list[str]] = {}
    for cat in CATEGORIES:
        items = data.get(cat, [])
        if not isinstance(items, list):
            return None
        cleaned = [str(item).strip() for item in items if isinstance(item, str) and item.strip()]
        out[cat] = cleaned
    return out


def strip_json_block(text: str) -> str:
    """Remove the fenced ```json block from a compaction response,
    leaving only the prose. Used after parsing so the prose summary
    written to history is clean."""
    if not text:
        return text
    return _FENCED_JSON_RE.sub("", text).strip()


# -- Merge ---------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def merge_slice(
    old: DecisionSlice,
    new_lists: dict[str, list[str]],
    *,
    max_per_category: int,
    now: str | None = None,
) -> DecisionSlice:
    """Reconcile the LLM's parsed lists with the existing slice.

    The LLM is the authority on "what should be in the slice now":
      - Existing entries whose text appears in ``new_lists[cat]`` are
        kept verbatim with their original ``created_at``.
      - Entries absent from ``new_lists[cat]`` are dropped (the LLM
        signaled they're obsolete).
      - Strings in ``new_lists[cat]`` that don't match an existing
        text become new entries with ``created_at = now``.

    After the merge, each category is capped at ``max_per_category``
    entries via FIFO (drop oldest by ``created_at``).
    """
    timestamp = now or _now_iso()
    merged_kwargs: dict[str, list[DecisionEntry]] = {}

    for cat in CATEGORIES:
        old_entries = getattr(old, cat)
        old_by_text = {e.text: e for e in old_entries}
        new_texts = new_lists.get(cat, []) or []

        seen: set[str] = set()
        merged: list[DecisionEntry] = []
        for text in new_texts:
            if text in seen:
                continue
            seen.add(text)
            existing = old_by_text.get(text)
            if existing is not None:
                merged.append(existing)
            else:
                merged.append(DecisionEntry(text=text, created_at=timestamp))

        # Cap: FIFO drop oldest if over the limit. Sort by created_at
        # ascending; trim from the front; preserve original order
        # within the kept set so the LLM's order is honored.
        if max_per_category > 0 and len(merged) > max_per_category:
            sorted_oldest_first = sorted(
                merged, key=lambda e: e.created_at,
            )
            keep = set(id(e) for e in sorted_oldest_first[-max_per_category:])
            merged = [e for e in merged if id(e) in keep]

        merged_kwargs[cat] = merged

    return DecisionSlice(**merged_kwargs)


# -- Render --------------------------------------------------------------------


def format_slice(slice_: DecisionSlice) -> str:
    """Render the slice as a compact markdown block wrapped in a
    ``<decision_slice>`` envelope, suitable for prepending to the
    prose compaction summary. Returns "" when the slice is empty so
    callers can use simple truthiness checks.

    The XML envelope matches the convention from #304 (outer XML,
    markdown inside) so the model can distinguish the structured
    slice from the prose summary that follows it within the same
    summary message.
    """
    if slice_.is_empty():
        return ""

    sections: list[str] = []
    for cat, heading in (
        ("decisions", "Decisions"),
        ("open_questions", "Open Questions"),
        ("artifacts", "Artifacts"),
    ):
        entries = getattr(slice_, cat)
        if not entries:
            continue
        sections.append(f"### {heading}")
        sections.extend(f"- {e.text}" for e in entries)
        sections.append("")  # blank line between sections

    if not sections:
        return ""

    body = "\n".join(sections).rstrip()
    # Trailing blank line at the very end so the prose starts on its own line.
    return f"<decision_slice>\n{body}\n</decision_slice>\n"
