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
        # STAR has real signal (retrieval); ORPHAN has none — its mocked
        # score of 0.0 should NOT show up as a planned change, since a page
        # with zero raw signal is left alone rather than zeroed (#197 cold
        # start).
        p1, p2 = _patch_signals({STAR: 10}, {})
        with p1, p2, patch(
            "decafclaw.skills.garden.tools.compute_importance_scores",
            return_value={STAR: 0.9, ORPHAN: 0.0},
        ):
            result = await tool_vault_recompute_importance(ctx, dry_run=True)

        assert result.data["dry_run"] is True
        by_path = {d["path"]: d for d in result.data["deltas"]}
        assert by_path[STAR]["new"] == 0.9
        assert ORPHAN not in by_path

        # Nothing written to disk in dry-run mode.
        text = (ctx.config.vault_agent_pages_dir / "star.md").read_text(encoding="utf-8")
        assert "importance" not in text

    @pytest.mark.asyncio
    async def test_writes_changed_scores_and_skips_unchanged(self, ctx, vault_pages):
        # orphan.md already carries the score it would be recomputed to —
        # this page must be skipped as unchanged (it also has zero raw
        # signal, so it would be skipped for that reason too).
        (ctx.config.vault_agent_pages_dir / "orphan.md").write_text(
            "---\nimportance: 0.0\n---\nNobody cares about this.\n", encoding="utf-8",
        )

        p1, p2 = _patch_signals({STAR: 10}, {})
        with p1, p2, patch(
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


class TestRecomputeScopedToAgentPages:
    """#197 whole-branch-review fix: automated importance writes must stay
    within `agent/` — the tool `vault_update_frontmatter` keeps its
    vault-wide reach, but the unattended weekly sweep must not touch the
    user's own hand-written pages living elsewhere in the vault."""

    @pytest.mark.asyncio
    async def test_user_page_outside_agent_is_never_written(self, ctx, vault_pages):
        # A user page living directly under vault_root, outside agent/.
        user_page = ctx.config.vault_root / "user-notes.md"
        user_page.write_text("Les's own hand-written notes.\n", encoding="utf-8")

        p1, p2 = _patch_signals({STAR: 10, "user-notes.md": 10}, {})
        with p1, p2:
            result = await tool_vault_recompute_importance(ctx)

        touched_paths = {d["path"] for d in result.data["deltas"]}
        assert "user-notes.md" not in touched_paths

        assert user_page.read_text(encoding="utf-8") == "Les's own hand-written notes.\n"

    @pytest.mark.asyncio
    async def test_nested_user_folder_outside_agent_is_never_written(self, ctx, vault_pages):
        # A user-owned subfolder outside agent/ (not just a top-level file).
        other_dir = ctx.config.vault_root / "reference"
        other_dir.mkdir(parents=True, exist_ok=True)
        other_page = other_dir / "notes.md"
        other_page.write_text("Reference material.\n", encoding="utf-8")

        p1, p2 = _patch_signals({STAR: 10}, {"reference/notes.md": 10})
        with p1, p2:
            result = await tool_vault_recompute_importance(ctx)

        touched_paths = {d["path"] for d in result.data["deltas"]}
        assert "reference/notes.md" not in touched_paths
        assert other_page.read_text(encoding="utf-8") == "Reference material.\n"


class TestRecomputeColdStart:
    """#197 whole-branch-review fix: pages with zero measured signal keep
    their existing importance (or lack of one) instead of being zeroed."""

    @pytest.mark.asyncio
    async def test_agent_page_with_real_signal_gets_written(self, ctx, vault_pages):
        p1, p2 = _patch_signals({STAR: 10}, {})
        with p1, p2:
            result = await tool_vault_recompute_importance(ctx)

        touched_paths = {d["path"] for d in result.data["deltas"]}
        assert STAR in touched_paths

        star_meta, _ = parse_frontmatter(
            (ctx.config.vault_agent_pages_dir / "star.md").read_text(encoding="utf-8")
        )
        # Only retrieval signal is present (default w_retrieval=0.6).
        assert star_meta["importance"] == pytest.approx(0.6)

    @pytest.mark.asyncio
    async def test_zero_signal_page_with_existing_importance_is_untouched(
        self, ctx, vault_pages,
    ):
        # A page with dream's initial importance guess but no measured
        # signal yet — recompute must not overwrite it, even though the
        # formula would compute 0.0 for it (no retrieval, no inbound links,
        # and its neighbor STAR absorbs all the normalized signal).
        (ctx.config.vault_agent_pages_dir / "orphan.md").write_text(
            "---\nimportance: 0.7\n---\nNobody cares about this.\n", encoding="utf-8",
        )

        p1, p2 = _patch_signals({STAR: 10}, {})
        with p1, p2:
            result = await tool_vault_recompute_importance(ctx)

        touched_paths = {d["path"] for d in result.data["deltas"]}
        assert ORPHAN not in touched_paths

        orphan_meta, _ = parse_frontmatter(
            (ctx.config.vault_agent_pages_dir / "orphan.md").read_text(encoding="utf-8")
        )
        assert orphan_meta["importance"] == 0.7

    @pytest.mark.asyncio
    async def test_zero_signal_page_with_no_importance_stays_unset(self, ctx, vault_pages):
        p1, p2 = _patch_signals({STAR: 10}, {})
        with p1, p2:
            result = await tool_vault_recompute_importance(ctx)

        touched_paths = {d["path"] for d in result.data["deltas"]}
        assert ORPHAN not in touched_paths

        orphan_meta, _ = parse_frontmatter(
            (ctx.config.vault_agent_pages_dir / "orphan.md").read_text(encoding="utf-8")
        )
        assert "importance" not in orphan_meta
