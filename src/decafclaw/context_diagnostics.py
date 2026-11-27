"""Composer diagnostics, context-status, and token accounting (#1491)."""

from __future__ import annotations

import dataclasses
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from .context_types import ComposedContext, ComposerState, effective_compaction_threshold
from .model_catalog import effective_context_window

if TYPE_CHECKING:
    from decafclaw.context import Context

log = logging.getLogger(__name__)


def get_effective_compaction_threshold(config, ctx: "Context | None" = None, *, lowered: int | None = None) -> int:
    """Effective auto-compaction token threshold for this context (#1099).

    Turn-local reactive lowering > per-conversation absolute override >
    per-conversation (else global) ``compaction.threshold_ratio`` applied to
    the model context window > fixed ``compaction.max_tokens`` fallback.
    """
    return effective_compaction_threshold(config, ctx, lowered=lowered)


def get_context_window_size(config, ctx: Any = None) -> int:
    """Return the effective context window size (#1360).

    Configured values only — the definition lives in
    :func:`decafclaw.model_catalog.effective_context_window`. Used by
    compose() for dynamic budget allocation; the async
    ``llm.ensure_model_context_window`` learns an unconfigured window from
    the provider first, then reads the same ladder.
    """
    return effective_context_window(config, getattr(ctx, "active_model", ""))


def build_context_status(total_tokens: int, context_window: int, message_count: int) -> str | None:
    """Build a one-line context usage note for the agent.

    Returns None if context_window is zero (unconfigured).
    """
    if context_window <= 0:
        return None
    pct = total_tokens / context_window * 100
    hint = ""
    if pct > 70:
        hint = " — consider being concise"
    return f"[Context: ~{total_tokens:,} / {context_window:,} tokens ({pct:.0f}%), {message_count} messages{hint}]"


def build_diagnostics(config, state: ComposerState, composed: ComposedContext, ctx: "Context | None" = None) -> dict:
    """Build the full diagnostics dict for the context sidecar file."""
    candidates = []
    for r in composed.memory_results:
        entry = {
            "file_path": r.get("file_path", ""),
            "source_type": r.get("source_type", ""),
            "composite_score": round(r.get("composite_score", 0), 3),
            "similarity": round(r.get("similarity", 0), 3),
            "recency": round(r.get("recency", 0.5), 3),
            "importance": round(r.get("importance", 0.5), 3),
            "modified_at": r.get("modified_at", ""),
            "tokens_estimated": r.get("tokens_estimated", 0),
        }
        if r.get("linked_from"):
            entry["linked_from"] = r["linked_from"]
        candidates.append(entry)

    sources = [dataclasses.asdict(s) for s in composed.sources]

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total_tokens_estimated": composed.total_tokens_estimated,
        "total_tokens_actual": state.last_prompt_tokens_actual,
        "cached_prompt_tokens": state.last_cached_prompt_tokens_actual,
        "cache_hit_rate": (
            round(state.last_cached_prompt_tokens_actual / state.last_prompt_tokens_actual, 4)
            if state.last_prompt_tokens_actual
            else 0.0
        ),
        "context_window_size": get_context_window_size(config, ctx),
        # Effective threshold (#1099): per-conversation override, then
        # global/per-conv ratio × model window, then the fixed cap. The
        # Context Inspector reads this to render the "Compact at" stat.
        "compaction_threshold": get_effective_compaction_threshold(config, ctx),
        "sources": sources,
        "memory_candidates": candidates,
        "cleanup": {
            "cleared_count": state.cleanup_cleared_count,
            "cleared_bytes": state.cleanup_cleared_bytes,
        },
    }
