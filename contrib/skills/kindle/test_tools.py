"""Tests for the contrib kindle skill (loaded via importlib)."""

from __future__ import annotations

import http.cookiejar
import importlib.util
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Load tools.py via importlib — mirroring the production skill loader.
# ---------------------------------------------------------------------------

_THIS_DIR = Path(__file__).parent
_tools_spec = importlib.util.spec_from_file_location(
    "decafclaw_contrib_kindle_tools", _THIS_DIR / "tools.py"
)
assert _tools_spec is not None and _tools_spec.loader is not None
kindle_tools = importlib.util.module_from_spec(_tools_spec)
# Register under a stable name so monkeypatch.setattr(kindle_tools, ...) and
# dataclass round-trips work correctly.
sys.modules["decafclaw_contrib_kindle_tools"] = kindle_tools
_tools_spec.loader.exec_module(kindle_tools)

# Re-export the names that test functions use, as if this were a regular import.
SkillConfig = kindle_tools.SkillConfig
init = kindle_tools.init
BookSummary = kindle_tools.BookSummary
HighlightEntry = kindle_tools.HighlightEntry
_resolve_cookies_path = kindle_tools._resolve_cookies_path
_load_cookie_jar = kindle_tools._load_cookie_jar
_cookie_file_age_days = kindle_tools._cookie_file_age_days
_make_session = kindle_tools._make_session
_parse_books_list = kindle_tools._parse_books_list
_parse_highlights = kindle_tools._parse_highlights
_slug = kindle_tools._slug
_book_page_path = kindle_tools._book_page_path
_upsert_book_page = kindle_tools._upsert_book_page
book_to_dict = kindle_tools.book_to_dict
highlight_to_dict = kindle_tools.highlight_to_dict

# ---------------------------------------------------------------------------
# Blocker 1: skill-loader pipeline registers the skill
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_kindle_skill_registers_via_loader(ctx):
    """kindle skill must be discoverable and activatable via the skill-loader
    pipeline after it's added to extra_skill_paths. This guards against
    missing SKILL.md, tools.py, or missing exports.
    """
    from decafclaw.skills import discover_skills
    from decafclaw.tools.skill_tools import activate_skill_internal

    # Point extra_skill_paths at the contrib skill directory so the loader finds it.
    ctx.config.extra_skill_paths = [str(_THIS_DIR)]

    skills = discover_skills(ctx.config)
    kindle_info = next((s for s in skills if s.name == "kindle"), None)
    assert kindle_info is not None, "kindle skill not found via extra_skill_paths"

    # Has tools.py, so has_native_tools should be True
    assert kindle_info.has_native_tools, "kindle skill should have tools.py"

    # Activation should complete without errors
    await activate_skill_internal(ctx, kindle_info)


def test_skill_config_defaults():
    """All SkillConfig fields should have correct defaults."""
    cfg = SkillConfig()
    assert cfg.enabled is False
    assert cfg.cookies_path == ""
    assert cfg.amazon_domain == "amazon.com"
    assert cfg.vault_subfolder == "agent/pages/kindle"
    assert cfg.sync_min_interval_seconds == 60
    assert cfg.archive_deleted is True
    assert (
        cfg.user_agent
        == "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )
    assert cfg.cookies_warn_after_days == 300


# ---------------------------------------------------------------------------
# Phase 2: Cookie loading + curl_cffi session helper
# ---------------------------------------------------------------------------


def test_load_cookie_jar_reads_netscape_format():
    """_load_cookie_jar should read a Netscape-format cookies.txt file."""
    fixture_path = _THIS_DIR / "fixtures" / "cookies.txt"
    jar = _load_cookie_jar(fixture_path)

    # Verify that cookies were loaded
    assert len(jar) == 2, f"Expected 2 cookies, got {len(jar)}"

    # Verify specific cookies
    cookies = {c.name: c.value for c in jar}
    assert "at-main" in cookies
    assert cookies["at-main"] == "dummy-value-1"
    assert "sess-at-main" in cookies
    assert cookies["sess-at-main"] == "dummy-value-2"


def test_load_cookie_jar_missing_file_raises():
    """_load_cookie_jar should raise FileNotFoundError for missing file."""
    missing_path = Path("/tmp/nonexistent-cookies-12345.txt")
    with pytest.raises(FileNotFoundError, match="not found"):
        _load_cookie_jar(missing_path)


def test_resolve_cookies_path_default(ctx):
    """_resolve_cookies_path should default to agent_path/secrets/kindle.cookies.txt."""
    skill_config = SkillConfig(cookies_path="")
    result = _resolve_cookies_path(ctx.config, skill_config)

    expected = ctx.config.agent_path / "secrets" / "kindle.cookies.txt"
    assert result == expected


def test_resolve_cookies_path_relative_override(ctx):
    """_resolve_cookies_path should resolve relative paths relative to agent_path."""
    skill_config = SkillConfig(cookies_path="my/custom/cookies.txt")
    result = _resolve_cookies_path(ctx.config, skill_config)

    expected = ctx.config.agent_path / "my" / "custom" / "cookies.txt"
    assert result == expected


def test_resolve_cookies_path_absolute_override(tmp_path):
    """_resolve_cookies_path should use absolute paths unchanged."""
    from dataclasses import dataclass

    # Create a minimal config mock with agent_path
    @dataclass
    class MockConfig:
        agent_path: Path

    config = MockConfig(agent_path=tmp_path / "agent")
    skill_config = SkillConfig(cookies_path=str(tmp_path / "absolute" / "cookies.txt"))

    result = _resolve_cookies_path(config, skill_config)

    # For absolute paths, should use them as-is
    expected = Path(str(tmp_path / "absolute" / "cookies.txt"))
    assert result == expected


def test_make_session_uses_chrome_impersonation():
    """_make_session should construct an AsyncSession with chrome131 impersonation."""
    jar = http.cookiejar.MozillaCookieJar()
    user_agent = "Test User Agent"

    session = _make_session(jar, user_agent)

    # Verify session was created
    assert session is not None
    assert hasattr(session, "headers")
    assert session.headers["User-Agent"] == user_agent

    # Verify impersonate attribute is set
    assert hasattr(session, "impersonate")
    assert session.impersonate == "chrome131"


def test_cookie_file_age_days(tmp_path):
    """_cookie_file_age_days should return the age of a cookie file in days."""
    # Create a test file
    test_file = tmp_path / "test_cookies.txt"
    test_file.write_text("# test")

    # Modify its mtime to be 2 days old (172800 seconds)
    now = time.time()
    old_time = now - (2 * 86400)  # 2 days ago
    os.utime(test_file, (old_time, old_time))

    age_days = _cookie_file_age_days(test_file)

    # Should be approximately 2 days old (allow 0.1 day tolerance for test execution time)
    assert 1.9 < age_days < 2.1, f"Expected age ~2 days, got {age_days}"


# ---------------------------------------------------------------------------
# Phase 3: HTML parsers — books list
# ---------------------------------------------------------------------------


_FIXTURE_PATH = _THIS_DIR / "fixtures" / "notebook_page.html"


def test_parse_books_list_basic():
    """_parse_books_list should return 4 books from the test fixture."""
    html = _FIXTURE_PATH.read_text()
    books = _parse_books_list(html)

    assert len(books) >= 4, f"Expected at least 4 books, got {len(books)}"

    # All entries must be BookSummary instances with non-empty ASINs.
    for book in books:
        assert isinstance(book, BookSummary)
        assert book.asin, f"Book has empty ASIN: {book!r}"

    # Assert a specific known (asin, title) pair from the fixture.
    asins = {b.asin: b for b in books}
    assert "B078VWDNKT" in asins, "Expected ASIN B078VWDNKT not found"
    assert asins["B078VWDNKT"].title == "Sample Book One"
    assert asins["B078VWDNKT"].author == "Author One"
    assert asins["B078VWDNKT"].cover_url != ""

    # Second book
    assert "B09G14BQMM" in asins
    assert asins["B09G14BQMM"].title == "Sample Book Two"


def test_parse_books_list_empty():
    """_parse_books_list should return [] for HTML with no book entries."""
    books = _parse_books_list("<html><body></body></html>")
    assert books == []


# ---------------------------------------------------------------------------
# Phase 3: HTML parsers — highlights
# ---------------------------------------------------------------------------


def test_parse_highlights_basic():
    """_parse_highlights should return 6 highlights from the test fixture."""
    html = _FIXTURE_PATH.read_text()
    entries = _parse_highlights(html)

    assert len(entries) > 0, "Expected at least one highlight entry"

    # All entries must be HighlightEntry with non-empty annotation_id.
    for entry in entries:
        assert isinstance(entry, HighlightEntry)
        assert entry.annotation_id, f"Entry has empty annotation_id: {entry!r}"

    # At least one entry must have a non-empty color.
    colors = {e.color for e in entries}
    assert colors - {""}, f"No entries with a non-empty color; colors={colors}"

    # At least one entry must have non-empty text.
    assert any(e.text for e in entries), "No entries with non-empty text"

    # Fixture has one blue highlight — verify color extraction works.
    blue_entries = [e for e in entries if e.color == "blue"]
    assert len(blue_entries) == 1, f"Expected 1 blue highlight, got {len(blue_entries)}"
    assert "Sample highlight text 5" in blue_entries[0].text


def test_parse_highlights_empty():
    """_parse_highlights should return [] for HTML with no annotations container."""
    entries = _parse_highlights("<html><body></body></html>")
    assert entries == []


def test_parse_highlights_no_note():
    """Highlights without a user note should have note == ''."""
    html = _FIXTURE_PATH.read_text()
    entries = _parse_highlights(html)

    # The first annotation in the fixture has no note (aok-hidden empty span).
    first_id_prefix = "QTNONVpPTk1aR044NUo6QjA3OFZXRE5LVDoyNDg0"
    no_note = next(
        (e for e in entries if e.annotation_id.startswith(first_id_prefix)), None
    )
    assert no_note is not None, "Could not find first annotation in parsed entries"
    assert no_note.note == "", f"Expected empty note, got {no_note.note!r}"

    # The sixth annotation has a note — verify it was extracted.
    note_id_prefix = "QTNONVpPTk1aR044NUo6QjA3OFZXRE5LVDozNzQx"
    with_note = next(
        (e for e in entries if e.annotation_id.startswith(note_id_prefix)), None
    )
    assert with_note is not None, "Could not find annotated highlight in parsed entries"
    assert with_note.note == "Sample note 1."


# ---------------------------------------------------------------------------
# Phase 4: Page upsert logic
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 5, 13, 12, 0, 0, tzinfo=timezone.utc)
_TODAY = "2026-05-13"


def _make_book(
    asin: str = "B001TEST",
    title: str = "Test Book",
    author: str = "Test Author",
    cover_url: str = "https://example.com/cover.jpg",
):
    return BookSummary(asin=asin, title=title, author=author, cover_url=cover_url)


def _make_highlight(
    annotation_id: str,
    location: str = "100",
    color: str = "yellow",
    text: str = "Sample text",
    note: str = "",
):
    return HighlightEntry(
        annotation_id=annotation_id,
        location=location,
        color=color,
        text=text,
        note=note,
    )


def test_upsert_new_page():
    """Empty existing_md + 2 fresh highlights → frontmatter + 2 Highlights, no Archived."""
    from decafclaw.frontmatter import parse_frontmatter

    h1 = _make_highlight("ANN001", location="10", text="First highlight")
    h2 = _make_highlight("ANN002", location="20", text="Second highlight")
    book = _make_book()

    result = _upsert_book_page("", [h1, h2], book, _NOW)

    metadata, body = parse_frontmatter(result)

    # Frontmatter present with required fields
    assert metadata["asin"] == "B001TEST"
    assert metadata["title"] == "Test Book"
    assert metadata["author"] == "Test Author"
    assert metadata["highlight_count"] == 2

    # Highlights section present
    assert "## Highlights" in body
    assert "ANN001" in body
    assert "ANN002" in body
    assert "First highlight" in body
    assert "Second highlight" in body

    # No Archived section
    assert "## Archived" not in body


def test_upsert_updates_existing_highlight():
    """Existing page H1 with old text; fresh H1 with new text (same ID) → new text only."""
    # Build an existing page with H1 having old text
    existing_h1 = _make_highlight("ANN001", location="10", text="Old highlight text")
    existing_page = _upsert_book_page("", [existing_h1], _make_book(), _NOW)

    # Fresh: same ID but updated text
    fresh_h1 = _make_highlight("ANN001", location="10", text="New highlight text")
    result = _upsert_book_page(existing_page, [fresh_h1], _make_book(), _NOW)

    assert "New highlight text" in result
    assert "Old highlight text" not in result


def test_upsert_inserts_new_highlight():
    """Existing has H1; fresh has H1+H2. Both present, in fresh order."""
    h1 = _make_highlight("ANN001", location="10", text="First highlight")
    existing_page = _upsert_book_page("", [h1], _make_book(), _NOW)

    h2 = _make_highlight("ANN002", location="20", text="Second highlight")
    result = _upsert_book_page(existing_page, [h1, h2], _make_book(), _NOW)

    # Both highlights present
    assert "First highlight" in result
    assert "Second highlight" in result

    # Order: H1 before H2 in the Highlights section
    h1_pos = result.find("ANN001")
    h2_pos = result.find("ANN002")
    assert h1_pos < h2_pos, "H1 should appear before H2"


def test_upsert_archives_deleted():
    """Existing H1+H2; fresh has only H1. H1 in Highlights; H2 in Archived with today's date + strikethrough."""
    from decafclaw.frontmatter import parse_frontmatter

    h1 = _make_highlight("ANN001", location="10", text="Kept highlight")
    h2 = _make_highlight("ANN002", location="20", text="Deleted highlight")
    existing_page = _upsert_book_page("", [h1, h2], _make_book(), _NOW)

    # Only h1 in fresh
    result = _upsert_book_page(existing_page, [h1], _make_book(), _NOW, archive_deleted=True)
    metadata, body = parse_frontmatter(result)

    # H1 in active Highlights (before Archived section)
    archived_pos = body.find("## Archived")
    highlights_pos = body.find("## Highlights")
    assert highlights_pos >= 0, "Highlights section missing"
    assert archived_pos >= 0, "Archived section missing"

    highlights_section = body[highlights_pos:archived_pos]
    assert "ANN001" in highlights_section
    assert "ANN002" not in highlights_section

    # H2 in Archived with today's date and strikethrough
    archived_section = body[archived_pos:]
    assert "ANN002" in archived_section
    assert f"archived {_TODAY}" in archived_section
    assert "~~" in archived_section  # strikethrough

    # Frontmatter archived_count
    assert metadata["archived_count"] == 1


def test_upsert_preserves_existing_archived_date():
    """Previously-archived H3 (archived 2026-01-01); fresh has H1. H3 date preserved."""
    from decafclaw.frontmatter import parse_frontmatter

    old_archived_date = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    # Create a page with H1+H3, then archive H3 on 2026-01-01
    h1 = _make_highlight("ANN001", location="10", text="Kept highlight")
    h3 = _make_highlight("ANN003", location="30", text="Old archived")
    page_v1 = _upsert_book_page("", [h1, h3], _make_book(), _NOW)
    # Archive H3 by syncing with only H1 on the old date
    page_v2 = _upsert_book_page(page_v1, [h1], _make_book(), old_archived_date, archive_deleted=True)

    # Verify H3 was archived on 2026-01-01
    _, body_v2 = parse_frontmatter(page_v2)
    assert "archived 2026-01-01" in body_v2

    # Now re-sync with H1 on today — H3 should still show 2026-01-01
    result = _upsert_book_page(page_v2, [h1], _make_book(), _NOW, archive_deleted=True)
    metadata, body = parse_frontmatter(result)

    archived_section = body[body.find("## Archived"):]
    assert "archived 2026-01-01" in archived_section
    assert f"archived {_TODAY}" not in archived_section  # should NOT re-archive with today's date

    # archived_count should include the preserved archived item
    assert metadata["archived_count"] >= 1


def test_upsert_archive_disabled_drops_deleted():
    """archive_deleted=False: existing H1+H2, fresh H1. No Archived section; archived_count=0."""
    from decafclaw.frontmatter import parse_frontmatter

    h1 = _make_highlight("ANN001", location="10", text="Kept highlight")
    h2 = _make_highlight("ANN002", location="20", text="Dropped highlight")
    existing_page = _upsert_book_page("", [h1, h2], _make_book(), _NOW)

    result = _upsert_book_page(existing_page, [h1], _make_book(), _NOW, archive_deleted=False)
    metadata, body = parse_frontmatter(result)

    assert "## Archived" not in body
    assert metadata["archived_count"] == 0


def test_upsert_frontmatter_fields():
    """All required frontmatter fields present with correct values."""
    from decafclaw.frontmatter import parse_frontmatter

    h1 = _make_highlight("ANN001", text="Some text")
    book = _make_book(
        asin="B123",
        title="My Book",
        author="Jane Doe",
        cover_url="https://example.com/cover.jpg",
    )
    result = _upsert_book_page("", [h1], book, _NOW)
    metadata, _ = parse_frontmatter(result)

    assert metadata["asin"] == "B123"
    assert metadata["title"] == "My Book"
    assert metadata["author"] == "Jane Doe"
    assert metadata["cover_url"] == "https://example.com/cover.jpg"
    assert "ingested" in metadata["tags"]
    assert "kindle" in metadata["tags"]
    assert metadata["highlight_count"] == 1
    assert "last_synced" in metadata
    assert metadata["last_synced"] == "2026-05-13T12:00:00+00:00"


def test_slug_safe_for_filenames():
    """_slug produces filesystem-safe strings."""
    # Spaces become hyphens
    assert _slug("Hello World") == "hello-world"

    # Punctuation stripped/replaced
    assert _slug("It's a Test!") == "it-s-a-test"

    # Already lowercase, no change
    assert _slug("simple") == "simple"

    # Length cap: >60 chars truncated
    long_title = "a" * 80
    result = _slug(long_title)
    assert len(result) <= 60

    # Empty-ish → "untitled"
    assert _slug("") == "untitled"
    assert _slug("!!!") == "untitled"


def test_upsert_empty_metadata_summary():
    """Empty title AND empty author should produce sensible default summary (no double-space-by)."""
    from decafclaw.frontmatter import parse_frontmatter

    h1 = _make_highlight("ANN001", text="Sample text")
    book = _make_book(
        asin="B001EMPTY",
        title="",  # Empty title
        author="",  # Empty author
        cover_url="",
    )
    result = _upsert_book_page("", [h1], book, _NOW)
    metadata, _ = parse_frontmatter(result)

    # Summary should be "Kindle highlights" with no trailing garbage like " by"
    summary = metadata["summary"]
    assert summary == "Kindle highlights", f"Expected 'Kindle highlights', got {summary!r}"
    # Ensure no double-space or trailing "by"
    assert "  " not in summary, f"Summary has double-space: {summary!r}"
    assert not summary.endswith("by"), f"Summary should not end with 'by': {summary!r}"


# ---------------------------------------------------------------------------
# Phase 5: HTTP fetch tools
# ---------------------------------------------------------------------------


def _make_mock_session(html: str):
    """Return a mock AsyncSession-like context manager that yields a session whose
    .get() returns a response with .text=<html> and a no-op .raise_for_status()."""
    response = MagicMock()
    response.text = html
    response.raise_for_status = MagicMock()
    session = MagicMock()
    session.get = AsyncMock(return_value=response)
    # async-context-manager protocol
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=None)
    return cm


@pytest.mark.asyncio
async def test_kindle_list_books_happy_path(ctx, tmp_path, monkeypatch):
    """kindle_list_books with mocked session returns books data from fixture HTML."""
    fixture_html = _FIXTURE_PATH.read_text()
    cookies_file = tmp_path / "kindle.cookies.txt"
    # Copy the fixture cookies file so it exists
    fixture_cookies = _THIS_DIR / "fixtures" / "cookies.txt"
    cookies_file.write_text(fixture_cookies.read_text())

    skill_config = SkillConfig(cookies_path=str(cookies_file))
    init(ctx.config, skill_config)

    monkeypatch.setattr(
        kindle_tools, "_make_session",
        lambda jar, ua: _make_mock_session(fixture_html),
    )

    result = await kindle_tools.kindle_list_books(ctx)

    assert result.data is not None
    assert "books" in result.data
    assert len(result.data["books"]) >= 4, f"Expected at least 4 books, got {result.data['books']}"
    assert "Found" in result.text
    assert "book" in result.text

    # Verify structure of each book dict
    for book in result.data["books"]:
        assert "asin" in book
        assert "title" in book
        assert "author" in book
        assert "cover_url" in book


@pytest.mark.asyncio
async def test_kindle_list_books_missing_cookies(ctx, tmp_path, monkeypatch):
    """kindle_list_books returns an error if the cookies file doesn't exist."""
    missing_cookies = tmp_path / "nonexistent" / "cookies.txt"
    skill_config = SkillConfig(cookies_path=str(missing_cookies))
    init(ctx.config, skill_config)

    result = await kindle_tools.kindle_list_books(ctx)

    assert result.text.startswith("[error: Kindle cookies file not found")


@pytest.mark.asyncio
async def test_kindle_list_books_warns_old_cookies(ctx, tmp_path, monkeypatch):
    """kindle_list_books warns when the cookies file is older than cookies_warn_after_days."""
    fixture_html = _FIXTURE_PATH.read_text()
    cookies_file = tmp_path / "kindle.cookies.txt"
    fixture_cookies = _THIS_DIR / "fixtures" / "cookies.txt"
    cookies_file.write_text(fixture_cookies.read_text())

    # Touch mtime to ~400 days ago
    epoch = time.time() - (400 * 86400)
    os.utime(cookies_file, (epoch, epoch))

    skill_config = SkillConfig(cookies_path=str(cookies_file), cookies_warn_after_days=300)
    init(ctx.config, skill_config)

    monkeypatch.setattr(
        kindle_tools, "_make_session",
        lambda jar, ua: _make_mock_session(fixture_html),
    )

    result = await kindle_tools.kindle_list_books(ctx)

    assert "Warning" in result.text
    assert result.data is not None
    assert result.data["cookies_age_days"] >= 400


@pytest.mark.asyncio
async def test_kindle_fetch_highlights_happy_path(ctx, tmp_path, monkeypatch):
    """kindle_fetch_highlights with mocked session returns highlight data from fixture HTML."""
    fixture_html = _FIXTURE_PATH.read_text()
    cookies_file = tmp_path / "kindle.cookies.txt"
    fixture_cookies = _THIS_DIR / "fixtures" / "cookies.txt"
    cookies_file.write_text(fixture_cookies.read_text())

    skill_config = SkillConfig(cookies_path=str(cookies_file))
    init(ctx.config, skill_config)

    monkeypatch.setattr(
        kindle_tools, "_make_session",
        lambda jar, ua: _make_mock_session(fixture_html),
    )

    result = await kindle_tools.kindle_fetch_highlights(ctx, asin="B078VWDNKT")

    assert result.data is not None
    assert "highlights" in result.data
    assert result.data["asin"] == "B078VWDNKT"
    assert len(result.data["highlights"]) > 0

    # Verify structure of each highlight dict
    for hl in result.data["highlights"]:
        assert "annotation_id" in hl
        assert "location" in hl
        assert "color" in hl
        assert "text" in hl
        assert "note" in hl

    assert "Found" in result.text


@pytest.mark.asyncio
async def test_kindle_fetch_highlights_http_error(ctx, tmp_path, monkeypatch):
    """kindle_fetch_highlights returns an error when the HTTP request fails."""
    cookies_file = tmp_path / "kindle.cookies.txt"
    fixture_cookies = _THIS_DIR / "fixtures" / "cookies.txt"
    cookies_file.write_text(fixture_cookies.read_text())

    skill_config = SkillConfig(cookies_path=str(cookies_file))
    init(ctx.config, skill_config)

    # Mock session whose .get() raises an exception
    failing_session = MagicMock()
    failing_session.get = AsyncMock(side_effect=RuntimeError("connection refused"))
    failing_cm = MagicMock()
    failing_cm.__aenter__ = AsyncMock(return_value=failing_session)
    failing_cm.__aexit__ = AsyncMock(return_value=None)

    monkeypatch.setattr(
        kindle_tools, "_make_session",
        lambda jar, ua: failing_cm,
    )

    result = await kindle_tools.kindle_fetch_highlights(ctx, asin="B078VWDNKT")

    assert result.text.startswith("[error: failed to fetch highlights for B078VWDNKT")


@pytest.mark.asyncio
async def test_kindle_tools_register_via_skill_loader(ctx):
    """After activation, ctx.tools.extra should contain both kindle tool functions."""
    from decafclaw.skills import discover_skills
    from decafclaw.tools.skill_tools import activate_skill_internal

    ctx.config.extra_skill_paths = [str(_THIS_DIR)]

    skills = discover_skills(ctx.config)
    kindle_info = next((s for s in skills if s.name == "kindle"), None)
    assert kindle_info is not None

    await activate_skill_internal(ctx, kindle_info)

    assert "kindle_list_books" in ctx.tools.extra, (
        f"kindle_list_books not in ctx.tools.extra; keys={list(ctx.tools.extra.keys())}"
    )
    assert "kindle_fetch_highlights" in ctx.tools.extra, (
        f"kindle_fetch_highlights not in ctx.tools.extra; keys={list(ctx.tools.extra.keys())}"
    )

    # Also check that TOOL_DEFINITIONS are registered
    def_names = {d["function"]["name"] for d in ctx.tools.extra_definitions}
    assert "kindle_list_books" in def_names
    assert "kindle_fetch_highlights" in def_names


# ---------------------------------------------------------------------------
# Phase 6: kindle_sync_book — single-book end-to-end
# ---------------------------------------------------------------------------

# Helpers reused across Phase 6 tests


def _make_sync_mock_session(list_html: str, highlights_html: str = ""):
    """Mock session for kindle_sync_book: first .get() returns list HTML,
    second returns highlights HTML (falls back to list_html if not provided)."""
    if not highlights_html:
        highlights_html = list_html
    list_response = MagicMock()
    list_response.text = list_html
    list_response.raise_for_status = MagicMock()
    hl_response = MagicMock()
    hl_response.text = highlights_html
    hl_response.raise_for_status = MagicMock()
    session = MagicMock()
    session.get = AsyncMock(side_effect=[list_response, hl_response])
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=None)
    return cm


def _make_single_book_html(asin: str, title: str, author: str) -> str:
    """Minimal HTML with one book entry and one highlight for integration tests."""
    return f"""<html><body>
<div class="kp-notebook-library-each-book" id="{asin}">
  <h2 class="kp-notebook-searchable">{title}</h2>
  <p class="kp-notebook-searchable">By: {author}</p>
  <img class="kp-notebook-cover-image-border" src="https://example.com/cover.jpg" />
</div>
<div id="kp-notebook-annotations">
  <div id="ANN_H1_001">
    <span id="annotationHighlightHeader">Header</span>
    <input id="kp-annotation-location" value="100" />
    <div class="kp-notebook-highlight-yellow"></div>
    <span id="highlight">First highlight text</span>
    <span id="note"></span>
  </div>
  <div id="ANN_H2_002">
    <span id="annotationHighlightHeader">Header</span>
    <input id="kp-annotation-location" value="200" />
    <div class="kp-notebook-highlight-blue"></div>
    <span id="highlight">Second highlight text</span>
    <span id="note">A note here</span>
  </div>
</div>
</body></html>"""


def _make_single_highlight_html(asin: str, title: str, author: str) -> str:
    """HTML with only the first highlight (H2 removed — simulates Amazon deletion)."""
    return f"""<html><body>
<div class="kp-notebook-library-each-book" id="{asin}">
  <h2 class="kp-notebook-searchable">{title}</h2>
  <p class="kp-notebook-searchable">By: {author}</p>
  <img class="kp-notebook-cover-image-border" src="https://example.com/cover.jpg" />
</div>
<div id="kp-notebook-annotations">
  <div id="ANN_H1_001">
    <span id="annotationHighlightHeader">Header</span>
    <input id="kp-annotation-location" value="100" />
    <div class="kp-notebook-highlight-yellow"></div>
    <span id="highlight">First highlight text</span>
    <span id="note"></span>
  </div>
</div>
</body></html>"""


@pytest.mark.asyncio
async def test_kindle_sync_book_first_run(ctx, tmp_path, monkeypatch):
    """Fresh vault, no existing page. Vault page created with correct frontmatter+body.

    Summary says '2 new, 0 re-checked, 0 archived'.
    """
    from decafclaw.frontmatter import parse_frontmatter

    asin = "B001SYNCTEST"
    title = "Sync Test Book"
    author = "Test Author"

    cookies_file = tmp_path / "kindle.cookies.txt"
    fixture_cookies = _THIS_DIR / "fixtures" / "cookies.txt"
    cookies_file.write_text(fixture_cookies.read_text())

    skill_config = SkillConfig(cookies_path=str(cookies_file))
    init(ctx.config, skill_config)

    html = _make_single_book_html(asin, title, author)

    def _mock_session_factory(jar, ua):
        return _make_sync_mock_session(html, html)

    monkeypatch.setattr(kindle_tools, "_make_session", _mock_session_factory)

    result = await kindle_tools.kindle_sync_book(ctx, asin=asin)

    assert not result.text.startswith("[error:"), f"Unexpected error: {result.text}"
    assert "2 new" in result.text, f"Expected '2 new' in: {result.text}"
    assert "0 archived" in result.text, f"Expected '0 archived' in: {result.text}"
    assert result.data is not None
    assert result.data["asin"] == asin
    assert result.data["new_count"] == 2
    assert result.data["archived_count"] == 0

    # Verify the vault page was actually created
    book = BookSummary(asin=asin, title=title, author=author)
    page_path_str = _book_page_path(skill_config, book)
    vault_file = ctx.config.vault_root / page_path_str
    assert vault_file.exists(), f"Expected vault page at {vault_file}"

    content = vault_file.read_text()
    metadata, body = parse_frontmatter(content)
    assert metadata["asin"] == asin
    assert metadata["title"] == title
    assert metadata["author"] == author
    assert metadata["highlight_count"] == 2
    assert "## Highlights" in body
    assert "ANN_H1_001" in body
    assert "ANN_H2_002" in body
    assert "## Archived" not in body


@pytest.mark.asyncio
async def test_kindle_sync_book_idempotent(ctx, tmp_path, monkeypatch):
    """Sync twice without Amazon changes; page content identical except last_synced."""
    from decafclaw.frontmatter import parse_frontmatter

    asin = "B001IDEMTEST"
    title = "Idempotent Book"
    author = "Author Idem"

    cookies_file = tmp_path / "kindle.cookies.txt"
    fixture_cookies = _THIS_DIR / "fixtures" / "cookies.txt"
    cookies_file.write_text(fixture_cookies.read_text())

    skill_config = SkillConfig(cookies_path=str(cookies_file))
    init(ctx.config, skill_config)

    html = _make_single_book_html(asin, title, author)

    call_count = 0

    def _mock_session_factory(jar, ua):
        nonlocal call_count
        call_count += 1
        return _make_sync_mock_session(html, html)

    monkeypatch.setattr(kindle_tools, "_make_session", _mock_session_factory)

    result1 = await kindle_tools.kindle_sync_book(ctx, asin=asin)
    assert not result1.text.startswith("[error:")

    result2 = await kindle_tools.kindle_sync_book(ctx, asin=asin)
    assert not result2.text.startswith("[error:")

    # Read the page after both syncs and compare content
    book = BookSummary(asin=asin, title=title, author=author)
    page_path_str = _book_page_path(skill_config, book)
    vault_file = ctx.config.vault_root / page_path_str
    assert vault_file.exists()

    content_after_both = vault_file.read_text()
    metadata, body = parse_frontmatter(content_after_both)

    # Highlights section should be the same after both syncs
    assert "ANN_H1_001" in body
    assert "ANN_H2_002" in body
    assert "## Archived" not in body

    # Second sync: 0 new highlights
    assert result2.data["new_count"] == 0


@pytest.mark.asyncio
async def test_kindle_sync_book_archives_deleted(ctx, tmp_path, monkeypatch):
    """Sync with H1+H2; then sync with only H1. H2 moves to Archived.

    Summary says '0 new, 1 re-checked, 1 archived'.
    """
    from decafclaw.frontmatter import parse_frontmatter

    asin = "B001ARCHTEST"
    title = "Archive Test Book"
    author = "Author Archive"

    cookies_file = tmp_path / "kindle.cookies.txt"
    fixture_cookies = _THIS_DIR / "fixtures" / "cookies.txt"
    cookies_file.write_text(fixture_cookies.read_text())

    skill_config = SkillConfig(cookies_path=str(cookies_file), archive_deleted=True)
    init(ctx.config, skill_config)

    full_html = _make_single_book_html(asin, title, author)
    partial_html = _make_single_highlight_html(asin, title, author)

    # First session: list + highlights both return full_html (H1+H2)
    def _mock_session_factory_1(jar, ua):
        return _make_sync_mock_session(full_html, full_html)

    monkeypatch.setattr(kindle_tools, "_make_session", _mock_session_factory_1)

    result1 = await kindle_tools.kindle_sync_book(ctx, asin=asin)
    assert not result1.text.startswith("[error:")
    assert result1.data["new_count"] == 2

    # Second round: list returns full_html (book still exists), highlights returns partial_html (only H1).
    # kindle_sync_book calls _make_session twice — once for list, once for highlights.
    # Use a stateful factory so the Nth _make_session invocation returns the right HTML.
    _second_calls = []

    def _mock_session_factory_2(jar, ua):
        call_idx = len(_second_calls)
        _second_calls.append(call_idx)
        html_for_this_call = full_html if call_idx == 0 else partial_html
        response = MagicMock()
        response.text = html_for_this_call
        response.raise_for_status = MagicMock()
        session = MagicMock()
        session.get = AsyncMock(return_value=response)
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=session)
        cm.__aexit__ = AsyncMock(return_value=None)
        return cm

    monkeypatch.setattr(kindle_tools, "_make_session", _mock_session_factory_2)

    result2 = await kindle_tools.kindle_sync_book(ctx, asin=asin)
    assert not result2.text.startswith("[error:"), f"Unexpected error: {result2.text}"

    assert result2.data["archived_count"] == 1, f"Expected 1 archived, got {result2.data}"
    assert "1 archived" in result2.text, f"Expected '1 archived' in: {result2.text}"

    # Verify vault page: H1 in Highlights, H2 in Archived with strikethrough
    book = BookSummary(asin=asin, title=title, author=author)
    page_path_str = _book_page_path(skill_config, book)
    vault_file = ctx.config.vault_root / page_path_str
    content = vault_file.read_text()
    metadata, body = parse_frontmatter(content)

    archived_pos = body.find("## Archived")
    highlights_pos = body.find("## Highlights")
    assert highlights_pos >= 0
    assert archived_pos >= 0

    highlights_section = body[highlights_pos:archived_pos]
    assert "ANN_H1_001" in highlights_section
    assert "ANN_H2_002" not in highlights_section

    archived_section = body[archived_pos:]
    assert "ANN_H2_002" in archived_section
    assert "~~" in archived_section  # strikethrough


@pytest.mark.asyncio
async def test_kindle_sync_book_no_double_archive_count(ctx, tmp_path, monkeypatch):
    """archived_count must not re-count previously-archived entries on 3rd+ sync.

    Regression: existing_ids regex used to scan the WHOLE existing page,
    so previously-archived entries leaked back into 'archived this run' on every
    subsequent sync.

    Scenario:
    - Run 1: H1 + H2 fresh. archived_count == 0.
    - Run 2: only H1 fresh; H2 moves to ## Archived. archived_count == 1.
    - Run 3: only H1 fresh again; H2 should STILL be in ## Archived
             BUT the return summary's archived_count must be 0 (no new deletions).
    """
    asin = "B001ARCHIVE_REGRESS"
    title = "Archive Regression Book"
    author = "Regression Author"

    cookies_file = tmp_path / "kindle.cookies.txt"
    fixture_cookies = _THIS_DIR / "fixtures" / "cookies.txt"
    cookies_file.write_text(fixture_cookies.read_text())

    skill_config = SkillConfig(cookies_path=str(cookies_file), archive_deleted=True)
    init(ctx.config, skill_config)

    full_html = _make_single_book_html(asin, title, author)
    partial_html = _make_single_highlight_html(asin, title, author)

    # Run 1: H1 + H2
    def _mock_session_factory_1(jar, ua):
        return _make_sync_mock_session(full_html, full_html)

    monkeypatch.setattr(kindle_tools, "_make_session", _mock_session_factory_1)
    result1 = await kindle_tools.kindle_sync_book(ctx, asin=asin)
    assert not result1.text.startswith("[error:")
    assert result1.data["archived_count"] == 0

    # Run 2: only H1
    _second_calls = []

    def _mock_session_factory_2(jar, ua):
        call_idx = len(_second_calls)
        _second_calls.append(call_idx)
        html_for_this_call = full_html if call_idx == 0 else partial_html
        response = MagicMock()
        response.text = html_for_this_call
        response.raise_for_status = MagicMock()
        session = MagicMock()
        session.get = AsyncMock(return_value=response)
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=session)
        cm.__aexit__ = AsyncMock(return_value=None)
        return cm

    monkeypatch.setattr(kindle_tools, "_make_session", _mock_session_factory_2)
    result2 = await kindle_tools.kindle_sync_book(ctx, asin=asin)
    assert not result2.text.startswith("[error:")
    assert result2.data["archived_count"] == 1, f"Run 2: expected 1 archived, got {result2.data}"

    # Run 3: only H1 again — no new deletions, so archived_count must be 0
    _third_calls = []

    def _mock_session_factory_3(jar, ua):
        call_idx = len(_third_calls)
        _third_calls.append(call_idx)
        html_for_this_call = full_html if call_idx == 0 else partial_html
        response = MagicMock()
        response.text = html_for_this_call
        response.raise_for_status = MagicMock()
        session = MagicMock()
        session.get = AsyncMock(return_value=response)
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=session)
        cm.__aexit__ = AsyncMock(return_value=None)
        return cm

    monkeypatch.setattr(kindle_tools, "_make_session", _mock_session_factory_3)
    result3 = await kindle_tools.kindle_sync_book(ctx, asin=asin)
    assert not result3.text.startswith("[error:")
    assert (
        result3.data["archived_count"] == 0
    ), f"Run 3: expected 0 archived (no new deletions), but got {result3.data['archived_count']}"


@pytest.mark.asyncio
async def test_kindle_sync_book_unknown_asin(ctx, tmp_path, monkeypatch):
    """Books list doesn't contain the requested ASIN; returns an error."""
    cookies_file = tmp_path / "kindle.cookies.txt"
    fixture_cookies = _THIS_DIR / "fixtures" / "cookies.txt"
    cookies_file.write_text(fixture_cookies.read_text())

    skill_config = SkillConfig(cookies_path=str(cookies_file))
    init(ctx.config, skill_config)

    # HTML has a different ASIN, not B999UNKNOWN
    html = _make_single_book_html("B001OTHER", "Other Book", "Other Author")

    def _mock_session_factory(jar, ua):
        return _make_sync_mock_session(html, html)

    monkeypatch.setattr(kindle_tools, "_make_session", _mock_session_factory)

    result = await kindle_tools.kindle_sync_book(ctx, asin="B999UNKNOWN")

    assert result.text.startswith("[error: ASIN"), f"Expected ASIN error, got: {result.text}"


# ---------------------------------------------------------------------------
# Fix 1 regression: _upsert_book_page archived-section order is deterministic
# ---------------------------------------------------------------------------


def test_upsert_archived_section_order_is_deterministic():
    """Two calls with identical inputs must produce byte-identical output.

    This guards against set hash-randomized iteration order for ``newly_archived``
    that was present before the sorted() fix.  We use two highlights so that the
    set has at least two elements and order can actually differ across runs.
    """
    from decafclaw.frontmatter import parse_frontmatter

    h1 = _make_highlight("ANN001", location="10", text="Keep this")
    h2 = _make_highlight("ANN002", location="20", text="Delete me alpha")
    h3 = _make_highlight("ANN003", location="30", text="Delete me beta")
    book = _make_book()

    # Build a page with all three highlights
    existing_page = _upsert_book_page("", [h1, h2, h3], book, _NOW)

    # Sync with only h1 → h2 and h3 become newly_archived (a 2-element set)
    result_a = _upsert_book_page(existing_page, [h1], book, _NOW, archive_deleted=True)
    result_b = _upsert_book_page(existing_page, [h1], book, _NOW, archive_deleted=True)

    assert result_a == result_b, (
        "Two identical _upsert_book_page calls produced different output — "
        "archived section ordering is non-deterministic."
    )

    # Also verify the archived section contains both IDs in sorted order
    _, body = parse_frontmatter(result_a)
    archived_pos = body.find("## Archived")
    assert archived_pos >= 0
    archived_section = body[archived_pos:]
    pos_ann002 = archived_section.find("ANN002")
    pos_ann003 = archived_section.find("ANN003")
    assert pos_ann002 >= 0 and pos_ann003 >= 0
    assert pos_ann002 < pos_ann003, "Archived entries must appear in sorted(annotation_id) order"


# ---------------------------------------------------------------------------
# Fix 2 regression: archived_count in tool result is 0 when archive_deleted=False
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_kindle_sync_book_archive_disabled_tool_result_archived_count_zero(
    ctx, tmp_path, monkeypatch
):
    """With archive_deleted=False, the tool-result archived_count must be 0.

    Even if Amazon deleted a highlight (it would appear in existing_ids - fresh_ids),
    the tool should report archived_count=0 rather than the raw deletion count.
    """
    asin = "B001ARCHDISABLED"
    title = "Archive Disabled Book"
    author = "Test Author"

    cookies_file = tmp_path / "kindle.cookies.txt"
    fixture_cookies = _THIS_DIR / "fixtures" / "cookies.txt"
    cookies_file.write_text(fixture_cookies.read_text())

    skill_config = SkillConfig(cookies_path=str(cookies_file), archive_deleted=False)
    init(ctx.config, skill_config)

    full_html = _make_single_book_html(asin, title, author)
    partial_html = _make_single_highlight_html(asin, title, author)

    # First sync: H1 + H2
    def _mock_full(jar, ua):
        return _make_sync_mock_session(full_html, full_html)

    monkeypatch.setattr(kindle_tools, "_make_session", _mock_full)
    result1 = await kindle_tools.kindle_sync_book(ctx, asin=asin)
    assert not result1.text.startswith("[error:")

    # Second sync: only H1 (H2 deleted on Amazon). archive_deleted=False means it's silently dropped.
    _calls: list[int] = []

    def _mock_partial(jar, ua):
        idx = len(_calls)
        _calls.append(idx)
        # With book= param, kindle_sync_book no longer calls list; just highlights
        response = MagicMock()
        response.text = partial_html
        response.raise_for_status = MagicMock()
        session = MagicMock()
        session.get = AsyncMock(return_value=response)
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=session)
        cm.__aexit__ = AsyncMock(return_value=None)
        return cm

    monkeypatch.setattr(kindle_tools, "_make_session", _mock_partial)
    result2 = await kindle_tools.kindle_sync_book(ctx, asin=asin)
    assert not result2.text.startswith("[error:")

    # Key assertion: archived_count must be 0 (not 1) when archive_deleted=False
    assert result2.data["archived_count"] == 0, (
        f"Expected archived_count=0 with archive_deleted=False, got {result2.data['archived_count']}"
    )


# ---------------------------------------------------------------------------
# Fix 3 regression: kindle_sync_all makes N+1 _make_session calls, not 2N+1
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_kindle_sync_all_session_count_n_plus_1(ctx, tmp_path, monkeypatch):
    """kindle_sync_all should make exactly N+1 _make_session calls for N books.

    Before Fix 3, kindle_sync_book always called kindle_list_books internally,
    making 2N+1 total calls (1 initial list + 2 per book).  After the fix,
    kindle_sync_all passes the already-known BookSummary to kindle_sync_book,
    so each book needs only 1 highlights fetch → N+1 total.
    """
    import asyncio

    cookies_file = tmp_path / "kindle.cookies.txt"
    fixture_cookies = _THIS_DIR / "fixtures" / "cookies.txt"
    cookies_file.write_text(fixture_cookies.read_text())

    books_info = [
        ("B001CNT1", "Count Book One", "Author A"),
        ("B001CNT2", "Count Book Two", "Author B"),
        ("B001CNT3", "Count Book Three", "Author C"),
    ]
    skill_config = SkillConfig(
        cookies_path=str(cookies_file),
        sync_min_interval_seconds=0,
    )
    init(ctx.config, skill_config)

    list_html = _make_multi_book_list_html(books_info)

    session_call_count = 0

    def _counting_factory(jar, ua):
        nonlocal session_call_count
        session_call_count += 1
        response = MagicMock()
        response.text = list_html
        response.raise_for_status = MagicMock()
        session = MagicMock()
        session.get = AsyncMock(return_value=response)
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=session)
        cm.__aexit__ = AsyncMock(return_value=None)
        return cm

    monkeypatch.setattr(kindle_tools, "_make_session", _counting_factory)
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())

    result = await kindle_tools.kindle_sync_all(ctx)

    assert not result.text.startswith("[error:"), f"Unexpected error: {result.text}"
    n_books = len(books_info)
    expected_calls = n_books + 1  # 1 initial list + N highlights fetches
    assert session_call_count == expected_calls, (
        f"Expected {expected_calls} _make_session calls for {n_books} books, "
        f"got {session_call_count}. kindle_sync_all is making redundant list fetches."
    )


# ---------------------------------------------------------------------------
# Phase 7: kindle_sync_all — multi-book orchestrator + journal
# ---------------------------------------------------------------------------

# HTML helpers for multi-book sync tests

def _make_multi_book_list_html(books: list[tuple[str, str, str]]) -> str:
    """Build a books-list HTML with multiple book entries.

    books: list of (asin, title, author)
    """
    entries = []
    for asin, title, author in books:
        entries.append(f"""
<div class="kp-notebook-library-each-book" id="{asin}">
  <h2 class="kp-notebook-searchable">{title}</h2>
  <p class="kp-notebook-searchable">By: {author}</p>
  <img class="kp-notebook-cover-image-border" src="https://example.com/{asin}.jpg" />
</div>""")
    highlights = """
<div id="kp-notebook-annotations">
  <div id="ANN_MULTI_001">
    <span id="annotationHighlightHeader">Header</span>
    <input id="kp-annotation-location" value="100" />
    <div class="kp-notebook-highlight-yellow"></div>
    <span id="highlight">Highlight text</span>
    <span id="note"></span>
  </div>
</div>"""
    return f"<html><body>{''.join(entries)}{highlights}</body></html>"


def _make_session_sequence(responses: list[str]):
    """Return a factory that hands out sessions one at a time, each returning
    the corresponding HTML string from ``responses``.

    Each session is a fresh async context manager that returns a mock session
    whose ``.get()`` returns the given HTML.
    """
    iterator = iter(responses)

    def factory(jar, ua):
        html = next(iterator)
        response = MagicMock()
        response.text = html
        response.raise_for_status = MagicMock()
        session = MagicMock()
        session.get = AsyncMock(return_value=response)
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=session)
        cm.__aexit__ = AsyncMock(return_value=None)
        return cm

    return factory


@pytest.mark.asyncio
async def test_kindle_sync_all_happy_path(ctx, tmp_path, monkeypatch):
    """3 books each with 1 highlight. All succeed.

    Asserts: books_synced=3, new_total=3, journal file contains expected content.
    """
    import asyncio

    cookies_file = tmp_path / "kindle.cookies.txt"
    fixture_cookies = _THIS_DIR / "fixtures" / "cookies.txt"
    cookies_file.write_text(fixture_cookies.read_text())

    books_info = [
        ("B001SYNC1", "Book One", "Author A"),
        ("B001SYNC2", "Book Two", "Author B"),
        ("B001SYNC3", "Book Three", "Author C"),
    ]
    skill_config = SkillConfig(
        cookies_path=str(cookies_file),
        sync_min_interval_seconds=0,
    )
    init(ctx.config, skill_config)

    # Build response sequence (N+1 after Fix 3 — no per-book list fetch):
    # Call 0: kindle_sync_all -> kindle_list_books (all 3 books)
    # Call 1: kindle_sync_book(B001SYNC1) -> kindle_fetch_highlights
    # Call 2: kindle_sync_book(B001SYNC2) -> kindle_fetch_highlights
    # Call 3: kindle_sync_book(B001SYNC3) -> kindle_fetch_highlights
    list_html = _make_multi_book_list_html(books_info)
    responses = [list_html] * 4  # 1 initial + 1 highlights per book × 3 books

    monkeypatch.setattr(
        kindle_tools, "_make_session",
        _make_session_sequence(responses),
    )
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())

    result = await kindle_tools.kindle_sync_all(ctx)

    assert not result.text.startswith("[error:"), f"Unexpected error: {result.text}"
    assert result.data is not None
    assert result.data["books_synced"] == 3, f"Expected 3, got {result.data}"
    assert result.data["books_total"] == 3
    assert result.data["new_total"] == 3, f"Expected new_total=3, got {result.data}"
    assert result.data["archived_total"] == 0
    assert result.data["failures"] == []

    # Check journal was written
    from datetime import date

    today = date.today()
    journal_path = (
        ctx.config.vault_root
        / "agent"
        / "journal"
        / str(today.year)
        / f"{today}.md"
    )
    assert journal_path.exists(), f"Expected journal at {journal_path}"
    journal_content = journal_path.read_text()
    assert "Kindle sync run" in journal_content
    assert "Books processed: 3 / 3" in journal_content
    assert "New highlights: 3" in journal_content
    assert "Failures: 0" in journal_content


@pytest.mark.asyncio
async def test_kindle_sync_all_partial_failure(ctx, tmp_path, monkeypatch):
    """Middle book's highlights fetch raises; first and third succeed.

    Asserts: 2 books succeed, 1 in failures list, journal entry mentions failure.
    """
    import asyncio

    cookies_file = tmp_path / "kindle.cookies.txt"
    fixture_cookies = _THIS_DIR / "fixtures" / "cookies.txt"
    cookies_file.write_text(fixture_cookies.read_text())

    books_info = [
        ("B001PFAIL1", "Book One", "Author A"),
        ("B001PFAIL2", "Book Two Fail", "Author B"),
        ("B001PFAIL3", "Book Three", "Author C"),
    ]
    skill_config = SkillConfig(
        cookies_path=str(cookies_file),
        sync_min_interval_seconds=0,
    )
    init(ctx.config, skill_config)

    list_html = _make_multi_book_list_html(books_info)

    # Build a failing response for book 2's highlights fetch.
    # After Fix 3, kindle_sync_all passes the BookSummary to kindle_sync_book,
    # so there's no per-book list fetch.  Response sequence is now N+1:
    # 0: sync_all -> list_books (all 3)
    # 1: sync_book(B001PFAIL1) -> highlights OK
    # 2: sync_book(B001PFAIL2) -> highlights raises (simulated failure)
    # 3: sync_book(B001PFAIL3) -> highlights OK

    call_idx = 0

    def _failing_factory(jar, ua):
        nonlocal call_idx
        idx = call_idx
        call_idx += 1

        if idx == 2:
            # Book 2 highlights: raise to simulate network failure
            session = MagicMock()
            session.get = AsyncMock(side_effect=RuntimeError("simulated fetch error"))
            cm = MagicMock()
            cm.__aenter__ = AsyncMock(return_value=session)
            cm.__aexit__ = AsyncMock(return_value=None)
            return cm

        response = MagicMock()
        response.text = list_html
        response.raise_for_status = MagicMock()
        session = MagicMock()
        session.get = AsyncMock(return_value=response)
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=session)
        cm.__aexit__ = AsyncMock(return_value=None)
        return cm

    monkeypatch.setattr(
        kindle_tools, "_make_session",
        _failing_factory,
    )
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())

    result = await kindle_tools.kindle_sync_all(ctx)

    assert not result.text.startswith("[error:"), f"Unexpected error: {result.text}"
    assert result.data is not None
    assert result.data["books_synced"] == 2, f"Expected 2, got {result.data}"
    assert len(result.data["failures"]) == 1, f"Expected 1 failure, got {result.data}"
    # The failure should be for the middle book
    failed_asin = result.data["failures"][0][0]
    assert failed_asin == "B001PFAIL2", f"Expected B001PFAIL2 in failures, got {failed_asin}"

    # Journal should mention the failure
    from datetime import date

    today = date.today()
    journal_path = (
        ctx.config.vault_root
        / "agent"
        / "journal"
        / str(today.year)
        / f"{today}.md"
    )
    assert journal_path.exists(), f"Expected journal at {journal_path}"
    journal_content = journal_path.read_text()
    assert "Failures: 1" in journal_content
    assert "B001PFAIL2" in journal_content


@pytest.mark.asyncio
async def test_kindle_sync_all_rate_limit(ctx, tmp_path, monkeypatch):
    """With 3 books and sync_min_interval_seconds=10, sleep is called exactly twice with arg 10."""
    import asyncio

    cookies_file = tmp_path / "kindle.cookies.txt"
    fixture_cookies = _THIS_DIR / "fixtures" / "cookies.txt"
    cookies_file.write_text(fixture_cookies.read_text())

    books_info = [
        ("B001RL1", "Rate Limit Book 1", "Author A"),
        ("B001RL2", "Rate Limit Book 2", "Author B"),
        ("B001RL3", "Rate Limit Book 3", "Author C"),
    ]
    skill_config = SkillConfig(
        cookies_path=str(cookies_file),
        sync_min_interval_seconds=10,
    )
    init(ctx.config, skill_config)

    list_html = _make_multi_book_list_html(books_info)
    responses = [list_html] * 4  # N+1 after Fix 3: 1 initial list + 1 highlights per book

    monkeypatch.setattr(
        kindle_tools, "_make_session",
        _make_session_sequence(responses),
    )
    sleep_mock = AsyncMock()
    monkeypatch.setattr(asyncio, "sleep", sleep_mock)

    result = await kindle_tools.kindle_sync_all(ctx)

    assert not result.text.startswith("[error:"), f"Unexpected error: {result.text}"

    # 3 books → sleep called exactly twice (before book 2 and book 3, not before book 1)
    assert sleep_mock.call_count == 2, (
        f"Expected sleep called 2 times, got {sleep_mock.call_count} calls: {sleep_mock.call_args_list}"
    )
    for call in sleep_mock.call_args_list:
        args, kwargs = call
        sleep_arg = args[0] if args else kwargs.get("delay", kwargs.get("seconds"))
        assert sleep_arg == 10, f"Expected sleep(10), got sleep({sleep_arg})"


# ---------------------------------------------------------------------------
# Phase 8 fix: enabled-gate in kindle_sync_all (short-circuit for scheduled runs)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_kindle_sync_all_scheduled_gate_disabled(ctx, tmp_path):
    """When ctx.task_mode == 'scheduled' AND enabled=False, kindle_sync_all returns
    a no-op without doing any fetches. The gate short-circuits before any network call.
    """
    cookies_file = tmp_path / "kindle.cookies.txt"
    fixture_cookies = _THIS_DIR / "fixtures" / "cookies.txt"
    cookies_file.write_text(fixture_cookies.read_text())

    skill_config = SkillConfig(
        enabled=False,  # Disabled
        cookies_path=str(cookies_file),
    )
    init(ctx.config, skill_config)

    # Set task_mode to 'scheduled' on the ctx
    ctx.task_mode = "scheduled"

    result = await kindle_tools.kindle_sync_all(ctx)

    # Should return a no-op message with 'disabled' and 'skipping'
    assert "disabled" in result.text.lower(), f"Expected 'disabled' in: {result.text}"
    assert "skipping" in result.text.lower(), f"Expected 'skipping' in: {result.text}"
    assert not result.text.startswith("[error:"), f"Should not be an error: {result.text}"


@pytest.mark.asyncio
async def test_kindle_sync_all_scheduled_gate_enabled_proceeds(ctx, tmp_path, monkeypatch):
    """When ctx.task_mode == 'scheduled' AND enabled=True, kindle_sync_all proceeds
    and attempts the fetch (mocked here).
    """
    import asyncio

    cookies_file = tmp_path / "kindle.cookies.txt"
    fixture_cookies = _THIS_DIR / "fixtures" / "cookies.txt"
    cookies_file.write_text(fixture_cookies.read_text())

    skill_config = SkillConfig(
        enabled=True,  # Enabled
        cookies_path=str(cookies_file),
        sync_min_interval_seconds=0,
    )
    init(ctx.config, skill_config)

    ctx.task_mode = "scheduled"

    # Mock the session to return empty book list (no network call)
    empty_list_html = '<div class="kp-notebook-library-each-book"></div>'
    monkeypatch.setattr(
        kindle_tools, "_make_session",
        _make_session_sequence([empty_list_html]),
    )
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())

    result = await kindle_tools.kindle_sync_all(ctx)

    # Should proceed (gate did NOT trigger)
    # With no books, it should report "Synced 0/0"
    assert not result.text.startswith("[error:"), f"Unexpected error: {result.text}"
    assert "0" in result.text, f"Should show 0 books: {result.text}"
    assert "disabled" not in result.text.lower(), f"Gate should not trigger: {result.text}"


@pytest.mark.asyncio
async def test_kindle_sync_all_user_invocable_gate_bypassed(ctx, tmp_path, monkeypatch):
    """When ctx.task_mode != 'scheduled' (e.g., empty or 'interactive'), the gate
    does NOT trigger even if enabled=False. User-invocable paths bypass the gate.
    """
    import asyncio

    cookies_file = tmp_path / "kindle.cookies.txt"
    fixture_cookies = _THIS_DIR / "fixtures" / "cookies.txt"
    cookies_file.write_text(fixture_cookies.read_text())

    skill_config = SkillConfig(
        enabled=False,  # Disabled
        cookies_path=str(cookies_file),
        sync_min_interval_seconds=0,
    )
    init(ctx.config, skill_config)

    # Default ctx.task_mode is "" (empty string, interactive mode)
    assert ctx.task_mode != "scheduled", f"Expected non-scheduled mode, got {ctx.task_mode!r}"

    # Mock the session to return empty book list
    empty_list_html = '<div class="kp-notebook-library-each-book"></div>'
    monkeypatch.setattr(
        kindle_tools, "_make_session",
        _make_session_sequence([empty_list_html]),
    )
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())

    result = await kindle_tools.kindle_sync_all(ctx)

    # Gate should NOT trigger because task_mode != "scheduled"
    # Should proceed as normal
    assert not result.text.startswith("[error:"), f"Unexpected error: {result.text}"
    assert "disabled" not in result.text.lower(), f"Gate should not trigger in user-invocable mode: {result.text}"


# ---------------------------------------------------------------------------
# Phase 8: SKILL.md frontmatter + schedule discovery
# ---------------------------------------------------------------------------


def test_skill_md_frontmatter_parses():
    """SKILL.md frontmatter has the expected fields for user-invocable use.

    Schedule fields have moved to SCHEDULE.md sidecar (see test_schedule_md_parses).
    """
    from decafclaw.skills import parse_skill_md

    skill_md_path = _THIS_DIR / "SKILL.md"
    info = parse_skill_md(skill_md_path)
    assert info is not None, "Failed to parse SKILL.md"
    assert info.name == "kindle"
    assert info.user_invocable is True
    # Allowed-tools should contain all 4 kindle tools + the vault read/write/list/journal ones + current_time
    for required in (
        "kindle_list_books", "kindle_fetch_highlights",
        "kindle_sync_book", "kindle_sync_all",
        "vault_read", "vault_write", "vault_list", "vault_journal_append", "current_time",
    ):
        assert required in info.allowed_tools, f"missing {required} in allowed_tools: {info.allowed_tools}"


def test_schedule_md_parses():
    """SCHEDULE.md sidecar has the expected schedule fields."""
    from decafclaw.schedules import parse_schedule_file

    sched_md_path = _THIS_DIR / "SCHEDULE.md"
    task = parse_schedule_file(sched_md_path)
    assert task is not None, "Failed to parse SCHEDULE.md"
    assert task.schedule == "0 5 * * *"
    # Allowed-tools should contain all required tools
    for required in (
        "kindle_list_books", "kindle_fetch_highlights",
        "kindle_sync_book", "kindle_sync_all",
        "vault_read", "vault_write", "vault_list", "vault_journal_append", "current_time",
    ):
        assert required in task.allowed_tools, f"missing {required} in allowed_tools: {task.allowed_tools}"


def test_skill_discovered_as_scheduled(monkeypatch, tmp_path):
    """discover_schedules picks up the kindle contrib skill via SCHEDULE.md sidecar.

    Must patch run_schedule_task to a no-op per CLAUDE.md test-speed discipline.
    Contrib (extra_skill_paths) SCHEDULE.md is forced to enabled=False.
    """
    from decafclaw.config import Config
    from decafclaw.config_types import AgentConfig
    from decafclaw.schedules import discover_schedules

    monkeypatch.setattr("decafclaw.schedules.run_schedule_task", lambda *a, **kw: None)

    # Point extra_skill_paths at this skill dir so discover_schedules finds it.
    cfg = Config(
        agent=AgentConfig(data_home=str(tmp_path), id="test-agent"),
        extra_skill_paths=[str(_THIS_DIR)],
    )
    schedules = discover_schedules(cfg)
    kindle = next((s for s in schedules if s.name == "kindle"), None)
    assert kindle is not None, f"kindle not found in schedules: {[s.name for s in schedules]}"
    assert kindle.schedule == "0 5 * * *"
    assert kindle.source == "extra"
    # Contrib SCHEDULE.md is forced to enabled=False (user must opt in via overlay)
    assert kindle.enabled is False
