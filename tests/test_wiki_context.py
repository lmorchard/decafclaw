"""Tests for wiki context injection — @[[PageName]] and open wiki page."""

import pytest

from decafclaw.agent import (
    _get_already_injected_pages,
    _parse_wiki_references,
    _read_wiki_page,
)

# -- _parse_wiki_references ---------------------------------------------------


def test_parse_no_mentions_no_page():
    """No @[[...]] and no wiki_page → empty list."""
    result = _parse_wiki_references("hello world")
    assert result == []


def test_parse_mention():
    """@[[TestPage]] is parsed."""
    result = _parse_wiki_references("check @[[TestPage]] please")
    assert len(result) == 1
    assert result[0]["page"] == "TestPage"
    assert result[0]["source"] == "mention"


def test_parse_open_page():
    """wiki_page param appears as open_page source."""
    result = _parse_wiki_references("hello", wiki_page="OpenPage")
    assert len(result) == 1
    assert result[0]["page"] == "OpenPage"
    assert result[0]["source"] == "open_page"


def test_parse_dedup_mention_and_open_page():
    """If @[[X]] and wiki_page=X, only one entry (mention wins)."""
    result = _parse_wiki_references("@[[Page]]", wiki_page="Page")
    assert len(result) == 1
    assert result[0]["source"] == "mention"


def test_parse_multiple_mentions():
    """Multiple @[[...]] in one message all parse."""
    result = _parse_wiki_references("see @[[A]] and @[[B]]")
    assert len(result) == 2
    pages = {r["page"] for r in result}
    assert pages == {"A", "B"}


def test_parse_duplicate_mention():
    """Same page mentioned twice → only one entry."""
    result = _parse_wiki_references("@[[X]] and @[[X]]")
    assert len(result) == 1


# -- _read_wiki_page ----------------------------------------------------------


def test_read_wiki_page_found(config):
    """Existing page returns content."""
    wiki_dir = config.workspace_path / "wiki"
    wiki_dir.mkdir(parents=True)
    (wiki_dir / "TestPage.md").write_text("# Test\nHello")
    assert _read_wiki_page(config, "TestPage") == "# Test\nHello"


def test_read_wiki_page_not_found(config):
    """Missing page returns None."""
    wiki_dir = config.workspace_path / "wiki"
    wiki_dir.mkdir(parents=True)
    assert _read_wiki_page(config, "Missing") is None


def test_read_wiki_page_no_wiki_dir(config):
    """No wiki directory → None (no crash)."""
    assert _read_wiki_page(config, "Anything") is None


# -- _get_already_injected_pages -----------------------------------------------


def test_already_injected_empty():
    assert _get_already_injected_pages([]) == set()


def test_already_injected_finds_wiki_context():
    history = [
        {"role": "user", "content": "hi"},
        {"role": "wiki_context", "content": "...", "wiki_page": "Page1"},
        {"role": "assistant", "content": "ok"},
        {"role": "wiki_context", "content": "...", "wiki_page": "Page2"},
    ]
    assert _get_already_injected_pages(history) == {"Page1", "Page2"}


def test_already_injected_ignores_other_roles():
    history = [
        {"role": "user", "content": "hi"},
        {"role": "memory_context", "content": "..."},
    ]
    assert _get_already_injected_pages(history) == set()


# -- Integration: injection in _prepare_messages --------------------------------


@pytest.mark.asyncio
async def test_prepare_messages_injects_wiki_context(ctx):
    """Wiki context messages are injected into history."""
    from decafclaw.agent import _prepare_messages

    wiki_dir = ctx.config.workspace_path / "wiki"
    wiki_dir.mkdir(parents=True)
    (wiki_dir / "TestPage.md").write_text("wiki content here")
    ctx.config.system_prompt = "system"
    ctx.skip_memory_context = True

    history = []
    messages, _ = await _prepare_messages(
        ctx, ctx.config, "tell me about @[[TestPage]]", history,
    )

    # Check that wiki_context was injected into history
    wiki_msgs = [m for m in history if m.get("role") == "wiki_context"]
    assert len(wiki_msgs) == 1
    assert "wiki content here" in wiki_msgs[0]["content"]
    assert wiki_msgs[0]["wiki_page"] == "TestPage"

    # Check that wiki_context is remapped to "user" in LLM messages
    user_msgs = [m for m in messages if m["role"] == "user"]
    assert any("wiki content here" in m["content"] for m in user_msgs)


@pytest.mark.asyncio
async def test_prepare_messages_skips_already_injected(ctx):
    """Pages already in history are not re-injected."""
    from decafclaw.agent import _prepare_messages

    wiki_dir = ctx.config.workspace_path / "wiki"
    wiki_dir.mkdir(parents=True)
    (wiki_dir / "TestPage.md").write_text("wiki content")
    ctx.config.system_prompt = "system"
    ctx.skip_memory_context = True

    history = [
        {"role": "wiki_context", "content": "[Referenced wiki page: TestPage]\n\nwiki content",
         "wiki_page": "TestPage"},
    ]
    messages, _ = await _prepare_messages(
        ctx, ctx.config, "tell me more about @[[TestPage]]", history,
    )

    wiki_msgs = [m for m in history if m.get("role") == "wiki_context"]
    assert len(wiki_msgs) == 1  # still just the original, no duplicate


@pytest.mark.asyncio
async def test_prepare_messages_injects_open_page(ctx):
    """Open wiki page from ctx.wiki_page is injected."""
    from decafclaw.agent import _prepare_messages

    wiki_dir = ctx.config.workspace_path / "wiki"
    wiki_dir.mkdir(parents=True)
    (wiki_dir / "OpenPage.md").write_text("open page content")
    ctx.config.system_prompt = "system"
    ctx.skip_memory_context = True
    ctx.wiki_page = "OpenPage"

    history = []
    messages, _ = await _prepare_messages(
        ctx, ctx.config, "hello", history,
    )

    wiki_msgs = [m for m in history if m.get("role") == "wiki_context"]
    assert len(wiki_msgs) == 1
    assert "[Currently viewing wiki page: OpenPage]" in wiki_msgs[0]["content"]


@pytest.mark.asyncio
async def test_prepare_messages_missing_page_error(ctx):
    """Missing @[[PageName]] injects error note."""
    from decafclaw.agent import _prepare_messages

    wiki_dir = ctx.config.workspace_path / "wiki"
    wiki_dir.mkdir(parents=True)
    ctx.config.system_prompt = "system"
    ctx.skip_memory_context = True

    history = []
    messages, _ = await _prepare_messages(
        ctx, ctx.config, "see @[[NonExistent]]", history,
    )

    wiki_msgs = [m for m in history if m.get("role") == "wiki_context"]
    assert len(wiki_msgs) == 1
    assert "[Wiki page 'NonExistent' not found]" in wiki_msgs[0]["content"]
