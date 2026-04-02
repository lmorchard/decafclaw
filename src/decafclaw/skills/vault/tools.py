"""Vault tools — unified knowledge base operations.

Replaces the separate wiki and memory tool systems with a single vault
that supports curated pages, daily journal entries, and user content.
"""

import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from decafclaw.media import ToolResult

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

    Returns None if the path would escape the vault directory.
    """
    if ".." in page or page.startswith("/"):
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

    # Log notice if writing outside agent folder
    if not _is_in_agent_dir(ctx.config, path):
        log.info(f"[tool:vault_write] writing outside agent folder: {page}")

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


async def tool_vault_list(ctx, folder: str = "", pattern: str = "") -> str:
    """List vault pages, optionally filtered by folder and pattern."""
    log.info(f"[tool:vault_list] folder={folder} pattern={pattern}")
    if folder:
        safe = _safe_folder(ctx.config, folder)
        if safe is None:
            return f"[error: invalid folder path '{folder}']"
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


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------

TOOLS = {
    "vault_read": tool_vault_read,
    "vault_write": tool_vault_write,
    "vault_journal_append": tool_vault_journal_append,
    "vault_search": tool_vault_search,
    "vault_list": tool_vault_list,
    "vault_backlinks": tool_vault_backlinks,
}

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "vault_read",
            "description": (
                "Read a vault page by name or path. Returns the full page content. "
                "Use to check existing content before writing updates."
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
                "Create or overwrite a vault page. ALWAYS vault_read first if "
                "updating an existing page to preserve content you want to keep. "
                "Use [[Page Name]] syntax to link to other pages. Include a "
                "## Sources section. Default to writing in agent/pages/ — only "
                "write outside the agent folder when the user explicitly asks."
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
]
