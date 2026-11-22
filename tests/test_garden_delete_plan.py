"""Tests for garden's batch delete plan and the vault_delete guard (#1717)."""

from unittest.mock import patch

import pytest

from decafclaw.skills.garden.tools import SkillConfig, tool_vault_delete_plan


@pytest.fixture
def delete_vault(config):
    pages = config.vault_agent_pages_dir
    pages.mkdir(parents=True, exist_ok=True)
    for name in ("A", "B", "C"):
        (pages / f"{name}.md").write_text(f"{name} page.\n", encoding="utf-8")
    return pages


class TestVaultDeletePlan:
    @pytest.mark.asyncio
    async def test_batch_deletes_pages_without_inbound_links(self, ctx, delete_vault):
        ctx.request_confirmation = None
        with patch("decafclaw.embeddings.delete_entries"):
            result = await tool_vault_delete_plan(
                ctx,
                ["agent/pages/A", "agent/pages/B", "agent/pages/C"],
                dry_run=False,
            )

        assert result.data["deleted"] == 3
        assert result.data["failed"] == 0
        assert all(not (delete_vault / f"{name}.md").exists() for name in ("A", "B", "C"))
        assert all(item["ok"] for item in result.data["items"])

    @pytest.mark.asyncio
    async def test_batch_refuses_linked_page_and_continues(self, ctx, delete_vault):
        (delete_vault / "Other.md").write_text("See [[A]].\n", encoding="utf-8")
        from decafclaw.backlinks import rebuild_index

        rebuild_index(ctx.config)
        with patch("decafclaw.embeddings.delete_entries"):
            result = await tool_vault_delete_plan(
                ctx,
                ["agent/pages/A", "agent/pages/B"],
                dry_run=False,
            )

        assert result.data["deleted"] == 1
        assert result.data["failed"] == 1
        assert (delete_vault / "A.md").exists()
        assert not (delete_vault / "B.md").exists()
        by_page = {item["page"]: item for item in result.data["items"]}
        assert by_page["agent/pages/A"]["ok"] is False
        assert "Other.md" in by_page["agent/pages/A"]["text"]

    @pytest.mark.asyncio
    async def test_batch_reports_per_item_missing_page(self, ctx, delete_vault):
        ctx.request_confirmation = None
        with patch("decafclaw.embeddings.delete_entries"):
            result = await tool_vault_delete_plan(
                ctx,
                ["agent/pages/A", "agent/pages/Missing"],
                dry_run=False,
            )

        assert result.data["deleted"] == 1
        assert result.data["failed"] == 1
        by_page = {item["page"]: item for item in result.data["items"]}
        assert by_page["agent/pages/A"]["ok"] is True
        assert by_page["agent/pages/Missing"]["ok"] is False

    @pytest.mark.asyncio
    async def test_batch_defaults_to_skill_config_dry_run(self, ctx, delete_vault):
        with patch("decafclaw.skills.garden.tools._skill_config", SkillConfig(dry_run=True)):
            result = await tool_vault_delete_plan(ctx, ["agent/pages/A"])

        assert result.data["dry_run"] is True
        assert result.data["deleted"] == 0
        assert result.data["would_delete"] == 1
        assert (delete_vault / "A.md").exists()

    @pytest.mark.asyncio
    async def test_batch_unattended_defaults_to_dry_run(self, ctx, delete_vault):
        ctx.request_confirmation = None
        with patch("decafclaw.skills.garden.tools._skill_config", SkillConfig(dry_run=False)):
            result = await tool_vault_delete_plan(ctx, ["agent/pages/A"])

        assert result.data["dry_run"] is True
        assert result.data["would_delete"] == 1
        assert (delete_vault / "A.md").exists()

    @pytest.mark.asyncio
    async def test_batch_allow_broken_links_reports_dangling_pages(self, ctx, delete_vault):
        (delete_vault / "Other.md").write_text("See [[A|alias]] and [[A#Section]].\n", encoding="utf-8")
        from decafclaw.backlinks import rebuild_index

        rebuild_index(ctx.config)
        with patch("decafclaw.embeddings.delete_entries"):
            result = await tool_vault_delete_plan(
                ctx,
                ["agent/pages/A"],
                dry_run=False,
                allow_broken_links=True,
            )

        assert result.data["deleted"] == 1
        item = result.data["items"][0]
        assert item["data"]["dangling_pages"] == ["agent/pages/Other.md"]
        assert "dangling" in item["text"].lower()

    @pytest.mark.asyncio
    async def test_empty_plan_is_an_error(self, ctx):
        result = await tool_vault_delete_plan(ctx, [])
        assert result.text.startswith("[error:")
