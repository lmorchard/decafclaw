"""Tests for the one-time vault-frontmatter backfill CLI (#197 Phase 3)."""

from unittest.mock import AsyncMock, patch

import pytest

from decafclaw.backfill_frontmatter import run_backfill
from decafclaw.frontmatter import parse_frontmatter

FIXED_FIELDS = {
    "summary": "A page about testing.",
    "keywords": ["testing", "backfill"],
    "tags": ["test"],
    "importance": 0.6,
}


@pytest.fixture
def agent_pages(config):
    """Create the agent pages directory."""
    d = config.vault_agent_pages_dir
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture
def patched_generate():
    """Patch the LLM call with fixed fields — no real LLM calls in this test."""
    with patch(
        "decafclaw.backfill_frontmatter.generate_fields_for_page",
        AsyncMock(return_value=dict(FIXED_FIELDS)),
    ) as mock:
        yield mock


class TestRunBackfill:
    @pytest.mark.asyncio
    async def test_fills_bare_page_and_skips_complete_page(
        self, config, agent_pages, patched_generate,
    ):
        bare = agent_pages / "bare.md"
        bare.write_text("Body of the bare page.\n", encoding="utf-8")

        complete = agent_pages / "complete.md"
        complete.write_text(
            "---\n"
            "summary: Already has one.\n"
            "keywords: [existing]\n"
            "tags: [done]\n"
            "importance: 0.9\n"
            "---\n"
            "Body of the complete page.\n",
            encoding="utf-8",
        )

        results = await run_backfill(config)

        by_path = {r["path"]: r for r in results}
        assert by_path["agent/pages/bare.md"]["action"] == "filled"
        assert by_path["agent/pages/complete.md"]["action"] == "skipped"

        # Only the bare page triggered an LLM call.
        patched_generate.assert_awaited_once()

        meta, body = parse_frontmatter(bare.read_text(encoding="utf-8"))
        assert meta["summary"] == FIXED_FIELDS["summary"]
        assert meta["keywords"] == FIXED_FIELDS["keywords"]
        assert meta["tags"] == FIXED_FIELDS["tags"]
        assert meta["importance"] == FIXED_FIELDS["importance"]
        assert body.strip() == "Body of the bare page."

        # The already-complete page is untouched.
        meta2, _ = parse_frontmatter(complete.read_text(encoding="utf-8"))
        assert meta2["summary"] == "Already has one."

    @pytest.mark.asyncio
    async def test_does_not_clobber_partial_manual_frontmatter(
        self, config, agent_pages, patched_generate,
    ):
        p = agent_pages / "partial.md"
        p.write_text(
            "---\nsummary: Hand-written summary.\n---\nBody.\n",
            encoding="utf-8",
        )

        results = await run_backfill(config)

        assert results[0]["action"] == "filled"
        meta, _ = parse_frontmatter(p.read_text(encoding="utf-8"))
        # Manual summary preserved; missing fields filled in.
        assert meta["summary"] == "Hand-written summary."
        assert meta["tags"] == FIXED_FIELDS["tags"]
        assert meta["importance"] == FIXED_FIELDS["importance"]

    @pytest.mark.asyncio
    async def test_dry_run_writes_nothing(self, config, agent_pages, patched_generate):
        p = agent_pages / "bare.md"
        original = "Body of the bare page.\n"
        p.write_text(original, encoding="utf-8")

        results = await run_backfill(config, dry_run=True)

        assert results[0]["action"] == "planned"
        assert results[0]["fields"]["summary"] == FIXED_FIELDS["summary"]
        assert p.read_text(encoding="utf-8") == original

    @pytest.mark.asyncio
    async def test_limit_caps_llm_calls_but_not_free_skips(
        self, config, agent_pages, patched_generate,
    ):
        # Named to sort before the bare pages, so it's visited (and skipped
        # for free) before the limit is reached.
        (agent_pages / "aaa-complete.md").write_text(
            "---\n"
            "summary: s\nkeywords: [a]\ntags: [b]\nimportance: 0.5\n"
            "---\nBody.\n",
            encoding="utf-8",
        )
        (agent_pages / "bare-a.md").write_text("Body A.\n", encoding="utf-8")
        (agent_pages / "bare-b.md").write_text("Body B.\n", encoding="utf-8")

        results = await run_backfill(config, limit=1)

        actions = [r["action"] for r in results]
        assert actions.count("filled") == 1
        assert actions.count("skipped") == 1
        patched_generate.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_skips_journal_entries(self, config, agent_pages, patched_generate):
        journal_dir = config.vault_agent_journal_dir
        journal_dir.mkdir(parents=True, exist_ok=True)
        (journal_dir / "2026-07-23.md").write_text("## Entry\nSome text.\n", encoding="utf-8")

        results = await run_backfill(config)

        assert results == []
        patched_generate.assert_not_awaited()
