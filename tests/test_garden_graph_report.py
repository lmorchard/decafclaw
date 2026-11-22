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
