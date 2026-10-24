"""Tests for wiki context injection — @[[PageName]] and open wiki page."""

import pytest

from decafclaw.memory_context import (
    get_already_injected_pages,
    get_already_retrieved_paths,
    parse_wiki_references,
    read_wiki_page,
)

# -- parse_wiki_references ----------------------------------------------------


def test_parse_no_mentions_no_page():
    """No @[[...]] and no wiki_page → empty list."""
    result = parse_wiki_references("hello world")
    assert result == []


def test_parse_mention():
    """@[[TestPage]] is parsed."""
    result = parse_wiki_references("check @[[TestPage]] please")
    assert len(result) == 1
    assert result[0]["page"] == "TestPage"
    assert result[0]["source"] == "mention"


def test_parse_open_page():
    """wiki_page param appears as open_page source."""
    result = parse_wiki_references("hello", wiki_page="OpenPage")
    assert len(result) == 1
    assert result[0]["page"] == "OpenPage"
    assert result[0]["source"] == "open_page"


def test_parse_dedup_mention_and_open_page():
    """If @[[X]] and wiki_page=X, only one entry (mention wins)."""
    result = parse_wiki_references("@[[Page]]", wiki_page="Page")
    assert len(result) == 1
    assert result[0]["source"] == "mention"


def test_parse_multiple_mentions():
    """Multiple @[[...]] in one message all parse."""
    result = parse_wiki_references("see @[[A]] and @[[B]]")
    assert len(result) == 2
    pages = {r["page"] for r in result}
    assert pages == {"A", "B"}


def test_parse_duplicate_mention():
    """Same page mentioned twice → only one entry."""
    result = parse_wiki_references("@[[X]] and @[[X]]")
    assert len(result) == 1


def test_parse_pipe_display_syntax():
    """@[[target|display]] extracts the target before the pipe."""
    result = parse_wiki_references("see @[[Tempest (arcade game)|Tempest arcade game]]")
    assert len(result) == 1
    assert result[0]["page"] == "Tempest (arcade game)"
    assert result[0]["source"] == "mention"


# -- read_wiki_page -----------------------------------------------------------


def test_read_wiki_page_found(config):
    """Existing page returns content."""
    vault_dir = config.vault_root
    vault_dir.mkdir(parents=True, exist_ok=True)
    (vault_dir / "TestPage.md").write_text("# Test\nHello")
    assert read_wiki_page(config, "TestPage") == "# Test\nHello"


def test_read_wiki_page_not_found(config):
    """Missing page returns None."""
    vault_dir = config.vault_root
    vault_dir.mkdir(parents=True, exist_ok=True)
    assert read_wiki_page(config, "Missing") is None


def test_read_wiki_page_no_vault_dir(config):
    """No vault directory → None (no crash)."""
    assert read_wiki_page(config, "Anything") is None


# -- get_already_injected_pages / get_already_retrieved_paths -----------------


def test_already_injected_empty():
    assert get_already_injected_pages([]) == set()


def test_already_injected_finds_vault_references():
    history = [
        {"role": "user", "content": "hi"},
        {"role": "vault_references", "content": "...", "wiki_page": "Page1"},
        {"role": "assistant", "content": "ok"},
        {"role": "vault_references", "content": "...", "wiki_page": "Page2"},
    ]
    assert get_already_injected_pages(history) == {"Page1", "Page2"}


def test_already_injected_ignores_vault_retrieval_and_other_roles():
    """Explicit-reference dedupe must ignore proactive vault_retrieval (#1157)."""
    history = [
        {"role": "user", "content": "hi"},
        {"role": "vault_retrieval", "content": "...", "file_paths": ["pages/Page1.md"]},
        {"role": "assistant", "content": "ok"},
    ]
    assert get_already_injected_pages(history) == set()


def test_already_retrieved_paths_finds_vault_retrieval():
    """Archived ``vault_retrieval`` messages contribute their injected paths (#1157)."""
    history = [
        {"role": "user", "content": "hi"},
        {"role": "vault_retrieval", "content": "...", "file_paths": ["pages/Page1.md"]},
        {"role": "assistant", "content": "ok"},
        {"role": "vault_retrieval", "content": "...", "file_paths": ["journal/Page2.md"]},
    ]
    assert get_already_retrieved_paths(history) == {"pages/Page1.md", "journal/Page2.md"}


def test_already_retrieved_paths_ignores_other_roles():
    history = [
        {"role": "user", "content": "hi"},
        {"role": "vault_references", "content": "...", "wiki_page": "Page1"},
        {"role": "vault_retrieval", "content": "..."},
    ]
    assert get_already_retrieved_paths(history) == set()


# -- Integration: wiki context via ContextComposer --------------------------------


def test_compose_wiki_context_injects(ctx):
    """Wiki context messages are produced for @[[PageName]] references."""
    from decafclaw.context_composer import ComposerMode, ContextComposer

    vault_dir = ctx.config.vault_root
    vault_dir.mkdir(parents=True, exist_ok=True)
    (vault_dir / "TestPage.md").write_text("wiki content here")

    composer = ContextComposer()
    msgs, entry = composer._compose_vault_references(
        ctx,
        ctx.config,
        "tell me about @[[TestPage]]",
        [],
        ComposerMode.INTERACTIVE,
    )
    assert len(msgs) == 1
    assert "wiki content here" in msgs[0]["content"]
    assert msgs[0]["wiki_page"] == "TestPage"
    assert entry is not None
    assert entry.items_included == 1


def test_compose_wiki_context_skips_already_injected(ctx):
    """Pages already in history are not re-injected."""
    from decafclaw.context_composer import ComposerMode, ContextComposer

    vault_dir = ctx.config.vault_root
    vault_dir.mkdir(parents=True, exist_ok=True)
    (vault_dir / "TestPage.md").write_text("wiki content")

    history = [
        {
            "role": "vault_references",
            "content": "[Referenced wiki page: TestPage]\n\nwiki content",
            "wiki_page": "TestPage",
        },
    ]
    composer = ContextComposer()
    msgs, entry = composer._compose_vault_references(
        ctx,
        ctx.config,
        "tell me more about @[[TestPage]]",
        history,
        ComposerMode.INTERACTIVE,
    )
    assert len(msgs) == 0  # no duplicate injection


def test_compose_wiki_context_injects_open_page(ctx):
    """Open wiki page from ctx.wiki_page is injected."""
    from decafclaw.context_composer import ComposerMode, ContextComposer

    vault_dir = ctx.config.vault_root
    vault_dir.mkdir(parents=True, exist_ok=True)
    (vault_dir / "OpenPage.md").write_text("open page content")
    ctx.wiki_page = "OpenPage"

    composer = ContextComposer()
    msgs, _ = composer._compose_vault_references(
        ctx,
        ctx.config,
        "hello",
        [],
        ComposerMode.INTERACTIVE,
    )
    assert len(msgs) == 1
    assert "[Currently viewing wiki page: OpenPage]" in msgs[0]["content"]


def test_compose_wiki_context_missing_page(ctx):
    """Missing @[[PageName]] produces error note."""
    from decafclaw.context_composer import ComposerMode, ContextComposer

    vault_dir = ctx.config.vault_root
    vault_dir.mkdir(parents=True, exist_ok=True)

    composer = ContextComposer()
    msgs, _ = composer._compose_vault_references(
        ctx,
        ctx.config,
        "see @[[NonExistent]]",
        [],
        ComposerMode.INTERACTIVE,
    )
    assert len(msgs) == 1
    assert "[Wiki page 'NonExistent' not found]" in msgs[0]["content"]


def test_compose_wiki_context_allows_explicit_reference_after_headline_retrieval(ctx):
    """#1157: an explicit @[[Page]] reference must not be suppressed by an earlier
    proactive vault_retrieval (e.g. headlines mode), even if the path matches."""
    from decafclaw.context_composer import ComposerMode, ContextComposer

    vault_dir = ctx.config.vault_root
    pages_dir = vault_dir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    (pages_dir / "Foo.md").write_text("full body of Foo")

    history = [
        {
            "role": "vault_retrieval",
            "content": "### Foo\nHeadline only summary.",
            "file_paths": ["pages/Foo.md"],
        },
    ]
    composer = ContextComposer()
    msgs, entry = composer._compose_vault_references(
        ctx,
        ctx.config,
        "tell me about @[[pages/Foo.md]]",
        history,
        ComposerMode.INTERACTIVE,
    )
    assert len(msgs) == 1
    assert "full body of Foo" in msgs[0]["content"]
    assert msgs[0]["wiki_page"] == "pages/Foo.md"
    assert entry is not None
    assert entry.items_included == 1
    assert entry.items_truncated == 0
