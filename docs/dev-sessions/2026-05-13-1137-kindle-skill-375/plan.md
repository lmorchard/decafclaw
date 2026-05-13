# Kindle skill — Implementation Plan

**Goal:** Build the bundled `kindle` skill — on-demand and scheduled ingest of Kindle highlights & notes from `read.amazon.com/notebook` into per-book vault pages, with archived-section handling for deleted highlights.

**Approach:** Mirror the `newsletter` bundled-skill shape (`SkillConfig` + `init` + `TOOL_DEFINITIONS`). Use `curl_cffi` (TLS-impersonating client) for fetches with cookies loaded from `data/{agent_id}/secrets/kindle.cookies.txt`. Per-book pages at `agent/pages/kindle/<asin>-<slug>.md` are fully agent-owned mechanical overwrites with an `## Archived` section for deleted highlights. Each sync run appends a journal entry tagged `[ingested, kindle]`.

**Tech stack:** Python 3.13+, `curl-cffi` (new dep), `http.cookiejar.MozillaCookieJar` (stdlib), `pyyaml` (existing), `croniter` (existing — schedule discovery), Pytest with `pytest-asyncio`.

---

## Phase 1: Skill scaffolding + new dependency [x] DONE — commit `1e44709`

Bundled `kindle` skill that registers via the skill loader with zero tools and a no-op SKILL.md body. Establishes the file layout and proves the loader integration before any logic exists.

**Files:**
- Modify: `pyproject.toml` — add `"curl-cffi>=0.7.0"` to `dependencies` (alphabetical insertion between `croniter` and `httpx` looks reasonable).
- Create: `src/decafclaw/skills/kindle/__init__.py` — empty file (marker for skill loader; no Python exports).
- Create: `src/decafclaw/skills/kindle/SKILL.md` — minimal frontmatter (`name: kindle`, `description: ...`, `user-invocable: true`, `context: inline`, `allowed-tools:` empty list, `required-skills: [kindle]`) plus a placeholder body that says "Phase 1 — no tools yet, do nothing." (We add real frontmatter fields incrementally; this prevents earlier phases from depending on a feature that lands later.)
- Create: `src/decafclaw/skills/kindle/tools.py` — `SkillConfig` dataclass with all the fields the spec calls for, plus `init(config, skill_config)` (mirror `src/decafclaw/skills/newsletter/tools.py:19-48`). No tool functions yet. `TOOLS = {}` and `TOOL_DEFINITIONS = []` exported for symmetry.
- Test: `tests/test_kindle_skill.py` — new test file covering Phase 1 only for now. Phases 2-7 extend it.

**Key changes:**

```python
# src/decafclaw/skills/kindle/tools.py
"""Kindle bundled skill — periodic ingest of Kindle highlights & notes."""

import logging
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

_config = None
_skill_config: "SkillConfig | None" = None


@dataclass
class SkillConfig:
    enabled: bool = field(
        default=False, metadata={"env_alias": "KINDLE_ENABLED"}
    )
    cookies_path: str = field(
        default="", metadata={"env_alias": "KINDLE_COOKIES_PATH"}
    )
    amazon_domain: str = field(
        default="amazon.com", metadata={"env_alias": "KINDLE_AMAZON_DOMAIN"}
    )
    vault_subfolder: str = field(
        default="agent/pages/kindle",
        metadata={"env_alias": "KINDLE_VAULT_SUBFOLDER"},
    )
    sync_min_interval_seconds: int = field(
        default=60, metadata={"env_alias": "KINDLE_SYNC_MIN_INTERVAL_SECONDS"}
    )
    archive_deleted: bool = field(
        default=True, metadata={"env_alias": "KINDLE_ARCHIVE_DELETED"}
    )
    user_agent: str = field(
        default=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        metadata={"env_alias": "KINDLE_USER_AGENT"},
    )
    cookies_warn_after_days: int = field(
        default=300, metadata={"env_alias": "KINDLE_COOKIES_WARN_AFTER_DAYS"}
    )


def init(config, skill_config: SkillConfig) -> None:
    """Initialize the kindle skill. Called by the skill loader on activation."""
    global _config, _skill_config
    _config = config
    _skill_config = skill_config


TOOLS: dict = {}
TOOL_DEFINITIONS: list = []
```

`cookies_path = ""` default means "resolve at activation to `config.agent_path / 'secrets' / 'kindle.cookies.txt'`." The resolution helper goes in Phase 2 where it's first used; the empty default in the dataclass keeps Phase 1 minimal.

The `SKILL.md` body is a few lines; intentionally empty of agent logic until Phase 8.

**Verification — automated:**
- [x] `make check` passes (lint + typecheck for both Python and JS)
- [x] `uv sync` after editing `pyproject.toml` resolves `curl-cffi`
- [x] `uv run python -c "import curl_cffi.requests; print(curl_cffi.requests.AsyncSession)"` prints the class (proves the dependency installs and imports)
- [x] `make test` passes, including the new test file (2433 tests)
- [x] `tests/test_kindle_skill.py::test_kindle_skill_registers_via_loader` passes
- [x] `tests/test_kindle_skill.py::test_skill_config_defaults` passes

**Verification — manual:**
- [x] `uv run python -c "from decafclaw.skills.kindle.tools import SkillConfig; print(SkillConfig())"` — output captured in session notes; all 8 fields match defaults.

---

## Phase 2: Cookie loading + curl_cffi session helper [x] DONE — commit `2d37a09`

Build the auth substrate: load Netscape `cookies.txt` into a cookie jar; construct a `curl_cffi.requests.AsyncSession` with that jar attached and a Chrome impersonation header. Tested in isolation; no network calls.

**Files:**
- Modify: `src/decafclaw/skills/kindle/tools.py` — add `_resolve_cookies_path(config, skill_config) -> Path`, `_load_cookie_jar(path: Path) -> http.cookiejar.MozillaCookieJar`, `_make_session(cookie_jar, user_agent) -> curl_cffi.requests.AsyncSession`. New imports at top: `http.cookiejar`, `curl_cffi.requests`, `pathlib.Path`, `datetime`.
- Test: `tests/test_kindle_skill.py` — extend with cookie-loading tests.
- Test fixture: `tests/fixtures/kindle/cookies.txt` — a synthetic Netscape-format cookie file with two cookies (`at-main` and `sess-at-main`) pointing at `.amazon.com`. Document at the top: "# Netscape HTTP Cookie File — synthetic, for testing only".

**Key changes:**

```python
import http.cookiejar
from datetime import datetime
from pathlib import Path

from curl_cffi.requests import AsyncSession


def _resolve_cookies_path(config, skill_config: "SkillConfig") -> Path:
    """Resolve the cookies file path. Defaults to admin secrets directory if unset."""
    if skill_config.cookies_path:
        p = Path(skill_config.cookies_path)
        return p if p.is_absolute() else config.agent_path / p
    return config.agent_path / "secrets" / "kindle.cookies.txt"


def _load_cookie_jar(path: Path) -> http.cookiejar.MozillaCookieJar:
    """Load a Netscape cookies.txt file. Raises FileNotFoundError if missing."""
    if not path.is_file():
        raise FileNotFoundError(f"Kindle cookies file not found: {path}")
    jar = http.cookiejar.MozillaCookieJar(str(path))
    # Both kwargs True — let the load tolerate expired cookies; curl_cffi can
    # still send them and Amazon decides whether to honor.
    jar.load(ignore_discard=True, ignore_expires=True)
    return jar


def _cookie_file_age_days(path: Path) -> float:
    """Return how many days old the cookies file is, based on mtime."""
    return (datetime.now().timestamp() - path.stat().st_mtime) / 86400.0


def _make_session(
    cookie_jar: http.cookiejar.MozillaCookieJar, user_agent: str
) -> AsyncSession:
    """Construct a curl_cffi AsyncSession with cookies + Chrome impersonation."""
    session = AsyncSession(
        impersonate="chrome131",
        headers={
            "User-Agent": user_agent,
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        },
        cookies=cookie_jar,
    )
    return session
```

**Verification — automated:**
- [x] `make check` passes (lint + typecheck)
- [x] `tests/test_kindle_skill.py::test_load_cookie_jar_reads_netscape_format`
- [x] `tests/test_kindle_skill.py::test_load_cookie_jar_missing_file_raises`
- [x] `tests/test_kindle_skill.py::test_resolve_cookies_path_default`
- [x] `tests/test_kindle_skill.py::test_resolve_cookies_path_relative_override`
- [x] `tests/test_kindle_skill.py::test_resolve_cookies_path_absolute_override`
- [x] `tests/test_kindle_skill.py::test_make_session_uses_chrome_impersonation`
- [x] `make test` passes overall (2440 tests; +7 from Phase 1's 2433).

**Verification — manual (deferred to end-of-branch live-smoke batch):**
- [ ] Place a real `cookies.txt` (exported from a logged-in Amazon session) at `data/{agent_id}/secrets/kindle.cookies.txt` in the main clone, and from the worktree run `uv run python -c "from decafclaw.config import load_config; from decafclaw.skills.kindle.tools import _resolve_cookies_path, _load_cookie_jar, SkillConfig; cfg = load_config(); p = _resolve_cookies_path(cfg, SkillConfig()); print(p); print(len(_load_cookie_jar(p)))"`. Expect: a positive count of cookies. If 0, the file format is off.

---

## Phase 3: HTML parsers — books list + per-book highlights [x] DONE — commit `2cd3f3b`

Two synchronous parsers that take HTML strings and return structured data. No network. The hardest piece of the skill; this is where DOM-selector fragility lives.

The parsers should reference [`obsidian-kindle-plugin`'s parser](https://github.com/hadynz/obsidian-kindle-plugin/tree/main/src/scraper) at implementation time for canonical selectors. We don't copy their TypeScript code; we mirror their selector strategy in BeautifulSoup-style Python.

**Files:**
- Modify: `src/decafclaw/skills/kindle/tools.py` — add `BookSummary` and `HighlightEntry` dataclasses; add `_parse_books_list(html: str) -> list[BookSummary]` and `_parse_highlights(html: str) -> list[HighlightEntry]`. New stdlib imports: `html.parser` (or use third-party `lxml` / `bs4` if already a dep).
- Modify: `pyproject.toml` — add `"beautifulsoup4>=4.12.0"` (and `"lxml>=5.0"` as the parser backend) to `dependencies` IF the implementer confirms BS4 isn't already pulled in transitively. Use a stdlib-only `html.parser`-based parser only if BS4 would be the sole dependency-pull justification; BS4 is the standard pick for this kind of scraping and the implementer should add it.
- Test: `tests/test_kindle_skill.py` — extend.
- Test fixtures:
  - Create: `tests/fixtures/kindle/books_list.html` — minimal HTML containing 2 books (`<div class="kp-notebook-library-each-book" id="ASIN1"><h2 class="kp-notebook-searchable">Title 1</h2><p>by Author 1</p>...`). Mirror the actual `read.amazon.com/notebook` library DOM. The implementer should derive selectors from obsidian-kindle-plugin's `scrapeBooks.ts`-equivalent.
  - Create: `tests/fixtures/kindle/highlights.html` — minimal HTML with 3 highlights for one book, varying colors and one with a user note. Capture from obsidian-kindle-plugin's `scrapeHighlights.ts`-equivalent.
  - Capture caveat: if the synthetic fixtures don't match real Amazon HTML at implementation time, the implementer should replace them with redacted-from-real captures and update the parsers accordingly. **Both parsers should be re-validated against a real fixture before merge** (see Verification — manual).

**Key changes:**

```python
from dataclasses import dataclass


@dataclass
class BookSummary:
    asin: str
    title: str
    author: str
    cover_url: str = ""
    last_annotation_date: str = ""  # ISO-8601, may be empty if Amazon doesn't surface it


@dataclass
class HighlightEntry:
    annotation_id: str          # stable Amazon annotation ID
    location: str               # "Location 412" or "Page 33", string-preserved as Amazon renders it
    color: str                  # "yellow" | "blue" | "pink" | "orange" | ""
    text: str                   # the highlighted passage
    note: str = ""              # optional user note attached to the highlight


def _parse_books_list(html: str) -> list[BookSummary]:
    """Parse the read.amazon.com/notebook library page. Returns ordered list of books."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    books: list[BookSummary] = []
    for node in soup.select(".kp-notebook-library-each-book"):
        asin = node.get("id", "").strip()
        if not asin:
            continue
        title_node = node.select_one("h2.kp-notebook-searchable")
        author_node = node.select_one("p.kp-notebook-searchable")
        cover_node = node.select_one("img")
        title = (title_node.get_text(strip=True) if title_node else "").strip()
        author = (author_node.get_text(strip=True) if author_node else "").strip()
        if author.lower().startswith("by "):
            author = author[3:]
        cover_url = (cover_node.get("src") if cover_node else "") or ""
        books.append(BookSummary(asin=asin, title=title, author=author, cover_url=cover_url))
    return books


def _parse_highlights(html: str) -> list[HighlightEntry]:
    """Parse a single book's highlights page. Returns highlights in document order."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    entries: list[HighlightEntry] = []
    for row in soup.select(".kp-notebook-row-separator"):
        annotation_id = row.get("id", "").strip()
        if not annotation_id:
            continue
        text_node = row.select_one("#highlight")
        note_node = row.select_one("#note")
        location_node = row.select_one("#kp-annotation-location")
        # Color comes from a hidden input or a class — encode the lookup at impl time:
        color_node = row.select_one("input.kp-notebook-color")
        color = (color_node.get("value") if color_node else "").lower() if color_node else ""

        entries.append(HighlightEntry(
            annotation_id=annotation_id,
            location=(location_node.get_text(strip=True) if location_node else ""),
            color=color,
            text=(text_node.get_text(strip=True) if text_node else ""),
            note=(note_node.get_text(strip=True) if note_node else ""),
        ))
    return entries
```

**Verification — automated:**
- [x] `make check` passes
- [x] `test_parse_books_list_basic` — 4 books from fixture incl. ASIN B078VWDNKT verified.
- [x] `test_parse_books_list_empty` — empty HTML returns `[]`.
- [x] `test_parse_highlights_basic` — 6 highlights parsed, colors extracted.
- [x] `test_parse_highlights_no_note` — note presence/absence distinguished.
- [x] `test_parse_highlights_empty` — empty HTML returns `[]`.
- [x] `make test` passes (2445 tests; +5 from Phase 2's 2440).

**Adaptations from sketch:** location is in `<input id="kp-annotation-location" value="N">`; author prefix can be `"By: "`; annotation filter uses `#annotationHighlightHeader` presence; `span#highlight` (not bare `#highlight`).

**Verification — manual:**
- [ ] Capture a real `read.amazon.com/notebook` page using your cookies (e.g., `curl -b cookies.txt -A '<UA>' 'https://read.amazon.com/notebook' > /tmp/real_books.html`). Run the parser against it via `uv run python -c "from decafclaw.skills.kindle.tools import _parse_books_list; print(len(_parse_books_list(open('/tmp/real_books.html').read())))"`. Verify the count matches Amazon's UI. If not, reconcile selectors and update fixtures.
- [ ] Same exercise for a single book's highlights page.

---

## Phase 4: Per-book page upsert logic [x] DONE — commits `3124d17` + cleanup `6e17ecb`

Pure-Python function: take an existing page's markdown (or empty), a fresh list of highlights from Amazon, a `BookSummary`, and a `now` timestamp; produce the new page markdown. Encodes the agent-owned upsert + archive model the spec defines.

No I/O. Tested via fixture markdown round-trips.

**Files:**
- Modify: `src/decafclaw/skills/kindle/tools.py` — add helpers `_slug(title) -> str`, `_book_page_path(skill_config, book) -> str`, `_format_highlight_section(entry, archived_date="") -> str`, `_archive_format_block(block, archived_date) -> str`, `_parse_existing_archived(body) -> dict[str, str]`, and the main `_upsert_book_page(existing_md, fresh_highlights, book, now, archive_deleted=True) -> str`. Rendering happens inside `_upsert_book_page` — no separate `_render_book_page`. Imports `re`, `datetime` already present after Phase 2.
- Test: `tests/test_kindle_skill.py` — extend.

**Key changes:**

```python
import re
from datetime import datetime, timezone


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(title: str, max_len: int = 60) -> str:
    """Lowercase + ASCII-fold-ish slug for page filenames."""
    s = title.lower().strip()
    s = _SLUG_RE.sub("-", s).strip("-")
    return s[:max_len] or "untitled"


def _book_page_path(skill_config: "SkillConfig", book: BookSummary) -> str:
    """Return the vault-relative page path for a book."""
    subfolder = skill_config.vault_subfolder.rstrip("/")
    return f"{subfolder}/{book.asin}-{_slug(book.title)}.md"


def _format_highlight_section(entry: HighlightEntry, archived_date: str = "") -> str:
    """Render one highlight (or archived highlight) as markdown. Marker first."""
    marker = f"<!-- annotation-id: {entry.annotation_id} -->"
    color = f" · *{entry.color}*" if entry.color else ""
    if archived_date:
        # Strikethrough form
        header = f"~~**{entry.location}**{color}~~ *(archived {archived_date})*"
        body = f"> ~~{entry.text}~~"
    else:
        header = f"**{entry.location}**{color}"
        body = f"> {entry.text}"
    out = [marker, header, "", body]
    if entry.note:
        out.append("")
        out.append(f"*Note:* {entry.note}")
    return "\n".join(out)


def _parse_existing_archived(body: str) -> dict[str, str]:
    """Extract a {annotation_id: archive_date} map from the existing ## Archived section.

    Lets us preserve the original archived-on date instead of stamping today's
    date over it on every re-sync.
    """
    out: dict[str, str] = {}
    # Find each annotation-id marker that appears anywhere after `## Archived`.
    marker = body.find("## Archived")
    if marker < 0:
        return out
    archived_body = body[marker:]
    # Match: <!-- annotation-id: X --> followed (eventually) by (archived YYYY-MM-DD)
    for m in re.finditer(
        r"<!-- annotation-id: (\S+) -->.*?\(archived (\d{4}-\d{2}-\d{2})\)",
        archived_body, flags=re.DOTALL,
    ):
        out[m.group(1)] = m.group(2)
    return out


def _upsert_book_page(
    existing_md: str,
    fresh_highlights: list[HighlightEntry],
    book: BookSummary,
    now: datetime,
    archive_deleted: bool = True,
) -> str:
    """Merge fresh highlights into an existing per-book page (or fresh-create).

    Algorithm:
      1. Parse existing frontmatter + body. If empty, treat as new page.
      2. Build {annotation_id: HighlightEntry} for fresh.
      3. Extract existing archived annotation-id → archive_date map.
      4. Walk fresh in document order → emit ## Highlights section.
      5. Find existing annotation-ids that are NOT in fresh → if archive_deleted,
         emit in ## Archived with their original archive_date (or today's date if
         this is their first sync to the archived state).
      6. Render frontmatter with updated counts + last_synced.
    """
    from decafclaw.frontmatter import parse_frontmatter, serialize_frontmatter

    metadata, body = parse_frontmatter(existing_md or "")
    archived_dates = _parse_existing_archived(body)

    # Index fresh by annotation_id
    fresh_ids = {h.annotation_id for h in fresh_highlights}

    # Existing IDs from the previous Highlights section (so we know what dropped out).
    existing_highlight_ids: set[str] = set()
    # Strip everything after ## Archived for the highlight scan.
    archive_split = body.find("## Archived")
    highlight_body = body[:archive_split] if archive_split >= 0 else body
    for m in re.finditer(r"<!-- annotation-id: (\S+) -->", highlight_body):
        existing_highlight_ids.add(m.group(1))

    # Highlights to archive on this run (previously current, now missing from fresh).
    newly_archived = existing_highlight_ids - fresh_ids

    # Build the new Highlights section
    sections: list[str] = ["## Highlights", ""]
    for entry in fresh_highlights:
        sections.append(_format_highlight_section(entry))
        sections.append("")  # blank line between

    # Build the new Archived section (if any)
    today = now.date().isoformat()
    archived_entries: list[HighlightEntry] = []
    # We need the HighlightEntry data for previously-archived items too, but we
    # only have the markers in the existing body — we can't recover the text/color
    # because the page itself is the source of truth. Re-parse the existing
    # archived section for the full text per ID.
    archived_blocks: dict[str, str] = {}
    if archive_split >= 0:
        archived_md = body[archive_split:]
        # Split on annotation-id markers and keep them.
        parts = re.split(r"(<!-- annotation-id: \S+ -->)", archived_md)
        for i in range(1, len(parts) - 1, 2):
            marker_line = parts[i]
            block = (marker_line + parts[i + 1]).rstrip() + "\n"
            id_match = re.match(r"<!-- annotation-id: (\S+) -->", marker_line)
            if id_match:
                archived_blocks[id_match.group(1)] = block

    archived_section: list[str] = []
    if archive_deleted:
        # Preserve previously-archived items in original order (sorted by date for determinism)
        for aid in sorted(archived_blocks.keys()):
            archived_section.append(archived_blocks[aid].rstrip("\n"))
            archived_section.append("")
        # Add newly-archived (from previously-current items). For these we have
        # the most recent text/color from the prior Highlights section — extract
        # the rendered block from highlight_body to preserve fidelity.
        for aid in newly_archived:
            block_match = re.search(
                rf"(<!-- annotation-id: {re.escape(aid)} -->.*?)(?=<!-- annotation-id: |\Z)",
                highlight_body, flags=re.DOTALL,
            )
            if block_match is None:
                continue
            original_block = block_match.group(1).rstrip()
            # Convert to archived form: wrap header/body in strikethrough, append date.
            archived_form = _archive_format_block(original_block, today)
            archived_section.append(archived_form)
            archived_section.append("")

    full_sections = sections
    if archived_section:
        full_sections.append("## Archived")
        full_sections.append("")
        full_sections.extend(archived_section)

    new_body = "\n".join(full_sections).rstrip() + "\n"

    # Update metadata
    metadata = dict(metadata)  # avoid mutating input
    metadata["asin"] = book.asin
    metadata["title"] = book.title
    metadata["author"] = book.author
    if book.cover_url:
        metadata["cover_url"] = book.cover_url
    metadata["tags"] = sorted(set((metadata.get("tags") or []) + ["ingested", "kindle"]))
    metadata["highlight_count"] = len(fresh_highlights)
    metadata["archived_count"] = (
        len(archived_blocks) + len(newly_archived) if archive_deleted else 0
    )
    metadata["last_synced"] = now.replace(microsecond=0).isoformat()
    # Vault embedding-retrieval fields: set sensible defaults if absent so the
    # page joins the semantic index meaningfully. User edits are preserved
    # via setdefault (we never overwrite existing summary/keywords/importance).
    metadata.setdefault(
        "summary",
        f"Kindle highlights from {book.title} by {book.author}".strip(),
    )
    metadata.setdefault("keywords", [])
    metadata.setdefault("importance", 0.5)

    return serialize_frontmatter(metadata, new_body)


def _archive_format_block(highlight_block: str, archived_date: str) -> str:
    """Convert a current-form rendered highlight block to its archived form."""
    # Replace **Location** with ~~**Location**~~ on the header line; same for body `> text`.
    # Conservative regex on the first occurrences only.
    out = re.sub(r"^\*\*(.+?)\*\*(.*?)$", r"~~**\1**\2~~ *(archived %s)*" % archived_date, highlight_block, count=1, flags=re.MULTILINE)
    out = re.sub(r"^> (.+)$", r"> ~~\1~~", out, count=1, flags=re.MULTILINE)
    return out
```

Archived entries round-trip *textually* from the existing page (Amazon won't return them again, so the page is their only home). The regex extraction in `_parse_existing_archived` + the block-extraction loop above is sufficient — do not introduce a parallel JSON sidecar to store archived state.

**Verification — automated:**
- [x] `make check` passes
- [x] `test_upsert_new_page` / `_updates_existing_highlight` / `_inserts_new_highlight` / `_archives_deleted`
- [x] `test_upsert_preserves_existing_archived_date` / `_archive_disabled_drops_deleted` / `_frontmatter_fields`
- [x] `test_slug_safe_for_filenames`
- [x] `test_upsert_empty_metadata_summary` (added in cleanup commit — regression for empty title+author edge case)
- [x] `make test` passes (2454 tests)

**Cleanup commit notes:** Removed unused `_parse_existing_archived` helper; fixed default-summary edge case for empty book metadata.

**Verification — manual (deferred to end-of-branch live-smoke batch):**
- [ ] Hand-eyeball one rendered page from a unit test to confirm readability — paste into Obsidian, confirm strikethrough renders correctly and `<!-- annotation-id -->` markers don't visually break anything.

---

## Phase 5: HTTP fetch tools — `kindle_list_books` and `kindle_fetch_highlights` [x] DONE — commit `400807e`

Two low-level async tool functions that combine session-creation + URL fetch + parser. Each returns a `ToolResult` with structured `data` for downstream tools. This is the network boundary — the only place that does I/O against Amazon.

**Playwright fallback hook:** `_fetch_books_list_html` and `_fetch_book_highlights_html` are the only functions that touch the network. If `curl_cffi` stops working (Amazon escalates detection), the fallback is implemented by swapping the bodies of these two helpers to use Playwright + a persistent profile. Everything else (parsing, upsert, vault writes, tool defs) stays unchanged. Keep these two functions thin; don't leak `curl_cffi`-specific types into their signatures.

**Files:**
- Modify: `src/decafclaw/skills/kindle/tools.py` — add `async def _fetch_books_list_html(session, domain) -> str`, `async def _fetch_book_highlights_html(session, asin, domain) -> str`, `async def kindle_list_books(ctx) -> ToolResult`, `async def kindle_fetch_highlights(ctx, asin) -> ToolResult`. Update `TOOLS` and `TOOL_DEFINITIONS`. Import `decafclaw.media.ToolResult`.
- Test: `tests/test_kindle_skill.py` — extend with mocked-session tests.

**Key changes:**

```python
from decafclaw.media import ToolResult


def _notebook_url(domain: str, asin: str | None = None) -> str:
    """URL for the books list (no asin) or a specific book's highlights (with asin).

    NOTE: The exact path for a single book's highlights page changes occasionally
    on read.amazon.com. The implementer must verify by capturing a real
    cookies-auth'd browser request and matching the URL. Document the captured
    URL in a comment when implementing. As a starting hypothesis:
      books list: https://read.amazon.{tld}/notebook
      book page:  https://read.amazon.{tld}/notebook?library=light-library&asin={asin}&contentLimitState=&
    """
    base = f"https://read.amazon.{domain.removeprefix('amazon.')}/notebook"
    if asin is None:
        return base
    return f"{base}?asin={asin}&contentLimitState=&"


async def _fetch_books_list_html(session: AsyncSession, domain: str) -> str:
    response = await session.get(_notebook_url(domain))
    response.raise_for_status()
    return response.text


async def _fetch_book_highlights_html(
    session: AsyncSession, asin: str, domain: str
) -> str:
    response = await session.get(_notebook_url(domain, asin))
    response.raise_for_status()
    return response.text


async def kindle_list_books(ctx) -> ToolResult:
    """List books with highlights from read.amazon.com/notebook.

    Reads cookies from the configured path; uses curl_cffi with Chrome
    impersonation. Returns one entry per book (asin, title, author, cover_url).
    """
    if _skill_config is None:
        return ToolResult(text="[error: kindle skill not initialized]")
    cookies_path = _resolve_cookies_path(_config, _skill_config)
    try:
        jar = _load_cookie_jar(cookies_path)
    except FileNotFoundError as exc:
        return ToolResult(text=f"[error: {exc}]")

    age_days = _cookie_file_age_days(cookies_path)
    warn = age_days > _skill_config.cookies_warn_after_days

    async with _make_session(jar, _skill_config.user_agent) as session:
        try:
            html = await _fetch_books_list_html(session, _skill_config.amazon_domain)
        except Exception as exc:  # noqa: BLE001 — network boundary
            return ToolResult(text=f"[error: failed to fetch books list: {exc}]")

    books = _parse_books_list(html)
    summary = f"Found {len(books)} book(s) with highlights."
    if warn:
        summary += f" Warning: cookies file is {int(age_days)} days old; consider re-exporting."
    return ToolResult(text=summary, data={
        "books": [book_to_dict(b) for b in books],
        "cookies_age_days": int(age_days),
    })


async def kindle_fetch_highlights(ctx, asin: str) -> ToolResult:
    """Fetch all highlights for a single book by ASIN."""
    if _skill_config is None:
        return ToolResult(text="[error: kindle skill not initialized]")
    cookies_path = _resolve_cookies_path(_config, _skill_config)
    try:
        jar = _load_cookie_jar(cookies_path)
    except FileNotFoundError as exc:
        return ToolResult(text=f"[error: {exc}]")
    async with _make_session(jar, _skill_config.user_agent) as session:
        try:
            html = await _fetch_book_highlights_html(
                session, asin, _skill_config.amazon_domain
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult(text=f"[error: failed to fetch highlights for {asin}: {exc}]")
    entries = _parse_highlights(html)
    return ToolResult(
        text=f"Found {len(entries)} highlight(s) for {asin}.",
        data={"highlights": [highlight_to_dict(h) for h in entries], "asin": asin},
    )


def book_to_dict(b: BookSummary) -> dict:
    return {"asin": b.asin, "title": b.title, "author": b.author, "cover_url": b.cover_url}


def highlight_to_dict(h: HighlightEntry) -> dict:
    return {
        "annotation_id": h.annotation_id,
        "location": h.location,
        "color": h.color,
        "text": h.text,
        "note": h.note,
    }


# Update TOOLS + TOOL_DEFINITIONS
TOOLS = {
    "kindle_list_books": kindle_list_books,
    "kindle_fetch_highlights": kindle_fetch_highlights,
}

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "kindle_list_books",
            "description": (
                "List books with highlights from your Amazon Kindle notebook "
                "(read.amazon.com/notebook). Requires a valid cookies.txt at the "
                "configured path. Returns asin, title, author, cover_url per book."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "kindle_fetch_highlights",
            "description": (
                "Fetch all highlights for a single book by ASIN from read.amazon.com/notebook. "
                "Returns annotation_id, location, color, text, note per highlight."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "asin": {"type": "string", "description": "Amazon ASIN of the book."}
                },
                "required": ["asin"],
            },
        },
    },
]
```

**Verification — automated:**
- [x] `make check` passes
- [x] `test_kindle_list_books_happy_path` / `_missing_cookies` / `_warns_old_cookies`
- [x] `test_kindle_fetch_highlights_happy_path` / `_http_error`
- [x] `test_kindle_tools_register_via_skill_loader` — both new tools registered
- [x] `make test` passes (2460 tests; +6 from Phase 4's 2454).

**Verification — manual:**
- [ ] One end-to-end live test against real Amazon, run from the worktree, **before** moving on:
  `uv run python -c "import asyncio; from decafclaw.config import load_config; from decafclaw.tools.skill_tools import activate_skill_internal; from decafclaw.context import make_context; from decafclaw.skills.kindle.tools import kindle_list_books, init, SkillConfig; cfg = load_config(); init(cfg, SkillConfig()); print(asyncio.run(kindle_list_books(None)))"` (sketch only — the implementer adapts to whatever bootstrap works). Expect: real book list. If Amazon returns a captcha page / 503 / login redirect, curl_cffi is being blocked → escalate (try a different impersonate target like `chrome120`, or pivot to Playwright per the fallback decision).

---

## Phase 6: `kindle_sync_book` — single-book end-to-end (spec Phase 1) [x] DONE — commit `613b152`

Compose Phase 4 + Phase 5: fetch one book's highlights, read the existing vault page (if any), run upsert, write back via `vault_write`. The first tool a user can call to actually see the skill do something useful.

**Files:**
- Modify: `src/decafclaw/skills/kindle/tools.py` — add `async def kindle_sync_book(ctx, asin: str, title: str = "") -> ToolResult` and update `TOOLS`/`TOOL_DEFINITIONS`. Imports: `decafclaw.skills.vault.tools.tool_vault_read, tool_vault_write` (or call them via the tool registry — see implementation note).
- Test: `tests/test_kindle_skill.py` — extend.

**Implementation note on calling vault tools:** The cleanest call into the vault is via direct function import — `tool_vault_write(ctx, page=..., content=...)`. This skips the tool-registry dispatch (timeouts, etc.) and treats it as an internal helper. Mirror this pattern; do not invent a new vault-write abstraction.

If `tool_vault_read` returns `[error: ...]` (page doesn't exist yet), treat as empty `existing_md=""` and continue.

**Key changes:**

```python
async def kindle_sync_book(ctx, asin: str, title: str = "") -> ToolResult:
    """Fetch highlights for a single book and upsert into its vault page.

    Returns a summary of new/edited/archived counts.
    """
    if _skill_config is None:
        return ToolResult(text="[error: kindle skill not initialized]")

    # First, get the BookSummary for this ASIN (need title/author/cover for frontmatter).
    list_result = await kindle_list_books(ctx)
    if list_result.text.startswith("[error:"):
        return list_result
    books = [BookSummary(**b) for b in list_result.data["books"]]
    matched = next((b for b in books if b.asin == asin), None)
    if matched is None:
        return ToolResult(text=f"[error: ASIN {asin!r} not found in Kindle library]")

    # Fetch highlights
    h_result = await kindle_fetch_highlights(ctx, asin)
    if h_result.text.startswith("[error:"):
        return h_result
    fresh = [HighlightEntry(**h) for h in h_result.data["highlights"]]

    # Read existing page (if any)
    from decafclaw.skills.vault.tools import tool_vault_read, tool_vault_write

    page = _book_page_path(_skill_config, matched)
    read_result = await tool_vault_read(ctx, page)
    existing_md = ""
    if isinstance(read_result, ToolResult) and not read_result.text.startswith("[error:"):
        existing_md = read_result.text

    now = datetime.now(timezone.utc)
    new_md = _upsert_book_page(
        existing_md, fresh, matched, now,
        archive_deleted=_skill_config.archive_deleted,
    )

    # Count diff for the return summary
    existing_ids = set(re.findall(r"<!-- annotation-id: (\S+) -->", existing_md))
    fresh_ids = {h.annotation_id for h in fresh}
    new_count = len(fresh_ids - existing_ids)
    archived_count = len(existing_ids - fresh_ids)
    edited_count = sum(1 for h in fresh if h.annotation_id in existing_ids)

    write_result = await tool_vault_write(ctx, page=page, content=new_md)
    if isinstance(write_result, ToolResult) and write_result.text.startswith("[error:"):
        return write_result

    summary = (
        f"Synced '{matched.title}' ({asin}): "
        f"{new_count} new, {edited_count} re-checked, {archived_count} archived."
    )
    return ToolResult(text=summary, data={
        "asin": asin,
        "page": page,
        "new_count": new_count,
        "archived_count": archived_count,
        "highlight_count": len(fresh),
    })


# Extend TOOLS and TOOL_DEFINITIONS (append to the dict/list from Phase 5):
TOOLS["kindle_sync_book"] = kindle_sync_book
TOOL_DEFINITIONS.append({
    "type": "function",
    "function": {
        "name": "kindle_sync_book",
        "description": (
            "Sync highlights for a single Kindle book by ASIN into its vault page "
            "under agent/pages/kindle/. Upserts new/edited highlights and moves "
            "any Amazon-side deletions into the page's ## Archived section."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "asin": {"type": "string", "description": "Amazon ASIN of the book to sync."},
                "title": {
                    "type": "string",
                    "description": "Optional title hint, used for logging; not required.",
                },
            },
            "required": ["asin"],
        },
    },
    "timeout": None,
})
```

**Verification — automated:**
- [x] `make check` passes
- [x] `test_kindle_sync_book_first_run` / `_idempotent` / `_archives_deleted` / `_unknown_asin`
- [x] `make test` passes (2464 tests; +4 from Phase 5's 2460).

**Verification — manual:**
- [ ] One live sync against real Amazon for a small book (low highlight count) from the worktree: invoke `kindle_sync_book` directly via a Python REPL (same bootstrap as Phase 5 manual step). Expect: a real vault page lands at `agent/pages/kindle/<asin>-<slug>.md` with sensible frontmatter and Highlights section.

---

## Phase 7: `kindle_sync_all` + per-run journal entry (spec Phase 2) [x] DONE — commit `69e5e43`

Multi-book sync orchestrator. Rate-limited via `sync_min_interval_seconds` between book fetches. Appends one `vault_journal_append` entry per run with structured summary.

**Files:**
- Modify: `src/decafclaw/skills/kindle/tools.py` — add `async def kindle_sync_all(ctx) -> ToolResult`; update `TOOLS`/`TOOL_DEFINITIONS`. Add `"timeout": None` to the `kindle_sync_all` and `kindle_sync_book` entries in `TOOL_DEFINITIONS` (multi-book sync at 60s intervals will exceed the 180s default by design). Import `asyncio`.
- Test: `tests/test_kindle_skill.py` — extend.

**Key changes:**

```python
import asyncio


async def kindle_sync_all(ctx) -> ToolResult:
    """Sync all books in the Kindle library that have highlights. Rate-limited.

    Appends a journal entry summarizing new/edited/archived counts across the run.
    """
    if _skill_config is None:
        return ToolResult(text="[error: kindle skill not initialized]")

    list_result = await kindle_list_books(ctx)
    if list_result.text.startswith("[error:"):
        return list_result
    books = [BookSummary(**b) for b in list_result.data["books"]]

    results = []
    new_total = 0
    archived_total = 0
    failures = []
    for i, book in enumerate(books):
        if i > 0:
            await asyncio.sleep(_skill_config.sync_min_interval_seconds)
        try:
            r = await kindle_sync_book(ctx, asin=book.asin)
        except Exception as exc:  # noqa: BLE001
            log.warning("kindle_sync_book(%s) failed: %s", book.asin, exc)
            failures.append((book.asin, str(exc)))
            continue
        if r.text.startswith("[error:"):
            failures.append((book.asin, r.text))
            continue
        results.append(r.data)
        new_total += r.data.get("new_count", 0)
        archived_total += r.data.get("archived_count", 0)

    # Journal entry — every run, even with no changes
    from decafclaw.skills.vault.tools import tool_vault_journal_append

    body = (
        f"**Kindle sync run**\n\n"
        f"- Books processed: {len(results)} / {len(books)}\n"
        f"- New highlights: {new_total}\n"
        f"- Archived (deleted on Amazon): {archived_total}\n"
        f"- Failures: {len(failures)}\n"
    )
    if failures:
        body += "\n**Failures:**\n"
        for asin, msg in failures:
            body += f"- {asin}: {msg}\n"
    await tool_vault_journal_append(ctx, tags=["ingested", "kindle"], content=body)

    summary = (
        f"Synced {len(results)}/{len(books)} books: "
        f"{new_total} new highlights, {archived_total} archived, "
        f"{len(failures)} failures."
    )
    return ToolResult(text=summary, data={
        "books_synced": len(results),
        "books_total": len(books),
        "new_total": new_total,
        "archived_total": archived_total,
        "failures": failures,
    })


# Extend TOOLS and TOOL_DEFINITIONS:
TOOLS["kindle_sync_all"] = kindle_sync_all
TOOL_DEFINITIONS.append({
    "type": "function",
    "function": {
        "name": "kindle_sync_all",
        "description": (
            "Sync every book with highlights in your Kindle library. "
            "Rate-limited by sync_min_interval_seconds (default 60s) between books. "
            "Writes a per-run journal entry tagged [ingested, kindle] summarizing "
            "new/edited/archived counts and any failures."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
    "timeout": None,
})
```

**Important test fixture note:** The tests for this phase must patch `asyncio.sleep` to a no-op (`asyncio.sleep = AsyncMock()` or patch the module attribute). Otherwise a 3-book sync test would take 2 minutes. Also patch `vault_journal_append`'s embedding side effect or accept its no-op behavior under a tmp `vault_root` per `CLAUDE.md`'s test-speed discipline.

**Verification — automated:**
- [ ] `make check` passes (lint + typecheck)
- [ ] `tests/test_kindle_skill.py::test_kindle_sync_all_happy_path` — 3 books each with 1 highlight; patch `asyncio.sleep` to a no-op; assert `new_total=3`, `books_synced=3`, journal entry written with the expected body.
- [ ] `tests/test_kindle_skill.py::test_kindle_sync_all_partial_failure` — middle book's fetch_highlights raises; assert that book is in `failures`, other two complete, journal entry mentions failures.
- [ ] `tests/test_kindle_skill.py::test_kindle_sync_all_rate_limit` — `sync_min_interval_seconds=10`; patch `asyncio.sleep` to record calls; with 3 books, assert `sleep` is called twice with `10`.
- [ ] `make test` passes overall. `pytest --durations=10` shows no kindle test in the slow tail.

**Verification — manual:**
- [ ] One live `kindle_sync_all` against real Amazon from the worktree (small library only — if you have >10 books, expect this to take >10 minutes due to the rate limit). Expect: per-book pages all written, one journal entry under `agent/journal/`.

---

## Phase 8: User-invokable command, SKILL.md body, scheduled trigger (spec Phase 3 — already covered by upsert; this wires the cron) [x] DONE — commits `c042036` + gate-fix `8f08ed2`

Flesh out `SKILL.md` so:
- `!kindle-sync` and `!kindle-sync <asin-or-title>` work as on-demand interactive commands
- Add `schedule: "0 5 * * *"` frontmatter so the bundled-skill discovery picks it up
- Add the gate logic in the prompt body: scheduled invocations check `enabled` + cookies-file presence; return silently if not ready

**Files:**
- Modify: `src/decafclaw/skills/kindle/SKILL.md` — replace placeholder body with the real prompt; add `schedule: "0 5 * * *"`; populate `allowed-tools: kindle_list_books, kindle_fetch_highlights, kindle_sync_book, kindle_sync_all, vault_read, vault_list, vault_write, vault_journal_append, current_time`; add `argument-hint: "[asin-or-title]"`. Update `name: kindle` and add a useful `description:` for the catalog.
- Test: `tests/test_kindle_skill.py` — add discovery + frontmatter tests.

**Key changes — SKILL.md body sketch:**

```markdown
---
name: kindle
description: Sync highlights & notes from your Kindle library (read.amazon.com/notebook) into per-book vault pages.
schedule: "0 5 * * *"
user-invocable: true
context: inline
argument-hint: "[asin-or-title]"
allowed-tools: kindle_list_books, kindle_fetch_highlights, kindle_sync_book, kindle_sync_all, vault_read, vault_list, vault_write, vault_journal_append, current_time
required-skills: [kindle]
---

# Kindle sync

You sync highlights & notes from `read.amazon.com/notebook` into per-book vault pages under `agent/pages/kindle/`.

## When to run

This skill runs in two contexts:

1. **Scheduled (daily 5am UTC)** — full library sync. Before doing anything: read the skill config. If `enabled` is False, return immediately with the single-line message `kindle skill disabled; skipping`. Don't do any fetches, don't write any journal entry. Same if the cookies file is missing (a `kindle_list_books` call returns `[error: Kindle cookies file not found ...]`).

2. **User-invokable (`!kindle-sync` / `/kindle-sync`)** — on-demand. The `enabled` gate does NOT apply here; the user explicitly asked. Still requires cookies.

## Argument parsing

Argument: `$ARGUMENTS`

- **Empty** (`!kindle-sync`) — call `kindle_sync_all`. Summarize the result.
- **Non-empty** (`!kindle-sync <arg>`) — single-book mode:
  1. Call `kindle_list_books` to get the library.
  2. If `<arg>` looks like an ASIN (10 alphanumeric chars, all-caps), look it up directly.
  3. Otherwise, treat `<arg>` as a title substring. Lowercase-substring match against each book's title. If exactly one matches, use its ASIN. If multiple match, return a numbered list and ask the user to re-invoke with the ASIN. If zero match, return `No book matching '<arg>' in your Kindle library`.
  4. Call `kindle_sync_book(asin=...)` and summarize.

## Notes

- The per-book page is fully agent-owned. **Do not** manually edit `agent/pages/kindle/*.md` — your edits will be overwritten on the next sync. Use a separate hand-curated page for cross-links and synthesis (e.g., `agent/pages/<book>-notes.md` that wiki-links to the agent page).
- If a fetch fails with a 401/403, your cookies have probably expired. Re-export `cookies.txt` from a logged-in browser session and place it at the configured path.
```

**Verification — automated:**
- [ ] `make check` passes (lint + typecheck)
- [ ] `tests/test_kindle_skill.py::test_skill_md_frontmatter_parses` — load `SKILL.md`, assert `schedule == "0 5 * * *"`, `user-invocable: true`, `allowed-tools` contains all 8 tools.
- [ ] `tests/test_kindle_skill.py::test_skill_discovered_as_scheduled` — call `discover_schedules` on a test config; assert kindle is in the returned list with `source == "bundled"` and matching schedule. **MUST patch `decafclaw.schedules.run_schedule_task` to a no-op for this test** per `CLAUDE.md` test-speed discipline (otherwise the bundled scheduled skills can fire real tool runs).
- [ ] `make test` passes overall.

**Verification — manual:**
- [ ] In the worktree, with cookies in place, run `make run` (interactive REPL mode) — type `!kindle-sync` and confirm the skill activates and runs against real Amazon. (If `make dev` is running in another worktree against the same Mattermost token, do NOT also start it here — single-bot rule per CLAUDE.md.)
- [ ] With cookies in place, change `enabled=False` in `data/{agent_id}/config.json`'s `skills.kindle` section, then trigger the scheduled body manually via `!kindle-sync` (still works — user-invocable bypasses gate). Then re-trigger via a one-off `croniter`-faked scheduled run, and confirm the body short-circuits with the disabled message.

---

## Phase 9: Documentation + key-files updates [x] DONE — commit `4dfe5f0`

Lock in the docs the spec touches.

**Files:**
- Create: `docs/kindle.md` — full skill documentation: setup (cookies export workflow, where to put the file, the warning threshold), config keys with defaults, the agent-owned vs synthesis page boundary, what to do when cookies expire, the on-demand vs scheduled distinction. Cross-link from this file to `docs/skills.md` (and reverse).
- Modify: `docs/index.md` — add a one-line entry for `docs/kindle.md`.
- Modify: `docs/skills.md` — if it lists bundled skills, add `kindle` to that list with a one-line description. (Read it first to confirm structure.)
- Modify: `CLAUDE.md` — in the "Skills (bundled)" line, add `kindle` to the comma-separated list: `skills/{vault,tabstack,dream,garden,project,claude_code,health,postmortem,ingest,background,mcp,newsletter,kindle}/`.

**Key changes:** Documentation only. No code logic.

**Verification — automated:**
- [ ] `make lint` passes (in case any code drifted)
- [ ] `make test` passes
- [ ] `grep -n kindle CLAUDE.md` — kindle appears in the bundled-skills list.
- [ ] `ls docs/kindle.md` — file exists.
- [ ] `grep -n kindle docs/index.md` — entry present.

**Verification — manual:**
- [ ] Read `docs/kindle.md` top-to-bottom. Confirm a new user could follow the setup instructions and land on a working install. Pay attention to the cookies-export step (it's the friction point).
- [ ] Walk through one fresh-install simulation in your head: clone repo, configure `skills.kindle.enabled=True`, drop cookies file, run `!kindle-sync`. Are there any silent failure modes the docs don't cover? If so, add them.
- [ ] Confirm the linked spec/plan/notes session docs in this dev session correctly describe the implementation that landed.
