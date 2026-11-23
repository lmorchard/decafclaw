"""Vault tools — unified knowledge base operations.

Replaces the separate wiki and memory tool systems with a single vault
that supports curated pages, daily journal entries, and user content.
"""

import logging
import re
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from decafclaw.media import ToolResult, WidgetRequest
from decafclaw.skills.vault._events import (
    KIND_CREATE,
    KIND_DELETE,
    KIND_JOURNAL,
    KIND_RENAME,
    KIND_UPDATE,
    publish_vault_changed,
)
from decafclaw.skills.vault._gate import (
    GateOutcome,  # noqa: F401 — re-exported for tests/test_vault_tools.py
    _check_user_write_allowed,
    _run_gate_or_confirm,
)
from decafclaw.skills.vault._grants import (
    add_grant,
    normalize_folder,
)
from decafclaw.skills.vault._journal import (
    format_recent_journal_for_context,
    read_recent_journal_entries,
)
from decafclaw.skills.vault._paths import (
    _agent_dir,
    _is_in_agent_dir,
    _safe_folder,
    _safe_write_path,
    _vault_root,
    resolve_page,
    sanitize_vault_page_name,
    suggest_pages,
)
from decafclaw.skills.vault._search import (
    _semantic_result_matches_tags,
    _semantic_results_widget,
    _substring_results_widget,  # noqa: F401 — re-exported for tests/test_widgets_vault_search.py
    _substring_search,
    _tag_filter_search,
)
from decafclaw.tags import (
    collect_all_tags,
    extract_tags,  # noqa: F401 — re-exported for tests; _search.py resolves it via the tools namespace
    normalize_tag,
    pages_with_tags,  # noqa: F401 — re-exported for tests; _search.py resolves it via the tools namespace
)
from decafclaw.text_patching import (
    AmbiguousMatch,
    InvalidLineRange,
    PatchError,
    TextNotFound,
    content_version,
    edit_not_found_hint,
    patch_exact_match,
    patch_line_range,
    split_physical_lines,
    unified_diff,
)
from decafclaw.tools._file_common import MAX_DIFF_WIDGET_CHARS, MAX_DIFF_WIDGET_LINES
from decafclaw.tools.confirmation import request_confirmation

if TYPE_CHECKING:
    from decafclaw.context import Context

log = logging.getLogger(__name__)


def _format_vault_write_preview(config, path: Path, content: str, exists: bool) -> str:
    """Confirmation preview for vault_write. Mirrors email's preview helper."""
    try:
        rel = str(path.resolve().relative_to(config.vault_root.resolve()))
    except ValueError:
        rel = path.name
    state = "(overwrites existing page)" if exists else "(new page)"
    size_kb = len(content.encode("utf-8")) / 1024
    preview = content[:200] + ("..." if len(content) > 200 else "")
    return "\n".join(
        [
            f"Vault write to: {rel}",
            state,
            f"Content: {size_kb:.1f} KB",
            "---",
            preview,
        ]
    )


def _format_vault_delete_preview(config, path: Path) -> str:
    """Confirmation preview for vault_delete."""
    try:
        rel = str(path.resolve().relative_to(config.vault_root.resolve()))
    except ValueError:
        rel = path.name
    return f"Vault delete: {rel}\n(cannot be undone — page and embeddings will be removed)"


def _format_vault_update_frontmatter_preview(
    config,
    path: Path,
    fields: dict,
) -> str:
    """Confirmation preview for vault_update_frontmatter."""
    try:
        rel = str(path.resolve().relative_to(config.vault_root.resolve()))
    except ValueError:
        rel = path.name
    return f"Vault frontmatter update to: {rel}\nFields: {', '.join(sorted(fields))}"


def _format_vault_rename_preview(config, old_path: Path, new_path: Path) -> str:
    """Confirmation preview for vault_rename. Shows both paths."""

    def _rel(p: Path) -> str:
        try:
            return str(p.resolve().relative_to(config.vault_root.resolve()))
        except ValueError:
            return p.name

    return f"Vault rename: {_rel(old_path)} → {_rel(new_path)}"


class VaultSourceType(StrEnum):
    """Embedding source type for a vault file (#1386).

    String-valued so the embeddings index, search results, and any JSON
    round-trip keep the existing wire values; the enum makes a typo'd
    source type impossible to construct.
    """

    JOURNAL = "journal"  # under agent/journal/ (daily journal entries)
    PAGE = "page"  # elsewhere under agent/ (curated pages)
    USER = "user"  # outside agent/ (user content)


def _source_type_for_path(config, path: Path) -> VaultSourceType:
    """Determine the embedding source type based on file location."""
    resolved = path.resolve()
    try:
        if resolved.is_relative_to(config.vault_agent_journal_dir.resolve()):
            return VaultSourceType.JOURNAL
        if resolved.is_relative_to(config.vault_agent_dir.resolve()):
            return VaultSourceType.PAGE
    except (ValueError, OSError):
        pass
    return VaultSourceType.USER


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


async def tool_vault_read(
    ctx: "Context",
    page: str,
    start_line: int | None = None,
    end_line: int | None = None,
) -> str | ToolResult:
    """Read a vault page by name or path, optionally a 1-based line range."""
    log.info(f"[tool:vault_read] page={page} start_line={start_line} end_line={end_line}")
    path = resolve_page(ctx.config, page)
    if path is None:
        suggestions = suggest_pages(ctx.config, page)
        if suggestions:
            return ToolResult(text=f"[error: vault page '{page}' not found. Did you mean: {', '.join(suggestions)}?]")
        return ToolResult(text=f"[error: vault page '{page}' not found]")
    content = path.read_text(encoding="utf-8")
    # Structured metadata for programmatic callers (code_execution sandbox).
    # Body intentionally NOT echoed here — it's already in `text` and would
    # double the tool-result token cost. Frontmatter is small and useful for
    # filtering / sorting in scripts.
    from decafclaw.frontmatter import parse_frontmatter, to_json_safe

    frontmatter, body = parse_frontmatter(content)
    base_data = {
        "path": page,
        # ToolResult.data is json.dumps'd into a fenced block, and a
        # `date:` line parses to a datetime.date. Without this the whole
        # data block was dropped with a warning (fail-open in
        # tool_execution), silently costing the agent its structured view.
        "frontmatter": to_json_safe(frontmatter),
        "body_size": len(body),
        "body_lines": body.count("\n") + (0 if body.endswith("\n") or not body else 1),
        "version": content_version(content),
    }

    if start_line is None and end_line is None:
        return ToolResult(text=content, display_short_text=page, data=base_data)

    lines = split_physical_lines(content)
    total = len(lines)
    start = max(1, start_line or 1)
    end = min(total, end_line or total)
    selected = lines[start - 1 : end]
    width = len(str(end))
    numbered = [f"{str(start + i).rjust(width)}| {line}" for i, line in enumerate(selected)]
    header = f"Lines {start}-{end} of {total}:\n"
    return ToolResult(
        text=header + "\n".join(numbered),
        display_short_text=page,
        data={**base_data, "range": [start, end]},
    )


async def tool_vault_write(ctx: "Context", page: str, content: str) -> str | ToolResult:
    """Create or overwrite a vault page."""
    log.info(f"[tool:vault_write] page={page}")
    page = sanitize_vault_page_name(page)
    path = _safe_write_path(ctx.config, page)
    if path is None:
        return ToolResult(text=f"[error: invalid page name '{page}' — must be within vault directory]")
    if not content or not content.strip():
        return ToolResult(text=f"[error: refusing to write empty vault page '{page}']")

    outcome = _check_user_write_allowed(ctx, path)
    gate_result = await _run_gate_or_confirm(
        ctx,
        [outcome],
        tool_name="vault_write",
        command=f"vault_write to '{page}'",
        preview=_format_vault_write_preview(ctx.config, path, content, path.exists()),
        non_interactive_error=(
            f"[error: vault_write to '{page}' outside agent folder "
            f"requires interactive confirmation; not available from this context]"
        ),
        denied_error=f"[error: vault_write to '{page}' was denied by user]",
    )
    if gate_result is not None:
        return gate_result

    existed = path.exists()  # capture before write so kind reflects pre-state
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

    await publish_vault_changed(
        ctx.event_bus,
        ctx.config,
        kind=KIND_UPDATE if existed else KIND_CREATE,
        path=path,
    )
    result_text = f"Vault page '{page}' saved."
    result_data = {
        "path": page,
        "created": not existed,
        "bytes_written": len(content.encode("utf-8")),
    }
    # Deterministic broken-link check (no LLM, fail-open). The page is already
    # on disk, so a [[link]] to itself resolves and is not reported. Uses the
    # same helpers as vault_backlinks so the rules match exactly.
    try:
        from decafclaw.backlinks import _build_page_lookup, _resolve_link_target

        vault = _vault_root(ctx.config).resolve()
        pages = sorted(vault.rglob("*.md"))
        full_lower_map, stem_lower_map = _build_page_lookup(pages, vault)
        unresolved: list[str] = []
        seen: set[str] = set()
        for match in _WIKI_LINK_RE.finditer(content):
            raw = match.group(1)
            if _resolve_link_target(raw, full_lower_map, stem_lower_map) is None and raw not in seen:
                seen.add(raw)
                unresolved.append(raw)
        if unresolved:
            result_text += "\nUnresolved links: " + ", ".join(f"[[{raw}]]" for raw in unresolved)
            result_data["unresolved_links"] = unresolved
    except Exception as exc:
        log.debug("vault_write: broken-link check failed for %r: %s", page, exc)
    return ToolResult(text=result_text, data=result_data)


async def _gate_vault_mutation(
    ctx: "Context",
    path: Path,
    *,
    tool_name: str,
    page: str,
    preview: str,
) -> ToolResult | None:
    """Run the user-folder write gate for a page mutation (vault_edit family).

    Returns a ToolResult error when the write must not proceed, or None when
    the caller may write. Same three-tier decision as vault_write.
    """
    outcome = _check_user_write_allowed(ctx, path)
    return await _run_gate_or_confirm(
        ctx,
        [outcome],
        tool_name=tool_name,
        command=f"{tool_name} to '{page}'",
        preview=preview,
        non_interactive_error=(
            f"[error: {tool_name} to '{page}' outside agent folder "
            f"requires interactive confirmation; not available from this context]"
        ),
        denied_error=f"[error: {tool_name} to '{page}' was denied by user]",
    )


def _diff_result(
    page: str,
    summary: str,
    diff: str,
    before: str,
    after: str,
    *,
    version: str,
    data: dict | None = None,
) -> ToolResult:
    """Build the shared edit-style result: summary, diff, version, diff widget."""
    text = f"{summary}\n\n{diff}" if diff else summary
    text += f"\n\nVersion: {version}"
    oversized = (
        len(before) > MAX_DIFF_WIDGET_CHARS
        or len(after) > MAX_DIFF_WIDGET_CHARS
        or before.count("\n") > MAX_DIFF_WIDGET_LINES
        or after.count("\n") > MAX_DIFF_WIDGET_LINES
    )
    widget = (
        WidgetRequest(
            widget_type="diff_view",
            data={"before": before, "after": after, "filename": page, "view": "unified"},
            target="inline",
        )
        if diff and not oversized
        else None
    )
    payload = {"path": page, "version": version}
    if data:
        payload.update(data)
    return ToolResult(text=text, data=payload, widget=widget)


async def tool_vault_edit(
    ctx: "Context",
    page: str,
    old_text: str,
    new_text: str,
    replace_all: bool = False,
    expected_version: str | None = None,
) -> ToolResult:
    """Edit a vault page by replacing exact text matches."""
    log.info(f"[tool:vault_edit] page={page}")
    path = resolve_page(ctx.config, page)
    if path is None:
        return ToolResult(text=f"[error: vault page '{page}' not found]")

    try:
        # newline="" keeps the file's line endings (CRLF or LF) intact.
        content = path.read_text(encoding="utf-8", newline="")
    except (OSError, UnicodeDecodeError) as e:
        return ToolResult(text=f"[error: could not read vault page '{page}': {e}]")

    version = content_version(content)
    if expected_version is not None and expected_version != version:
        return ToolResult(
            text=(
                f"[error: stale version for {page}: expected {expected_version}, but the page has "
                f"changed since the last read (current version: {version}). "
                "Re-read the page with vault_read and retry the edit with the version "
                "returned by the fresh read.]"
            )
        )

    normalized = content.replace("\r\n", "\n")
    old = old_text.replace("\r\n", "\n")
    try:
        new_content, diff = patch_exact_match(content, old_text, new_text, replace_all)
    except TextNotFound:
        hint = edit_not_found_hint(normalized, old)
        text = (
            f"[error: text not found in vault page '{page}'. "
            "Make sure old_text matches exactly, including whitespace and indentation."
        )
        if hint:
            text += f" {hint}"
        return ToolResult(text=text)
    except AmbiguousMatch as exc:
        return ToolResult(
            text=f"[error: found {exc.count} matches in vault page '{page}'. "
            "Use replace_all=true for bulk replacement, "
            "or provide more surrounding context to make old_text unique.]"
        )
    except PatchError as exc:
        return ToolResult(text=f"[error: {exc} in vault page '{page}']")

    count = normalized.count(old)
    new_normalized = new_content.replace("\r\n", "\n")
    gate_result = await _gate_vault_mutation(
        ctx,
        path,
        tool_name="vault_edit",
        page=page,
        preview=f"{page}\n\n{diff or '(no changes)'}",
    )
    if gate_result is not None:
        return gate_result

    path.write_text(new_content, encoding="utf-8", newline="")
    await _reindex_page(ctx, path)
    await publish_vault_changed(ctx.event_bus, ctx.config, kind=KIND_UPDATE, path=path)
    return _diff_result(
        page,
        f"Edited vault page '{page}': replaced {count} occurrence(s)",
        diff,
        normalized,
        new_normalized,
        version=content_version(new_content),
        data={"replaced": count},
    )


async def tool_vault_append(ctx: "Context", page: str, content: str) -> ToolResult:
    """Append text to a vault page, creating it when it does not exist."""
    log.info(f"[tool:vault_append] page={page}")
    page = sanitize_vault_page_name(page)
    path = resolve_page(ctx.config, page)
    created = path is None
    if path is None:
        path = _safe_write_path(ctx.config, page)
        if path is None:
            return ToolResult(text=f"[error: invalid page name '{page}' — must be within vault directory]")

    if not content or not content.strip():
        return ToolResult(text=f"[error: refusing to append empty content to vault page '{page}']")

    if created:
        existing = ""
        preview = f"{page} (new page, {len(content)} characters)\n\n{content}"
    else:
        try:
            existing = path.read_text(encoding="utf-8", newline="")
        except (OSError, UnicodeDecodeError) as e:
            return ToolResult(text=f"[error: could not read vault page '{page}': {e}]")
        preview = f"{page} (append, {len(content)} characters)\n\n{content}"

    separator = "\n" if existing and not existing.endswith("\n") else ""
    new_content = existing + separator + content
    diff = unified_diff(existing, new_content, page) if existing else ""

    gate_result = await _gate_vault_mutation(
        ctx,
        path,
        tool_name="vault_append",
        page=page,
        preview=preview,
    )
    if gate_result is not None:
        return gate_result

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new_content, encoding="utf-8", newline="")
    await _reindex_page(ctx, path)
    await publish_vault_changed(
        ctx.event_bus,
        ctx.config,
        kind=KIND_CREATE if created else KIND_UPDATE,
        path=path,
    )
    summary = f"Appended {len(content)} characters to vault page '{page}'"
    return _diff_result(
        page,
        summary,
        diff,
        existing.replace("\r\n", "\n"),
        new_content.replace("\r\n", "\n"),
        version=content_version(new_content),
        data={"created": created, "bytes_appended": len(content.encode("utf-8"))},
    )


async def tool_vault_replace_lines(
    ctx: "Context", page: str, start_line: int, end_line: int, content: str = ""
) -> ToolResult:
    """Replace a 1-based inclusive line range in a vault page."""
    log.info(f"[tool:vault_replace_lines] page={page} lines {start_line}-{end_line}")
    path = resolve_page(ctx.config, page)
    if path is None:
        return ToolResult(text=f"[error: vault page '{page}' not found]")

    try:
        existing = path.read_text(encoding="utf-8", newline="")
    except (OSError, UnicodeDecodeError) as e:
        return ToolResult(text=f"[error: could not read vault page '{page}': {e}]")

    try:
        new_content, diff = patch_line_range(existing, start_line, end_line, content)
    except InvalidLineRange as exc:
        return ToolResult(text=f"[error: {exc}]")

    gate_result = await _gate_vault_mutation(
        ctx,
        path,
        tool_name="vault_replace_lines",
        page=page,
        preview=f"{page}\n\n{diff or '(no changes)'}",
    )
    if gate_result is not None:
        return gate_result

    path.write_text(new_content, encoding="utf-8", newline="")
    await _reindex_page(ctx, path)
    await publish_vault_changed(ctx.event_bus, ctx.config, kind=KIND_UPDATE, path=path)
    if not content:
        summary = f"Deleted lines {start_line}-{end_line} from vault page '{page}'"
    else:
        replacement_text = content if content.endswith("\n") else content + "\n"
        replacement_count = len(split_physical_lines(replacement_text, keepends=True))
        summary = f"Replaced lines {start_line}-{end_line} with {replacement_count} line(s) in vault page '{page}'"
    return _diff_result(
        page,
        summary,
        diff,
        existing.replace("\r\n", "\n"),
        new_content.replace("\r\n", "\n"),
        version=content_version(new_content),
    )


def _deleted_link_match(raw: str, deleted_rel: str) -> bool:
    """True when raw [[link]] text targeted the deleted page."""
    target = raw.split("|")[0].split("#")[0].strip()
    if target.endswith(".md"):
        target = target[:-3]
    if not target:
        return False
    deleted_no_ext = deleted_rel[:-3] if deleted_rel.endswith(".md") else deleted_rel
    if target.lower() == deleted_no_ext.lower():
        return True
    return Path(target).stem.lower() == Path(deleted_rel).stem.lower()


def _inbound_referrers(config, rel_path: str) -> list[str]:
    """Pages that link to `rel_path`, from the persisted backlink index."""
    from decafclaw.backlinks import load_index

    try:
        return sorted(load_index(config).get(rel_path, []))
    except Exception as exc:  # fail-open
        log.debug("vault_delete: inbound lookup failed for %r: %s", rel_path, exc)
        return []


def _dangling_referrers_after_delete(config, deleted_rel: str, referrers: list[str]) -> list[str]:
    """Referrers whose links to the deleted page no longer resolve."""
    from decafclaw.backlinks import _build_page_lookup, _resolve_link_target

    vault = _vault_root(config).resolve()
    pages = sorted(vault.rglob("*.md"))
    full_lower_map, stem_lower_map = _build_page_lookup(pages, vault)
    dangling: list[str] = []
    for referrer in referrers:
        try:
            text = (vault / referrer).read_text(encoding="utf-8")
        except OSError as exc:
            log.debug("vault_delete: could not read referrer %r: %s", referrer, exc)
            continue
        for match in _WIKI_LINK_RE.finditer(text):
            raw = match.group(1)
            if _deleted_link_match(raw, deleted_rel) and (
                _resolve_link_target(raw, full_lower_map, stem_lower_map) is None
            ):
                dangling.append(referrer)
                break
    return sorted(set(dangling))


async def tool_vault_delete(
    ctx: "Context",
    page: str,
    allow_broken_links: bool = False,
    dry_run: bool | None = None,
) -> ToolResult:
    """Delete a vault page with an inbound-link guard.

    Agent pages allow direct delete; user pages require confirmation per call
    (or grant/allowlist short-circuit). A page with inbound links is refused
    unless ``allow_broken_links`` is set; the allowed path reports the pages
    that now contain dangling links. In unattended contexts
    (``ctx.request_confirmation is None``), an unset ``dry_run`` defaults to
    reporting rather than deleting (#1717).
    """
    log.info(f"[tool:vault_delete] page={page} allow_broken_links={allow_broken_links} dry_run={dry_run}")
    path = _safe_write_path(ctx.config, page)
    if path is None:
        return ToolResult(text=f"[error: invalid page name '{page}' — must be within vault directory]")
    if not path.exists():
        return ToolResult(text=f"[error: vault page '{page}' not found]")

    vault_resolved = _vault_root(ctx.config).resolve()
    rel_path = str(path.resolve().relative_to(vault_resolved))
    referrers = _inbound_referrers(ctx.config, rel_path)
    effective_dry_run = (ctx.request_confirmation is None) if dry_run is None else dry_run

    if effective_dry_run:
        if referrers and not allow_broken_links:
            preview = ", ".join(referrers[:10]) + ("…" if len(referrers) > 10 else "")
            return ToolResult(
                text=(
                    f"[dry run] Would delete '{page}', but {len(referrers)} page(s) link to it "
                    f"({preview}). Pass allow_broken_links=true to proceed and report the "
                    "dangling links."
                ),
                data={
                    "dry_run": True,
                    "would_delete": False,
                    "blocked": True,
                    "inbound_links": referrers,
                },
            )
        suffix = f" It is linked from {len(referrers)} page(s)." if referrers else ""
        return ToolResult(
            text=f"[dry run] Would delete '{page}'.{suffix}",
            data={"dry_run": True, "would_delete": True, "blocked": False, "inbound_links": referrers},
        )

    if referrers and not allow_broken_links:
        preview = ", ".join(referrers[:10]) + ("…" if len(referrers) > 10 else "")
        return ToolResult(
            text=(
                f"[error: refusing to delete '{page}': {len(referrers)} page(s) link to it "
                f"({preview}). Pass allow_broken_links=true to delete and report the "
                "dangling links.]"
            ),
            data={"inbound_links": referrers},
        )

    outcome = _check_user_write_allowed(ctx, path)
    gate_result = await _run_gate_or_confirm(
        ctx,
        [outcome],
        tool_name="vault_delete",
        command=f"vault_delete '{page}'",
        preview=_format_vault_delete_preview(ctx.config, path),
        non_interactive_error=(
            f"[error: vault_delete of '{page}' outside agent folder "
            f"requires interactive confirmation; not available from this context]"
        ),
        denied_error=f"[error: vault_delete of '{page}' was denied by user]",
    )
    if gate_result is not None:
        return gate_result

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

    await publish_vault_changed(
        ctx.event_bus,
        ctx.config,
        kind=KIND_DELETE,
        path=path,
    )
    result_text = f"Vault page '{page}' deleted."
    result_data = {"path": page, "inbound_links": referrers}
    if referrers and allow_broken_links:
        dangling = _dangling_referrers_after_delete(ctx.config, rel_path, referrers)
        result_data["dangling_pages"] = dangling
        if dangling:
            preview = ", ".join(dangling[:10]) + ("…" if len(dangling) > 10 else "")
            result_text += f" Warning: {len(dangling)} page(s) now contain dangling links: {preview}."
    return ToolResult(text=result_text, data=result_data)


async def tool_vault_rename(ctx: "Context", page: str, rename_to: str) -> ToolResult:
    """Rename or move a vault page. Agent pages allow direct rename; user pages
    require confirmation per call (or grant/allowlist short-circuit). A single
    confirmation covers both source and target paths."""
    log.info(f"[tool:vault_rename] {page} -> {rename_to}")
    rename_to = sanitize_vault_page_name(rename_to)
    old_path = _safe_write_path(ctx.config, page)
    if old_path is None:
        return ToolResult(text=f"[error: invalid page name '{page}' — must be within vault directory]")
    new_path = _safe_write_path(ctx.config, rename_to)
    if new_path is None:
        return ToolResult(text=f"[error: invalid target '{rename_to}' — must be within vault directory]")
    if not old_path.exists():
        return ToolResult(text=f"[error: vault page '{page}' not found]")
    if new_path.exists():
        return ToolResult(text=f"[error: target '{rename_to}' already exists]")

    # Combined gate: a single confirmation must cover BOTH paths.
    # If either path is non-interactive-error, the whole op fails.
    # If either needs confirmation (and neither errors), confirm once.
    old_outcome = _check_user_write_allowed(ctx, old_path)
    new_outcome = _check_user_write_allowed(ctx, new_path)
    gate_result = await _run_gate_or_confirm(
        ctx,
        [old_outcome, new_outcome],
        tool_name="vault_rename",
        command=f"vault_rename '{page}' -> '{rename_to}'",
        preview=_format_vault_rename_preview(ctx.config, old_path, new_path),
        non_interactive_error=(
            f"[error: vault_rename '{page}' -> '{rename_to}' touches a path "
            f"outside the agent folder and requires interactive confirmation; "
            f"not available from this context]"
        ),
        denied_error=f"[error: vault_rename '{page}' -> '{rename_to}' was denied by user]",
    )
    if gate_result is not None:
        return gate_result

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

    await publish_vault_changed(
        ctx.event_bus,
        ctx.config,
        kind=KIND_RENAME,
        path=new_path,
    )
    return ToolResult(text=f"Vault page '{page}' renamed to '{rename_to}'.")


async def tool_vault_grant_folder(ctx: "Context", folder: str, reason: str) -> ToolResult:
    """Request per-conversation trust for a vault folder.

    The folder is normalized via `normalize_folder` (leading `/` stripped,
    trailing `/` enforced, `..` rejected) so inputs are accepted on the same
    terms as `vault.user_writable_paths` config entries.

    After user approval, vault_write/delete/rename under the granted folder
    skip per-call confirmation for the rest of the conversation. The grant is
    persisted as a sidecar at workspace/conversations/{conv_id}/vault_grants.json.
    """
    log.info(f"[tool:vault_grant_folder] folder={folder!r}")

    # Path safety: empty / .. / vault-escape are rejected up front. Leading
    # `/` is stripped by normalize_folder for consistency with the allowlist
    # config (which also strips it), so /etc/passwd lands as etc/passwd/ and
    # is then evaluated by the vault-containment check below.
    if not isinstance(folder, str) or not folder.strip():
        return ToolResult(text="[error: folder is required]")
    if not isinstance(reason, str) or not reason.strip():
        return ToolResult(text="[error: reason is required]")

    normalized = normalize_folder(folder)
    if not normalized:
        return ToolResult(text=f"[error: invalid folder '{folder}']")

    vault = _vault_root(ctx.config).resolve()
    candidate = (vault / normalized.rstrip("/")).resolve()
    if not candidate.is_relative_to(vault):
        return ToolResult(text=f"[error: folder '{folder}' escapes the vault]")

    # Reject if folder is inside agent/ (no grant needed — direct write allowed).
    # `is_relative_to` returns True when the path equals itself, so it covers
    # both the equality and subtree cases.
    try:
        agent = _agent_dir(ctx.config).resolve()
        if candidate.is_relative_to(agent):
            return ToolResult(text=f"[error: '{folder}' is inside the agent folder; no grant needed]")
    except (ValueError, OSError):
        pass

    # Validate context BEFORE prompting, so we never ask for a doomed grant.
    if ctx.request_confirmation is None:
        return ToolResult(
            text=("[error: vault_grant_folder requires interactive confirmation; not available from this context]")
        )
    if not ctx.conv_id:
        return ToolResult(text="[error: vault_grant_folder requires a conversation context]")

    rel = str(candidate.relative_to(vault)).replace("\\", "/")
    if not rel.endswith("/"):
        rel = rel + "/"

    msg = (
        f"Grant vault write/delete/rename trust for folder '{rel}' "
        f"for the rest of this conversation?\n\nReason: {reason}"
    )
    approval = await request_confirmation(
        ctx,
        tool_name="vault_grant_folder",
        command=f"trust folder '{rel}'",
        message=msg,
    )
    if not approval.get("approved"):
        return ToolResult(text=f"[error: folder grant for '{rel}' was denied by user]")

    if not add_grant(ctx.config, ctx.conv_id, rel):
        return ToolResult(text=f"[error: failed to persist folder grant for '{rel}']")
    return ToolResult(text=f"Folder '{rel}' trusted for this conversation.")


async def tool_vault_journal_append(ctx: "Context", tags: list[str], content: str) -> ToolResult:
    """Append a timestamped entry to today's journal file."""
    log.info(f"[tool:vault_journal_append] tags={tags}")
    now = datetime.now()
    journal_dir = ctx.config.vault_agent_journal_dir / str(now.year)
    journal_dir.mkdir(parents=True, exist_ok=True)

    filepath = journal_dir / f"{now:%Y-%m-%d}.md"
    tag_str = ", ".join(tags) if tags else "untagged"
    # Inline #tags mirror the bullet for extract_tags/#318 (see tags.py); a
    # tag containing whitespace can't round-trip as a single inline token
    # (Obsidian-style #tags don't allow spaces), so it's skipped there and
    # only surfaces via the bullet.
    stripped_tags = [t.lstrip("#").strip() for t in tags]
    inline_tags = [t for t in stripped_tags if t and not any(ch.isspace() for ch in t)]
    inline_tag_line = " ".join(f"#{t}" for t in inline_tags)

    entry = f"\n## {now:%Y-%m-%d %H:%M}\n\n"
    channel_name = ctx.channel_name
    channel_id = ctx.channel_id
    thread_id = ctx.thread_id
    if channel_name or channel_id:
        entry += f"- **channel:** {channel_name} ({channel_id})\n"
    if thread_id:
        entry += f"- **thread:** {thread_id}\n"
    entry += f"- **tags:** {tag_str}\n"
    if inline_tag_line:
        entry += f"{inline_tag_line}\n"
    entry += f"\n{content}\n"

    with open(filepath, "a", encoding="utf-8") as f:
        f.write(entry)

    # Index for semantic search
    try:
        from decafclaw.embeddings import index_entry

        rel_path = str(filepath.resolve().relative_to(ctx.config.vault_root.resolve()))
        # Format the full entry for embedding (self-contained with date/metadata)
        entry_text = f"## {now:%Y-%m-%d %H:%M}\n\n"
        if channel_name or channel_id:
            entry_text += f"- **channel:** {channel_name} ({channel_id})\n"
        if thread_id:
            entry_text += f"- **thread:** {thread_id}\n"
        entry_text += f"- **tags:** {tag_str}\n"
        if inline_tag_line:
            entry_text += f"{inline_tag_line}\n"
        entry_text += f"\n{content}"
        await index_entry(ctx.config, rel_path, entry_text, source_type=VaultSourceType.JOURNAL)
    except Exception as e:
        log.warning(f"Failed to index journal entry: {e}")

    await publish_vault_changed(
        ctx.event_bus,
        ctx.config,
        kind=KIND_JOURNAL,
        path=filepath,
    )
    log.info(f"Saved journal entry tagged [{tag_str}]")
    vault_root = ctx.config.vault_root.resolve()
    rel = str(filepath.resolve().relative_to(vault_root))
    return ToolResult(
        text=f"Saved journal entry tagged [{tag_str}]",
        data={
            "path": rel,
            "tags": list(tags) if tags else [],
            "entry_size": len(entry.encode("utf-8")),
        },
    )


async def tool_vault_search(
    ctx: "Context",
    query: str = "",
    source_type: str = "",
    days: int = 0,
    folder: str = "",
    tags: list[str] | None = None,
    any_tag: bool = False,
) -> str | ToolResult:
    """Search the vault using semantic or substring matching, optionally
    filtered by tag.

    ``query`` defaults to ``""`` so the tags-only mode below is callable at
    all. It was a required positional until #669, which made the pure tag
    filter documented here raise ``TypeError`` — the schema advertised the
    mode via the ``tags`` description while simultaneously marking ``query``
    required, so a compliant tags-only call could not succeed.

    Tag filtering has two modes:
    - Empty ``query`` + non-empty ``tags``: pure tag filter, bypasses
      semantic/substring search entirely (`pages_with_tags`).
    - Non-empty ``query`` + non-empty ``tags``: run the normal search, then
      drop results whose extracted tags don't satisfy the filter.
    Empty/omitted ``tags`` leaves behavior unchanged (no filtering) — a
    non-empty `tags` list is required to enter either tag-filter mode, since
    `pages_with_tags(config, [])` would otherwise vacuously match everything.
    The one exception is a call that narrows on *nothing*: a blank ``query``
    with no ``tags``/``folder``/``source_type``/``days`` is refused outright and
    told to use `vault_list`, rather than returning the whole vault framed as
    search results (#673).
    """
    req_tags = {normalize_tag(t) for t in tags} if tags else set()
    log.info(
        f"[tool:vault_search] query={query!r} source_type={source_type} "
        f"days={days} folder={folder} tags={sorted(req_tags)} "
        f"any_tag={any_tag}"
    )

    vault = _vault_root(ctx.config)
    if not vault.is_dir():
        return ToolResult(text="Vault directory does not exist.", display_short_text="no vault")

    if folder:
        safe = _safe_folder(ctx.config, folder)
        if safe is None:
            return ToolResult(text=f"[error: invalid folder path '{folder}']")

    # Nothing narrows the call: refuse rather than enumerate the vault (#673).
    # This has to sit ahead of the tags-only branch below, so an unconstrained
    # call never reaches any filter path. Deliberately carries no `data` —
    # `execute_single_tool` appends `json.dumps(result.data)` to the
    # model-visible tool message, so attaching the page list there would defeat
    # the point even with the text replaced.
    # `query.strip()`, not `query`: a query of " " is a substring of nearly
    # every markdown file, so a truthiness test here would let `query=" "`
    # return the whole vault — the exact defect this guard exists to stop, one
    # character away from the refused call.
    if not query.strip() and not req_tags and not folder and not source_type and days <= 0:
        return ToolResult(
            text=(
                "[error: vault_search needs at least one of `query`, `tags`, "
                "`folder`, `source_type` or `days`. A call with none of them is not a "
                "search — it would return every page in the vault. Use "
                "`vault_list` to enumerate pages, or retry with a query.]"
            ),
            display_short_text="no search criteria",
        )

    # `.strip()` here for the same reason as the guard above: a whitespace-only
    # query is no query, so `query=" "` + tags belongs in the pure tag filter
    # rather than falling through to a substring scan of the whole vault that
    # is then tag-filtered to the same answer, more expensively.
    if not query.strip() and req_tags:
        return _tag_filter_search(ctx.config, req_tags, any_tag, source_type=source_type, folder=folder, days=days)

    # Try semantic search first
    if ctx.config.embedding.search_strategy == "semantic":
        try:
            from decafclaw.embeddings import search_similar

            st = source_type if source_type else None
            results = await search_similar(ctx.config, query, top_k=10, source_type=st)

            # Apply days filter
            if days > 0:
                from datetime import timedelta

                cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)
                filtered = []
                for r in results:
                    fp = vault / r.get("file_path", "")
                    if fp.exists():
                        mtime = datetime.fromtimestamp(fp.stat().st_mtime, tz=timezone.utc)
                        if mtime >= cutoff:
                            filtered.append(r)
                results = filtered

            # Apply folder filter
            if folder:
                results = [r for r in results if r.get("file_path", "").startswith(folder)]

            # Apply tag filter (post-search: drop non-matching results)
            if req_tags:
                results = [r for r in results if _semantic_result_matches_tags(ctx.config, r, req_tags, any_tag)]

            if results:
                lines = [f"Found {len(results)} result(s):\n"]
                for i, r in enumerate(results):
                    sim = f"{r['similarity']:.2f}"
                    src = r.get("source_type", "?")
                    lines.append(f"--- Result {i + 1} [{src}] (relevance: {sim}) ---\n{r['entry_text']}")
                text = "\n\n".join(lines)
                widget = _semantic_results_widget(query, results)
                # Structured shape for programmatic callers. Mirrors the
                # widget rows but with the full entry_text for scripts that
                # want to compute on snippets without re-fetching.
                data_results = [
                    {
                        "path": r.get("file_path", "").removesuffix(".md"),
                        "similarity": round(float(r.get("similarity", 0.0)), 3),
                        "source_type": r.get("source_type", ""),
                        "snippet": r.get("entry_text", ""),
                    }
                    for r in results
                ]
                return ToolResult(
                    text=text,
                    display_short_text=f"'{query}' — {len(results)} result(s)",
                    widget=widget,
                    data={
                        "query": query,
                        "mode": "semantic",
                        "results": data_results,
                    },
                )
            # Fall through to substring
            log.info("Semantic search returned no results, falling back to substring")
        except Exception as e:
            log.error(f"Semantic search failed, falling back to substring: {e}")

    # Substring search across vault
    return _substring_search(
        ctx.config, query, days=days, folder=folder, req_tags=req_tags, any_tag=any_tag, source_type=source_type
    )


async def tool_vault_list(ctx: "Context", folder: str = "", pattern: str = "") -> str | ToolResult:
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
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
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


def collect_recent_pages(config, cutoff_ts: float, folder: str = "", source_type: str = "") -> list[dict]:
    """Vault ``.md`` pages modified at/after ``cutoff_ts`` (epoch seconds).

    Returns ``[{path, mtime (float), source_type, size}]`` (unsorted; callers
    sort). Symlink-safe: a file whose resolved path escapes the vault root is
    skipped, so symlinked directories can't leak external files into the
    listing. Shared by ``vault_recent`` and the newsletter's change scan.
    """
    search_root = _safe_folder(config, folder)
    if search_root is None or not search_root.is_dir():
        return []
    vault = _vault_root(config)
    vault_resolved = vault.resolve()

    out: list[dict] = []
    for path in search_root.rglob("*.md"):
        try:
            resolved = path.resolve()
            if not resolved.is_relative_to(vault_resolved):
                continue
            stat = resolved.stat()
        except OSError:
            continue
        if stat.st_mtime < cutoff_ts:
            continue
        st = _source_type_for_path(config, path)
        if source_type and st != source_type:
            continue
        # Use the resolved pair consistently: when vault_root is a symlink and
        # `folder` is given, search_root (and thus `path`) is resolved while
        # `vault` is not, so `path.relative_to(vault)` would raise ValueError.
        out.append(
            {
                "path": resolved.relative_to(vault_resolved).as_posix(),
                "mtime": stat.st_mtime,
                "source_type": st,
                "size": stat.st_size,
            }
        )
    return out


async def tool_vault_recent(ctx: "Context", days: int = 7, folder: str = "", source_type: str = "") -> str | ToolResult:
    """List vault pages modified within the last `days`, newest first."""
    log.info(f"[tool:vault_recent] days={days} folder={folder} source_type={source_type}")
    if days <= 0:
        return ToolResult(text="[error: days must be a positive integer]")
    safe = _safe_folder(ctx.config, folder)
    if safe is None:
        return ToolResult(text=f"[error: invalid folder path '{folder}']")
    if not safe.is_dir():
        if folder:
            return ToolResult(text=f"[error: folder '{folder}' does not exist]")
        return "Vault directory does not exist."
    cutoff = (datetime.now(tz=timezone.utc) - timedelta(days=days)).timestamp()
    rows = collect_recent_pages(ctx.config, cutoff, folder=folder, source_type=source_type)
    rows.sort(key=lambda r: r["mtime"], reverse=True)

    scope = f" in '{folder}'" if folder else ""
    st_note = f" (source_type={source_type})" if source_type else ""
    if not rows:
        return f"No pages modified in the last {days} day(s){scope}{st_note}."

    limit = 100
    shown = rows[:limit]

    def _fmt(ts):
        return datetime.fromtimestamp(ts, tz=timezone.utc)

    lines = [
        f"- {r['path']} ({r['source_type']}, modified: {_fmt(r['mtime']).strftime('%Y-%m-%d %H:%M')})" for r in shown
    ]
    header = f"{len(rows)} page(s) modified in the last {days} day(s){scope}{st_note}"
    if len(rows) > limit:
        header += f" (showing {limit} most recent)"
    return ToolResult(
        text=header + ":\n\n" + "\n".join(lines),
        data={
            "days": days,
            "folder": folder,
            "source_type": source_type,
            "count": len(rows),
            "pages": [
                {
                    "path": r["path"],
                    "source_type": r["source_type"],
                    "modified": _fmt(r["mtime"]).isoformat(),
                }
                for r in shown
            ],
        },
    )


async def tool_vault_tags(ctx: "Context") -> ToolResult:
    """List all tags currently in use across the vault, with usage counts."""
    log.info("[tool:vault_tags]")
    tag_map = collect_all_tags(ctx.config)
    if not tag_map:
        return ToolResult(text="No tags found in the vault.", data={"tags": []})

    # Sorted by count desc, tie-broken by (normalized) tag name asc so the
    # ordering is deterministic regardless of dict iteration order.
    ordered = sorted(tag_map.items(), key=lambda kv: (-kv[1]["count"], kv[0]))
    tags = [{"tag": info["display"], "count": info["count"]} for _, info in ordered]

    lines = [f"- {t['tag']} ({t['count']})" for t in tags]
    header = f"{len(tags)} tag(s) in use across the vault"
    return ToolResult(text=f"{header}:\n\n" + "\n".join(lines), data={"tags": tags})


_WIKI_LINK_RE = re.compile(r"\[\[([^\]]+)\]\]")


async def tool_vault_backlinks(ctx: "Context", page: str) -> str:
    """Find all vault pages that link to the given page via [[wiki-links]]."""
    log.info(f"[tool:vault_backlinks] page={page}")
    from decafclaw.backlinks import _build_page_lookup, _resolve_link_target, load_index

    vault = _vault_root(ctx.config)
    if not vault.is_dir():
        return f"No backlinks to '{page}' (vault directory does not exist)."
    # resolve_page() returns a fully-resolved path, so `vault` must be
    # resolved too before relative_to() — otherwise a symlinked vault_root
    # component makes relative_to() raise (fail-open swallows it silently).
    vault = vault.resolve()

    resolved = resolve_page(ctx.config, page)
    if resolved is None:
        return f"No pages link to '{page}'."
    target_rel = resolved.relative_to(vault).as_posix()

    linkers = load_index(ctx.config).get(target_rel, [])
    if not linkers:
        return f"No pages link to '{page}'."

    # Persisted index tells us WHICH pages link here; re-read just those
    # pages (not the whole vault) to pull a one-line quote for context.
    full_lower_map, stem_lower_map = _build_page_lookup(sorted(vault.rglob("*.md")), vault)

    results = []
    for linker_rel in linkers:
        try:
            text = (vault / linker_rel).read_text(encoding="utf-8")
        except OSError as exc:
            log.debug("vault_backlinks: failed reading %s: %s", linker_rel, exc)
            continue
        context_line = ""
        for match in _WIKI_LINK_RE.finditer(text):
            if _resolve_link_target(match.group(1), full_lower_map, stem_lower_map) == target_rel:
                line_no = text[: match.start()].count("\n")
                lines = text.splitlines()
                context_line = lines[line_no].strip()[:200] if line_no < len(lines) else ""
                break
        rel_display = Path(linker_rel).with_suffix("")
        results.append(f"- **{rel_display}**: {context_line}")

    return f"{len(results)} page(s) link to '{page}':\n\n" + "\n".join(results)


async def _reindex_page(ctx: "Context", path: Path) -> None:
    """Reindex a single vault page in the embedding index (fail-open)."""
    try:
        from decafclaw.embeddings import delete_entries, index_entry
        from decafclaw.frontmatter import build_composite_text, parse_frontmatter

        rel = str(path.resolve().relative_to(ctx.config.vault_root.resolve()))
        source_type = _source_type_for_path(ctx.config, path)
        content = path.read_text(encoding="utf-8")
        delete_entries(ctx.config, rel, source_type=source_type)
        metadata, body = parse_frontmatter(content)
        embed_text = build_composite_text(metadata, body)
        await index_entry(ctx.config, rel, embed_text, source_type=source_type)
    except Exception as e:
        log.warning(f"Failed to reindex vault page '{path}': {e}")


async def tool_vault_update_frontmatter(
    ctx,
    page: str,
    fields: dict,
    overwrite: bool = False,
) -> ToolResult:
    """Merge frontmatter fields into a vault page (thin wrapper over
    `merge_frontmatter`).

    Interactive callers updating a user-owned page outside agent/ go through
    the same confirmation gate as `vault_write`. Non-interactive callers
    (scheduled dream/garden) can't prompt, so they proceed ungated — vault-
    wide reach for scheduled maintenance is this tool's intended purpose.
    """
    log.info(f"[tool:vault_update_frontmatter] page={page!r} overwrite={overwrite}")
    path = resolve_page(ctx.config, page)
    if path is None:
        return ToolResult(text=f"[error: vault page '{page}' not found]")

    if ctx.request_confirmation is not None:
        outcome = _check_user_write_allowed(ctx, path)
        gate_result = await _run_gate_or_confirm(
            ctx,
            [outcome],
            tool_name="vault_update_frontmatter",
            command=f"vault_update_frontmatter on '{page}'",
            preview=_format_vault_update_frontmatter_preview(ctx.config, path, fields),
            non_interactive_error=(
                f"[error: vault_update_frontmatter on '{page}' outside agent "
                f"folder requires interactive confirmation; not available "
                f"from this context]"
            ),
            denied_error=(f"[error: vault_update_frontmatter on '{page}' was denied by user]"),
        )
        if gate_result is not None:
            return gate_result

    from decafclaw.frontmatter import (
        build_composite_text,
        merge_frontmatter,
        parse_frontmatter,
        serialize_frontmatter,
    )

    content = path.read_text(encoding="utf-8")
    metadata, body = parse_frontmatter(content)
    merged = merge_frontmatter(metadata, fields, overwrite)
    changed = sorted(field for field in fields if merged.get(field) != metadata.get(field))

    if not changed:
        return ToolResult(
            text=f"No frontmatter changes for '{page}'",
            data={"path": page, "changed": []},
        )

    # Only reindex when the embedded text actually changed. Fields such as
    # `importance` are not part of `build_composite_text`; reindexing for
    # them costs an embedding round trip and changes nothing (#1687).
    reindex_needed = build_composite_text(metadata, body) != build_composite_text(merged, body)
    path.write_text(serialize_frontmatter(merged, body), encoding="utf-8")
    if reindex_needed:
        await _reindex_page(ctx, path)
    await publish_vault_changed(
        ctx.event_bus,
        ctx.config,
        kind=KIND_UPDATE,
        path=path,
    )

    return ToolResult(
        text=f"Updated frontmatter for '{page}': {', '.join(changed)}",
        data={"path": page, "changed": changed},
    )


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------

TOOLS = {
    "vault_read": tool_vault_read,
    "vault_write": tool_vault_write,
    "vault_delete": tool_vault_delete,
    "vault_rename": tool_vault_rename,
    "vault_grant_folder": tool_vault_grant_folder,
    "vault_journal_append": tool_vault_journal_append,
    "vault_search": tool_vault_search,
    "vault_list": tool_vault_list,
    "vault_recent": tool_vault_recent,
    "vault_tags": tool_vault_tags,
    "vault_backlinks": tool_vault_backlinks,
    "vault_edit": tool_vault_edit,
    "vault_append": tool_vault_append,
    "vault_replace_lines": tool_vault_replace_lines,
    "vault_update_frontmatter": tool_vault_update_frontmatter,
}

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "priority": "critical",
        "writes": False,
        "function": {
            "name": "vault_read",
            "description": (
                "Read a vault page (knowledge base) by name or path. ONLY for "
                "vault knowledge pages — NOT for workspace files, blog posts, "
                "code, or project files (use workspace_read for those). Returns "
                "the full page content, or a 1-based inclusive line range with "
                "start_line/end_line. Each read returns a content version token; "
                "pass it as expected_version to vault_edit to reject edits "
                "against a stale read."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "page": {
                        "type": "string",
                        "description": ("Page name or path (e.g. 'Les Orchard', 'agent/pages/DecafClaw')"),
                    },
                    "start_line": {
                        "type": "integer",
                        "description": "First line to read (1-based, inclusive). Omit to start from the beginning.",
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "Last line to read (1-based, inclusive). Omit to read to the end of the page.",
                    },
                },
                "required": ["page"],
            },
        },
    },
    {
        "type": "function",
        "priority": "critical",
        "timeout": None,
        "writes": True,
        "function": {
            "name": "vault_write",
            "description": (
                "Create or overwrite a vault page (knowledge base). ONLY for "
                "vault knowledge pages — NOT for workspace files, blog posts, "
                "code, configs, or any file in a project directory. Use "
                "workspace_write for those. ALWAYS vault_read first if "
                "updating an existing page to preserve content you want to keep. "
                "Use [[Page Name]] syntax to link to other pages. Include a "
                "## Sources section. Pages outside the agent folder (agent/pages/, "
                "agent/journal/) require user confirmation per call. For batch "
                "operations on user folders, request folder trust via "
                "vault_grant_folder first."
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
        "priority": "normal",
        "timeout": None,
        "writes": True,
        "function": {
            "name": "vault_delete",
            "description": (
                "DESTRUCTIVE — permanently delete a vault page and its embedding "
                "entries. Refuses when other pages link to it unless "
                "allow_broken_links=true, which reports the pages left with "
                "dangling links. In unattended contexts (no interactive "
                "confirmation available), dry_run defaults to true and only "
                "reports what would happen. Pages outside the agent folder "
                "(agent/pages/, agent/journal/) require user confirmation per "
                "call. For batch operations use vault_delete_plan from the garden "
                "skill. Prefer vault_write with updated content to retire or mark "
                "pages superseded; use delete only when the page is definitively "
                "wrong, duplicate, or no longer reachable."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "page": {
                        "type": "string",
                        "description": ("Page path relative to vault root (e.g. 'agent/pages/Stale Draft')."),
                    },
                    "allow_broken_links": {
                        "type": "boolean",
                        "description": (
                            "Delete even when other pages link here, and report which pages now contain dangling links."
                        ),
                    },
                    "dry_run": {
                        "type": "boolean",
                        "description": (
                            "Report what would happen without deleting. Defaults to true in unattended contexts."
                        ),
                    },
                },
                "required": ["page"],
            },
        },
    },
    {
        "type": "function",
        "priority": "low",
        "timeout": None,
        "writes": True,
        "function": {
            "name": "vault_rename",
            "description": (
                "Rename or move a vault page. Updates the embedding index so "
                "search results stay consistent. Pages outside the agent folder "
                "(agent/pages/, agent/journal/) require user confirmation per "
                "call. For batch operations, request folder trust via "
                "vault_grant_folder first. Use this to reorganize or refine page "
                "names — prefer it over delete + rewrite when the content stays "
                "the same. Target must not already exist."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "page": {
                        "type": "string",
                        "description": ("Current page path relative to vault root (e.g. 'agent/pages/old-name')."),
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
        "priority": "low",
        "timeout": None,
        "writes": True,
        "function": {
            "name": "vault_grant_folder",
            "description": (
                "Request user trust for a vault folder for the rest of this "
                "conversation. Use this BEFORE a batch operation on user-owned "
                "folders (3+ writes/deletes/renames in the same area) to avoid "
                "per-page confirmations. After approval, vault_write, "
                "vault_delete, and vault_rename under the folder skip "
                "confirmation. For one-off operations, the regular tools handle "
                "confirmation per-call — don't call this for a single page."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "folder": {
                        "type": "string",
                        "description": ("Vault-relative folder path (e.g. 'creative/in-progress/fungal-world')."),
                    },
                    "reason": {
                        "type": "string",
                        "description": (
                            "Short user-facing reason. Shown in the confirmation "
                            "message so the user knows why the grant is being "
                            "requested."
                        ),
                    },
                },
                "required": ["folder", "reason"],
            },
        },
    },
    {
        "type": "function",
        "priority": "critical",
        "writes": True,
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
        "priority": "critical",
        "writes": False,
        "function": {
            "name": "vault_search",
            "description": (
                "Search the vault — your curated knowledge base of pages, journal "
                "entries, and user notes — using semantic or substring matching. "
                "**Use this for resolved knowledge: decisions, conclusions, design "
                "choices, project facts, and anything you previously wrote down.** "
                "Prefer this over conversation_search whenever the user is asking "
                "about a settled question, even when phrased as 'what did we decide' "
                "or 'we talked about' — decisions live as curated vault pages, not "
                "in raw chat history. ALWAYS try this BEFORE web search.\n\n"
                "Use natural language queries.\n\n"
                "**IMPORTANT:** When this tool returns results, you MUST use them. "
                "Do NOT ignore results or claim you have no information when results "
                "were returned. Try at least 3 query variations before giving up."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "Search text (natural language or keywords). "
                            "Optional: omit it (or pass an empty string) "
                            "together with `tags` to run a pure tag filter "
                            "that skips search entirely. Do NOT omit it with "
                            "no other filter — an empty query with no `tags`/"
                            "`folder`/`source_type`/`days` is refused, because "
                            "it would return every page in the vault rather "
                            "than searching. Use `vault_list` when you want to "
                            "enumerate pages."
                        ),
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
                        "description": ("Limit to files modified in the last N days. Useful for recent context."),
                    },
                    "folder": {
                        "type": "string",
                        "description": ("Limit search to a vault subfolder (e.g. 'agent/journal' or 'agent/pages')"),
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Filter to pages/entries carrying these tags "
                            "(case-insensitive, leading '#' optional). "
                            "Default AND: must have ALL listed tags — set "
                            "any_tag=true for OR. Empty query + tags = pure "
                            "tag filter (skips search); query + tags = "
                            "search first, then drop non-matching results. "
                            "Empty/omitted = no tag filter."
                        ),
                    },
                    "any_tag": {
                        "type": "boolean",
                        "description": (
                            "With tags set, match ANY listed tag (OR) "
                            "instead of requiring all (AND, the default). "
                            "Ignored if tags is empty."
                        ),
                    },
                },
                # No required params: `query` is optional so a tags-only call
                # is possible (#669). Marking it required contradicted the
                # `tags` description's "empty query + tags = pure tag filter".
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "priority": "critical",
        "writes": False,
        "function": {
            "name": "vault_list",
            "description": (
                "List vault pages with last-modified dates. Optionally filter by folder and/or name pattern."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "folder": {
                        "type": "string",
                        "description": (
                            "Subfolder to list (e.g. 'agent/pages', 'agent/journal/2026'). Empty = entire vault."
                        ),
                    },
                    "pattern": {
                        "type": "string",
                        "description": (
                            "Optional filter pattern (e.g. 'project' to match pages with 'project' in the name)"
                        ),
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "priority": "normal",
        "writes": False,
        "function": {
            "name": "vault_recent",
            "description": (
                "List vault pages MODIFIED in the last N days (newest first), "
                "with each page's modified date and source_type. This is the "
                "'what changed / what's new / recent activity' tool — use it to "
                "review everything added or updated lately, whether from me or "
                "from ingestion. Unlike vault_search (which needs a content "
                "query and only returns matching pages) and vault_list (which "
                "lists a folder regardless of date), vault_recent is purely "
                "recency-based. Optionally scope by folder and/or source_type "
                "(e.g. source_type='user' for my own notes/journal)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {
                        "type": "integer",
                        "description": ("Look back this many days (by modified time). Defaults to 7."),
                    },
                    "folder": {
                        "type": "string",
                        "description": (
                            "Optional subfolder to scope to (e.g. 'agent/pages', 'journals'). Empty = entire vault."
                        ),
                    },
                    "source_type": {
                        "type": "string",
                        "description": (
                            "Optional filter: 'page' (agent pages), 'journal' "
                            "(agent journal), or 'user' (my own notes/journal "
                            "outside agent/). Empty = all."
                        ),
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "priority": "normal",
        "writes": False,
        "function": {
            "name": "vault_tags",
            "description": (
                "List all tags currently in use across the vault with usage "
                "counts, sorted by count descending. Use this to see what "
                "tags exist before filtering — e.g. 'what tags have I used' "
                "or 'list all my tags'. Unlike vault_search (which finds or "
                "filters content by tag/query), vault_tags takes no "
                "arguments and just enumerates the tag vocabulary itself."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "priority": "normal",
        "writes": False,
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
        "priority": "critical",
        "timeout": None,
        "writes": True,
        "function": {
            "name": "vault_edit",
            "description": (
                "The default tool for surgical edits to a vault page — "
                "single-line or multi-line. Replace an exact block of text with "
                "another without rewriting the whole page. Copy old_text from a "
                "fresh vault_read (or a prior edit's diff) so the match is exact; "
                "never reconstruct it from memory — matching is "
                "character-for-character, every space, tab, and newline. Fails "
                "if old_text is not found or matches more than once; set "
                "replace_all=true for bulk replacement. Pages outside the agent "
                "folder (agent/pages/, agent/journal/) require user confirmation "
                "per call. For edits by line number use vault_replace_lines; for "
                "a new page or full rewrite use vault_write."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "page": {
                        "type": "string",
                        "description": (
                            "Page path relative to vault root or bare name (e.g. 'agent/pages/note', 'My Page')."
                        ),
                    },
                    "old_text": {
                        "type": "string",
                        "description": (
                            "Exact text currently in the page to replace, "
                            "single-line or multi-line. Copy it from a fresh "
                            "vault_read (or a previous edit's diff) so it matches "
                            "the page character-for-character, including every "
                            "space, tab, and newline."
                        ),
                    },
                    "new_text": {
                        "type": "string",
                        "description": "Text to replace old_text with",
                    },
                    "replace_all": {
                        "type": "boolean",
                        "description": "Replace all occurrences instead of requiring a unique match (default: false)",
                    },
                    "expected_version": {
                        "type": "string",
                        "description": "Content version from a fresh vault_read; when stale the edit is rejected and not written.",
                    },
                },
                "required": ["page", "old_text", "new_text"],
            },
        },
    },
    {
        "type": "function",
        "priority": "normal",
        "timeout": None,
        "writes": True,
        "function": {
            "name": "vault_append",
            "description": (
                "Append text to the end of a vault page without reading it first. "
                "Creates the page when it does not exist and adds a newline "
                "separator when the page doesn't end with one. Use for journal-like "
                "additions, running logs, or appending a section. Pages outside the "
                "agent folder (agent/pages/, agent/journal/) require user "
                "confirmation per call."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "page": {
                        "type": "string",
                        "description": (
                            "Page path relative to vault root or bare name (e.g. 'agent/pages/note', 'My Page')."
                        ),
                    },
                    "content": {
                        "type": "string",
                        "description": "Text to append to the page",
                    },
                },
                "required": ["page", "content"],
            },
        },
    },
    {
        "type": "function",
        "priority": "low",
        "timeout": None,
        "writes": True,
        "function": {
            "name": "vault_replace_lines",
            "description": (
                "Replace a range of lines (1-based, inclusive) in a vault page with "
                "new content. Pass empty content to delete lines. Use vault_read "
                "with start_line/end_line first to see line numbers. Pages outside "
                "the agent folder require user confirmation per call."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "page": {
                        "type": "string",
                        "description": (
                            "Page path relative to vault root or bare name (e.g. 'agent/pages/note', 'My Page')."
                        ),
                    },
                    "start_line": {
                        "type": "integer",
                        "description": "First line to replace (1-based, inclusive)",
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "Last line to replace (1-based, inclusive)",
                    },
                    "content": {
                        "type": "string",
                        "description": "Replacement text (empty string to delete lines)",
                    },
                },
                "required": ["page", "start_line", "end_line"],
            },
        },
    },
    {
        "type": "function",
        "priority": "low",
        "timeout": None,
        "writes": True,
        "function": {
            "name": "vault_update_frontmatter",
            "description": (
                "Merge frontmatter fields (summary, importance, tags, keywords, "
                "etc.) into an existing vault page. By default only fills fields "
                "that are absent or empty — set overwrite=true to replace existing "
                "values. Reindexes the page afterward. Does not touch the page body."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "page": {
                        "type": "string",
                        "description": (
                            "Page path relative to vault root or bare name (e.g. 'agent/pages/note', 'My Page')."
                        ),
                    },
                    "fields": {
                        "type": "object",
                        "description": (
                            "Frontmatter fields to merge, e.g. "
                            '{"summary": "...", "importance": 0.7, "tags": ["a", "b"]}.'
                        ),
                    },
                    "overwrite": {
                        "type": "boolean",
                        "description": (
                            "If true, replace existing field values. "
                            "If false (default), only fill absent or empty fields."
                        ),
                    },
                },
                "required": ["page", "fields"],
            },
        },
    },
]
