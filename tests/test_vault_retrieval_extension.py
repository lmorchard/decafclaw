"""Tests for VaultRetrievalExtension (#1489, #301, #1048, #1157)."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from decafclaw.config_types import ExtensionConfig, RetrievalMode
from decafclaw.context_types import ComposerMode
from decafclaw.events import EventBus
from decafclaw.extensions import init_extensions
from decafclaw.extensions.vault_retrieval import (
    VaultRetrievalExtension,
    resolve_retrieval_mode,
    score_candidates,
)
from decafclaw.util import estimate_tokens

RETRIEVE_PATCH = "decafclaw.extensions.vault_retrieval.retrieve_memory_context"
FORMAT_PATCH = "decafclaw.extensions.vault_retrieval.format_memory_context"
HEADLINES_PATCH = "decafclaw.extensions.vault_retrieval.format_memory_headlines"
TAPERED_PATCH = "decafclaw.extensions.vault_retrieval.format_memory_tapered"


def _ext(ctx) -> VaultRetrievalExtension:
    return VaultRetrievalExtension({}, ctx.config)


async def _hook(ctx, message="hello", history=None, ext=None):
    return await (ext or _ext(ctx)).pre_compose_hook(ctx, message, history or [])


def _candidate(path="pages/first.md", *, similarity=0.8, entry_text="some memory", **extra):
    candidate = {
        "entry_text": entry_text,
        "source_type": "page",
        "similarity": similarity,
        "file_path": path,
        "modified_at": "",
        "importance": 0.5,
    }
    candidate.update(extra)
    return candidate


class TestGates:
    async def test_skip_flag_yields_nothing(self, ctx):
        ctx.skip_vault_retrieval = True
        with patch(RETRIEVE_PATCH, new_callable=AsyncMock) as retrieve:
            assert await _hook(ctx) is None
        retrieve.assert_not_awaited()

    @pytest.mark.parametrize(
        "mode",
        [ComposerMode.HEARTBEAT, ComposerMode.SCHEDULED, ComposerMode.CHILD_AGENT],
    )
    async def test_non_interactive_modes_skip(self, ctx, mode):
        ctx.composer.mode = mode
        with patch(RETRIEVE_PATCH, new_callable=AsyncMock) as retrieve:
            assert await _hook(ctx) is None
        retrieve.assert_not_awaited()

    async def test_fail_open_on_retrieve_error(self, ctx):
        ctx.composer.last_retrieved_context_text = "stale"
        ctx.composer.last_memory_results = [{"file_path": "stale.md"}]
        with patch(RETRIEVE_PATCH, new_callable=AsyncMock, side_effect=RuntimeError("boom")):
            assert await _hook(ctx) is None
        assert ctx.composer.last_retrieved_context_text == ""
        assert ctx.composer.last_memory_results == []


class TestContribution:
    async def test_returns_tail_message_and_source(self, ctx):
        with (
            patch(RETRIEVE_PATCH, new_callable=AsyncMock, return_value=[_candidate()]),
            patch(FORMAT_PATCH, return_value="formatted memory"),
        ):
            contribution = await _hook(ctx)

        assert contribution is not None
        assert contribution.slot.value == "working_memory"
        assert len(contribution.messages) == 1
        assert contribution.messages[0]["role"] == "vault_retrieval"
        assert contribution.messages[0]["content"] == "formatted memory"
        assert contribution.messages[0]["file_paths"] == ["pages/first.md"]
        entry = contribution.source_entry
        assert entry is not None
        assert entry.source == "memory"
        assert entry.items_included == 1
        assert entry.details["mode"] == RetrievalMode.ALWAYS
        assert entry.details["injection_skipped"] is False
        assert contribution.provenance == {
            "vault": [
                {
                    "page": "pages/first.md",
                    "score": round(
                        entry.details["top_score"],
                        3,
                    ),
                    "excerpt": "some memory",
                }
            ]
        }

    async def test_hybrid_candidate_survives_composite_threshold(self, ctx):
        ctx.config.relevance.min_composite_score = 0.65
        candidate = _candidate("pages/exact.md", similarity=0.31, rrf_score_normalized=0.75)
        with (
            patch(RETRIEVE_PATCH, new_callable=AsyncMock, return_value=[candidate]),
            patch(FORMAT_PATCH, return_value="formatted"),
        ):
            contribution = await _hook(ctx)

        assert contribution.source_entry.items_included == 1
        assert contribution.messages[0]["file_paths"] == ["pages/exact.md"]

    async def test_no_candidates_yields_skipped_entry(self, ctx):
        with patch(RETRIEVE_PATCH, new_callable=AsyncMock, return_value=[]):
            contribution = await _hook(ctx)

        assert contribution is not None
        assert contribution.messages == []
        assert contribution.source_entry.details["injection_skipped"] is True

    async def test_dynamic_budget_limits_injection(self, ctx):
        candidates = [
            _candidate("pages/first.md", entry_text="first entry"),
            _candidate("pages/second.md", entry_text="second entry " * 50),
        ]
        budget = estimate_tokens("first entry") + 1
        ctx.composer.dynamic_token_budget = budget
        with (
            patch(RETRIEVE_PATCH, new_callable=AsyncMock, return_value=candidates),
            patch(FORMAT_PATCH, return_value="formatted"),
        ):
            contribution = await _hook(ctx)

        # The trimmer always keeps the first candidate; the oversized second
        # one must not fit the composer's dynamic budget.
        assert contribution.messages[0]["file_paths"] == ["pages/first.md"]
        assert contribution.source_entry.details["token_budget"] == budget

    async def test_zero_budget_skips_injection(self, ctx):
        ctx.composer.dynamic_token_budget = 0
        with patch(RETRIEVE_PATCH, new_callable=AsyncMock, return_value=[_candidate()]):
            contribution = await _hook(ctx)

        assert contribution.messages == []
        assert contribution.source_entry.items_included == 0

    async def test_caps_to_max_results(self, ctx):
        ctx.config.vault_retrieval.max_results = 2
        ctx.config.relevance.min_composite_score = 0.0
        candidates = [
            _candidate(f"pages/{i}.md", similarity=s, importance=i / 10)
            for i, s in enumerate([0.95, 0.90, 0.85, 0.80], start=1)
        ]
        with (
            patch(RETRIEVE_PATCH, new_callable=AsyncMock, return_value=candidates),
            patch(FORMAT_PATCH, return_value="formatted"),
        ):
            contribution = await _hook(ctx)

        assert contribution.messages[0]["file_paths"] == ["pages/1.md", "pages/2.md"]
        assert contribution.source_entry.items_included == 2
        assert contribution.source_entry.items_truncated == 2

    async def test_tracks_injected_paths_and_turn_state(self, ctx):
        candidates = [_candidate("pages/first.md"), _candidate("journal/second.md", entry_text="second")]
        with (
            patch(RETRIEVE_PATCH, new_callable=AsyncMock, return_value=candidates),
            patch(FORMAT_PATCH, return_value="formatted"),
        ):
            contribution = await _hook(ctx)

        assert "pages/first.md" in ctx.composer.injected_paths
        assert "journal/second.md" in ctx.composer.injected_paths
        assert ctx.composer.last_retrieved_context_text == "formatted"
        assert [r["file_path"] for r in ctx.composer.last_memory_results] == [
            "pages/first.md",
            "journal/second.md",
        ]
        assert contribution.messages[0]["file_paths"] == ["pages/first.md", "journal/second.md"]

    async def test_suppresses_already_injected_and_reports_drop(self, ctx):
        with (
            patch(RETRIEVE_PATCH, new_callable=AsyncMock, return_value=[_candidate()]),
            patch(FORMAT_PATCH, return_value="formatted"),
        ):
            await _hook(ctx)
            second = await _hook(ctx)

        assert second.messages == []
        entry = second.source_entry
        assert entry.details["injection_skipped"] is True
        assert entry.details["drop_reason"] == "already_injected"
        assert entry.details["suppressed"] == 1
        assert entry.details["candidates_considered"] == 1

    async def test_history_dedupe_suppresses_archived_pages(self, ctx):
        history = [
            {"role": "vault_retrieval", "content": "earlier", "file_paths": ["pages/first.md"]},
            {"role": "user", "content": "hello again"},
        ]
        with (
            patch(RETRIEVE_PATCH, new_callable=AsyncMock, return_value=[_candidate()]),
            patch(FORMAT_PATCH, return_value="formatted"),
        ):
            contribution = await _hook(ctx, history=history)

        assert contribution.messages == []
        assert contribution.source_entry.details["drop_reason"] == "already_injected"

    async def test_history_metadata_does_not_suppress_subsequent_turns(self, ctx):
        """Prior turns' working_memory metadata does not suppress subsequent turns (#1574)."""
        history = [
            {
                "role": "user",
                "content": "hello again",
                "working_memory": {"vault_paths": ["pages/first.md"]},
            },
        ]
        with (
            patch(RETRIEVE_PATCH, new_callable=AsyncMock, return_value=[_candidate()]),
            patch(FORMAT_PATCH, return_value="formatted"),
        ):
            contribution = await _hook(ctx, history=history)

        assert contribution is not None
        assert len(contribution.messages) == 1
        assert contribution.messages[0]["file_paths"] == ["pages/first.md"]


def test_retrieved_paths_only_reads_resident_legacy_rows():
    """get_already_retrieved_paths only tracks pages actually resident in history (#1574)."""
    from decafclaw.memory_context import get_already_retrieved_paths

    history = [
        {"role": "user", "content": "x", "working_memory": {"vault_paths": "pages/a.md"}},
        {
            "role": "user",
            "content": "y",
            "working_memory": {"vault_paths": ["pages/b.md", None, 3]},
        },
        {"role": "vault_retrieval", "content": "z", "file_paths": ["pages/c.md"]},
        {"role": "vault_retrieval", "content": "w", "file_paths": "pages/d.md"},
    ]
    assert get_already_retrieved_paths(history) == {"pages/c.md"}


class TestRetrievalModes:
    def test_enum_values_match_wire_strings(self):
        assert RetrievalMode.ALWAYS == "always"
        assert RetrievalMode.HEADLINES == "headlines"
        assert RetrievalMode.TAPERED == "tapered"
        assert RetrievalMode.ON_DEMAND == "on_demand"

    async def test_always_emits_full_body(self, ctx):
        ctx.config.vault_retrieval.mode = RetrievalMode.ALWAYS
        ctx.config.relevance.min_composite_score = 0.0
        with patch(RETRIEVE_PATCH, new_callable=AsyncMock, return_value=[_candidate(entry_text="FULL BODY HERE")]):
            contribution = await _hook(ctx)

        assert "FULL BODY HERE" in contribution.messages[0]["content"]
        assert contribution.source_entry.details["mode"] is RetrievalMode.ALWAYS

    async def test_headlines_emit_compact_lines(self, ctx):
        ctx.config.vault_retrieval.mode = RetrievalMode.HEADLINES
        ctx.config.relevance.min_composite_score = 0.0
        candidate = _candidate(entry_text="Long body text not to inject", summary="Concise summary")
        with patch(RETRIEVE_PATCH, new_callable=AsyncMock, return_value=[candidate]):
            with patch(HEADLINES_PATCH, return_value="headlines block") as headlines:
                contribution = await _hook(ctx)

        headlines.assert_called_once()
        assert contribution.messages[0]["content"] == "headlines block"
        assert contribution.source_entry.details["mode"] is RetrievalMode.HEADLINES

    async def test_tapered_emits_abstracts_and_headlines(self, ctx):
        ctx.config.vault_retrieval.mode = RetrievalMode.TAPERED
        ctx.config.relevance.min_composite_score = 0.0
        candidates = [
            _candidate("pages/first.md", summary="First summary", l1_excerpt="First abstract."),
            _candidate("pages/second.md", summary="Second summary"),
        ]
        with patch(RETRIEVE_PATCH, new_callable=AsyncMock, return_value=candidates):
            contribution = await _hook(ctx)

        content = contribution.messages[0]["content"]
        assert "First abstract." in content
        assert "More matches (L0 only)" in content
        assert "Second summary" in content
        assert contribution.source_entry.details["mode"] is RetrievalMode.TAPERED

    async def test_tapered_options_control_formatter(self, ctx):
        ctx.config.vault_retrieval.mode = RetrievalMode.TAPERED
        ctx.config.relevance.min_composite_score = 0.0
        ext = VaultRetrievalExtension(
            {"top_n_detailed": 2, "headline_summary_max_chars": 80},
            ctx.config,
        )
        with (
            patch(RETRIEVE_PATCH, new_callable=AsyncMock, return_value=[_candidate()]),
            patch(TAPERED_PATCH, return_value="tapered block") as tapered,
        ):
            contribution = await _hook(ctx, ext=ext)

        assert contribution.messages[0]["content"] == "tapered block"
        tapered.assert_called_once()
        assert tapered.call_args.kwargs["top_n_detailed"] == 2
        assert tapered.call_args.kwargs["max_summary_chars"] == 80

    async def test_tapered_token_budget_trims_before_formatting(self, ctx):
        ctx.config.vault_retrieval.mode = RetrievalMode.TAPERED
        ctx.config.relevance.min_composite_score = 0.0
        candidates = [
            _candidate("pages/first.md", entry_text="first entry"),
            _candidate("pages/second.md", entry_text="second entry " * 50),
        ]
        ctx.composer.dynamic_token_budget = estimate_tokens("first entry") + 1
        with (
            patch(RETRIEVE_PATCH, new_callable=AsyncMock, return_value=candidates),
            patch(TAPERED_PATCH, return_value="tapered") as tapered,
        ):
            await _hook(ctx)

        passed = tapered.call_args.args[0]
        assert [r["file_path"] for r in passed] == ["pages/first.md"]

    async def test_on_demand_skips_retrieval(self, ctx):
        ctx.config.vault_retrieval.mode = RetrievalMode.ON_DEMAND
        with patch(RETRIEVE_PATCH, new_callable=AsyncMock) as retrieve:
            contribution = await _hook(ctx)

        retrieve.assert_not_awaited()
        assert contribution.messages == []
        assert contribution.source_entry.details["mode"] is RetrievalMode.ON_DEMAND
        assert contribution.source_entry.details["injection_skipped"] is True

    async def test_unknown_mode_falls_back_to_always(self, ctx, caplog):
        ctx.config.vault_retrieval.mode = "bogus"  # type: ignore[assignment]
        with patch(RETRIEVE_PATCH, new_callable=AsyncMock, return_value=[_candidate(entry_text="BODY")]):
            contribution = await _hook(ctx)

        assert "BODY" in contribution.messages[0]["content"]
        assert contribution.source_entry.details["mode"] is RetrievalMode.ALWAYS
        assert "falling back to 'always'" in caplog.text

    def test_resolve_mode_accepts_plain_strings(self, ctx):
        ctx.config.vault_retrieval.mode = "headlines"  # type: ignore[assignment]
        assert resolve_retrieval_mode(ctx.config) is RetrievalMode.HEADLINES


class TestTelemetry:
    async def test_event_carries_full_candidate_list_with_verdicts(self, ctx):
        events = []

        async def capture(event):
            if event.get("type") == "retrieval_event":
                events.append(event)

        ctx.event_bus.subscribe(capture)
        ctx.config.relevance.min_composite_score = 0.5
        candidates = [
            _candidate("pages/hit.md", similarity=0.9, importance=0.9),
            _candidate("pages/miss.md", similarity=0.01, importance=0.1),
        ]
        with patch(RETRIEVE_PATCH, new_callable=AsyncMock, return_value=candidates):
            await _hook(ctx)

        assert len(events) == 1
        assert events[0]["conv_id"] == ctx.conv_id
        verdicts = {c["file_path"]: c for c in events[0]["candidates"]}
        assert verdicts["pages/hit.md"]["included"] is True
        assert verdicts["pages/hit.md"]["drop_reason"] is None
        assert verdicts["pages/miss.md"]["included"] is False
        assert verdicts["pages/miss.md"]["drop_reason"] == "score"

    async def test_event_published_when_all_candidates_suppressed(self, ctx):
        events = []

        async def capture(event):
            if event.get("type") == "retrieval_event":
                events.append(event)

        ctx.event_bus.subscribe(capture)
        history = [{"role": "vault_retrieval", "content": "earlier", "file_paths": ["pages/first.md"]}]
        with patch(RETRIEVE_PATCH, new_callable=AsyncMock, return_value=[_candidate()]):
            await _hook(ctx, history=history)

        assert len(events) == 1
        assert events[0]["candidates"][0]["drop_reason"] == "already_injected"


class TestScoring:
    def test_produces_composite_scores(self, config):
        scored = score_candidates(
            [{"similarity": 0.8, "modified_at": "", "importance": 0.5}, {"similarity": 0.6}],
            config,
        )
        assert all("composite_score" in c for c in scored)

    def test_sorted_by_score_descending(self, config):
        scored = score_candidates(
            [{"similarity": s, "modified_at": "", "importance": 0.5} for s in (0.3, 0.9, 0.6)],
            config,
        )
        scores = [c["composite_score"] for c in scored]
        assert scores == sorted(scores, reverse=True)

    def test_recency_favors_recent(self, config):
        now = datetime.now()
        recent = (now - timedelta(hours=1)).isoformat()
        old = (now - timedelta(hours=1000)).isoformat()
        scored = score_candidates(
            [
                {"similarity": 0.5, "modified_at": old, "importance": 0.5},
                {"similarity": 0.5, "modified_at": recent, "importance": 0.5},
            ],
            config,
        )
        assert scored[0]["modified_at"] == recent

    def test_importance_breaks_equal_relevance_ties(self, config):
        scored = score_candidates(
            [
                {"similarity": 0.5, "modified_at": "", "importance": 0.1},
                {"similarity": 0.5, "modified_at": "", "importance": 0.9},
            ],
            config,
        )
        assert scored[0]["importance"] == 0.9

    def test_importance_does_not_change_threshold_score(self, config):
        low = score_candidates([{"similarity": 0.8, "importance": 0.0}], config)[0]["composite_score"]
        high = score_candidates([{"similarity": 0.8, "importance": 1.0}], config)[0]["composite_score"]

        assert low == high

    def test_renormalized_relevance_does_not_collapse_threshold(self, config):
        # Default 0.625 * 0.9 + 0.375 * 0.5 (missing modified_at) = 0.75.
        # The old 0.5/0.3 weights would score 0.60 and drop this candidate.
        scored = score_candidates([{"similarity": 0.9, "importance": 0.0}], config)

        assert scored[0]["composite_score"] == pytest.approx(0.75)
        assert scored[0]["composite_score"] >= config.relevance.min_composite_score

    def test_relevance_weights_renormalize_when_overridden(self, config):
        config.relevance.w_similarity = 0.9
        config.relevance.w_recency = 0.1
        scored = score_candidates([{"similarity": 1.0, "importance": 0.0}], config)

        assert scored[0]["composite_score"] == pytest.approx(0.95)

    def test_missing_modified_at_defaults_recency(self, config):
        scored = score_candidates([{"similarity": 0.5, "importance": 0.5}], config)
        assert scored[0]["recency"] == 0.5

    def test_hybrid_rrf_order_is_authoritative(self, config):
        scored = score_candidates(
            [
                _candidate("pages/lexical.md", similarity=0.31, rrf_score_normalized=0.75),
                _candidate("pages/dense.md", similarity=0.95, rrf_score_normalized=0.60),
            ],
            config,
        )

        assert [c["file_path"] for c in scored] == ["pages/lexical.md", "pages/dense.md"]
        assert scored[0]["composite_score"] == pytest.approx(0.75)

    def test_hybrid_recency_breaks_rrf_ties(self, config):
        now = datetime.now()
        recent = (now - timedelta(hours=1)).isoformat()
        old = (now - timedelta(hours=1000)).isoformat()
        scored = score_candidates(
            [
                _candidate("pages/old.md", modified_at=old, rrf_score_normalized=0.7),
                _candidate("pages/new.md", modified_at=recent, rrf_score_normalized=0.7),
            ],
            config,
        )

        assert scored[0]["file_path"] == "pages/new.md"


class TestComposerIntegration:
    async def test_compose_publishes_event_and_populates_result(self, ctx):
        events = []

        async def capture(event):
            if event.get("type") == "vault_retrieval":
                events.append(event)

        ctx.event_bus.subscribe(capture)
        with (
            patch(RETRIEVE_PATCH, new_callable=AsyncMock, return_value=[_candidate()]),
            patch(FORMAT_PATCH, return_value="formatted memory"),
        ):
            from decafclaw.context_composer import ContextComposer

            composed = await ContextComposer().compose(ctx, "hello", [], mode=ComposerMode.INTERACTIVE)

        assert composed.retrieved_context_text == "formatted memory"
        assert [r["file_path"] for r in composed.memory_results] == ["pages/first.md"]
        # Retrieval is per-turn working memory; it reaches the prompt but not
        # the conversation archive (#1574).
        assert any("<vault_retrieval>" in str(m.get("content", "")) for m in composed.messages)
        assert not any(m["role"] == "vault_retrieval" for m in composed.messages_to_archive)
        # Paths survive as metadata on the archived user row for provenance/auditing.
        user_rows = [m for m in composed.messages_to_archive if m.get("role") == "user"]
        assert user_rows[0]["working_memory"]["vault_paths"] == ["pages/first.md"]
        assert all("working_memory" not in m for m in composed.messages)
        from decafclaw.memory_context import get_already_retrieved_paths

        # Prompt-residency deduplication: messages_to_archive has no vault_retrieval rows,
        # so get_already_retrieved_paths returns empty and does not suppress Turn 2.
        assert get_already_retrieved_paths(composed.messages_to_archive) == set()
        assert any(s.source == "memory" and s.items_included == 1 for s in composed.sources)
        assert len(events) == 1
        assert events[0]["text"] == "formatted memory"

    async def test_multiturn_followup_reretrieves_evicted_page(self, ctx):
        """Turn 2 can re-retrieve a page evicted from Turn 1's working memory (#1574)."""
        from decafclaw.context_composer import ContextComposer

        composer = ContextComposer()
        with (
            patch(RETRIEVE_PATCH, new_callable=AsyncMock, return_value=[_candidate()]),
            patch(FORMAT_PATCH, return_value="formatted memory"),
        ):
            turn1 = await composer.compose(ctx, "tell me about project", [], mode=ComposerMode.INTERACTIVE)
            assert turn1.retrieved_context_text == "formatted memory"

            # Turn 1 settles: archived messages contain user row + assistant reply
            history = list(turn1.messages_to_archive)
            history.append({"role": "assistant", "content": "Here is the summary."})

            # Turn 2: follow-up question. Page is NOT suppressed by turn 1's working memory metadata.
            turn2 = await ContextComposer().compose(
                ctx, "explain more about that project", history, mode=ComposerMode.INTERACTIVE
            )
            assert turn2.retrieved_context_text == "formatted memory"
            assert any("<vault_retrieval>" in str(m.get("content", "")) for m in turn2.messages)

    async def test_failed_compose_rolls_back_injected_paths(self, ctx):
        """A failed composition must not suppress a page that never reached the prompt."""
        from decafclaw.context_composer import ContextComposer

        with (
            patch(RETRIEVE_PATCH, new_callable=AsyncMock, return_value=[_candidate()]),
            patch(FORMAT_PATCH, return_value="formatted memory"),
            patch("decafclaw.context_composer.assemble_provider_history", side_effect=RuntimeError("assembly")),
        ):
            with pytest.raises(RuntimeError):
                await ContextComposer().compose(ctx, "hello", [], mode=ComposerMode.INTERACTIVE)

        assert ctx.composer.injected_paths == set()

        with (
            patch(RETRIEVE_PATCH, new_callable=AsyncMock, return_value=[_candidate()]),
            patch(FORMAT_PATCH, return_value="formatted memory"),
        ):
            composed = await ContextComposer().compose(ctx, "hello", [], mode=ComposerMode.INTERACTIVE)

        assert any("<vault_retrieval>" in str(m.get("content", "")) for m in composed.messages)

    async def test_disabled_extension_leaves_manual_tools_only(self, ctx):
        ctx.event_bus = EventBus()
        ctx.config.extensions["vault_retrieval"] = ExtensionConfig(enabled=False, type="vault_retrieval")
        manager = init_extensions(ctx.config, ctx.event_bus)
        assert not manager.is_enabled("vault_retrieval")

        with patch(RETRIEVE_PATCH, new_callable=AsyncMock) as retrieve:
            from decafclaw.context_composer import ContextComposer

            composed = await ContextComposer().compose(ctx, "hello", [], mode=ComposerMode.INTERACTIVE)

        retrieve.assert_not_awaited()
        assert composed.retrieved_context_text == ""
        assert not any(s.source == "memory" for s in composed.sources)

    def test_declares_generous_pre_compose_timeout(self, ctx):
        assert _ext(ctx).pre_compose_timeout == 5.0


class TestOptionsOverride:
    async def test_mode_option_overrides_legacy_section(self, ctx):
        ctx.config.vault_retrieval.mode = RetrievalMode.ALWAYS
        ext = VaultRetrievalExtension({"mode": "on_demand"}, ctx.config)
        with patch(RETRIEVE_PATCH, new_callable=AsyncMock) as retrieve:
            contribution = await _hook(ctx, ext=ext)

        retrieve.assert_not_awaited()
        assert contribution.source_entry.details["mode"] is RetrievalMode.ON_DEMAND

    async def test_max_results_option_overrides_legacy_section(self, ctx):
        ctx.config.vault_retrieval.max_results = 10
        ctx.config.relevance.min_composite_score = 0.0
        ext = VaultRetrievalExtension({"max_results": 1}, ctx.config)
        candidates = [_candidate(f"pages/{i}.md", similarity=0.9 - i / 100) for i in range(3)]
        with patch(RETRIEVE_PATCH, new_callable=AsyncMock, return_value=candidates):
            contribution = await _hook(ctx, ext=ext)

        assert contribution.source_entry.items_included == 1

    async def test_show_in_ui_false_skips_publish(self, ctx):
        events = []

        async def capture(event):
            if event.get("type") == "vault_retrieval":
                events.append(event)

        ctx.event_bus.subscribe(capture)
        ctx.config.vault_retrieval.show_in_ui = False
        with (
            patch(RETRIEVE_PATCH, new_callable=AsyncMock, return_value=[_candidate()]),
            patch(FORMAT_PATCH, return_value="formatted"),
        ):
            contribution = await _hook(ctx)

        assert contribution.messages
        assert events == []
