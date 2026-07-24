"""Tests for garden's deterministic importance recompute (#197 Phase 5).

`compute_importance_scores` is pure and config-driven: retrieval frequency
(`retrieval_telemetry.aggregate`) and inbound-link count
(`backlinks.inbound_count`) are patched directly at their source modules so
the formula's math is exercised without any real telemetry log or backlink
index on disk.
"""

from dataclasses import replace
from unittest.mock import patch

import pytest

from decafclaw.frontmatter import parse_frontmatter
from decafclaw.skills.garden.tools import (
    compute_importance_scores,
    tool_vault_recompute_importance,
)

STAR = "agent/pages/star.md"
ORPHAN = "agent/pages/orphan.md"


@pytest.fixture
def vault_pages(config):
    """A small vault: a heavily-retrieved/linked "star" page and an orphan."""
    pages_dir = config.vault_agent_pages_dir
    pages_dir.mkdir(parents=True, exist_ok=True)
    (pages_dir / "star.md").write_text("The star page.\n", encoding="utf-8")
    (pages_dir / "orphan.md").write_text("Nobody cares about this.\n", encoding="utf-8")
    return pages_dir


def _patch_signals(retrieval_counts: dict[str, int], inbound_counts: dict[str, int]):
    return (
        patch(
            "decafclaw.retrieval_telemetry.aggregate",
            return_value={
                path: {"retrieval_count": n} for path, n in retrieval_counts.items()
            },
        ),
        patch(
            "decafclaw.backlinks.inbound_count",
            side_effect=lambda cfg, page: inbound_counts.get(page, 0),
        ),
    )


class TestComputeImportanceScores:
    def test_frequently_retrieved_and_linked_page_scores_near_one(
        self, config, vault_pages,
    ):
        p1, p2 = _patch_signals({STAR: 10}, {STAR: 5})
        with p1, p2:
            scores = compute_importance_scores(config)

        assert scores[STAR] == pytest.approx(1.0)
        assert scores[ORPHAN] == pytest.approx(0.0)

    def test_clamps_to_one_even_when_weights_sum_above_one(self, config, vault_pages):
        # Deliberately push weights above 1.0 combined to exercise the clamp,
        # not just the normalization-to-1.0 case above.
        config = replace(
            config,
            importance=replace(config.importance, w_retrieval=0.9, w_inbound=0.9),
        )
        p1, p2 = _patch_signals({STAR: 1}, {STAR: 1})
        with p1, p2:
            scores = compute_importance_scores(config)

        assert scores[STAR] == pytest.approx(1.0)
        assert scores[STAR] <= 1.0

    def test_empty_data_scores_all_zero_no_divide_by_zero(self, config, vault_pages):
        p1, p2 = _patch_signals({}, {})
        with p1, p2:
            scores = compute_importance_scores(config)

        assert scores == {STAR: 0.0, ORPHAN: 0.0}

    def test_excludes_journal_entries(self, config, vault_pages):
        journal_dir = config.vault_agent_journal_dir
        journal_dir.mkdir(parents=True, exist_ok=True)
        (journal_dir / "2026-07-23.md").write_text("## Entry\n", encoding="utf-8")

        p1, p2 = _patch_signals({}, {})
        with p1, p2:
            scores = compute_importance_scores(config)

        assert all("journal" not in p for p in scores)

    def test_no_vault_returns_empty(self, config):
        # vault_root doesn't exist in a fresh tmp config.
        p1, p2 = _patch_signals({}, {})
        with p1, p2:
            scores = compute_importance_scores(config)

        assert scores == {}


class TestToolVaultRecomputeImportance:
    @pytest.mark.asyncio
    async def test_dry_run_returns_deltas_without_writing(self, ctx, vault_pages):
        with patch(
            "decafclaw.skills.garden.tools.compute_importance_scores",
            return_value={STAR: 0.9, ORPHAN: 0.0},
        ):
            result = await tool_vault_recompute_importance(ctx, dry_run=True)

        assert result.data["dry_run"] is True
        by_path = {d["path"]: d for d in result.data["deltas"]}
        assert by_path[STAR]["new"] == 0.9
        # orphan.md's score (0.0) matches its unset importance (None != 0.0
        # rounds differently) — still counts as a planned change since
        # there's no existing frontmatter to compare against.
        assert ORPHAN in by_path

        # Nothing written to disk in dry-run mode.
        text = (ctx.config.vault_agent_pages_dir / "star.md").read_text(encoding="utf-8")
        assert "importance" not in text

    @pytest.mark.asyncio
    async def test_writes_changed_scores_and_skips_unchanged(self, ctx, vault_pages):
        # orphan.md already carries the score it would be recomputed to —
        # this page must be skipped as unchanged.
        (ctx.config.vault_agent_pages_dir / "orphan.md").write_text(
            "---\nimportance: 0.0\n---\nNobody cares about this.\n", encoding="utf-8",
        )

        with patch(
            "decafclaw.skills.garden.tools.compute_importance_scores",
            return_value={STAR: 0.9, ORPHAN: 0.0},
        ):
            result = await tool_vault_recompute_importance(ctx)

        assert result.data["dry_run"] is False
        changed_paths = {d["path"] for d in result.data["deltas"]}
        assert changed_paths == {STAR}

        star_meta, _ = parse_frontmatter(
            (ctx.config.vault_agent_pages_dir / "star.md").read_text(encoding="utf-8")
        )
        assert star_meta["importance"] == 0.9

        orphan_meta, orphan_body = parse_frontmatter(
            (ctx.config.vault_agent_pages_dir / "orphan.md").read_text(encoding="utf-8")
        )
        assert orphan_meta["importance"] == 0.0
        assert orphan_body.strip() == "Nobody cares about this."
