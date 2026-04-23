"""Vault tools — unified knowledge base operations.

Replaces the separate wiki and memory tool systems with a single vault
that supports curated pages, daily journal entries, and user content.
"""

import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from decafclaw.media import ToolResult
from decafclaw.skills.vault._sections import Document, _insert_into_doc

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _vault_root(config) -> Path:
    return config.vault_root


def _agent_dir(config) -> Path:
    return config.vault_agent_dir


def resolve_page(config, page: str, from_page: str | None = None) -> Path | None:
    """Resolve a page name to a file path within the vault.

    Resolution order:
    1. Exact path match (relative to vault root)
    2. Search all subdirectories for stem match, preferring closest to from_page
    Returns None if the page doesn't exist or the name is invalid.
    """
    if ".." in page or page.startswith("/"):
        return None
    # Strip .md suffix if the caller included it (prevents Foo.md.md)
    if page.endswith(".md"):
        page = page[:-3]
    vault = _vault_root(config).resolve()
    if not vault.is_dir():
        return None

    # Try direct path first
    direct = (vault / f"{page}.md").resolve()
    if direct.is_relative_to(vault) and direct.exists():
        return direct

    # Search subdirectories by comparing stems
    matches: list[Path] = []
    # The page param might be a bare name or a partial path — match on stem
    target_stem = Path(page).stem
    for path in vault.rglob("*.md"):
        if path.stem == target_stem and path.resolve().is_relative_to(vault):
            matches.append(path)

    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]

    # Multiple matches — sort by path for deterministic results,
    # then prefer closest to from_page's directory if provided
    matches.sort(key=lambda p: str(p))
    if from_page:
        from_dir = (vault / from_page).parent
        def _distance(p: Path) -> int:
            """Count path components between from_dir and p's parent."""
            try:
                rel = p.parent.relative_to(from_dir)
                return len(rel.parts)
            except ValueError:
                try:
                    rel = from_dir.relative_to(p.parent)
                    return len(rel.parts)
                except ValueError:
                    return 999
        matches.sort(key=_distance)

    return matches[0]


def _safe_write_path(config, page: str) -> Path | None:
    """Validate and return a safe write path within the vault root.

    Returns None for invalid names (empty, trailing slash, path traversal)
    or paths that would escape the vault directory.
    """
    if not isinstance(page, str):
        return None
    page = page.strip()
    if not page:
        return None
    if ".." in page or page.startswith("/") or page.endswith("/"):
        return None
    # Strip .md suffix if the caller included it (prevents Foo.md.md)
    if page.endswith(".md"):
        page = page[:-3]
    # After stripping, the final path component must still be a real name —
    # reject inputs like "" / "foo/" / ".md" that would resolve to a hidden
    # ".md" file with an empty stem.
    if not Path(page).name:
        return None
    vault = _vault_root(config).resolve()
    path = (vault / f"{page}.md").resolve()
    if not path.is_relative_to(vault):
        return None
    return path


def _is_in_agent_dir(config, path: Path) -> bool:
    """Check if a path is within the agent's folder."""
    try:
        agent = _agent_dir(config).resolve()
        return path.resolve().is_relative_to(agent)
    except (ValueError, OSError):
        return False


def _safe_folder(config, folder: str) -> Path | None:
    """Validate a folder path within the vault. Returns resolved path or None."""
    if not folder:
        return _vault_root(config)
    if ".." in folder or folder.startswith("/"):
        return None
    vault = _vault_root(config)
    resolved = (vault / folder).resolve()
    if not resolved.is_relative_to(vault.resolve()):
        return None
    return resolved


def _source_type_for_path(config, path: Path) -> str:
    """Determine the embedding source type based on file location."""
    resolved = path.resolve()
    try:
        if resolved.is_relative_to(config.vault_agent_journal_dir.resolve()):
            return "journal"
        if resolved.is_relative_to(config.vault_agent_dir.resolve()):
            return "page"
    except (ValueError, OSError):
        pass
    return "user"


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

async def tool_vault_read(ctx, page: str) -> str | ToolResult:
    """Read a vault page by name or path."""
    log.info(f"[tool:vault_read] page={page}")
    path = resolve_page(ctx.config, page)
    if path is None:
        return ToolResult(text=f"[error: vault page '{page}' not found]")
    return ToolResult(text=path.read_text(), display_short_text=page)


async def tool_vault_write(ctx, page: str, content: str) -> str | ToolResult:
    """Create or overwrite a vault page."""
    log.info(f"[tool:vault_write] page={page}")
    path = _safe_write_path(ctx.config, page)
    if path is None:
        return ToolResult(
            text=f"[error: invalid page name '{page}' — must be within vault directory]")
    if not content or not content.strip():
        return ToolResult(text=f"[error: refusing to write empty vault page '{page}']")
    if not _is_in_agent_dir(ctx.config, path):
        return ToolResult(
            text=f"[error: refusing to write '{page}' — "
                 f"only pages under the agent folder may be written]")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)

    # Update semantic search index with composite text (frontmatter-enriched)
    source_type = _source_type_for_path(ctx.config, path)
    try:
        from decafclaw.embeddings import delete_entries, index_entry
        from decafclaw.frontmatter import build_composite_text, parse_frontmatter
        rel_path = str(path.resolve().relative_to(ctx.config.vault_root.resolve()))
        delete_entries(ctx.config, rel_path, source_type=source_type)
        metadata, body = parse_frontmatter(content)
        embed_text = build_composite_text(metadata, body)
        await index_entry(ctx.config, rel_path, embed_text, source_type=source_type)
    except Exception as e:
        log.warning(f"Failed to index vault page '{page}': {e}")

    return f"Vault page '{page}' saved."


async def tool_vault_delete(ctx, page: str) -> ToolResult:
    """Delete a vault page. Agent-owned pages only (under the agent folder)."""
    log.info(f"[tool:vault_delete] page={page}")
    path = _safe_write_path(ctx.config, page)
    if path is None:
        return ToolResult(
            text=f"[error: invalid page name '{page}' — must be within vault directory]")
    if not path.exists():
        return ToolResult(text=f"[error: vault page '{page}' not found]")
    if not _is_in_agent_dir(ctx.config, path):
        return ToolResult(
            text=f"[error: refusing to delete '{page}' — only pages under the agent folder may be deleted]")

    vault_resolved = _vault_root(ctx.config).resolve()
    rel_path = str(path.resolve().relative_to(vault_resolved))
    source_type = _source_type_for_path(ctx.config, path)

    path.unlink()

    # Clean up empty parent directories up to (but not including) the vault root.
    parent = path.parent
    while parent.resolve() != vault_resolved:
        try:
            parent.rmdir()  # only succeeds if empty
        except OSError:
            break
        parent = parent.parent

    # Remove from embedding index
    try:
        from decafclaw.embeddings import delete_entries
        delete_entries(ctx.config, rel_path, source_type=source_type)
    except Exception as e:
        log.warning(f"Failed to remove embeddings for '{page}': {e}")

    return ToolResult(text=f"Vault page '{page}' deleted.")


async def tool_vault_rename(ctx, page: str, rename_to: str) -> ToolResult:
    """Rename or move a vault page. Agent-owned pages only."""
    log.info(f"[tool:vault_rename] {page} -> {rename_to}")
    old_path = _safe_write_path(ctx.config, page)
    if old_path is None:
        return ToolResult(
            text=f"[error: invalid page name '{page}' — must be within vault directory]")
    new_path = _safe_write_path(ctx.config, rename_to)
    if new_path is None:
        return ToolResult(
            text=f"[error: invalid target '{rename_to}' — must be within vault directory]")
    if not old_path.exists():
        return ToolResult(text=f"[error: vault page '{page}' not found]")
    if new_path.exists():
        return ToolResult(text=f"[error: target '{rename_to}' already exists]")
    if not _is_in_agent_dir(ctx.config, old_path):
        return ToolResult(
            text=f"[error: refusing to rename '{page}' — only pages under the agent folder may be renamed]")
    if not _is_in_agent_dir(ctx.config, new_path):
        return ToolResult(
            text=f"[error: refusing to move '{page}' outside the agent folder]")

    vault_resolved = _vault_root(ctx.config).resolve()
    old_rel = str(old_path.resolve().relative_to(vault_resolved))
    old_source_type = _source_type_for_path(ctx.config, old_path)

    new_path.parent.mkdir(parents=True, exist_ok=True)
    old_path.rename(new_path)

    # Clean up empty parent directories of the old path (up to vault root).
    parent = old_path.parent
    while parent.resolve() != vault_resolved:
        try:
            parent.rmdir()
        except OSError:
            break
        parent = parent.parent

    # Re-index: drop the old entry and index the new one.
    try:
        from decafclaw.embeddings import delete_entries, index_entry
        from decafclaw.frontmatter import build_composite_text, parse_frontmatter
        delete_entries(ctx.config, old_rel, source_type=old_source_type)
        new_rel = str(new_path.resolve().relative_to(vault_resolved))
        new_source_type = _source_type_for_path(ctx.config, new_path)
        new_content = new_path.read_text()
        metadata, body = parse_frontmatter(new_content)
        embed_text = build_composite_text(metadata, body)
        await index_entry(ctx.config, new_rel, embed_text, source_type=new_source_type)
    except Exception as e:
        log.warning(f"Failed to re-index after rename '{page}' -> '{rename_to}': {e}")

    return ToolResult(text=f"Vault page '{page}' renamed to '{rename_to}'.")


async def tool_vault_journal_append(ctx, tags: list[str], content: str) -> str:
    """Append a timestamped entry to today's journal file."""
    log.info(f"[tool:vault_journal_append] tags={tags}")
    now = datetime.now()
    journal_dir = ctx.config.vault_agent_journal_dir / str(now.year)
    journal_dir.mkdir(parents=True, exist_ok=True)

    filepath = journal_dir / f"{now:%Y-%m-%d}.md"
    tag_str = ", ".join(tags) if tags else "untagged"

    entry = f"\n## {now:%Y-%m-%d %H:%M}\n\n"
    channel_name = ctx.channel_name
    channel_id = ctx.channel_id
    thread_id = ctx.thread_id
    if channel_name or channel_id:
        entry += f"- **channel:** {channel_name} ({channel_id})\n"
    if thread_id:
        entry += f"- **thread:** {thread_id}\n"
    entry += f"- **tags:** {tag_str}\n"
    entry += f"\n{content}\n"

    with open(filepath, "a", encoding="utf-8") as f:
        f.write(entry)

    # Index for semantic search
    try:
        from decafclaw.embeddings import index_entry
        rel_path = str(filepath.resolve().relative_to(
            ctx.config.vault_root.resolve()))
        # Format the full entry for embedding (self-contained with date/metadata)
        entry_text = f"## {now:%Y-%m-%d %H:%M}\n\n"
        if channel_name or channel_id:
            entry_text += f"- **channel:** {channel_name} ({channel_id})\n"
        if thread_id:
            entry_text += f"- **thread:** {thread_id}\n"
        entry_text += f"- **tags:** {tag_str}\n\n{content}"
        await index_entry(ctx.config, rel_path, entry_text,
                          source_type="journal")
    except Exception as e:
        log.warning(f"Failed to index journal entry: {e}")

    log.info(f"Saved journal entry tagged [{tag_str}]")
    return f"Saved journal entry tagged [{tag_str}]"


async def tool_vault_search(ctx, query: str, source_type: str = "",
                            days: int = 0, folder: str = "") -> str | ToolResult:
    """Search the vault using semantic or substring matching."""
    log.info(f"[tool:vault_search] query={query!r} source_type={source_type} "
             f"days={days} folder={folder}")

    vault = _vault_root(ctx.config)
    if not vault.is_dir():
        return ToolResult(text="Vault directory does not exist.",
                          display_short_text="no vault")

    if folder:
        safe = _safe_folder(ctx.config, folder)
        if safe is None:
            return ToolResult(text=f"[error: invalid folder path '{folder}']")

    # Try semantic search first
    if ctx.config.embedding.search_strategy == "semantic":
        try:
            from decafclaw.embeddings import search_similar
            st = source_type if source_type else None
            results = await search_similar(ctx.config, query, top_k=10,
                                           source_type=st)

            # Apply days filter
            if days > 0:
                from datetime import timedelta
                cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)
                filtered = []
                for r in results:
                    fp = vault / r.get("file_path", "")
                    if fp.exists():
                        mtime = datetime.fromtimestamp(
                            fp.stat().st_mtime, tz=timezone.utc)
                        if mtime >= cutoff:
                            filtered.append(r)
                results = filtered

            # Apply folder filter
            if folder:
                results = [r for r in results
                           if r.get("file_path", "").startswith(folder)]

            if results:
                lines = [f"Found {len(results)} result(s):\n"]
                for i, r in enumerate(results):
                    sim = f"{r['similarity']:.2f}"
                    src = r.get("source_type", "?")
                    lines.append(
                        f"--- Result {i+1} [{src}] (relevance: {sim}) ---\n"
                        f"{r['entry_text']}")
                text = "\n\n".join(lines)
                return ToolResult(
                    text=text,
                    display_short_text=f"'{query}' — {len(results)} result(s)")
            # Fall through to substring
            log.info("Semantic search returned no results, falling back to substring")
        except Exception as e:
            log.error(f"Semantic search failed, falling back to substring: {e}")

    # Substring search across vault
    return _substring_search(ctx.config, query, days=days, folder=folder)


def _substring_search(config, query: str, days: int = 0,
                      folder: str = "") -> str | ToolResult:
    """Substring search across vault markdown files."""
    vault = _vault_root(config)
    search_root = vault / folder if folder else vault

    if not search_root.is_dir():
        return ToolResult(text=f"No pages found in '{folder}'.",
                          display_short_text=f"'{query}' — no results")

    query_lower = query.lower() if query else ""
    cutoff = None
    if days > 0:
        from datetime import timedelta
        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)

    results: list[str] = []
    for path in sorted(search_root.rglob("*.md")):
        if cutoff:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            if mtime < cutoff:
                continue

        if not query_lower:
            # No query — return recent files (used for memory_recent replacement)
            rel = path.relative_to(vault)
            mtime_str = datetime.fromtimestamp(
                path.stat().st_mtime, tz=timezone.utc
            ).strftime("%Y-%m-%d %H:%M")
            results.append(f"- {rel.with_suffix('')} (modified: {mtime_str})")
            continue

        text = path.read_text()
        name = path.stem
        if query_lower in name.lower() or query_lower in text.lower():
            excerpt = ""
            for line in text.splitlines():
                if query_lower in line.lower():
                    excerpt = line.strip()[:200]
                    break
            rel = path.relative_to(vault)
            results.append(
                f"- **{rel.with_suffix('')}**"
                + (f": {excerpt}" if excerpt else ""))

    if not results:
        msg = f"No results matching '{query}'." if query else "No files found."
        return ToolResult(text=msg,
                          display_short_text=f"'{query}' — no results")

    text = f"Found {len(results)} result(s):\n\n" + "\n".join(results)
    return ToolResult(text=text,
                      display_short_text=f"'{query}' — {len(results)} result(s)")


async def tool_vault_list(ctx, folder: str = "", pattern: str = "") -> str | ToolResult:
    """List vault pages, optionally filtered by folder and pattern."""
    log.info(f"[tool:vault_list] folder={folder} pattern={pattern}")
    if folder:
        safe = _safe_folder(ctx.config, folder)
        if safe is None:
            return ToolResult(text=f"[error: invalid folder path '{folder}']")
    vault = _vault_root(ctx.config)
    search_root = vault / folder if folder else vault

    if not search_root.is_dir():
        return "Vault directory does not exist."

    glob_pattern = f"*{pattern}*.md" if pattern else "*.md"
    pages = []

    for path in sorted(search_root.rglob(glob_pattern)):
        rel = path.relative_to(vault)
        mtime = datetime.fromtimestamp(
            path.stat().st_mtime, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
        display = str(rel.with_suffix(""))
        pages.append(f"- {display} (modified: {mtime})")

    if not pages:
        msg = "No pages found."
        if folder:
            msg += f" (folder: {folder})"
        if pattern:
            msg += f" (pattern: {pattern})"
        return msg

    return f"{len(pages)} page(s):\n\n" + "\n".join(pages)


_WIKI_LINK_RE = re.compile(r"\[\[([^\]]+)\]\]")


async def tool_vault_backlinks(ctx, page: str) -> str:
    """Find all vault pages that link to the given page via [[wiki-links]]."""
    log.info(f"[tool:vault_backlinks] page={page}")
    vault = _vault_root(ctx.config)
    if not vault.is_dir():
        return f"No backlinks to '{page}' (vault directory does not exist)."

    page_lower = page.lower()
    # Also match by stem for paths like "agent/pages/Foo"
    page_stem_lower = Path(page).stem.lower()
    results = []

    for path in sorted(vault.rglob("*.md")):
        stem_lower = path.stem.lower()
        if stem_lower == page_lower or stem_lower == page_stem_lower:
            continue  # skip the page itself
        text = path.read_text()
        for match in _WIKI_LINK_RE.finditer(text):
            raw_link = match.group(1)
            # Handle [[target|display]] — extract target before pipe
            link = raw_link.split("|")[0].strip().lower()
            link_stem = Path(link).stem.lower()
            if link == page_lower or link_stem == page_stem_lower:
                line_no = text[:match.start()].count("\n")
                lines = text.splitlines()
                context_line = (lines[line_no].strip()[:200]
                                if line_no < len(lines) else "")
                rel = path.relative_to(vault)
                results.append(
                    f"- **{rel.with_suffix('')}**: {context_line}")
                break  # one backlink per page

    if not results:
        return f"No pages link to '{page}'."

    return f"{len(results)} page(s) link to '{page}':\n\n" + "\n".join(results)


async def tool_vault_show_sections(
    ctx, page: str, section: str | None = None
) -> ToolResult:
    """Show a vault page's section outline or a specific section with line numbers."""
    log.info(f"[tool:vault_show_sections] page={page!r} section={section!r}")
    path = resolve_page(ctx.config, page)
    if path is None or not path.exists():
        return ToolResult(text=f"[error: page not found: {page}]")
    text = path.read_text(encoding="utf-8")
    doc = Document.from_text(text)
    if section is None:
        # Outline: every heading, 1-based line numbered
        lines = []
        for _depth, sec in doc.list_sections():
            line_no = sec.heading_line + 1  # 1-based
            hashes = "#" * sec.level
            lines.append(f"{line_no}: {hashes} {sec.title}")
        return ToolResult(text="\n".join(lines) if lines else "(no sections)")
    # Specific section: show heading + body with 1-based line numbers
    sec = doc.find_section(section)
    if sec is None:
        return ToolResult(text=f"[error: section not found: {section}]")
    start = sec.heading_line
    end = sec.content_end  # exclusive
    numbered = [
        f"{i + 1}: {doc.lines[i].rstrip()}" for i in range(start, end)
    ]
    return ToolResult(text="\n".join(numbered))


async def tool_vault_section(
    ctx,
    page: str,
    action: str,
    section: str | None = None,
    title: str | None = None,
    level: int = 1,
    after: str | None = None,
    before: str | None = None,
    parent: str | None = None,
) -> ToolResult:
    """Section operations on a vault page: add, remove, rename, or move."""
    log.info(
        f"[tool:vault_section] page={page!r} action={action!r} "
        f"section={section!r} title={title!r}"
    )
    path = resolve_page(ctx.config, page)
    if path is None or not path.exists():
        return ToolResult(text=f"[error: page not found: {page}]")
    if not _is_in_agent_dir(ctx.config, path):
        return ToolResult(
            text=f"[error: cannot modify page outside agent folder: {page}]"
        )
    doc = Document.from_text(path.read_text(encoding="utf-8"))

    if action == "add":
        if not title:
            return ToolResult(text="[error: 'title' required for add]")
        if doc.add_section(title, level=level, after=after, before=before, parent=parent):
            path.write_text(doc.to_text(), encoding="utf-8")
            return ToolResult(text=f"Added section: {title}")
        return ToolResult(text="[error: target section not found]")

    elif action == "remove":
        if not section:
            return ToolResult(text="[error: 'section' required for remove]")
        removed = doc.remove_section(section)
        if removed is not None:
            path.write_text(doc.to_text(), encoding="utf-8")
            return ToolResult(text=f"Removed section: {section}")
        return ToolResult(text=f"[error: section not found: {section}]")

    elif action == "rename":
        if not section or not title:
            return ToolResult(text="[error: 'section' and 'title' required for rename]")
        if doc.rename_section(section, title):
            path.write_text(doc.to_text(), encoding="utf-8")
            return ToolResult(text=f"Renamed section: {section} → {title}")
        return ToolResult(text=f"[error: section not found: {section}]")

    elif action == "move":
        if not section:
            return ToolResult(text="[error: 'section' required for move]")
        if doc.move_section(section, after=after, before=before):
            path.write_text(doc.to_text(), encoding="utf-8")
            return ToolResult(text=f"Moved section: {section}")
        return ToolResult(text="[error: section or target not found]")

    else:
        return ToolResult(
            text=f"[error: unknown action: {action}. Use add/remove/rename/move]"
        )


async def tool_vault_move_lines(
    ctx,
    from_page: str,
    to_page: str,
    lines: str,
    to_section: str | None = None,
    position: str = "append",
) -> ToolResult:
    """Move specific lines (by line number) from one vault page to another."""
    log.info(
        f"[tool:vault_move_lines] from={from_page!r} to={to_page!r} "
        f"lines={lines!r} section={to_section!r} position={position!r}"
    )
    # Source must be resolvable and writable (we're removing lines from it)
    from_path = resolve_page(ctx.config, from_page)
    if from_path is None or not from_path.exists():
        return ToolResult(text=f"[error: source page not found: {from_page}]")
    if not _is_in_agent_dir(ctx.config, from_path):
        return ToolResult(
            text=f"[error: cannot modify page outside agent folder: {from_page}]"
        )
    # Target must be writable
    to_path = resolve_page(ctx.config, to_page)
    if to_path is None or not to_path.exists():
        return ToolResult(text=f"[error: target page not found: {to_page}]")
    if not _is_in_agent_dir(ctx.config, to_path):
        return ToolResult(
            text=f"[error: cannot write to page outside agent folder: {to_page}]"
        )
    # Parse line numbers
    try:
        line_nums = sorted({int(s.strip()) for s in lines.split(",") if s.strip()})
    except ValueError:
        return ToolResult(text=f"[error: invalid lines argument: {lines!r}]")
    if not line_nums:
        return ToolResult(text="[error: no line numbers provided]")
    from_doc = Document.from_text(from_path.read_text(encoding="utf-8"))
    to_doc = Document.from_text(to_path.read_text(encoding="utf-8"))
    # Collect line text in original order, then delete in reverse
    moved: list[str] = []
    for n in line_nums:
        idx = n - 1
        if idx < 0 or idx >= len(from_doc.lines):
            return ToolResult(text=f"[error: line {n} out of range in {from_page}]")
        moved.append(from_doc.lines[idx].rstrip("\n"))
    for n in sorted(line_nums, reverse=True):
        from_doc._delete_lines(n - 1, 1)
    # Insert into target
    err = _insert_into_doc(to_doc, moved, to_section, position)
    if err:
        return ToolResult(text=f"[error: {err}]")
    from_path.write_text(from_doc.to_text(), encoding="utf-8")
    to_path.write_text(to_doc.to_text(), encoding="utf-8")
    return ToolResult(
        text=f"Moved {len(moved)} line(s) from {from_page} to {to_page}"
        + (f" section '{to_section}'" if to_section else "")
    )


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------

TOOLS = {
    "vault_read": tool_vault_read,
    "vault_write": tool_vault_write,
    "vault_delete": tool_vault_delete,
    "vault_rename": tool_vault_rename,
    "vault_journal_append": tool_vault_journal_append,
    "vault_search": tool_vault_search,
    "vault_list": tool_vault_list,
    "vault_backlinks": tool_vault_backlinks,
    "vault_show_sections": tool_vault_show_sections,
    "vault_move_lines": tool_vault_move_lines,
    "vault_section": tool_vault_section,
}

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "vault_read",
            "description": (
                "Read a vault page (knowledge base) by name or path. ONLY for "
                "vault knowledge pages — NOT for workspace files, blog posts, "
                "code, or project files (use workspace_read for those). Returns "
                "the full page content. Use to check existing content before "
                "writing updates."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "page": {
                        "type": "string",
                        "description": (
                            "Page name or path (e.g. 'Les Orchard', "
                            "'agent/pages/DecafClaw')"
                        ),
                    },
                },
                "required": ["page"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vault_write",
            "description": (
                "Create or overwrite a vault page (knowledge base). ONLY for "
                "vault knowledge pages — NOT for workspace files, blog posts, "
                "code, configs, or any file in a project directory. Use "
                "workspace_write for those. ALWAYS vault_read first if "
                "updating an existing page to preserve content you want to keep. "
                "Use [[Page Name]] syntax to link to other pages. Include a "
                "## Sources section. Writes are restricted to the agent folder "
                "(agent/pages/, agent/journal/); admin and user pages are "
                "off-limits."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "page": {
                        "type": "string",
                        "description": (
                            "Page path relative to vault root "
                            "(e.g. 'agent/pages/Les Orchard'). "
                            "Default to agent/pages/ for new pages."
                        ),
                    },
                    "content": {
                        "type": "string",
                        "description": "Full markdown content of the page",
                    },
                },
                "required": ["page", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vault_delete",
            "description": (
                "DESTRUCTIVE — permanently delete a vault page and its embedding "
                "entries. Only pages under the agent folder (agent/pages/, "
                "agent/journal/) may be deleted; admin and user pages are "
                "off-limits. Prefer vault_write with updated content to retire "
                "or mark pages superseded; use delete only when the page is "
                "definitively wrong, duplicate, or no longer reachable."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "page": {
                        "type": "string",
                        "description": (
                            "Page path relative to vault root "
                            "(e.g. 'agent/pages/Stale Draft')."
                        ),
                    },
                },
                "required": ["page"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vault_rename",
            "description": (
                "Rename or move a vault page. Updates the embedding index so "
                "search results stay consistent. Agent-owned pages only (under "
                "the agent folder); target must also land under the agent "
                "folder and must not already exist. Use this to reorganize or "
                "refine page names — prefer it over delete + rewrite when the "
                "content stays the same."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "page": {
                        "type": "string",
                        "description": (
                            "Current page path relative to vault root "
                            "(e.g. 'agent/pages/old-name')."
                        ),
                    },
                    "rename_to": {
                        "type": "string",
                        "description": (
                            "New page path relative to vault root "
                            "(e.g. 'agent/pages/new-name', or "
                            "'agent/pages/people/Alice' to move into a subfolder)."
                        ),
                    },
                },
                "required": ["page", "rename_to"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vault_journal_append",
            "description": (
                "Save a journal entry — a timestamped observation, fact, or note. "
                "Entries are appended to today's journal file. Use this for anything "
                "worth recording: user preferences, facts, project details, decisions, "
                "conversation context. Rich tagging is critical for future retrieval."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Free-form tags to categorize this entry. Be GENEROUS — "
                            "include specific terms, broader categories, synonyms, and "
                            "related concepts. More tags = more searchable."
                        ),
                    },
                    "content": {
                        "type": "string",
                        "description": "The journal entry content",
                    },
                },
                "required": ["tags", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vault_search",
            "description": (
                "Search the vault using semantic or substring matching. ALWAYS try "
                "this BEFORE web search — the vault may already have the answer.\n\n"
                "Searches across all vault content: your pages, journal entries, and "
                "user notes. Use natural language queries.\n\n"
                "**IMPORTANT:** When this tool returns results, you MUST use them. "
                "Do NOT ignore results or claim you have no information when results "
                "were returned. Try at least 3 query variations before giving up."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search text (natural language or keywords)",
                    },
                    "source_type": {
                        "type": "string",
                        "description": (
                            "Filter by source type: 'journal' (agent journal), "
                            "'page' (agent pages), 'user' (user notes). "
                            "Empty = all."
                        ),
                    },
                    "days": {
                        "type": "integer",
                        "description": (
                            "Limit to files modified in the last N days. "
                            "Useful for recent context."
                        ),
                    },
                    "folder": {
                        "type": "string",
                        "description": (
                            "Limit search to a vault subfolder "
                            "(e.g. 'agent/journal' or 'agent/pages')"
                        ),
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vault_list",
            "description": (
                "List vault pages with last-modified dates. "
                "Optionally filter by folder and/or name pattern."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "folder": {
                        "type": "string",
                        "description": (
                            "Subfolder to list (e.g. 'agent/pages', "
                            "'agent/journal/2026'). Empty = entire vault."
                        ),
                    },
                    "pattern": {
                        "type": "string",
                        "description": (
                            "Optional filter pattern "
                            "(e.g. 'project' to match pages with 'project' in the name)"
                        ),
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vault_backlinks",
            "description": (
                "Find all vault pages that link to the given page via "
                "[[wiki-links]]. Useful for understanding how a topic "
                "connects to other knowledge."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "page": {
                        "type": "string",
                        "description": "Page name to find backlinks for",
                    },
                },
                "required": ["page"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vault_show_sections",
            "description": (
                "Show a vault page's section structure (headings with absolute line "
                "numbers) or a specific section's content with line numbers. Use this "
                "to see what's in a page before editing with vault_write or "
                "vault_move_lines."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "page": {
                        "type": "string",
                        "description": (
                            "Page path relative to vault root or bare name "
                            "(e.g. 'agent/pages/note', 'My Page')."
                        ),
                    },
                    "section": {
                        "type": "string",
                        "description": (
                            "Optional slash-separated section path to show that "
                            "section's content with line numbers "
                            "(e.g. 'top/sub a'). Omit to get the full outline."
                        ),
                    },
                },
                "required": ["page"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vault_move_lines",
            "description": (
                "Move specific lines (by absolute line number) from one vault page to "
                "another. Use vault_show_sections first to see line numbers. Good for "
                "migrating to-do items between daily notes. Both pages must be under the "
                "agent folder. When to_section is omitted, moves into the whole target file."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "from_page": {
                        "type": "string",
                        "description": (
                            "Source page path relative to vault root "
                            "(e.g. 'agent/pages/yesterday')."
                        ),
                    },
                    "to_page": {
                        "type": "string",
                        "description": (
                            "Target page path relative to vault root "
                            "(e.g. 'agent/pages/today')."
                        ),
                    },
                    "lines": {
                        "type": "string",
                        "description": (
                            "Comma-separated absolute line numbers to move "
                            "(e.g. '3,4,7'). Use vault_show_sections to get line numbers."
                        ),
                    },
                    "to_section": {
                        "type": "string",
                        "description": (
                            "Slash-separated section path in the target page "
                            "(e.g. 'today/inbox'). Omit to append to the whole file."
                        ),
                    },
                    "position": {
                        "type": "string",
                        "enum": ["append", "prepend"],
                        "description": (
                            "Where to insert within the target section: "
                            "'append' (default) adds after existing content, "
                            "'prepend' adds before it."
                        ),
                    },
                },
                "required": ["from_page", "to_page", "lines"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vault_section",
            "description": (
                "Section operations on a vault page: add, remove, rename, or move a "
                "section. Actions: 'add', 'remove', 'rename', 'move'. Page must be under "
                "the agent folder."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "page": {
                        "type": "string",
                        "description": (
                            "Page path relative to vault root or bare name "
                            "(e.g. 'agent/pages/note', 'My Page')."
                        ),
                    },
                    "action": {
                        "type": "string",
                        "enum": ["add", "remove", "rename", "move"],
                        "description": "Operation: 'add', 'remove', 'rename', or 'move'.",
                    },
                    "section": {
                        "type": "string",
                        "description": (
                            "Slash-separated section path to operate on "
                            "(e.g. 'top/sub a'). Required for remove, rename, move."
                        ),
                    },
                    "title": {
                        "type": "string",
                        "description": (
                            "Title for the section. Required for add; "
                            "used as the new title for rename."
                        ),
                    },
                    "level": {
                        "type": "integer",
                        "description": (
                            "Heading level (1–6) for the new section. "
                            "Only used by add. Default: 1."
                        ),
                    },
                    "after": {
                        "type": "string",
                        "description": (
                            "Slash-separated section path to insert/move after "
                            "(e.g. 'top/first'). Used by add and move."
                        ),
                    },
                    "before": {
                        "type": "string",
                        "description": (
                            "Slash-separated section path to insert/move before. "
                            "Used by add and move."
                        ),
                    },
                    "parent": {
                        "type": "string",
                        "description": (
                            "Slash-separated section path to nest the new section under. "
                            "Only used by add."
                        ),
                    },
                },
                "required": ["page", "action"],
            },
        },
    },
]
