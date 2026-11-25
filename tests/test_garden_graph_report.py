"""Tests for garden's deterministic vault graph report (#1714)."""

import pytest

from decafclaw import backlinks
from decafclaw.skills.garden.tools import TOOL_DEFINITIONS, TOOLS, tool_vault_graph_report


@pytest.fixture
def graph_vault(config):
    """A small hand-built link graph with one of every reported shape."""
    vault = config.vault_root
    pages = config.vault_agent_pages_dir
    pages.mkdir(parents=True, exist_ok=True)
    journal = config.vault_agent_journal_dir
    journal.mkdir(parents=True, exist_ok=True)
    user = vault / "user"
    user.mkdir(parents=True, exist_ok=True)

    def write(rel: str, text: str) -> None:
        path = vault / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    # A-B component, joined by hub and fed by c/d/e.
    write("agent/pages/a.md", "Links to [[b]].\n")
    write("agent/pages/b.md", "Links to [[a]].\n")
    write("agent/pages/hub.md", "Links to [[a]].\n")
    for name in ("c", "d", "e"):
        write(f"agent/pages/{name}.md", "Links to [[hub]].\n")

    # Sink: high outbound, zero inbound.
    write("agent/pages/sink.md", "".join(f"[[t{i}]]\n" for i in range(10)))
    for i in range(10):
        write(f"agent/pages/t{i}.md", "target\n")

    # Toehold: high inbound, zero outbound.
    write("agent/pages/toehold.md", "no outbound links\n")
    for i in range(10):
        write(f"agent/pages/s{i}.md", "Links to [[toehold]].\n")

    # True isolate.
    write("agent/pages/iso.md", "isolated\n")

    # Non-agent sources still count as inbound signal.
    write("agent/journal/2026-01-01.md", "Links to [[c]].\n")
    write("user/notes.md", "Links to [[d]].\n")

    backlinks.rebuild_index(config)
    return config


class TestPageRank:
    def test_rewards_inbound_links_and_preserves_mass(self):
        ranks = backlinks.compute_pagerank({"b": ["a"], "c": ["b"]}, nodes={"a", "b", "c"})

        assert ranks["c"] > ranks["b"] > ranks["a"]
        assert sum(ranks.values()) == pytest.approx(1.0)

    def test_isolated_node_gets_floor_not_zero(self):
        ranks = backlinks.compute_pagerank({"b": ["a"]}, nodes={"a", "b", "iso"})

        assert 0 < ranks["iso"] < ranks["b"]
        assert sum(ranks.values()) == pytest.approx(1.0)


class TestVaultGraphReport:
    @pytest.mark.asyncio
    async def test_reports_totals_and_each_section(self, ctx, graph_vault):
        result = await tool_vault_graph_report(ctx)

        data = result.data
        assert data["totals"] == {
            "pages": 29,
            "edges": 28,
            "isolates": 1,
            "orphans": 13,
            "components": 4,
            "largest_component": 11,
        }
        assert data["isolates"] == ["agent/pages/iso.md"]
        assert [c["size"] for c in data["components"]] == [11, 11, 6, 1]
        assert data["sinks"] == [{"path": "agent/pages/sink.md", "inbound": 0, "outbound": 10}]
        assert data["toeholds"] == [{"path": "agent/pages/toehold.md", "inbound": 10, "outbound": 0}]
        assert len(data["pages"]) == 29
        assert all(row["pagerank"] > 0 for row in data["pages"])
        by_path = {row["path"]: row for row in data["pages"]}
        assert (
            by_path["agent/pages/toehold.md"]["pagerank"]
            > by_path["agent/pages/hub.md"]["pagerank"]
            > by_path["agent/pages/iso.md"]["pagerank"]
        )
        assert by_path["agent/pages/sink.md"]["pagerank"] < by_path["agent/pages/toehold.md"]["pagerank"]

    @pytest.mark.asyncio
    async def test_counts_inbound_from_journals_and_user_notes(self, ctx, graph_vault):
        result = await tool_vault_graph_report(ctx, sections=["pages"], top_n=100)

        by_path = {row["path"]: row for row in result.data["pages"]}
        # c is linked from a journal entry, d from a user note.
        assert by_path["agent/pages/c.md"]["inbound"] >= 1
        assert by_path["agent/pages/d.md"]["inbound"] >= 1
        assert by_path["agent/pages/iso.md"]["inbound"] == 0

    @pytest.mark.asyncio
    async def test_sections_bound_the_output(self, ctx, graph_vault):
        result = await tool_vault_graph_report(ctx, sections=["totals"])

        assert set(result.data) == {"totals"}
        assert "Graph report" in result.text

    @pytest.mark.asyncio
    async def test_top_n_bounds_per_page_table(self, ctx, graph_vault):
        result = await tool_vault_graph_report(ctx, sections=["pages"], top_n=2)

        assert len(result.data["pages"]) == 2

    @pytest.mark.asyncio
    async def test_unknown_section_is_an_error(self, ctx, graph_vault):
        result = await tool_vault_graph_report(ctx, sections=["nope"])

        assert result.text.startswith("[error:")
        assert "nope" in result.text

    def test_registered_as_read_only(self):
        assert TOOLS["vault_graph_report"] is tool_vault_graph_report
        entry = next(d for d in TOOL_DEFINITIONS if d["function"]["name"] == "vault_graph_report")
        assert entry["writes"] is False


class TestExcludedFolders:
    """Machine-generated folders must not participate in graph metrics.

    ``notifications/`` is written by the notification channel subscriber on
    every scheduled run — 216 pages at 69% isolate on the live vault. Those
    pages were never meant to be linked, so counting them as isolates
    overstated the editorial backlog by roughly 46%.
    """

    @pytest.fixture
    def exclude_vault(self, config, monkeypatch):
        from decafclaw.skills.garden import tools as garden_tools

        monkeypatch.setattr(garden_tools, "_skill_config", garden_tools.SkillConfig())

        vault = config.vault_root
        pages = config.vault_agent_pages_dir
        pages.mkdir(parents=True, exist_ok=True)

        def write(rel: str, text: str) -> None:
            path = vault / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")

        write("agent/pages/real.md", "Links to [[other]].\n")
        write("agent/pages/other.md", "target\n")
        # A pile of machine-generated isolates, as the live vault has.
        for i in range(5):
            write(f"agent/pages/notifications/2026-11-{i:02d}.md", "notification rollup\n")
        write("agent/pages/kindle/book-one.md", "highlights\n")
        # A subfolder is NOT excluded just because a sibling is.
        write("agent/pages/notifications-archive/kept.md", "real page\n")

        backlinks.rebuild_index(config)
        return config

    @pytest.mark.asyncio
    async def test_excluded_pages_not_counted_in_totals(self, exclude_vault):
        from types import SimpleNamespace

        result = await tool_vault_graph_report(SimpleNamespace(config=exclude_vault))
        totals = result.data["totals"]
        # real + other + notifications-archive/kept = 3
        assert totals["pages"] == 3

    @pytest.mark.asyncio
    async def test_excluded_pages_absent_from_isolates(self, exclude_vault):
        from types import SimpleNamespace

        result = await tool_vault_graph_report(SimpleNamespace(config=exclude_vault))
        isolates = result.data["isolates"]
        assert not [p for p in isolates if "/notifications/" in p]
        assert not [p for p in isolates if "/kindle/" in p]

    @pytest.mark.asyncio
    async def test_similarly_named_folder_is_not_excluded(self, exclude_vault):
        from types import SimpleNamespace

        result = await tool_vault_graph_report(SimpleNamespace(config=exclude_vault))
        assert "agent/pages/notifications-archive/kept.md" in result.data["isolates"]

    @pytest.mark.asyncio
    async def test_broken_links_ignores_excluded_pages(self, exclude_vault):
        from types import SimpleNamespace

        from decafclaw.skills.garden.tools import tool_vault_broken_links

        (exclude_vault.vault_root / "agent/pages/notifications/broken.md").write_text(
            "See [[nowhere at all]].\n", encoding="utf-8"
        )
        result = await tool_vault_broken_links(SimpleNamespace(config=exclude_vault))
        pages = {entry["page"] for entry in result.data["broken"]}
        assert not [p for p in pages if "/notifications/" in p]

    def test_is_excluded_matches_only_whole_folder_segments(self, config, monkeypatch):
        from decafclaw.skills.garden import tools as garden_tools

        monkeypatch.setattr(garden_tools, "_skill_config", garden_tools.SkillConfig())
        assert garden_tools._is_excluded("agent/pages/notifications/2026-11-01.md")
        assert garden_tools._is_excluded("agent/pages/kindle/book.md")
        assert not garden_tools._is_excluded("agent/pages/notifications-archive/kept.md")
        assert not garden_tools._is_excluded("agent/pages/notifications.md")
        assert not garden_tools._is_excluded("agent/pages/real.md")

    def test_excluded_folders_configurable(self, monkeypatch):
        from decafclaw.skills.garden import tools as garden_tools

        monkeypatch.setattr(
            garden_tools,
            "_skill_config",
            garden_tools.SkillConfig(excluded_folders=("bookmarks",)),
        )
        assert garden_tools._is_excluded("agent/pages/bookmarks/x.md")
        assert not garden_tools._is_excluded("agent/pages/notifications/2026-11-01.md")
