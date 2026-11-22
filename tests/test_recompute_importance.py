"""Tests for garden's structural importance recompute (#1713).

Importance is PageRank over the full backlink graph. Retrieval frequency is
deliberately absent: it made the score self-referential. The tests patch the
signal provider so the formula and write path run without a real index.
"""

from dataclasses import replace
from unittest.mock import patch

import pytest

from decafclaw.frontmatter import parse_frontmatter
from decafclaw.skills.garden.tools import (
    compute_importance_scores,
    compute_importance_signals,
    tool_vault_recompute_importance,
)

STAR = "agent/pages/star.md"
ORPHAN = "agent/pages/orphan.md"


@pytest.fixture
def vault_pages(config):
    pages_dir = config.vault_agent_pages_dir
    pages_dir.mkdir(parents=True, exist_ok=True)
    (pages_dir / "star.md").write_text("The star page.\n", encoding="utf-8")
    (pages_dir / "orphan.md").write_text("Nobody links here.\n", encoding="utf-8")
    return pages_dir


def _patch_signals(ranks: dict[str, float]):
    return patch("decafclaw.skills.garden.tools.compute_importance_signals", return_value=ranks)


class TestComputeImportanceScores:
    def test_pagerank_is_the_score(self, config, vault_pages):
        with _patch_signals({STAR: 0.02, ORPHAN: 0.0001}):
            scores = compute_importance_scores(config)

        assert scores[STAR] == pytest.approx(0.02)
        assert scores[ORPHAN] == pytest.approx(0.0001)
        assert scores[STAR] > scores[ORPHAN]

    def test_retrieval_telemetry_is_never_read(self, config, vault_pages):
        with (
            patch(
                "decafclaw.retrieval_telemetry.aggregate",
                side_effect=AssertionError("retrieval must not feed importance"),
            ),
            patch(
                "decafclaw.retrieval_telemetry.load_records",
                side_effect=AssertionError("retrieval must not feed importance"),
            ),
            _patch_signals({STAR: 0.01}),
        ):
            scores = compute_importance_scores(config)

        assert scores[STAR] == pytest.approx(0.01)

    def test_default_weights_are_structural_only(self, config):
        assert config.importance.w_retrieval == 0.0
        assert config.importance.w_reference == 0.0
        assert config.importance.w_inbound == 1.0

    def test_clamps_to_one(self, config, vault_pages):
        config = replace(config, importance=replace(config.importance, w_inbound=2.0))
        with _patch_signals({STAR: 0.9}):
            assert compute_importance_scores(config)[STAR] == 1.0

    def test_high_retrieval_zero_inbound_scores_at_floor(self, config, vault_pages):
        with (
            patch(
                "decafclaw.skills.garden.tools.backlinks.load_pagerank",
                return_value={STAR: 0.0},
            ),
            patch(
                "decafclaw.retrieval_telemetry.aggregate",
                return_value={STAR: {"retrieval_count": 999}},
            ),
        ):
            scores = compute_importance_scores(config)

        assert scores[STAR] == 0.0

    def test_zero_retrieval_high_inbound_scores_well(self, config, vault_pages):
        with (
            patch(
                "decafclaw.skills.garden.tools.backlinks.load_pagerank",
                return_value={STAR: 0.02},
            ),
            patch("decafclaw.retrieval_telemetry.aggregate", return_value={}),
        ):
            scores = compute_importance_scores(config)

        assert scores[STAR] == pytest.approx(0.02)

    def test_signals_are_agent_pages_only(self, config, vault_pages):
        with patch(
            "decafclaw.skills.garden.tools.backlinks.load_pagerank",
            return_value={STAR: 0.02, "agent/journal/2026-01-01.md": 0.5},
        ):
            signals = compute_importance_signals(config)

        assert signals[STAR] == pytest.approx(0.02)
        assert signals[ORPHAN] == 0.0
        assert "agent/journal/2026-01-01.md" not in signals

    def test_no_vault_returns_empty(self, config):
        with _patch_signals({}):
            assert compute_importance_scores(config) == {}

    def test_high_pagerank_beats_low(self, config, vault_pages):
        with _patch_signals({STAR: 0.02, ORPHAN: 0.001}):
            scores = compute_importance_scores(config)
        assert scores[STAR] > scores[ORPHAN]


class TestToolVaultRecomputeImportance:
    @pytest.mark.asyncio
    async def test_dry_run_returns_deltas_without_writing(self, ctx, vault_pages):
        with (
            _patch_signals({STAR: 0.02, ORPHAN: 0.0}),
            patch(
                "decafclaw.skills.garden.tools.compute_importance_scores",
                return_value={STAR: 0.9, ORPHAN: 0.0},
            ),
        ):
            result = await tool_vault_recompute_importance(ctx, dry_run=True)

        assert result.data["dry_run"] is True
        by_path = {d["path"]: d for d in result.data["deltas"]}
        assert by_path[STAR]["new"] == 0.9
        assert (ctx.config.vault_agent_pages_dir / "star.md").read_text(encoding="utf-8") == "The star page.\n"

    @pytest.mark.asyncio
    async def test_writes_changed_scores_and_skips_unchanged(self, ctx, vault_pages):
        (ctx.config.vault_agent_pages_dir / "orphan.md").write_text(
            "---\nimportance: 0.0\n---\nNobody links here.\n",
            encoding="utf-8",
        )
        with (
            _patch_signals({STAR: 0.02, ORPHAN: 0.0}),
            patch(
                "decafclaw.skills.garden.tools.compute_importance_scores",
                return_value={STAR: 0.9, ORPHAN: 0.0},
            ),
        ):
            result = await tool_vault_recompute_importance(ctx)

        assert result.data["dry_run"] is False
        assert {d["path"] for d in result.data["deltas"]} == {STAR}
        star_meta, _ = parse_frontmatter((ctx.config.vault_agent_pages_dir / "star.md").read_text(encoding="utf-8"))
        assert star_meta["importance"] == 0.9

    @pytest.mark.asyncio
    async def test_zero_inbound_page_gets_floor_importance(self, ctx, vault_pages):
        (ctx.config.vault_agent_pages_dir / "orphan.md").write_text(
            "---\nimportance: 0.7\n---\nNobody links here.\n",
            encoding="utf-8",
        )
        with (
            _patch_signals({STAR: 0.02, ORPHAN: 0.0}),
            patch(
                "decafclaw.skills.garden.tools.compute_importance_scores",
                return_value={STAR: 0.02, ORPHAN: 0.0},
            ),
        ):
            await tool_vault_recompute_importance(ctx)

        orphan_meta, _ = parse_frontmatter((ctx.config.vault_agent_pages_dir / "orphan.md").read_text(encoding="utf-8"))
        assert orphan_meta["importance"] == 0.0

    @pytest.mark.asyncio
    async def test_page_outside_agent_is_never_written(self, ctx, vault_pages):
        user_page = ctx.config.vault_root / "user-notes.md"
        user_page.write_text("Les's own hand-written notes.\n", encoding="utf-8")

        with patch(
            "decafclaw.skills.garden.tools.backlinks.load_pagerank",
            return_value={STAR: 0.02, ORPHAN: 0.0, "user-notes.md": 0.5},
        ):
            result = await tool_vault_recompute_importance(ctx)

        touched = {d["path"] for d in result.data["deltas"]}
        assert "user-notes.md" not in touched
        assert user_page.read_text(encoding="utf-8") == "Les's own hand-written notes.\n"

    @pytest.mark.asyncio
    async def test_nested_user_folder_outside_agent_is_never_written(self, ctx, vault_pages):
        other = ctx.config.vault_root / "reference" / "notes.md"
        other.parent.mkdir(parents=True, exist_ok=True)
        other.write_text("Reference material.\n", encoding="utf-8")

        with patch(
            "decafclaw.skills.garden.tools.backlinks.load_pagerank",
            return_value={STAR: 0.02, ORPHAN: 0.0, "reference/notes.md": 0.5},
        ):
            result = await tool_vault_recompute_importance(ctx)

        touched = {d["path"] for d in result.data["deltas"]}
        assert "reference/notes.md" not in touched
        assert other.read_text(encoding="utf-8") == "Reference material.\n"


class TestRecomputeSignalsOnce:
    @pytest.mark.asyncio
    async def test_computes_signals_once_per_run(self, ctx, vault_pages):
        with (
            patch("decafclaw.skills.garden.tools.backlinks.load_pagerank", return_value={}),
            patch(
                "decafclaw.skills.garden.tools.compute_importance_signals",
                wraps=compute_importance_signals,
            ) as signals,
        ):
            await tool_vault_recompute_importance(ctx, dry_run=True)

        assert signals.call_count == 1
