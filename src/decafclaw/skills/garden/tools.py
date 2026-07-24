"""Garden skill tools — deterministic importance recompute (#197 Phase 5).

Vault-page `importance` frontmatter starts out as an LLM's subjective guess
(backfill, dream generation). This module replaces that guess on a weekly
cadence with a deterministic score driven by two measured signals:
retrieval frequency (`retrieval_telemetry`, Phase 0) and inbound-link count
(`backlinks`, Phase 4). No LLM call, no randomness — same inputs always
produce the same score, so `compute_importance_scores` is pure and unit
testable without a running agent.

`tool_vault_recompute_importance` is the thin async wrapper garden calls
during its weekly sweep: it scores every non-journal vault page, writes
changed scores via `vault_update_frontmatter(overwrite=True)`, and skips
pages whose rounded score didn't change.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import cast

from decafclaw import backlinks, retrieval_telemetry
from decafclaw.frontmatter import get_frontmatter_field, parse_frontmatter
from decafclaw.media import ToolResult
from decafclaw.skills.vault.tools import tool_vault_update_frontmatter

log = logging.getLogger(__name__)

# Rounding precision for written scores and the unchanged-skip comparison.
# Three decimal places is plenty of resolution for a 0..1 importance score
# and avoids rewriting a page for float noise between runs.
_SCORE_PRECISION = 3


def _iter_importance_candidates(config) -> list[tuple[str, Path]]:
    """List (vault-relative POSIX path, Path) for every scoreable vault page.

    Mirrors the walk in `embeddings._iter_vault_pages` /
    `backfill_frontmatter._iter_frontmatter_candidates`: every vault `.md`
    file except journal entries (journal is episodic, not curated
    knowledge — it never carries an `importance` field).
    """
    vault = config.vault_root
    if not vault.is_dir():
        return []
    journal_dir = config.vault_agent_journal_dir
    candidates: list[tuple[str, Path]] = []
    for path in sorted(vault.rglob("*.md")):
        try:
            if path.resolve().is_relative_to(journal_dir.resolve()):
                continue
        except (ValueError, OSError):
            pass
        candidates.append((path.relative_to(vault).as_posix(), path))
    return candidates


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _norm(value: float, max_value: float) -> float:
    """value / max_value, or 0.0 when max_value is 0 (no divide-by-zero)."""
    return value / max_value if max_value > 0 else 0.0


def compute_importance_scores(config) -> dict[str, float]:
    """Deterministic per-page importance score (#197 Phase 5, formula v1).

    ``importance = clamp01(w_retrieval * norm(retrieval_freq) +
    w_inbound * norm(inbound_links))``

    ``norm(x) = x / max(x across all vault pages)``, defined as 0 when
    that max is 0. Weights come from ``config.importance``
    (``ImportanceConfig``). ``w_reference`` is reserved / not yet
    computed — no explicit-reference signal exists yet, so it defaults
    to 0 and that term is omitted from the sum entirely rather than
    computed against an all-zero signal.

    Pure and config-driven: no ``ctx``, no I/O beyond reading the
    telemetry log, the backlink index, and vault page paths (never page
    contents). Returns ``{}`` if the vault has no non-journal pages.
    """
    weights = config.importance
    candidates = _iter_importance_candidates(config)
    pages = [rel for rel, _ in candidates]

    stats = retrieval_telemetry.aggregate(retrieval_telemetry.load_records(config))
    retrieval_freq = {p: stats.get(p, {}).get("retrieval_count", 0) for p in pages}
    inbound_links = {p: backlinks.inbound_count(config, p) for p in pages}

    max_retrieval = max(retrieval_freq.values(), default=0)
    max_inbound = max(inbound_links.values(), default=0)

    return {
        p: _clamp01(
            weights.w_retrieval * _norm(retrieval_freq[p], max_retrieval)
            + weights.w_inbound * _norm(inbound_links[p], max_inbound)
        )
        for p in pages
    }


async def tool_vault_recompute_importance(ctx, dry_run: bool = False) -> ToolResult:
    """Recompute every vault page's importance score deterministically.

    Scores come from `compute_importance_scores` (retrieval frequency +
    inbound-link count — not an LLM's subjective judgment). Pages whose
    rounded score is unchanged are skipped, so a re-run only touches
    pages that actually moved. `dry_run=True` reports the planned deltas
    without writing anything.
    """
    log.info(f"[tool:vault_recompute_importance] dry_run={dry_run}")
    scores = compute_importance_scores(ctx.config)

    deltas: list[dict] = []
    written = 0
    for rel, new_score in sorted(scores.items()):
        path = ctx.config.vault_root / rel
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            log.debug(f"vault_recompute_importance: could not read {rel}: {exc}")
            continue

        metadata, _ = parse_frontmatter(text)
        # get_frontmatter_field's declared return type is a broad union
        # across all frontmatter fields; for "importance" it's always
        # float|None at runtime (get_frontmatter_field clamps it).
        old_score = cast("float | None", get_frontmatter_field(metadata, "importance"))
        new_rounded = round(new_score, _SCORE_PRECISION)
        if old_score is not None and round(old_score, _SCORE_PRECISION) == new_rounded:
            continue

        deltas.append({"path": rel, "old": old_score, "new": new_rounded})
        if dry_run:
            continue

        result = await tool_vault_update_frontmatter(
            ctx, rel, {"importance": new_rounded}, overwrite=True,
        )
        if result.text.startswith("[error:"):
            log.warning(
                f"vault_recompute_importance: failed to write {rel}: {result.text}"
            )
            continue
        written += 1

    if dry_run:
        text = (
            f"Dry run: {len(deltas)} of {len(scores)} scored page(s) "
            f"would change importance."
        )
    else:
        text = (
            f"Recomputed importance for {len(scores)} page(s): "
            f"{written} updated, {len(scores) - written} unchanged."
        )
    return ToolResult(text=text, data={"deltas": deltas, "dry_run": dry_run})


# -- Registry ---------------------------------------------------------------

TOOLS = {
    "vault_recompute_importance": tool_vault_recompute_importance,
}

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "vault_recompute_importance",
            "description": (
                "Deterministically recompute every vault page's `importance` "
                "frontmatter score from measured signals (retrieval frequency, "
                "inbound-link count) — not an LLM judgment call. Weekly garden "
                "maintenance step. Set dry_run=true to preview planned changes "
                "without writing."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "dry_run": {
                        "type": "boolean",
                        "description": (
                            "If true, report planned importance changes without "
                            "writing them. Default false."
                        ),
                    },
                },
                "required": [],
            },
        },
    },
]
