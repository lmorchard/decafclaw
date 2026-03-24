"""Wiki tools — Obsidian-compatible knowledge base operations."""

import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from decafclaw.media import ToolResult

log = logging.getLogger(__name__)


def _wiki_dir(config) -> Path:
    """Return the wiki directory path."""
    return config.workspace_path / "wiki"


def _resolve_page(config, page: str) -> Path | None:
    """Resolve a page name to a file path, searching subdirectories.

    Returns None if the page doesn't exist or the name is invalid.
    """
    if ".." in page or page.startswith("/"):
        return None
    wiki_root = _wiki_dir(config).resolve()
    # Try direct path first
    direct = (wiki_root / f"{page}.md").resolve()
    if direct.is_relative_to(wiki_root) and direct.exists():
        return direct
    # Search subdirectories by comparing stems (avoids glob metachar issues)
    for path in wiki_root.rglob("*.md"):
        if path.stem == page and path.resolve().is_relative_to(wiki_root):
            return path
    return None


def _safe_write_path(config, page: str) -> Path | None:
    """Validate and return a safe write path within the wiki root.

    Returns None if the path would escape the wiki directory.
    """
    if ".." in page or page.startswith("/"):
        return None
    wiki_root = _wiki_dir(config).resolve()
    path = (wiki_root / f"{page}.md").resolve()
    if not path.is_relative_to(wiki_root):
        return None
    return path


async def tool_wiki_read(ctx, page: str) -> str | ToolResult:
    """Read a wiki page by name."""
    log.info(f"[tool:wiki_read] page={page}")
    path = _resolve_page(ctx.config, page)
    if path is None:
        return ToolResult(text=f"[error: wiki page '{page}' not found]")
    return path.read_text()


async def tool_wiki_write(ctx, page: str, content: str) -> str | ToolResult:
    """Create or overwrite a wiki page."""
    log.info(f"[tool:wiki_write] page={page}")
    path = _safe_write_path(ctx.config, page)
    if path is None:
        return ToolResult(
            text=f"[error: invalid page name '{page}' — must be within wiki directory]")
    if not content or not content.strip():
        return ToolResult(text=f"[error: refusing to write empty wiki page '{page}']")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)

    # Update semantic search index (replace old entry for this page)
    try:
        from decafclaw.embeddings import delete_entries, index_entry
        rel_path = str(path.resolve().relative_to(ctx.config.workspace_path.resolve()))
        delete_entries(ctx.config, rel_path, source_type="wiki")
        await index_entry(ctx.config, rel_path, content, source_type="wiki")
    except Exception as e:
        log.warning(f"Failed to index wiki page '{page}': {e}")

    return f"Wiki page '{page}' saved."


async def tool_wiki_search(ctx, query: str) -> str:
    """Search wiki pages by title and content (substring match)."""
    log.info(f"[tool:wiki_search] query={query}")
    wiki = _wiki_dir(ctx.config)
    if not wiki.is_dir():
        return "No wiki pages found."

    query_lower = query.lower()
    results = []

    for path in sorted(wiki.rglob("*.md")):
        name = path.stem
        text = path.read_text()
        name_match = query_lower in name.lower()
        content_match = query_lower in text.lower()

        if name_match or content_match:
            # Extract a relevant excerpt
            excerpt = ""
            if content_match:
                for line in text.splitlines():
                    if query_lower in line.lower():
                        excerpt = line.strip()[:200]
                        break
            results.append(f"- **{name}**" + (f": {excerpt}" if excerpt else ""))

    if not results:
        return f"No wiki pages matching '{query}'."

    return f"Found {len(results)} page(s):\n\n" + "\n".join(results)


async def tool_wiki_list(ctx, pattern: str = "") -> str:
    """List all wiki pages."""
    log.info(f"[tool:wiki_list] pattern={pattern}")
    wiki = _wiki_dir(ctx.config)
    if not wiki.is_dir():
        return "No wiki pages found (wiki directory does not exist)."

    glob_pattern = f"*{pattern}*.md" if pattern else "*.md"
    pages = []

    for path in sorted(wiki.rglob(glob_pattern)):
        name = path.stem
        rel = path.relative_to(wiki)
        mtime = datetime.fromtimestamp(
            path.stat().st_mtime, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
        # Show subdir prefix if not in root
        display = str(rel.parent / name) if rel.parent != Path(".") else name
        pages.append(f"- {display} (modified: {mtime})")

    if not pages:
        return "No wiki pages found." + (f" (pattern: {pattern})" if pattern else "")

    return f"{len(pages)} page(s):\n\n" + "\n".join(pages)


_WIKI_LINK_RE = re.compile(r"\[\[([^\]]+)\]\]")


async def tool_wiki_backlinks(ctx, page: str) -> str:
    """Find all pages that link to the given page via [[wiki-links]]."""
    log.info(f"[tool:wiki_backlinks] page={page}")
    wiki = _wiki_dir(ctx.config)
    if not wiki.is_dir():
        return f"No backlinks to '{page}' (wiki directory does not exist)."

    page_lower = page.lower()
    results = []

    for path in sorted(wiki.rglob("*.md")):
        if path.stem.lower() == page_lower:
            continue  # skip the page itself
        text = path.read_text()
        for match in _WIKI_LINK_RE.finditer(text):
            if match.group(1).lower() == page_lower:
                # Find the line containing the match for context
                line_no = text[:match.start()].count("\n")
                lines = text.splitlines()
                context_line = lines[line_no].strip()[:200] if line_no < len(lines) else ""
                results.append(f"- **{path.stem}**: {context_line}")
                break  # one backlink per page is enough

    if not results:
        return f"No pages link to '{page}'."

    return f"{len(results)} page(s) link to '{page}':\n\n" + "\n".join(results)


TOOLS = {
    "wiki_read": tool_wiki_read,
    "wiki_write": tool_wiki_write,
    "wiki_search": tool_wiki_search,
    "wiki_list": tool_wiki_list,
    "wiki_backlinks": tool_wiki_backlinks,
}

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "wiki_read",
            "description": (
                "Read a wiki page by name. Returns the full page content. "
                "Use to check existing content before writing updates."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "page": {
                        "type": "string",
                        "description": "Page name (e.g. 'Les Orchard', 'DecafClaw')",
                    },
                },
                "required": ["page"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wiki_write",
            "description": (
                "Create or overwrite a wiki page. ALWAYS wiki_read first if updating "
                "an existing page to preserve content you want to keep. Use [[Page Name]] "
                "syntax to link to other wiki pages. Include a ## Sources section."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "page": {
                        "type": "string",
                        "description": "Page name (becomes the filename, e.g. 'Les Orchard')",
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
            "name": "wiki_search",
            "description": (
                "Search wiki pages by title and content (substring match). "
                "ALWAYS search before creating a new page — add to existing pages "
                "rather than creating duplicates."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search term",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wiki_list",
            "description": "List all wiki pages with last-modified dates.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Optional filter pattern (e.g. 'project' to match pages with 'project' in the name)",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wiki_backlinks",
            "description": (
                "Find all wiki pages that link to the given page via [[wiki-links]]. "
                "Useful for understanding how a topic connects to other knowledge."
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
