import pytest
from pathlib import Path
from dataclasses import replace
from decafclaw.skills.garden.tools import tool_vault_reorganize_folders
from decafclaw.skills.garden.tools import SkillConfig, init

pytestmark = pytest.mark.asyncio

async def test_garden_detects_and_suggests_cluster_folder_moves(tmp_path, ctx):
    pages_dir = tmp_path / "vault" / "agent" / "pages"
    pages_dir.mkdir(parents=True)
    
    (pages_dir / "apple.md").write_text("---\ntags: [fruit]\n---\nApple is a fruit.")
    (pages_dir / "banana.md").write_text("---\ntags: [fruit]\n---\nBanana is a fruit.")
    (pages_dir / "cherry.md").write_text("---\ntags: [fruit]\n---\nCherry is a fruit.")
    (pages_dir / "dog.md").write_text("---\ntags: [animal]\n---\nDog.")
    
    ctx.config = replace(ctx.config, vault=replace(ctx.config.vault, vault_path=str(tmp_path / "vault")))
    
    res = await tool_vault_reorganize_folders(ctx, dry_run=False)
    
    assert "Moved 3 pages" in res.text or "Reorganized" in res.text
    
    assert (pages_dir / "fruit" / "apple.md").exists()
    assert (pages_dir / "fruit" / "banana.md").exists()
    assert (pages_dir / "fruit" / "cherry.md").exists()
    
    assert (pages_dir / "dog.md").exists()

async def test_garden_folder_move_updates_links(tmp_path, ctx):
    pages_dir = tmp_path / "vault" / "agent" / "pages"
    pages_dir.mkdir(parents=True)
    
    (pages_dir / "apple.md").write_text("---\ntags: [fruit]\n---\nApple is a fruit.")
    (pages_dir / "banana.md").write_text("---\ntags: [fruit]\n---\nBanana is a fruit.")
    (pages_dir / "cherry.md").write_text("---\ntags: [fruit]\n---\nCherry is a fruit.")
    (pages_dir / "index.md").write_text("See [[apple]], [[banana]], and [[cherry]].")
    
    ctx.config = replace(ctx.config, vault=replace(ctx.config.vault, vault_path=str(tmp_path / "vault")))
    
    await tool_vault_reorganize_folders(ctx, dry_run=False)
    
    content = (pages_dir / "index.md").read_text()
    assert "[[agent/pages/fruit/apple|apple]]" in content

async def test_garden_folder_move_dry_run_and_respect_user_folders(tmp_path, ctx):
    pages_dir = tmp_path / "vault" / "agent" / "pages"
    pages_dir.mkdir(parents=True)
    
    (pages_dir / "apple.md").write_text("---\ntags: [fruit]\n---\nApple is a fruit.")
    (pages_dir / "banana.md").write_text("---\ntags: [fruit]\n---\nBanana is a fruit.")
    (pages_dir / "cherry.md").write_text("---\ntags: [fruit]\n---\nCherry is a fruit.")
    
    ctx.config = replace(ctx.config, vault=replace(ctx.config.vault, vault_path=str(tmp_path / "vault")))
    
    init(ctx.config, SkillConfig(dry_run=True))
    
    res = await tool_vault_reorganize_folders(ctx)
    
    assert "Dry run" in res.text or "Proposed" in res.text
    
    assert (pages_dir / "apple.md").exists()
    assert (pages_dir / "fruit" / "apple.md").exists() is False
