"""Proactive vault retrieval core extension (#1489).

Moves semantic vault retrieval (#301, #1048, #1157) out of
``ContextComposer`` into a ``PRE_COMPOSE`` contribution. The extension
queries ``embeddings.db`` through ``memory_context.retrieve_memory_context``,
scores candidates with the configured relevance weights, filters pages the
conversation already surfaced, trims to the turn's token budget, and returns a
``WORKING_MEMORY`` ``ContextContribution`` holding the ``vault_retrieval``
block. The block reaches the provider for the current turn only and is never
archived (#1574).

Retrieval runs on every interactive turn unless
``ctx.skip_vault_retrieval`` is set. Modes (``vault_retrieval.mode``):

- ``always`` — scored full-body candidates (default, back-compatible).
- ``headlines`` — title + summary + score only; the model pulls bodies with
  ``vault_read``.
- ``on_demand`` — no injection; the model drives ``vault_search`` /
  ``vault_read`` itself. A skipped source entry still reaches diagnostics.

The composer owns the fixed-cost accounting and publishes the remaining
dynamic budget on ``ComposerState.dynamic_token_budget``; the extension trims
retrieval to it, falling back to ``vault_retrieval.max_tokens`` when the model
window is unknown.

Side effects the extension owns: the ``vault_retrieval`` UI event, retrieval
telemetry (``retrieval_event``), and the per-conversation injected-path set
that suppresses repeats. ``ctx.composer.last_retrieved_context_text`` and
``last_memory_results`` carry the results to the composer's ``ComposedContext``
for reflection and the Context Inspector.

Disabled (``extensions.vault_retrieval.enabled: false``) leaves the agent with
manual ``vault_search`` / ``vault_read`` tools and no proactive injection.
Fail-open: retrieval, scoring, formatting, telemetry, and the publish all log
and degrade instead of failing the turn.

There is no in-memory retrieval cache: every turn queries ``embeddings.db``
through the retrieval provider, so a vault write needs no cache invalidation
hook (the issue's optional ``ON_VAULT_WRITE`` subscription).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from decafclaw.config_types import RetrievalMode
from decafclaw.events import EventBus, TurnLifecycle
from decafclaw.memory_context import (
    _trim_to_token_budget,
    format_memory_context,
    format_memory_headlines,
    get_already_retrieved_paths,
    retrieve_memory_context,
)
from decafclaw.util import estimate_tokens

from . import BaseExtension, ContextContribution, ContextSlot, provenance_excerpt, register_extension_type

log = logging.getLogger(__name__)


def resolve_retrieval_mode(config: Any, raw: Any = None) -> RetrievalMode:
    """Validate a retrieval mode; unknown values fall back to always."""
    if raw is None:
        raw = getattr(getattr(config, "vault_retrieval", None), "mode", None) or RetrievalMode.ALWAYS
    try:
        return RetrievalMode(raw)
    except ValueError:
        log.warning("Unknown vault_retrieval.mode %r; falling back to 'always'", raw)
        return RetrievalMode.ALWAYS


def score_candidates(candidates: list[dict], config: Any) -> list[dict]:
    """Score candidates by relevance, then order ties by structural importance.

    The threshold-bearing ``composite_score`` is similarity + recency only,
    renormalized over their configured sum. Importance is deliberately not in
    that sum: a global structural prior may reorder equally relevant
    candidates, but must not push a below-threshold candidate into context
    (#1713). Renormalizing keeps ``min_composite_score`` comparable after the
    importance term was removed.
    """
    relevance = config.relevance
    now = datetime.now()
    weight_sum = relevance.w_similarity + relevance.w_recency
    if weight_sum > 0:
        w_similarity = relevance.w_similarity / weight_sum
        w_recency = relevance.w_recency / weight_sum
    else:
        w_similarity = 0.0
        w_recency = 0.0

    for c in candidates:
        similarity = min(1.0, max(0.0, c.get("similarity", 0.0)))

        modified_at = c.get("modified_at", "")
        if modified_at:
            try:
                mod_time = datetime.fromisoformat(modified_at)
                hours = max(0.0, (now - mod_time).total_seconds() / 3600)
                recency = min(1.0, max(0.0, relevance.recency_decay_rate**hours))
            except (ValueError, TypeError):
                recency = 0.5
        else:
            recency = 0.5

        importance = min(1.0, max(0.0, c.get("importance", 0.5)))

        c["recency"] = recency
        c["importance"] = importance
        c["composite_score"] = w_similarity * similarity + w_recency * recency

    candidates.sort(
        key=lambda c: (c.get("composite_score", 0.0), c.get("importance", 0.0)),
        reverse=True,
    )
    return candidates


class VaultRetrievalExtension(BaseExtension):
    """Inject scored proactive vault retrieval as a WORKING_MEMORY contribution."""

    # Local embedding plus SQLite query; the shared 300 ms collector bound is
    # too tight for the first call that loads the embedding model.
    pre_compose_timeout = 5.0

    # Trims to the dynamic pool, so the composer runs this hook after fixed
    # contributions are charged and the budget is published (#1365).
    consumes_dynamic_budget = True

    def __init__(self, options: dict[str, Any], config: Any):
        super().__init__(name="vault_retrieval")
        self.options = options or {}
        self.config = config

    def enable(self, bus: EventBus) -> list[str]:
        self.disable(bus)
        self.subscription_ids = [bus.subscribe(TurnLifecycle.PRE_COMPOSE, self.pre_compose_hook)]
        return self.subscription_ids

    def _option(self, config: Any, name: str, default: Any) -> Any:
        if name in self.options:
            return self.options[name]
        return getattr(getattr(config, "vault_retrieval", None), name, default)

    async def pre_compose_hook(
        self,
        ctx: Any,
        user_message: str = "",
        history: list | None = None,
    ) -> ContextContribution | None:
        """Retrieve and format vault context; fail open on any error."""
        try:
            return await self._contribution(ctx, user_message, history)
        except Exception:
            log.warning("Vault retrieval injection failed", exc_info=True)
            self._clear_turn_state(ctx)
            return None

    async def _contribution(
        self,
        ctx: Any,
        user_message: str,
        history: list | None,
    ) -> ContextContribution | None:
        config = getattr(ctx, "config", None) or self.config
        self._clear_turn_state(ctx)

        from decafclaw.context_types import ComposerMode

        mode = getattr(getattr(ctx, "composer", None), "mode", ComposerMode.INTERACTIVE)
        if getattr(ctx, "skip_vault_retrieval", False) or mode.is_skip:
            return None

        retrieval_mode = resolve_retrieval_mode(config, self._option(config, "mode", RetrievalMode.ALWAYS))
        if retrieval_mode == RetrievalMode.ON_DEMAND:
            return ContextContribution(
                slot=ContextSlot.WORKING_MEMORY,
                messages=[],
                source_entry=self._source_entry(
                    tokens=0,
                    included=0,
                    truncated=0,
                    details={"mode": retrieval_mode, "injection_skipped": True},
                ),
            )

        results = await retrieve_memory_context(config, user_message)
        if not results:
            return ContextContribution(
                slot=ContextSlot.WORKING_MEMORY,
                messages=[],
                source_entry=self._source_entry(
                    tokens=0,
                    included=0,
                    truncated=0,
                    details={"mode": retrieval_mode, "injection_skipped": True},
                ),
            )

        budget = getattr(getattr(ctx, "composer", None), "dynamic_token_budget", None)
        if budget is None:
            budget = self._option(config, "max_tokens", 4000)
        score_threshold = config.relevance.min_composite_score

        # Score the full pool before dedupe so suppressed candidates remain
        # observable in retrieval telemetry (#197, #1157).
        all_scored = score_candidates(list(results), config)
        total_candidates = len(all_scored)

        composer_state = getattr(ctx, "composer", None)
        already_injected = get_already_retrieved_paths(history) if history is not None else set()
        already_injected |= set(getattr(composer_state, "injected_paths", set()))
        results = [r for r in all_scored if r.get("file_path", "") not in already_injected]
        suppressed = total_candidates - len(results)
        if suppressed:
            log.debug("Memory context: suppressed %d already-injected candidates", suppressed)

        if not results:
            await self._publish_retrieval_telemetry(
                ctx,
                all_scored=all_scored,
                survived_paths=set(),
                already_injected=already_injected,
                score_threshold=score_threshold,
            )
            return ContextContribution(
                slot=ContextSlot.WORKING_MEMORY,
                messages=[],
                source_entry=self._source_entry(
                    tokens=0,
                    included=0,
                    truncated=suppressed,
                    details={
                        "mode": retrieval_mode,
                        "injection_skipped": True,
                        "drop_reason": "already_injected",
                        "suppressed": suppressed,
                        "candidates_considered": total_candidates,
                        "token_budget": budget,
                        "budget_source": "dynamic"
                        if getattr(composer_state, "dynamic_token_budget", None) is not None
                        else "fixed",
                    },
                ),
            )

        results = [r for r in results if r.get("composite_score", 0) >= score_threshold]

        max_results = self._option(config, "max_results", 5)
        if max_results and max_results > 0:
            results = results[:max_results]

        if budget <= 0:
            results = []
        else:
            results = _trim_to_token_budget(results, budget)

        await self._publish_retrieval_telemetry(
            ctx,
            all_scored=all_scored,
            survived_paths={r.get("file_path", "") for r in results},
            already_injected=already_injected,
            score_threshold=score_threshold,
        )

        if not results:
            return ContextContribution(
                slot=ContextSlot.WORKING_MEMORY,
                messages=[],
                source_entry=self._source_entry(
                    tokens=0,
                    included=0,
                    truncated=total_candidates,
                    details={
                        "mode": retrieval_mode,
                        "injection_skipped": True,
                        "token_budget": budget,
                        "budget_source": "dynamic"
                        if getattr(composer_state, "dynamic_token_budget", None) is not None
                        else "fixed",
                    },
                ),
            )

        for r in results:
            r["tokens_estimated"] = estimate_tokens(r.get("entry_text", ""))

        if retrieval_mode == RetrievalMode.HEADLINES:
            formatted = format_memory_headlines(
                results,
                max_summary_chars=self._option(config, "headline_summary_max_chars", 200),
            )
        else:
            formatted = format_memory_context(results)

        msg: dict = {"role": "vault_retrieval", "content": formatted}
        msg["file_paths"] = [r.get("file_path", "") for r in results if r.get("file_path")]

        if results and self._option(config, "show_in_ui", True):
            await ctx.publish("vault_retrieval", text=formatted, results=results)

        # Record the injection only after the publish, so a cancellation
        # cannot suppress pages that were never delivered.
        if composer_state is not None:
            for r in results:
                path = r.get("file_path", "")
                if path:
                    composer_state.injected_paths.add(path)
            composer_state.last_retrieved_context_text = formatted
            composer_state.last_memory_results = results

        top_score = results[0].get("composite_score", 0)
        min_score = results[-1].get("composite_score", 0)
        return ContextContribution(
            slot=ContextSlot.WORKING_MEMORY,
            messages=[msg],
            source_entry=self._source_entry(
                tokens=estimate_tokens(formatted),
                included=len(results),
                truncated=total_candidates - len(results),
                details={
                    "mode": retrieval_mode,
                    "injection_skipped": False,
                    "top_score": round(top_score, 3),
                    "min_score": round(min_score, 3),
                    "candidates_considered": total_candidates,
                    "token_budget": budget,
                    "budget_source": "dynamic"
                    if getattr(composer_state, "dynamic_token_budget", None) is not None
                    else "fixed",
                },
            ),
            provenance={
                "vault": [
                    {
                        "page": r.get("file_path", ""),
                        "score": round(r.get("composite_score", 0.0), 3),
                        "excerpt": provenance_excerpt(r.get("summary") or r.get("entry_text", "")),
                    }
                    for r in results
                ]
            },
        )

    @staticmethod
    def _clear_turn_state(ctx: Any) -> None:
        composer_state = getattr(ctx, "composer", None)
        if composer_state is None:
            return
        composer_state.last_retrieved_context_text = ""
        composer_state.last_memory_results = []

    @staticmethod
    def _source_entry(*, tokens: int, included: int, truncated: int, details: dict) -> Any:
        from decafclaw.context_types import SourceEntry

        return SourceEntry(
            source="memory",
            tokens_estimated=tokens,
            items_included=included,
            items_truncated=truncated,
            details=details,
        )

    @staticmethod
    async def _publish_retrieval_telemetry(
        ctx: Any,
        all_scored: list[dict],
        survived_paths: set[str],
        already_injected: set[str],
        score_threshold: float,
    ) -> None:
        """Publish the full scored candidate list tagged with include/drop verdicts."""
        try:
            candidates_payload = []
            for c in all_scored:
                path = c.get("file_path", "")
                included = path in survived_paths
                if included:
                    drop_reason = None
                elif path in already_injected:
                    drop_reason = "already_injected"
                elif c.get("composite_score", 0) < score_threshold:
                    drop_reason = "score"
                else:
                    drop_reason = "budget"
                candidates_payload.append(
                    {
                        "file_path": path,
                        "source_type": c.get("source_type", ""),
                        "similarity": c.get("similarity", 0.0),
                        "recency": c.get("recency", 0.0),
                        "importance": c.get("importance", 0.0),
                        "composite_score": c.get("composite_score", 0.0),
                        "included": included,
                        "drop_reason": drop_reason,
                    }
                )
            await ctx.publish("retrieval_event", conv_id=ctx.conv_id, candidates=candidates_payload)
        except Exception as exc:  # fail-open telemetry
            log.debug("Retrieval telemetry emit failed: %s", exc)


register_extension_type("vault_retrieval", VaultRetrievalExtension, default_enabled=True)
