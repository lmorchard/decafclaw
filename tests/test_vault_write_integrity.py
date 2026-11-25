"""Tests for the post-write integrity advisory (#1745).

`detect_write_regressions` is the deterministic helper the vault write tools
use to report metadata a write destroyed, or metadata a new page shipped
without. It must be advisory: never raise, never block a write.
"""

from unittest.mock import AsyncMock, patch

import pytest

from decafclaw.skills.vault.tools import (
    detect_write_regressions,
    tool_vault_append,
    tool_vault_edit,
    tool_vault_write,
)

WITH_FRONTMATTER = """---
summary: A page about wires.
tags: [homelab, wiring]
keywords: [wire, cable]
---

# Wiring Notes

Body text.
"""

NO_FRONTMATTER = "# Wiring Notes\n\nBody text.\n"

BODY_ONLY = """---
summary: A page about wires.
tags: [homelab, wiring]
---

Body text with no heading.
"""


@pytest.fixture
def agent_pages(config):
    d = config.vault_agent_pages_dir
    d.mkdir(parents=True, exist_ok=True)
    return d


class TestDetectWriteRegressions:
    def test_clean_edit_reports_nothing(self):
        after = WITH_FRONTMATTER.replace("Body text.", "Body text, revised.")
        assert detect_write_regressions(WITH_FRONTMATTER, after) == []

    def test_dropped_summary_is_reported(self):
        after = WITH_FRONTMATTER.replace("summary: A page about wires.\n", "")
        warnings = detect_write_regressions(WITH_FRONTMATTER, after)
        assert any("frontmatter lost" in w and "summary" in w for w in warnings)

    def test_dropped_tags_are_reported(self):
        after = WITH_FRONTMATTER.replace("tags: [homelab, wiring]\n", "")
        warnings = detect_write_regressions(WITH_FRONTMATTER, after)
        assert any("tags" in w for w in warnings)

    def test_whole_frontmatter_block_removed_reports_all_fields(self):
        warnings = detect_write_regressions(WITH_FRONTMATTER, NO_FRONTMATTER)
        joined = " ".join(warnings)
        for field in ("summary", "tags", "keywords"):
            assert field in joined

    def test_lost_h1_is_reported(self):
        warnings = detect_write_regressions(WITH_FRONTMATTER, BODY_ONLY)
        assert any("level-1 heading" in w for w in warnings)

    def test_heading_kept_reports_no_heading_warning(self):
        after = WITH_FRONTMATTER.replace("Body text.", "Changed.")
        warnings = detect_write_regressions(WITH_FRONTMATTER, after)
        assert not any("heading" in w for w in warnings)

    def test_new_page_without_frontmatter_is_reported(self):
        warnings = detect_write_regressions(None, NO_FRONTMATTER)
        assert any("no frontmatter block" in w for w in warnings)

    def test_new_page_with_frontmatter_reports_nothing(self):
        assert detect_write_regressions(None, WITH_FRONTMATTER) == []

    def test_adding_metadata_is_not_a_regression(self):
        after = WITH_FRONTMATTER.replace("tags: [homelab, wiring]", "tags: [homelab, wiring, cables]")
        assert detect_write_regressions(WITH_FRONTMATTER, after) == []

    def test_malformed_frontmatter_does_not_raise(self):
        broken = "---\nsummary: [unclosed\n---\n\n# Title\n"
        assert isinstance(detect_write_regressions(WITH_FRONTMATTER, broken), list)

    def test_empty_strings_are_treated_as_absent(self):
        before = WITH_FRONTMATTER
        after = WITH_FRONTMATTER.replace("tags: [homelab, wiring]", "tags: []")
        assert any("tags" in w for w in detect_write_regressions(before, after))


class TestWriteToolAdvisories:
    @pytest.fixture
    def fm_page(self, agent_pages):
        path = agent_pages / "wiring.md"
        path.write_text(WITH_FRONTMATTER)
        return path

    @pytest.mark.asyncio
    async def test_edit_dropping_frontmatter_surfaces_advisory(self, ctx, fm_page):
        with patch("decafclaw.embeddings.index_entry", new_callable=AsyncMock):
            result = await tool_vault_edit(
                ctx,
                "agent/pages/wiring",
                old_text="---\nsummary: A page about wires.\ntags: [homelab, wiring]\nkeywords: [wire, cable]\n---\n\n",
                new_text="",
            )
        assert "Advisory:" in result.text
        assert "summary" in result.text

    @pytest.mark.asyncio
    async def test_edit_losing_h1_surfaces_advisory(self, ctx, fm_page):
        with patch("decafclaw.embeddings.index_entry", new_callable=AsyncMock):
            result = await tool_vault_edit(
                ctx,
                "agent/pages/wiring",
                old_text="# Wiring Notes\n",
                new_text="",
            )
        assert "Advisory:" in result.text
        assert "level-1 heading" in result.text

    @pytest.mark.asyncio
    async def test_clean_edit_has_no_advisory(self, ctx, fm_page):
        with patch("decafclaw.embeddings.index_entry", new_callable=AsyncMock):
            result = await tool_vault_edit(
                ctx,
                "agent/pages/wiring",
                old_text="Body text.",
                new_text="Body text, revised.",
            )
        assert "Advisory:" not in result.text

    @pytest.mark.asyncio
    async def test_append_dropping_nothing_has_no_advisory(self, ctx, fm_page):
        with patch("decafclaw.embeddings.index_entry", new_callable=AsyncMock):
            result = await tool_vault_append(ctx, "agent/pages/wiring", "\n\nMore body.\n")
        assert "Advisory:" not in result.text

    @pytest.mark.asyncio
    async def test_append_creating_bare_page_surfaces_advisory(self, ctx, agent_pages):
        with patch("decafclaw.embeddings.index_entry", new_callable=AsyncMock):
            result = await tool_vault_append(ctx, "agent/pages/bare", "# Bare\n\nText.\n")
        assert "Advisory:" in result.text
        assert "no frontmatter block" in result.text

    @pytest.mark.asyncio
    async def test_write_creating_bare_page_surfaces_advisory(self, ctx, agent_pages):
        result = await tool_vault_write(ctx, "agent/pages/bare2", "# Bare\n\nText.\n")
        assert "Advisory:" in result.text
        assert result.data["advisories"]

    @pytest.mark.asyncio
    async def test_write_overwriting_away_frontmatter_surfaces_advisory(self, ctx, fm_page):
        result = await tool_vault_write(ctx, "agent/pages/wiring", NO_FRONTMATTER)
        assert "Advisory:" in result.text
        assert "frontmatter lost" in result.text

    @pytest.mark.asyncio
    async def test_write_with_frontmatter_has_no_advisory(self, ctx, agent_pages):
        result = await tool_vault_write(ctx, "agent/pages/ok", WITH_FRONTMATTER)
        assert "Advisory:" not in result.text
