"""Tool-result clearing — a lightweight pre-compaction tier.

Walks the agent's in-memory history once per agent loop iteration and
replaces the body of old, large tool messages with a short stub so
the model doesn't keep paying attention budget on raw tool output it
has already synthesized. The original body remains durably written to
the conversation's JSONL archive — this is purely an in-memory edit.

See ``docs/context-composer.md`` and #298 for design rationale.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)

_STUB_PREFIX = "[tool output cleared:"


@dataclass
class ClearStats:
    """Counts of what got cleared. Per-call deltas; the agent loop
    accumulates these onto ``ctx.composer.cleanup_cleared_count`` and
    ``ctx.composer.cleanup_cleared_bytes``."""
    cleared_count: int = 0
    cleared_bytes: int = 0

    def merge(self, other: ClearStats) -> None:
        self.cleared_count += other.cleared_count
        self.cleared_bytes += other.cleared_bytes


def _is_already_cleared(content) -> bool:
    """Return True if this content already starts with the stub
    prefix (idempotent re-runs)."""
    return isinstance(content, str) and content.startswith(_STUB_PREFIX)


def _user_turn_boundary_indices(history: list[dict]) -> list[int]:
    """Indices in ``history`` where ``role == "user"`` — these mark
    the start of each user turn."""
    return [i for i, m in enumerate(history) if m.get("role") == "user"]


def _build_tool_name_index(history: list[dict]) -> dict[str, str]:
    """Build a one-pass ``{tool_call_id: tool_name}`` lookup.

    Walks assistant messages once and collects every named tool call.
    Pre-computing this at the start of a clearing pass avoids the
    O(n²) scan that a per-message lookup would do on long histories.

    Tool names that can't be resolved (e.g. the originating assistant
    message was compacted away into a summary) simply won't appear in
    the returned map. Callers should treat a missing entry as "name
    unknown — preserve_tools can't be honored, eligibility falls back
    to old-and-large-is-sufficient."
    """
    by_call_id: dict[str, str] = {}
    for msg in history:
        if msg.get("role") != "assistant":
            continue
        for tc in msg.get("tool_calls", []) or []:
            if not isinstance(tc, dict):
                continue
            call_id = tc.get("id")
            if not call_id:
                continue
            fn = tc.get("function") or {}
            name = fn.get("name") if isinstance(fn, dict) else None
            if name:
                by_call_id[call_id] = name
    return by_call_id


def clear_old_tool_results(history: list[dict], config) -> ClearStats:
    """Mutate ``history`` in place: for each tool message that's old,
    large, and not allowlisted, replace ``content`` with a short stub.

    Eligibility (all must hold):
      1. ``role == "tool"``
      2. ``content`` is not already a stub (idempotent re-run guard)
      3. originating tool name is not in ``cleanup.preserve_tools``
         (None — name-not-resolvable — is treated as eligible)
      4. message is older than ``cleanup.min_turn_age`` user-turn
         boundaries
      5. ``len(content.encode("utf-8")) >= cleanup.min_size_bytes``
      6. the stub itself would actually be smaller than the original
         (skips the pathological case where ``min_size_bytes`` is set
         below the stub length)

    Returns a ``ClearStats`` with the count + bytes of *this call's*
    deletions (not cumulative).
    """
    stats = ClearStats()
    cfg = config.cleanup
    if not cfg.enabled:
        return stats

    # Defensive guard: a misconfigured ``min_turn_age`` <= 0 would index
    # ``boundaries[-0] == boundaries[0]`` and silently clear everything
    # past the first user message (or raise on a negative value), so
    # treat it as a safe no-op. Same for non-positive ``min_size_bytes``
    # — it can't legitimately mean "clear everything."
    if cfg.min_turn_age <= 0 or cfg.min_size_bytes <= 0:
        return stats

    boundaries = _user_turn_boundary_indices(history)
    if len(boundaries) < cfg.min_turn_age:
        # Not enough user turns yet — nothing is "old".
        return stats

    # The cutoff is the index of the user message that begins the
    # protected window. Any tool message at index >= cutoff is in a
    # protected turn; messages strictly before cutoff are eligible.
    cutoff = boundaries[-cfg.min_turn_age]
    preserve = set(cfg.preserve_tools)
    tool_names_by_call_id = _build_tool_name_index(history)

    for i, msg in enumerate(history):
        if i >= cutoff:
            break
        if msg.get("role") != "tool":
            continue
        content = msg.get("content") or ""
        if _is_already_cleared(content):
            continue
        if not isinstance(content, str):
            # Provider-specific shapes (e.g. parts arrays) — skip
            # rather than guess at a safe edit.
            continue
        content_bytes = len(content.encode("utf-8"))
        if content_bytes < cfg.min_size_bytes:
            continue
        tool_name = tool_names_by_call_id.get(msg.get("tool_call_id", ""))
        if tool_name is not None and tool_name in preserve:
            continue
        stub = f"{_STUB_PREFIX} {content_bytes} bytes]"
        reclaimed = content_bytes - len(stub.encode("utf-8"))
        if reclaimed <= 0:
            # Stub is no smaller than the original — nothing to reclaim.
            continue
        msg["content"] = stub
        stats.cleared_count += 1
        stats.cleared_bytes += reclaimed
        log.debug(
            "Cleared tool message at history[%d] (tool=%s, %d bytes)",
            i, tool_name or "?", content_bytes,
        )

    return stats
