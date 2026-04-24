"""Tests for the vault_search → data_table widget retrofit."""

import pytest

from decafclaw.skills.vault.tools import (
    _semantic_results_widget,
    _substring_results_widget,
    _substring_search,
    tool_vault_search,
)

# ------------- widget builder units -------------


def test_semantic_widget_shape():
    results = [
        {"file_path": "a/b.md", "similarity": 0.987654321,
         "source_type": "page", "entry_text": "hello world"},
        {"file_path": "c.md", "similarity": 0.5, "source_type": "journal",
         "entry_text": "x" * 300},
    ]
    widget = _semantic_results_widget("hello", results)
    assert widget.widget_type == "data_table"
    data = widget.data
    assert "Page" in [c["label"] for c in data["columns"]]
    assert data["rows"][0]["page"] == "a/b"
    assert data["rows"][0]["similarity"] == 0.988
    assert data["rows"][0]["source"] == "page"
    # Snippet is truncated.
    assert len(data["rows"][1]["snippet"]) <= 161
    assert data["rows"][1]["snippet"].endswith("…")
    assert "hello" in data["caption"]


def test_substring_widget_with_query():
    rows = [{"page": "Topic", "excerpt": "a line with the query"}]
    widget = _substring_results_widget("query", rows)
    cols = [c["key"] for c in widget.data["columns"]]
    assert cols == ["page", "excerpt"]
    assert widget.data["rows"][0]["page"] == "Topic"


def test_substring_widget_no_query():
    rows = [{"page": "Topic", "modified": "2026-04-24 12:00"}]
    widget = _substring_results_widget("", rows)
    cols = [c["key"] for c in widget.data["columns"]]
    assert cols == ["page", "modified"]


# ------------- semantic path integration -------------


class _StubResult(dict):
    pass


async def _fake_search_similar_hits(*args, **kwargs):
    return [
        {"file_path": "projects/widgets.md", "similarity": 0.92,
         "source_type": "page", "entry_text": "Widgets are rendered…"},
        {"file_path": "journal/2026-04-20.md", "similarity": 0.71,
         "source_type": "journal", "entry_text": "Talked about widgets today."},
    ]


async def _fake_search_similar_empty(*args, **kwargs):
    return []


@pytest.mark.asyncio
async def test_vault_search_semantic_emits_widget(config, monkeypatch):
    # Ensure a real vault dir exists so the tool doesn't early-return.
    config.vault_root.mkdir(parents=True, exist_ok=True)
    config.embedding.search_strategy = "semantic"

    monkeypatch.setattr(
        "decafclaw.embeddings.search_similar", _fake_search_similar_hits)

    # Minimal ctx: the tool only uses ctx.config.
    class _Ctx:
        pass
    ctx = _Ctx()
    ctx.config = config

    result = await tool_vault_search(ctx, query="widgets")
    assert result.widget is not None
    assert result.widget.widget_type == "data_table"
    assert len(result.widget.data["rows"]) == 2
    # LLM-facing text preserved.
    assert "Widgets are rendered" in result.text


@pytest.mark.asyncio
async def test_vault_search_semantic_empty_no_widget_falls_through(
        config, tmp_path, monkeypatch):
    """Empty semantic results fall through to the substring path, which
    will return 'No results matching' when the vault is empty, and that
    path does not emit a widget."""
    config.vault_root.mkdir(parents=True, exist_ok=True)
    config.embedding.search_strategy = "semantic"
    monkeypatch.setattr(
        "decafclaw.embeddings.search_similar", _fake_search_similar_empty)

    class _Ctx:
        pass
    ctx = _Ctx()
    ctx.config = config

    result = await tool_vault_search(ctx, query="nothing_there")
    assert result.widget is None
    assert "No results" in result.text


# ------------- substring path integration -------------


def test_substring_search_with_hits_emits_widget(config):
    config.vault_root.mkdir(parents=True, exist_ok=True)
    (config.vault_root / "doc.md").write_text(
        "# Hello\nwidgets are cool\nmore text here\n")
    result = _substring_search(config, query="widgets")
    assert result.widget is not None
    data = result.widget.data
    assert any(r["page"] == "doc" for r in data["rows"])
    # Widget text still includes the markdown bullet version.
    assert "**doc**" in result.text


def test_substring_search_no_results_no_widget(config):
    config.vault_root.mkdir(parents=True, exist_ok=True)
    (config.vault_root / "doc.md").write_text("# Hello\nnothing here\n")
    result = _substring_search(config, query="widgets")
    assert result.widget is None


def test_substring_search_no_query_uses_modified_column(config):
    config.vault_root.mkdir(parents=True, exist_ok=True)
    (config.vault_root / "a.md").write_text("x")
    (config.vault_root / "b.md").write_text("y")
    result = _substring_search(config, query="")
    assert result.widget is not None
    cols = [c["key"] for c in result.widget.data["columns"]]
    assert "modified" in cols
