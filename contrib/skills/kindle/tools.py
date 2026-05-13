"""Kindle bundled skill — periodic ingest of Kindle highlights & notes."""

import asyncio
import http.cookiejar
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup
from curl_cffi.requests import AsyncSession

from decafclaw.frontmatter import parse_frontmatter, serialize_frontmatter
from decafclaw.media import ToolResult
from decafclaw.skills.vault.tools import (
    tool_vault_journal_append,
    tool_vault_read,
    tool_vault_write,
)

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


# TOOLS and TOOL_DEFINITIONS are populated at the bottom of this module
# after the tool functions are defined.
TOOLS: dict = {}
TOOL_DEFINITIONS: list = []


# ---------------------------------------------------------------------------
# Auth substrate: cookie loading + curl_cffi session helper
# ---------------------------------------------------------------------------


def _resolve_cookies_path(config, skill_config: SkillConfig) -> Path:
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
    jar.load(ignore_discard=True, ignore_expires=True)
    return jar


def _cookie_file_age_days(path: Path) -> float:
    """Return how many days old the cookies file is, based on mtime."""
    return (datetime.now().timestamp() - path.stat().st_mtime) / 86400.0


def _make_session(
    cookie_jar: http.cookiejar.MozillaCookieJar, user_agent: str
) -> AsyncSession:
    """Construct a curl_cffi AsyncSession with cookies + Chrome impersonation."""
    return AsyncSession(
        impersonate="chrome131",
        headers={
            "User-Agent": user_agent,
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        },
        cookies=cookie_jar,
    )


# ---------------------------------------------------------------------------
# HTML parsers: books list + highlights
# ---------------------------------------------------------------------------

_HIGHLIGHT_COLORS = frozenset({"yellow", "blue", "pink", "orange"})


@dataclass
class BookSummary:
    asin: str
    title: str
    author: str
    cover_url: str = ""
    last_annotation_date: str = ""  # ISO-8601 or Amazon's date string; may be empty


@dataclass
class HighlightEntry:
    annotation_id: str  # stable Amazon annotation ID (base64-encoded string)
    location: str  # raw value from the location input, e.g. "166"
    color: str  # "yellow" | "blue" | "pink" | "orange" | ""
    text: str  # the highlighted passage
    note: str = ""  # optional user note attached to the highlight


def _parse_books_list(html: str) -> list[BookSummary]:
    """Parse the read.amazon.com/notebook library pane.

    Returns an ordered list of BookSummary objects, one per book entry found.
    Each book entry in the page has class ``kp-notebook-library-each-book`` and
    uses its ``id`` attribute as the ASIN.
    """
    soup = BeautifulSoup(html, "lxml")
    books: list[BookSummary] = []
    for node in soup.select(".kp-notebook-library-each-book"):
        asin = str(node.get("id") or "").strip()
        if not asin:
            continue
        title_node = node.select_one("h2.kp-notebook-searchable")
        author_node = node.select_one("p.kp-notebook-searchable")
        cover_node = node.select_one("img.kp-notebook-cover-image-border")
        if cover_node is None:
            cover_node = node.select_one("img")
        title = title_node.get_text(strip=True) if title_node else ""
        author = author_node.get_text(strip=True) if author_node else ""
        # Amazon renders "By: Author Name" — strip the prefix.
        if author.lower().startswith("by:"):
            author = author[3:].strip()
        elif author.lower().startswith("by "):
            author = author[3:].strip()
        cover_url = str(cover_node.get("src") or "") if cover_node else ""
        books.append(
            BookSummary(asin=asin, title=title, author=author, cover_url=cover_url)
        )
    return books


def _parse_highlights(html: str) -> list[HighlightEntry]:
    """Parse a book's highlights pane from read.amazon.com/notebook.

    Returns highlights in document order.  Each annotation row is identified by
    the presence of an ``#annotationHighlightHeader`` span, which is only present
    on genuine annotation entries (not wrapper divs or the empty-pane placeholder).
    """
    soup = BeautifulSoup(html, "lxml")
    entries: list[HighlightEntry] = []
    container = soup.select_one("#kp-notebook-annotations")
    if container is None:
        return entries

    for row in container.select("div[id]"):
        # Only process divs that have the annotation header — this reliably
        # distinguishes real annotation rows from wrapper/placeholder divs.
        if row.select_one("#annotationHighlightHeader") is None:
            continue
        annotation_id = str(row.get("id") or "").strip()
        if not annotation_id:
            continue

        # Location: stored as the value of a hidden <input id="kp-annotation-location">.
        loc_input = row.select_one("input#kp-annotation-location")
        location = str(loc_input.get("value") or "") if loc_input else ""

        # Color: look for a descendant div with class kp-notebook-highlight-<color>.
        color = ""
        for elem in row.select("[class]"):
            for cls in elem.get("class") or []:
                if cls.startswith("kp-notebook-highlight-"):
                    suffix = cls[len("kp-notebook-highlight-"):]
                    if suffix in _HIGHLIGHT_COLORS:
                        color = suffix
                        break
            if color:
                break

        # Highlight text: inside <span id="highlight"> (not the div with id="highlight-...").
        text_node = row.select_one("span#highlight")
        text = text_node.get_text(strip=True) if text_node else ""

        # Note text: inside <span id="note">.  Only capture if non-empty — the note
        # container exists on every row but is aok-hidden and empty when there's no note.
        note_node = row.select_one("span#note")
        note = note_node.get_text(strip=True) if note_node else ""

        entries.append(
            HighlightEntry(
                annotation_id=annotation_id,
                location=location,
                color=color,
                text=text,
                note=note,
            )
        )
    return entries


# ---------------------------------------------------------------------------
# Page upsert helpers
# ---------------------------------------------------------------------------

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(title: str, max_len: int = 60) -> str:
    """Lowercase + ASCII-fold-ish slug for page filenames."""
    s = title.lower().strip()
    s = _SLUG_RE.sub("-", s).strip("-")
    return s[:max_len] or "untitled"


def _book_page_path(skill_config: SkillConfig, book: BookSummary) -> str:
    """Return the vault-relative page path for a book."""
    subfolder = skill_config.vault_subfolder.rstrip("/")
    return f"{subfolder}/{book.asin}-{_slug(book.title)}.md"


def _format_highlight_section(entry: HighlightEntry, archived_date: str = "") -> str:
    """Render one highlight (or archived highlight) as markdown. Marker first."""
    marker = f"<!-- annotation-id: {entry.annotation_id} -->"
    color = f" · *{entry.color}*" if entry.color else ""
    if archived_date:
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


def _archive_format_block(highlight_block: str, archived_date: str) -> str:
    """Convert a current-form rendered highlight block to its archived form."""
    out = re.sub(
        r"^\*\*(.+?)\*\*(.*?)$",
        rf"~~**\1**\2~~ *(archived {archived_date})*",
        highlight_block,
        count=1,
        flags=re.MULTILINE,
    )
    out = re.sub(r"^> (.+)$", r"> ~~\1~~", out, count=1, flags=re.MULTILINE)
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
      1. Parse existing frontmatter + body.
      2. Detect existing annotation IDs (Highlights vs Archived sections).
      3. Walk fresh in document order → emit ## Highlights section.
      4. Newly-deleted (existing-not-in-fresh) → move to ## Archived with today's date.
      5. Previously-archived → preserve in ## Archived with their original date.
      6. Update frontmatter (counts, last_synced, etc.).
    """
    metadata, body = parse_frontmatter(existing_md or "")

    # Index fresh
    fresh_ids = {h.annotation_id for h in fresh_highlights}

    # Split body at "## Archived"
    archive_split = body.find("## Archived")
    highlight_body = body[:archive_split] if archive_split >= 0 else body

    existing_highlight_ids: set[str] = set()
    for m in re.finditer(r"<!-- annotation-id: (\S+) -->", highlight_body):
        existing_highlight_ids.add(m.group(1))

    newly_archived = existing_highlight_ids - fresh_ids

    # Render new Highlights section
    sections: list[str] = ["## Highlights", ""]
    for entry in fresh_highlights:
        sections.append(_format_highlight_section(entry))
        sections.append("")

    # Extract previously-archived blocks (preserve original date)
    archived_blocks: dict[str, str] = {}
    if archive_split >= 0:
        archived_md = body[archive_split:]
        parts = re.split(r"(<!-- annotation-id: \S+ -->)", archived_md)
        for i in range(1, len(parts) - 1, 2):
            marker_line = parts[i]
            block = (marker_line + parts[i + 1]).rstrip() + "\n"
            id_match = re.match(r"<!-- annotation-id: (\S+) -->", marker_line)
            if id_match:
                archived_blocks[id_match.group(1)] = block

    today = now.date().isoformat()
    archived_section: list[str] = []
    if archive_deleted:
        # Preserve previously-archived items
        for aid in sorted(archived_blocks.keys()):
            archived_section.append(archived_blocks[aid].rstrip("\n"))
            archived_section.append("")
        # Newly-archived: extract block from prior Highlights and convert
        for aid in sorted(newly_archived):
            block_match = re.search(
                rf"(<!-- annotation-id: {re.escape(aid)} -->.*?)(?=<!-- annotation-id: |\Z)",
                highlight_body,
                flags=re.DOTALL,
            )
            if block_match is None:
                continue
            original_block = block_match.group(1).rstrip()
            archived_form = _archive_format_block(original_block, today)
            archived_section.append(archived_form)
            archived_section.append("")

    full_sections = sections
    if archived_section:
        full_sections.append("## Archived")
        full_sections.append("")
        full_sections.extend(archived_section)

    new_body = "\n".join(full_sections).rstrip() + "\n"

    # Update metadata (avoid mutating the parsed input)
    metadata = dict(metadata)
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
    # Embedding-retrieval defaults: only set if absent (user edits preserved).
    parts = ["Kindle highlights"]
    if book.title:
        parts.append(f"from {book.title}")
    if book.author:
        parts.append(f"by {book.author}")
    metadata.setdefault("summary", " ".join(parts))
    metadata.setdefault("keywords", [])
    metadata.setdefault("importance", 0.5)

    return serialize_frontmatter(metadata, new_body)


# ---------------------------------------------------------------------------
# URL helpers + low-level network fetchers
# ---------------------------------------------------------------------------


def _notebook_url(domain: str, asin: str | None = None) -> str:
    """URL for the books list (no asin) or a specific book's highlights (with asin).

    Starting hypothesis:
      books list: https://read.amazon.{tld}/notebook
      book page:  https://read.amazon.{tld}/notebook?asin={asin}&contentLimitState=&
    Verify the exact URL against a real cookie-auth'd browser request before merging.
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


# ---------------------------------------------------------------------------
# Tool helper: dataclass → dict serializers
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Tool functions
# ---------------------------------------------------------------------------


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
    return ToolResult(
        text=summary,
        data={
            "books": [book_to_dict(b) for b in books],
            "cookies_age_days": int(age_days),
        },
    )


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


async def kindle_sync_book(ctx, asin: str, *, book: "BookSummary | None" = None) -> ToolResult:
    """Fetch highlights for a single book and upsert into its vault page.

    Returns a summary of new/edited/archived counts.

    The optional keyword-only ``book`` param accepts an already-fetched BookSummary.
    When provided, the library-list fetch is skipped (used by kindle_sync_all to
    avoid 2N+1 network calls).  Do NOT expose this in TOOL_DEFINITIONS — BookSummary
    is not JSON-serializable for the LLM.
    """
    if _skill_config is None:
        return ToolResult(text="[error: kindle skill not initialized]")

    if book is None:
        # Standalone call — look up the book in the library.
        list_result = await kindle_list_books(ctx)
        if list_result.text.startswith("[error:"):
            return list_result
        list_data = list_result.data or {}
        books = [BookSummary(**b) for b in list_data.get("books", [])]
        book = next((b for b in books if b.asin == asin), None)
        if book is None:
            return ToolResult(text=f"[error: ASIN {asin!r} not found in Kindle library]")

    matched = book

    # Fetch highlights
    h_result = await kindle_fetch_highlights(ctx, asin)
    if h_result.text.startswith("[error:"):
        return h_result
    h_data = h_result.data or {}
    fresh = [HighlightEntry(**h) for h in h_data.get("highlights", [])]

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
    # Restrict existing_ids to Highlights section only (match what _upsert_book_page does)
    # to avoid re-counting previously-archived entries on subsequent syncs.
    existing_highlights_body = (
        existing_md.split("## Archived")[0]
        if "## Archived" in existing_md
        else existing_md
    )
    existing_ids = set(re.findall(r"<!-- annotation-id: (\S+) -->", existing_highlights_body))
    fresh_ids = {h.annotation_id for h in fresh}
    new_count = len(fresh_ids - existing_ids)
    archived_count = len(existing_ids - fresh_ids) if _skill_config.archive_deleted else 0
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


async def kindle_sync_all(ctx) -> ToolResult:
    """Sync all books in the Kindle library that have highlights. Rate-limited.

    Appends a journal entry summarizing new/edited/archived counts across the run.
    """
    if _skill_config is None:
        return ToolResult(text="[error: kindle skill not initialized]")

    # Gate: scheduled invocations skip silently when enabled=False.
    # User-invocable invocations (ctx.task_mode != "scheduled") proceed regardless.
    if ctx.task_mode == "scheduled" and not _skill_config.enabled:
        return ToolResult(text="kindle skill disabled; skipping (set skills.kindle.enabled=true to opt in)")

    list_result = await kindle_list_books(ctx)
    if list_result.text.startswith("[error:"):
        return list_result
    list_data = list_result.data or {}
    books = [BookSummary(**b) for b in list_data.get("books", [])]

    results: list[dict] = []
    new_total = 0
    archived_total = 0
    failures: list[tuple[str, str]] = []
    for i, book in enumerate(books):
        if i > 0:
            await asyncio.sleep(_skill_config.sync_min_interval_seconds)
        try:
            # Pass the already-known BookSummary so kindle_sync_book skips the
            # redundant library-list fetch (reduces 2N+1 requests to N+1).
            r = await kindle_sync_book(ctx, asin=book.asin, book=book)
        except Exception as exc:  # noqa: BLE001 — isolation boundary for partial-failure handling
            log.warning("kindle_sync_book(%s) failed: %s", book.asin, exc)
            failures.append((book.asin, str(exc)))
            continue
        if r.text.startswith("[error:"):
            failures.append((book.asin, r.text))
            continue
        data = r.data or {}
        results.append(data)
        new_total += data.get("new_count", 0)
        archived_total += data.get("archived_count", 0)

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


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------

# Replace the placeholder TOOLS / TOOL_DEFINITIONS declared above.
TOOLS = {
    "kindle_list_books": kindle_list_books,
    "kindle_fetch_highlights": kindle_fetch_highlights,
    "kindle_sync_book": kindle_sync_book,
    "kindle_sync_all": kindle_sync_all,
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
        "timeout": None,
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
    {
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
                },
                "required": ["asin"],
            },
        },
        "timeout": None,
    },
    {
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
    },
]
