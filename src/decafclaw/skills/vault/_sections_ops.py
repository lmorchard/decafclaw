"""Vault section-operation tools — vault_show_sections, vault_section,
vault_move_lines.

Extracted from ``tools.py`` (#1370) so the section ops live beside the
section model in ``_sections.py``. ``tools.py`` re-imports the three tools,
so the TOOLS registry and existing importers (tests) are unchanged.

``_reindex_page`` stays in ``tools.py`` — it is shared with
``vault_update_frontmatter`` — and the writers below import it at call time
to avoid a module cycle.
"""

import logging
from typing import TYPE_CHECKING

from decafclaw.media import ToolResult
from decafclaw.skills.vault._events import KIND_MOVE, KIND_SECTION, publish_vault_changed
from decafclaw.skills.vault._paths import _is_in_agent_dir, resolve_page
from decafclaw.skills.vault._sections import Document, describe_section_miss

if TYPE_CHECKING:
    from decafclaw.context import Context

log = logging.getLogger(__name__)


async def tool_vault_show_sections(ctx, page: str, section: str | None = None) -> ToolResult:
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
        return ToolResult(text=f"[error: {describe_section_miss(doc, section)}]")
    start = sec.heading_line
    end = sec.content_end  # exclusive
    numbered = [f"{i + 1}: {doc.lines[i].rstrip('\n')}" for i in range(start, end)]
    return ToolResult(text="\n".join(numbered))


async def tool_vault_section(
    ctx: "Context",
    page: str,
    action: str,
    section: str | None = None,
    title: str | None = None,
    level: int | None = None,
    content: str | None = None,
    after: str | None = None,
    before: str | None = None,
    parent: str | None = None,
) -> ToolResult:
    """Section operations on a vault page: add, remove, rename, or move."""
    log.info(f"[tool:vault_section] page={page!r} action={action!r} section={section!r} title={title!r}")
    # Function-level import breaks the tools.py <-> _sections_ops cycle; _reindex_page
    # stays in tools.py (shared with vault_update_frontmatter).
    from decafclaw.skills.vault.tools import _reindex_page

    path = resolve_page(ctx.config, page)
    if path is None or not path.exists():
        return ToolResult(text=f"[error: page not found: {page}]")
    if not _is_in_agent_dir(ctx.config, path):
        return ToolResult(text=f"[error: cannot modify page outside agent folder: {page}]")
    doc = Document.from_text(path.read_text(encoding="utf-8"))

    if action == "add":
        if not title:
            return ToolResult(text="[error: 'title' required for add]")
        # None means "infer from the anchor" — add_section resolves it.
        if level is not None and (not isinstance(level, int) or level < 1 or level > 6):
            return ToolResult(text=f"[error: level must be between 1 and 6, got {level}]")
        if doc.add_section(
            title,
            level=level,
            content=content or "",
            after=after,
            before=before,
            parent=parent,
        ):
            path.write_text(doc.to_text(), encoding="utf-8")
            await _reindex_page(ctx, path)
            await publish_vault_changed(
                ctx.event_bus,
                ctx.config,
                kind=KIND_SECTION,
                path=path,
            )
            return ToolResult(text=f"Added section: {title}")
        target = after or before or parent or ""
        return ToolResult(text=f"[error: {describe_section_miss(doc, target)}]")

    elif action == "remove":
        if not section:
            return ToolResult(text="[error: 'section' required for remove]")
        removed = doc.remove_section(section)
        if removed is not None:
            path.write_text(doc.to_text(), encoding="utf-8")
            await _reindex_page(ctx, path)
            await publish_vault_changed(
                ctx.event_bus,
                ctx.config,
                kind=KIND_SECTION,
                path=path,
            )
            return ToolResult(text=f"Removed section: {section}")
        return ToolResult(text=f"[error: {describe_section_miss(doc, section)}]")

    elif action == "rename":
        if not section or not title:
            return ToolResult(text="[error: 'section' and 'title' required for rename]")
        if doc.rename_section(section, title):
            path.write_text(doc.to_text(), encoding="utf-8")
            await _reindex_page(ctx, path)
            await publish_vault_changed(
                ctx.event_bus,
                ctx.config,
                kind=KIND_SECTION,
                path=path,
            )
            return ToolResult(text=f"Renamed section: {section} → {title}")
        return ToolResult(text=f"[error: {describe_section_miss(doc, section)}]")

    elif action == "move":
        if not section:
            return ToolResult(text="[error: 'section' required for move]")
        if doc.move_section(section, after=after, before=before):
            path.write_text(doc.to_text(), encoding="utf-8")
            await _reindex_page(ctx, path)
            await publish_vault_changed(
                ctx.event_bus,
                ctx.config,
                kind=KIND_SECTION,
                path=path,
            )
            return ToolResult(text=f"Moved section: {section}")
        return ToolResult(text="[error: section or target not found]")

    else:
        return ToolResult(text=f"[error: unknown action: {action}. Use add/remove/rename/move]")


async def tool_vault_move_lines(
    ctx: "Context",
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
    # Function-level import breaks the tools.py <-> _sections_ops cycle; _reindex_page
    # stays in tools.py (shared with vault_update_frontmatter).
    from decafclaw.skills.vault.tools import _reindex_page

    # Source must be resolvable and writable (we're removing lines from it)
    from_path = resolve_page(ctx.config, from_page)
    if from_path is None or not from_path.exists():
        return ToolResult(text=f"[error: source page not found: {from_page}]")
    if not _is_in_agent_dir(ctx.config, from_path):
        return ToolResult(text=f"[error: cannot modify page outside agent folder: {from_page}]")
    # Target must be writable
    to_path = resolve_page(ctx.config, to_page)
    if to_path is None or not to_path.exists():
        return ToolResult(text=f"[error: target page not found: {to_page}]")
    if not _is_in_agent_dir(ctx.config, to_path):
        return ToolResult(text=f"[error: cannot write to page outside agent folder: {to_page}]")
    # Guard: same-file moves silently drop data (second write wins, undoing first)
    if from_path.resolve() == to_path.resolve():
        return ToolResult(
            text="[error: from_page and to_page must be different pages "
            "(same-file moves not supported). For intra-page moves, "
            "use vault_section with action='move']"
        )
    # Parse line numbers
    try:
        line_nums = sorted({int(s.strip()) for s in lines.split(",") if s.strip()})
    except ValueError:
        return ToolResult(text=f"[error: invalid lines argument: {lines!r}]")
    if not line_nums:
        return ToolResult(text="[error: no line numbers provided]")
    if position not in ("append", "prepend"):
        return ToolResult(text=f"[error: position must be 'append' or 'prepend', got {position!r}]")
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
        from_doc.delete_lines(n - 1, 1)
    # Insert into target
    err = to_doc.insert_lines(moved, to_section, position)
    if err:
        return ToolResult(text=f"[error: {err}]")
    # Write target first, source second.  If the target write fails, the source
    # is still intact and the agent can retry cleanly.  If the source write
    # fails after target succeeded, the lines exist in both places — visible as
    # duplicates but recoverable — which is strictly better than silent data loss.
    to_path.write_text(to_doc.to_text(), encoding="utf-8")
    from_path.write_text(from_doc.to_text(), encoding="utf-8")

    # Update embedding index for both affected pages (fail-open)
    await _reindex_page(ctx, to_path)
    await _reindex_page(ctx, from_path)

    # Publish a vault_changed event for each affected page so the UI can
    # refresh both source and target views.
    await publish_vault_changed(
        ctx.event_bus,
        ctx.config,
        kind=KIND_MOVE,
        path=to_path,
    )
    await publish_vault_changed(
        ctx.event_bus,
        ctx.config,
        kind=KIND_MOVE,
        path=from_path,
    )

    return ToolResult(
        text=f"Moved {len(moved)} line(s) from {from_page} to {to_page}"
        + (f" section '{to_section}'" if to_section else "")
    )
