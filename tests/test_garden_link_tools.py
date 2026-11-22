"""Tests for garden's deterministic link discovery/apply tools (#1715)."""

from unittest.mock import AsyncMock, patch

import pytest

from decafclaw.skills.garden.tools import (
    TOOL_DEFINITIONS,
    TOOLS,
    SkillConfig,
    tool_vault_apply_link_plan,
    tool_vault_link_opportunities,
)


@pytest.fixture
def link_vault(config):
    pages = config.vault_agent_pages_dir
    pages.mkdir(parents=True, exist_ok=True)
    (pages / "Proxmox.md").write_text("# Proxmox\n\nHost notes.\n", encoding="utf-8")
    (pages / "Rust.md").write_text("# Rust\n\nLanguage notes.\n", encoding="utf-8")
    (pages / "Aerostat.md").write_text(
        "---\n"
        "summary: Proxmox in frontmatter is ignored\n"
        "---\n"
        "# Aerostat\n\n"
        "Runs on Proxmox. Proxmox is the host.\n\n"
        "See [[Rust]] for the language.\n\n"
        "```\n"
        "Proxmox in a code fence is ignored\n"
        "```\n",
        encoding="utf-8",
    )
    return pages


class TestVaultLinkOpportunities:
    @pytest.mark.asyncio
    async def test_finds_unlinked_title_mentions(self, ctx, link_vault):
        result = await tool_vault_link_opportunities(ctx)

        opportunities = {o["target"]: o for o in result.data["opportunities"]}
        assert "agent/pages/Proxmox.md" in opportunities
        proxmox = opportunities["agent/pages/Proxmox.md"]
        assert proxmox["page"] == "agent/pages/Aerostat.md"
        assert proxmox["occurrences"] == 2
        assert "Runs on Proxmox" in proxmox["sample_context"]

    @pytest.mark.asyncio
    async def test_excludes_existing_links_and_frontmatter(self, ctx, link_vault):
        result = await tool_vault_link_opportunities(ctx)

        targets = {o["target"] for o in result.data["opportunities"] if o["page"] == "agent/pages/Aerostat.md"}
        # Rust is already linked; Proxmox appears once in frontmatter, twice in
        # the body, and once in a code fence (frontmatter/code excluded).
        assert "agent/pages/Rust.md" not in targets
        assert "agent/pages/Proxmox.md" in targets
        proxmox = next(o for o in result.data["opportunities"] if o["page"] == "agent/pages/Aerostat.md")
        assert proxmox["occurrences"] == 2

    @pytest.mark.asyncio
    async def test_min_occurrences_and_limit_bound_output(self, ctx, link_vault):
        result = await tool_vault_link_opportunities(ctx, min_occurrences=3, limit=1)

        assert result.data["opportunities"] == []
        assert result.data["counts"]["candidates"] == 0

        result = await tool_vault_link_opportunities(ctx, limit=1)
        assert len(result.data["opportunities"]) <= 1

    @pytest.mark.asyncio
    async def test_escapes_regex_characters_in_titles(self, ctx, config):
        pages = config.vault_agent_pages_dir
        pages.mkdir(parents=True, exist_ok=True)
        (pages / "C++ Notes.md").write_text("# C++ Notes\n", encoding="utf-8")
        (pages / "Mention.md").write_text("Talked about C++ Notes today.\n", encoding="utf-8")

        result = await tool_vault_link_opportunities(ctx)

        assert any(
            o["page"] == "agent/pages/Mention.md" and o["target"] == "agent/pages/C++ Notes.md"
            for o in result.data["opportunities"]
        )


@pytest.fixture
def plan_vault(config):
    pages = config.vault_agent_pages_dir
    pages.mkdir(parents=True, exist_ok=True)
    (pages / "Topic.md").write_text(
        "# Topic\n\nIntro.\n\n## Related\n\n- [[Existing]]\n\n## Archive\n\nOld notes.\n",
        encoding="utf-8",
    )
    return pages


class TestVaultApplyLinkPlan:
    @pytest.mark.asyncio
    async def test_applies_section_and_after_heading_insertions(self, ctx, plan_vault):
        with (
            patch("decafclaw.skills.garden.tools._reindex_page", new_callable=AsyncMock) as reindex,
            patch("decafclaw.skills.garden.tools.publish_vault_changed", new_callable=AsyncMock) as publish,
        ):
            result = await tool_vault_apply_link_plan(
                ctx,
                [
                    {"page": "agent/pages/Topic", "insert_in_section": "Related", "text": "- [[New]]"},
                    {"page": "agent/pages/Topic", "insert_after_heading": "## Archive", "text": "- [[After]]"},
                ],
                dry_run=False,
            )

        assert result.data["applied"] == 2
        text = (plan_vault / "Topic.md").read_text(encoding="utf-8")
        assert "- [[New]]" in text
        assert text.index("- [[New]]") < text.index("## Archive")
        assert "- [[After]]" in text
        assert text.index("## Archive") < text.index("- [[After]]")
        assert reindex.await_count == 2
        assert publish.await_count == 2

    @pytest.mark.asyncio
    async def test_is_idempotent(self, ctx, plan_vault):
        plan = [{"page": "agent/pages/Topic", "insert_in_section": "Related", "text": "- [[New]]"}]
        with (
            patch("decafclaw.skills.garden.tools._reindex_page", new_callable=AsyncMock),
            patch("decafclaw.skills.garden.tools.publish_vault_changed", new_callable=AsyncMock),
        ):
            first = await tool_vault_apply_link_plan(ctx, plan, dry_run=False)
            second = await tool_vault_apply_link_plan(ctx, plan, dry_run=False)

        assert first.data["applied"] == 1
        assert second.data["applied"] == 0
        assert second.data["skipped"] == 1
        assert (plan_vault / "Topic.md").read_text(encoding="utf-8").count("- [[New]]") == 1

    @pytest.mark.asyncio
    async def test_dry_run_defaults_from_skill_config(self, ctx, plan_vault):
        plan = [{"page": "agent/pages/Topic", "insert_in_section": "Related", "text": "- [[New]]"}]
        with patch("decafclaw.skills.garden.tools._skill_config", SkillConfig(dry_run=True)):
            result = await tool_vault_apply_link_plan(ctx, plan)

        assert result.data["dry_run"] is True
        assert result.data["applied"] == 0
        assert "- [[New]]" not in (plan_vault / "Topic.md").read_text(encoding="utf-8")

    @pytest.mark.asyncio
    async def test_reports_per_item_failures(self, ctx, plan_vault):
        with (
            patch("decafclaw.skills.garden.tools._reindex_page", new_callable=AsyncMock),
            patch("decafclaw.skills.garden.tools.publish_vault_changed", new_callable=AsyncMock),
        ):
            result = await tool_vault_apply_link_plan(
                ctx,
                [
                    {"page": "agent/pages/Topic", "insert_in_section": "Related", "text": "- [[New]]"},
                    {"page": "agent/pages/Missing", "insert_in_section": "Related", "text": "- [[X]]"},
                    {"page": "agent/pages/Topic", "insert_in_section": "No Such Section", "text": "- [[Y]]"},
                ],
                dry_run=False,
            )

        assert result.data["applied"] == 1
        assert result.data["failed"] == 2
        outcomes = [(item["ok"], item["page"]) for item in result.data["items"]]
        assert outcomes == [
            (True, "agent/pages/Topic"),
            (False, "agent/pages/Missing"),
            (False, "agent/pages/Topic"),
        ]

    @pytest.mark.asyncio
    async def test_empty_plan_is_an_error(self, ctx):
        result = await tool_vault_apply_link_plan(ctx, [])
        assert result.text.startswith("[error:")


def test_link_tools_registered():
    for name, fn in (
        ("vault_link_opportunities", tool_vault_link_opportunities),
        ("vault_apply_link_plan", tool_vault_apply_link_plan),
    ):
        assert TOOLS[name] is fn
        entry = next(d for d in TOOL_DEFINITIONS if d["function"]["name"] == name)
        assert entry["writes"] is (name == "vault_apply_link_plan")
