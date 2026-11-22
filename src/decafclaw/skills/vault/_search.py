"""Vault search internals — vault_search's engines and result widgets.

Extracted from ``tools.py`` (#1368): the three search engines —
``_tag_filter_search`` (pure tag filter), ``_substring_search``, and the
semantic branch's per-result tag check ``_semantic_result_matches_tags`` —
plus the two data_table widget builders are a self-contained search concern,
unrelated to page-name resolution or the write gate.

The import graph stays one-way (``tools.py`` imports this module, never the
reverse at module level). Three lookups reach back into ``tools.py`` and are
resolved through it at call time (see ``_tools``):

- ``_source_type_for_path`` is defined in ``tools.py`` (and imported by
  ``http_server.py``), so a module-level import back would be circular.
- Tests patch ``decafclaw.skills.vault.tools.extract_tags`` and
  ``...tools.pages_with_tags`` (tests/test_vault_tools.py,
  tests/test_vault_search_tags_only.py); resolving through the tools namespace
  keeps those patches intercepting these calls, exactly as when this code
  lived in ``tools.py``. Same pattern as ``_gate.py``'s
  ``request_confirmation`` lookup.

Path helpers read straight off ``config`` (``vault_root``) rather than
calling ``tools._vault_root``, mirroring ``_gate.py``.
"""

import logging
from datetime import datetime, timedelta, timezone

from decafclaw.media import ToolResult, WidgetRequest

log = logging.getLogger(__name__)


def _tools():
    """The vault tools module, resolved at call time.

    ``tools.py`` imports this module, so a module-level import back would be
    circular. Call-time resolution also keeps test patches on
    ``decafclaw.skills.vault.tools.{extract_tags,pages_with_tags}`` effective:
    the lookups below go through the tools namespace, exactly as they did when
    this code lived there.
    """
    from decafclaw.skills.vault import tools

    return tools


def _semantic_result_matches_tags(config, result: dict, req_tags: set[str], any_tag: bool) -> bool:
    """Fail-open per-result tag check for a semantic search result row.

    Reads the result's source file directly (results carry composite
    embedding text, not raw content) and calls `extract_tags` on it. An
    unreadable file is excluded rather than raised, matching
    `pages_with_tags`'s per-file fail-open behavior.
    """
    tools = _tools()
    file_path = result.get("file_path", "")
    if not file_path:
        return False
    path = config.vault_root / file_path
    try:
        content = path.read_text()
        source_type = result.get("source_type") or tools._source_type_for_path(config, path)
        file_tags = tools.extract_tags(content, source_type)
    except Exception as exc:
        log.debug(f"vault_search: failed reading/tagging {path} for tag filter: {exc}")
        return False
    if any_tag:
        return bool(file_tags & req_tags)
    return req_tags <= file_tags


def _tag_filter_search(
    config, req_tags: set[str], any_tag: bool, source_type: str = "", folder: str = "", days: int = 0
) -> ToolResult:
    """Pure tag filter (empty query): list vault pages matching the tag
    filter, formatted like the existing no-query substring search output.
    """
    tools = _tools()
    vault = config.vault_root
    matches = tools.pages_with_tags(config, sorted(req_tags), any_tag=any_tag)

    cutoff = None
    if days > 0:
        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)

    lines: list[str] = []
    rows: list[dict] = []
    for rel_path in sorted(matches):
        if folder and not rel_path.startswith(folder):
            continue
        path = vault / rel_path
        if source_type and tools._source_type_for_path(config, path) != source_type:
            continue
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        except Exception as exc:
            log.debug(f"vault_search: failed stat'ing {path} for tag filter: {exc}")
            continue
        if cutoff and mtime < cutoff:
            continue
        mtime_str = mtime.strftime("%Y-%m-%d %H:%M")
        page = str(path.relative_to(vault).with_suffix(""))
        lines.append(f"- {page} (modified: {mtime_str})")
        rows.append({"page": page, "modified": mtime_str})

    tag_list = sorted(req_tags)
    if not lines:
        return ToolResult(
            text=f"No pages found matching tags {tag_list}.",
            display_short_text="tags — no results",
            data={"query": "", "mode": "tags", "tags": tag_list, "any_tag": any_tag, "results": []},
        )

    text = f"Found {len(lines)} result(s):\n\n" + "\n".join(lines)
    widget = _substring_results_widget("", rows)
    return ToolResult(
        text=text,
        display_short_text=f"tags {tag_list} — {len(lines)} result(s)",
        widget=widget,
        data={"query": "", "mode": "tags", "tags": tag_list, "any_tag": any_tag, "results": rows},
    )


def _substring_search(
    config,
    query: str,
    days: int = 0,
    folder: str = "",
    req_tags: set[str] | None = None,
    any_tag: bool = False,
    source_type: str = "",
) -> str | ToolResult:
    """Substring search across vault markdown files."""
    tools = _tools()
    vault = config.vault_root
    search_root = vault / folder if folder else vault

    if not search_root.is_dir():
        return ToolResult(text=f"No pages found in '{folder}'.", display_short_text=f"'{query}' — no results")

    query_lower = query.lower() if query else ""
    cutoff = None
    if days > 0:
        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)

    # Structured rows parallel to the markdown lines, for the data_table
    # widget. Same result set, two views.
    lines: list[str] = []
    rows: list[dict] = []
    for path in sorted(search_root.rglob("*.md")):
        if source_type and tools._source_type_for_path(config, path) != source_type:
            continue
        if cutoff:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            if mtime < cutoff:
                continue

        rel = path.relative_to(vault)
        page = str(rel.with_suffix(""))

        if not query_lower:
            # No query — list recent files.
            mtime_str = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
            lines.append(f"- {page} (modified: {mtime_str})")
            rows.append({"page": page, "modified": mtime_str})
            continue

        text = path.read_text()
        name = path.stem
        if query_lower in name.lower() or query_lower in text.lower():
            if req_tags:
                try:
                    file_tags = tools.extract_tags(text, tools._source_type_for_path(config, path))
                except Exception as exc:
                    log.debug(f"vault_search: failed reading/tagging {path} for tag filter: {exc}")
                    continue
                matches = bool(file_tags & req_tags) if any_tag else req_tags <= file_tags
                if not matches:
                    continue
            excerpt = ""
            for line in text.splitlines():
                if query_lower in line.lower():
                    excerpt = line.strip()[:200]
                    break
            lines.append(f"- **{page}**" + (f": {excerpt}" if excerpt else ""))
            rows.append({"page": page, "excerpt": excerpt})

    if not lines:
        if req_tags:
            tag_list = sorted(req_tags)
            msg = (
                f"0 documents matched query '{query}' with the tag filter {tag_list} — "
                f"no page both matched the query and carried these tags. This is not "
                f"evidence the vault lacks relevant content. If you expected matches, "
                f"retry with a broader query or without the `tags` filter."
            )
            data = {
                "query": query,
                "mode": "substring",
                "tags": tag_list,
                "any_tag": any_tag,
                "results": [],
            }
        else:
            msg = f"No results matching '{query}'." if query else "No files found."
            data = {"query": query, "mode": "substring", "results": []}
        return ToolResult(
            text=msg,
            display_short_text=f"'{query}' — no results",
            data=data,
        )

    text = f"Found {len(lines)} result(s):\n\n" + "\n".join(lines)
    widget = _substring_results_widget(query, rows)
    return ToolResult(
        text=text,
        display_short_text=f"'{query}' — {len(lines)} result(s)",
        widget=widget,
        data={"query": query, "mode": "substring", "results": rows},
    )


def _semantic_results_widget(query: str, results: list[dict]) -> WidgetRequest:
    """Build a data_table WidgetRequest for semantic vault_search results."""
    rows = []
    for r in results:
        snippet = (r.get("entry_text") or "").strip()
        if len(snippet) > 160:
            snippet = snippet[:160].rstrip() + "…"
        rows.append(
            {
                "page": r.get("file_path", "").removesuffix(".md"),
                "similarity": round(float(r.get("similarity", 0.0)), 3),
                "source": r.get("source_type", "?"),
                "snippet": snippet,
            }
        )
    caption = f'vault_search: "{query}" — {len(results)} result(s)'
    return WidgetRequest(
        widget_type="data_table",
        data={
            "caption": caption,
            "columns": [
                {"key": "page", "label": "Page"},
                {"key": "similarity", "label": "Similarity"},
                {"key": "source", "label": "Source"},
                {"key": "snippet", "label": "Snippet"},
            ],
            "rows": rows,
        },
    )


def _substring_results_widget(query: str, rows: list[dict]) -> WidgetRequest:
    """Build a data_table WidgetRequest for substring vault_search results.

    Column set depends on whether a query was provided: without a query,
    rows have `page` + `modified`; with a query, rows have `page` +
    `excerpt`. Columns are chosen to match whichever keys the rows carry.
    """
    has_query = bool(query)
    if has_query:
        columns = [
            {"key": "page", "label": "Page"},
            {"key": "excerpt", "label": "Excerpt"},
        ]
        caption = f'vault_search: "{query}" — {len(rows)} result(s)'
    else:
        columns = [
            {"key": "page", "label": "Page"},
            {"key": "modified", "label": "Modified"},
        ]
        caption = f"vault_search: {len(rows)} page(s)"
    return WidgetRequest(
        widget_type="data_table",
        data={"caption": caption, "columns": columns, "rows": rows},
    )
