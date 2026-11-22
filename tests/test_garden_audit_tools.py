"""Tests for garden's deterministic audit tools (#1716)."""

import pytest

from decafclaw.embeddings import index_entry_sync
from decafclaw.skills.garden.tools import (
    TOOL_DEFINITIONS,
    TOOLS,
    tool_vault_broken_links,
    tool_vault_find_duplicate_candidates,
    tool_vault_frontmatter_audit,
)


@pytest.fixture
def links_vault(config):
    pages = config.vault_agent_pages_dir
    pages.mkdir(parents=True, exist_ok=True)
    (pages / "Aerostat.md").write_text("Aerostat page.\n", encoding="utf-8")
    (pages / "Broken.md").write_text(
        "---\ntags: [x]\n---\nLine one\n"
        "See [[Aerostats]], [[Aerostat|the page]], [[Aerostat#History]], "
        "and [[Totally Missing Thing]].\n",
        encoding="utf-8",
    )
    return pages


class TestVaultBrokenLinks:
    @pytest.mark.asyncio
    async def test_reports_only_unresolved_links_with_suggestions(self, ctx, links_vault):
        result = await tool_vault_broken_links(ctx)

        assert result.data["counts"] == {"pages": 1, "links": 2}
        entries = {entry["link"]: entry for entry in result.data["broken"]}
        assert set(entries) == {"[[Aerostats]]", "[[Totally Missing Thing]]"}

        moved = entries["[[Aerostats]]"]
        assert moved["page"] == "agent/pages/Broken.md"
        assert moved["line"] == 5
        assert moved["suggestions"] == ["agent/pages/Aerostat.md"]
        assert moved["kind"] == "possible_move"

        missing = entries["[[Totally Missing Thing]]"]
        assert missing["suggestions"] == []
        assert missing["kind"] == "missing"

    @pytest.mark.asyncio
    async def test_limit_bounds_entries(self, ctx, links_vault):
        result = await tool_vault_broken_links(ctx, limit=1)

        assert len(result.data["broken"]) == 1
        assert result.data["truncated"] is True

    @pytest.mark.asyncio
    async def test_clean_vault_returns_no_links(self, ctx, config):
        pages = config.vault_agent_pages_dir
        pages.mkdir(parents=True, exist_ok=True)
        (pages / "A.md").write_text("Links to [[A]].\n", encoding="utf-8")

        result = await tool_vault_broken_links(ctx)

        assert result.data["broken"] == []
        assert result.data["counts"]["links"] == 0


@pytest.fixture
def audit_vault(config):
    pages = config.vault_agent_pages_dir
    pages.mkdir(parents=True, exist_ok=True)

    def write(name: str, text: str) -> None:
        (pages / name).write_text(text, encoding="utf-8")

    write(
        "malformed.md",
        "---\nsummary: Star Trek: Strange New Worlds\n---\n> tl;dr: broken\nBody.\n",
    )
    write("missing.md", "> tl;dr: fields are missing\nBody.\n")
    write(
        "oversized.md",
        "---\nsummary: long page\nkeywords: [k]\ntags: [t]\nimportance: 0.5\n---\n"
        "# Oversized\n\n> tl;dr: long\n\n" + ("line\n" * 120),
    )
    write(
        "notldr.md",
        "---\nsummary: no summary line\nkeywords: [k]\ntags: [t]\nimportance: 0.5\n---\n" + ("line\n" * 25),
    )
    write(
        "stale.md",
        "---\nsummary: banana orange\nkeywords: [k]\ntags: [t]\nimportance: 0.5\n---\n"
        "# Stale\n\n> tl;dr: stale\n\n" + ("quantum chromodynamics lattice gauge theory\n" * 8),
    )
    write(
        "healthy.md",
        "---\nsummary: quantum chromodynamics overview\nkeywords: [k]\ntags: [t]\nimportance: 0.5\n---\n"
        "# Healthy\n\n> tl;dr: healthy\n\n" + ("quantum chromodynamics lattice gauge theory\n" * 8),
    )
    return pages


class TestVaultFrontmatterAudit:
    @pytest.mark.asyncio
    async def test_reports_each_category(self, ctx, audit_vault):
        result = await tool_vault_frontmatter_audit(ctx)
        data = result.data

        assert data["counts"]["malformed_yaml"] == 1
        assert [entry["path"] for entry in data["malformed_yaml"]] == ["agent/pages/malformed.md"]
        assert "mapping values" in data["malformed_yaml"][0]["error"]

        # The malformed page loses all frontmatter, so it counts as missing
        # every field too; the no-frontmatter page does the same.
        for field in ("summary", "keywords", "tags", "importance"):
            assert data["counts"]["missing"][field] == 2

        assert [entry["path"] for entry in data["oversized"]] == ["agent/pages/oversized.md"]
        assert [entry["path"] for entry in data["missing_tldr"]] == ["agent/pages/notldr.md"]

        assert [entry["path"] for entry in data["stale_summary"]] == ["agent/pages/stale.md"]
        assert data["stale_summary"][0]["advisory"] is True
        assert data["stale_summary"][0]["overlap"] == 0.0

    @pytest.mark.asyncio
    async def test_limit_bounds_examples(self, ctx, audit_vault):
        result = await tool_vault_frontmatter_audit(ctx, limit=1)

        assert len(result.data["missing"]["summary"]) == 1
        assert result.data["truncated"] is True


@pytest.fixture
def duplicate_vault(config):
    pages = config.vault_agent_pages_dir
    pages.mkdir(parents=True, exist_ok=True)
    for name in ("Alpha", "Beta", "Gamma"):
        (pages / f"{name}.md").write_text(f"{name} page.\n", encoding="utf-8")

    dims = config.embedding.dimensions

    def vector(x: float, y: float) -> list[float]:
        return [x, y, *([0.0] * (dims - 2))]

    index_entry_sync(config, "agent/pages/Alpha.md", "alpha", vector(1.0, 0.0), source_type="page")
    index_entry_sync(config, "agent/pages/Beta.md", "beta", vector(0.99, 0.1), source_type="page")
    index_entry_sync(config, "agent/pages/Gamma.md", "gamma", vector(0.0, 1.0), source_type="page")
    return pages


class TestVaultFindDuplicateCandidates:
    @pytest.mark.asyncio
    async def test_returns_high_similarity_pairs_without_reembedding(self, ctx, duplicate_vault):
        result = await tool_vault_find_duplicate_candidates(ctx, threshold=0.9)

        assert result.data["pairs"] == [
            {
                "a": "agent/pages/Alpha.md",
                "b": "agent/pages/Beta.md",
                "similarity": pytest.approx(0.994937),
            }
        ]
        assert result.data["indexed_pages"] == 3

    @pytest.mark.asyncio
    async def test_limit_bounds_pairs(self, ctx, duplicate_vault):
        result = await tool_vault_find_duplicate_candidates(ctx, threshold=0.0, limit=1)

        assert len(result.data["pairs"]) == 1

    @pytest.mark.asyncio
    async def test_missing_index_returns_empty(self, ctx):
        result = await tool_vault_find_duplicate_candidates(ctx)

        assert result.data["pairs"] == []
        assert "index" in result.text.lower()


def test_audit_tools_registered_read_only():
    for name, fn in (
        ("vault_broken_links", tool_vault_broken_links),
        ("vault_frontmatter_audit", tool_vault_frontmatter_audit),
        ("vault_find_duplicate_candidates", tool_vault_find_duplicate_candidates),
    ):
        assert TOOLS[name] is fn
        entry = next(d for d in TOOL_DEFINITIONS if d["function"]["name"] == name)
        assert entry["writes"] is False
