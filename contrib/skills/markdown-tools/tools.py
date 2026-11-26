"""Structured markdown tools for workspace files."""

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from markdown_it import MarkdownIt

from decafclaw.media import ToolResult
from decafclaw.tools.file_locks import file_lock
from decafclaw.web.workspace_paths import PathContainmentError, resolve_contained
from decafclaw.workspace_index import invalidate_workspace_file_cache

if TYPE_CHECKING:
    from decafclaw.context import Context

log = logging.getLogger(__name__)

_MD = MarkdownIt("commonmark").enable("table")
_HEADING_SELECTOR_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_CHECKLIST_INLINE_RE = re.compile(r"^\[(?P<marker>[^\]])\]\s+(?P<text>.*)$")
_CHECKLIST_LINE_RE = re.compile(r"^(?P<indent>\s*)(?P<bullet>[-*+])\s+\[(?P<marker>[^\]])\]\s+(?P<text>.*)$")
_TABLE_SEPARATOR_RE = re.compile(r"^:?-{1,}:?$")


@dataclass(frozen=True)
class SectionRange:
    heading: str
    title: str
    level: int
    start_line: int
    body_start_line: int
    end_line: int


@dataclass(frozen=True)
class TableRange:
    section: SectionRange
    start_line: int
    end_line: int
    headers: list[str]
    rows: list[dict[str, str]]


@dataclass(frozen=True)
class ChecklistItem:
    text: str
    marker: str
    checked: bool
    line_number: int
    indent: int
    bullet: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "marker": self.marker,
            "checked": self.checked,
            "status": "completed" if self.checked else "open",
            "line_number": self.line_number,
            "indent": self.indent,
            "bullet": self.bullet,
        }


def _resolve_workspace_path(ctx: "Context", path: str) -> Path | ToolResult:
    try:
        return resolve_contained(Path(ctx.config.workspace_path), path, label="the workspace")
    except PathContainmentError as exc:
        return ToolResult(text=f"[error: {exc}]")


def _read_workspace_text(resolved: Path, path: str) -> str | ToolResult:
    try:
        return resolved.read_text(newline="")
    except FileNotFoundError:
        return ToolResult(text=f"[error: file not found: {path}]")
    except PermissionError as exc:
        return ToolResult(text=f"[error: {exc}]")
    except UnicodeDecodeError:
        return ToolResult(text=f"[error: file is not valid UTF-8 text: {path}]")


def _detect_newline(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def _normalize_newlines(text: str, newline: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", newline)


def _preserve_trailing_newline(original: str, updated: str, newline: str) -> str:
    had_trailing_newline = original.endswith(("\n", "\r"))
    has_trailing_newline = updated.endswith(("\n", "\r"))
    if had_trailing_newline and not has_trailing_newline:
        return updated + newline
    if not had_trailing_newline and has_trailing_newline:
        if updated.endswith("\r\n"):
            return updated[:-2]
        return updated[:-1]
    return updated


def _replace_line_range(text: str, start_line: int, end_line: int, replacement: str) -> str:
    newline = _detect_newline(text)
    lines = text.splitlines(keepends=True)
    prefix = "".join(lines[:start_line])
    suffix = "".join(lines[end_line:])
    replacement_text = _normalize_newlines(replacement, newline)
    if prefix and replacement_text and not prefix.endswith(("\n", "\r")):
        replacement_text = newline + replacement_text
    if replacement_text and suffix and not replacement_text.endswith(("\n", "\r")):
        replacement_text += newline
    return _preserve_trailing_newline(text, prefix + replacement_text + suffix, newline)


def _parse_heading_selector(heading: str) -> tuple[int | None, str]:
    cleaned = heading.strip()
    if not cleaned:
        raise ValueError("heading is required")
    match = _HEADING_SELECTOR_RE.match(cleaned)
    if match:
        return len(match.group(1)), match.group(2).strip()
    return None, cleaned


def _iter_sections(text: str) -> list[SectionRange]:
    tokens = _MD.parse(text)
    total_lines = len(text.splitlines())
    sections: list[SectionRange] = []
    for index, token in enumerate(tokens):
        if token.type != "heading_open" or token.map is None:
            continue
        if index + 1 >= len(tokens) or tokens[index + 1].type != "inline":
            continue
        inline = tokens[index + 1]
        level = int(token.tag[1])
        sections.append(
            SectionRange(
                heading=f"{'#' * level} {inline.content.strip()}",
                title=inline.content.strip(),
                level=level,
                start_line=token.map[0],
                body_start_line=token.map[1],
                end_line=total_lines,
            )
        )
    completed: list[SectionRange] = []
    for idx, section in enumerate(sections):
        end_line = total_lines
        for candidate in sections[idx + 1 :]:
            if candidate.level <= section.level:
                end_line = candidate.start_line
                break
        completed.append(
            SectionRange(
                heading=section.heading,
                title=section.title,
                level=section.level,
                start_line=section.start_line,
                body_start_line=section.body_start_line,
                end_line=end_line,
            )
        )
    return completed


def _find_section(text: str, heading: str) -> SectionRange:
    target_level, target_title = _parse_heading_selector(heading)
    matches = [
        section
        for section in _iter_sections(text)
        if section.title == target_title and (target_level is None or section.level == target_level)
    ]
    if not matches:
        raise ValueError(f"heading not found: {heading}")
    if len(matches) > 1:
        raise ValueError(f"heading is ambiguous: {heading}")
    return matches[0]


def _split_table_row(row: str) -> list[str]:
    cells: list[str] = []
    current: list[str] = []
    backslashes = 0
    for ch in row:
        if ch == "|" and backslashes % 2 == 0:
            cells.append("".join(current))
            current = []
            backslashes = 0
            continue
        current.append(ch)
        if ch == "\\":
            backslashes += 1
        else:
            backslashes = 0
    cells.append("".join(current))
    if row.startswith("|"):
        cells = cells[1:]
    if row.endswith("|"):
        cells = cells[:-1]
    return [cell.strip().replace("\\|", "|") for cell in cells]


def _parse_table_block(table_text: str) -> tuple[list[str], list[dict[str, str]]]:
    lines = [line.rstrip("\r\n") for line in table_text.splitlines() if line.strip()]
    if len(lines) < 2:
        raise ValueError("table is incomplete")
    headers = _split_table_row(lines[0])
    separators = _split_table_row(lines[1])
    if not headers:
        raise ValueError("table has no header row")
    if len(set(headers)) != len(headers):
        raise ValueError("table header contains duplicate column names")
    if len(separators) < len(headers) or not all(
        _TABLE_SEPARATOR_RE.match(cell) for cell in separators[: len(headers)]
    ):
        raise ValueError("table separator row is invalid")
    rows: list[dict[str, str]] = []
    for raw_line in lines[2:]:
        cells = _split_table_row(raw_line)
        if len(cells) < len(headers):
            cells.extend([""] * (len(headers) - len(cells)))
        if len(cells) > len(headers):
            cells = cells[: len(headers)]
        rows.append(dict(zip(headers, cells, strict=True)))
    return headers, rows


def _find_table(text: str, section_heading: str) -> TableRange:
    section = _find_section(text, section_heading)
    tokens = _MD.parse(text)
    matches = [
        token
        for token in tokens
        if token.type == "table_open"
        and token.map is not None
        and token.map[0] >= section.body_start_line
        and token.map[1] <= section.end_line
    ]
    if not matches:
        raise ValueError(f"no table found in section: {section_heading}")
    if len(matches) > 1:
        raise ValueError(f"multiple tables found in section: {section_heading}")
    table_token = matches[0]
    if table_token.map is None:
        raise ValueError(f"table location unavailable in section: {section_heading}")
    lines = text.splitlines(keepends=True)
    start_line, end_line = table_token.map
    headers, rows = _parse_table_block("".join(lines[start_line:end_line]))
    return TableRange(section=section, start_line=start_line, end_line=end_line, headers=headers, rows=rows)


def _stringify_cell(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br>")


def _escape_table_cell(value: str) -> str:
    return value.replace("|", "\\|")


def _validate_row_keys(headers: list[str], rows: list[dict[str, Any]]) -> None:
    allowed = set(headers)
    extras = sorted({key for row in rows for key in row if key not in allowed})
    if extras:
        raise ValueError(f"unknown table columns: {', '.join(extras)}")


def _render_table(headers: list[str], rows: list[dict[str, str]], newline: str) -> str:
    escaped_headers = [_escape_table_cell(header) for header in headers]
    escaped_rows = [[_escape_table_cell(row.get(header, "")) for header in headers] for row in rows]
    widths = [
        max(3, len(escaped_headers[idx]), *(len(row[idx]) for row in escaped_rows)) for idx in range(len(headers))
    ]

    def format_row(values: list[str]) -> str:
        padded = [value.ljust(widths[idx]) for idx, value in enumerate(values)]
        return "| " + " | ".join(padded) + " |"

    separator = ["-" * widths[idx] for idx in range(len(headers))]
    rendered = [format_row(escaped_headers), format_row(separator)]
    rendered.extend(format_row(row) for row in escaped_rows)
    return newline.join(rendered)


def _collect_checklist_items(text: str, section_heading: str | None = None) -> list[ChecklistItem]:
    start_line = 0
    end_line = len(text.splitlines())
    if section_heading is not None:
        section = _find_section(text, section_heading)
        start_line = section.body_start_line
        end_line = section.end_line

    tokens = _MD.parse(text)
    source_lines = text.splitlines()
    items: list[ChecklistItem] = []
    for index, token in enumerate(tokens):
        if token.type != "list_item_open" or token.map is None:
            continue
        line_index = token.map[0]
        if line_index < start_line or line_index >= end_line or line_index >= len(source_lines):
            continue
        inline_token = None
        for candidate in tokens[index + 1 :]:
            if candidate.type == "list_item_close":
                break
            if candidate.type == "inline" and candidate.map is not None and candidate.map[0] == line_index:
                inline_token = candidate
                break
        if inline_token is None:
            continue
        inline_match = _CHECKLIST_INLINE_RE.match(inline_token.content.strip())
        line_match = _CHECKLIST_LINE_RE.match(source_lines[line_index])
        if inline_match is None or line_match is None:
            continue
        marker = line_match.group("marker")
        items.append(
            ChecklistItem(
                text=line_match.group("text"),
                marker=marker,
                checked=marker.lower() == "x",
                line_number=line_index + 1,
                indent=len(line_match.group("indent")),
                bullet=line_match.group("bullet"),
            )
        )
    return items


def markdown_get_section(ctx: "Context", path: str, heading: str) -> ToolResult:
    """Return a heading section and its nested subsections."""
    resolved = _resolve_workspace_path(ctx, path)
    if isinstance(resolved, ToolResult):
        return resolved
    content = _read_workspace_text(resolved, path)
    if isinstance(content, ToolResult):
        return content
    try:
        section = _find_section(content, heading)
    except ValueError as exc:
        return ToolResult(text=f"[error: {exc}]")
    lines = content.splitlines(keepends=True)
    section_text = "".join(lines[section.start_line : section.end_line])
    return ToolResult(
        text=section_text or "(empty section)",
        data={
            "path": path,
            "heading": section.heading,
            "title": section.title,
            "level": section.level,
            "start_line": section.start_line + 1,
            "end_line": section.end_line,
        },
    )


def markdown_replace_section(ctx: "Context", path: str, heading: str, content: str) -> ToolResult:
    """Replace a section body while preserving the heading line."""
    resolved = _resolve_workspace_path(ctx, path)
    if isinstance(resolved, ToolResult):
        return resolved
    with file_lock(resolved):
        existing = _read_workspace_text(resolved, path)
        if isinstance(existing, ToolResult):
            return existing
        try:
            section = _find_section(existing, heading)
        except ValueError as exc:
            return ToolResult(text=f"[error: {exc}]")
        updated = _replace_line_range(existing, section.body_start_line, section.end_line, content)
        if updated == existing:
            return ToolResult(text=f"No changes made to section {section.heading} in {path}")
        resolved.write_text(updated, newline="")
        invalidate_workspace_file_cache(ctx.config)
    return ToolResult(
        text=f"Replaced section body for {section.heading} in {path}",
        data={"path": path, "heading": section.heading},
    )


def markdown_table_read(ctx: "Context", path: str, section: str) -> ToolResult:
    """Read a markdown table in a section into row objects."""
    resolved = _resolve_workspace_path(ctx, path)
    if isinstance(resolved, ToolResult):
        return resolved
    content = _read_workspace_text(resolved, path)
    if isinstance(content, ToolResult):
        return content
    try:
        table = _find_table(content, section)
    except ValueError as exc:
        return ToolResult(text=f"[error: {exc}]")
    return ToolResult(
        text=f"Read {len(table.rows)} row(s) from table in {table.section.heading} of {path}",
        data={"path": path, "section": table.section.heading, "columns": table.headers, "rows": table.rows},
    )


def markdown_table_append(ctx: "Context", path: str, section: str, rows: list[dict[str, Any]]) -> ToolResult:
    """Append rows to a markdown table in a section."""
    if not rows:
        return ToolResult(text="[error: rows must contain at least one row]")
    resolved = _resolve_workspace_path(ctx, path)
    if isinstance(resolved, ToolResult):
        return resolved
    with file_lock(resolved):
        existing = _read_workspace_text(resolved, path)
        if isinstance(existing, ToolResult):
            return existing
        try:
            table = _find_table(existing, section)
            _validate_row_keys(table.headers, rows)
        except ValueError as exc:
            return ToolResult(text=f"[error: {exc}]")

        appended_rows = table.rows + [
            {header: _stringify_cell(row.get(header, "")) for header in table.headers} for row in rows
        ]
        rendered = _render_table(table.headers, appended_rows, _detect_newline(existing))
        updated = _replace_line_range(existing, table.start_line, table.end_line, rendered)
        resolved.write_text(updated, newline="")
        invalidate_workspace_file_cache(ctx.config)
    return ToolResult(
        text=f"Appended {len(rows)} row(s) to table in {table.section.heading} of {path}",
        data={"path": path, "section": table.section.heading, "appended": len(rows), "rows": appended_rows},
    )


def markdown_table_update(
    ctx: "Context", path: str, section: str, match: dict[str, Any], updates: dict[str, Any]
) -> ToolResult:
    """Update matching markdown table rows in a section."""
    if not match:
        return ToolResult(text="[error: match must contain at least one column]")
    if not updates:
        return ToolResult(text="[error: updates must contain at least one column]")
    resolved = _resolve_workspace_path(ctx, path)
    if isinstance(resolved, ToolResult):
        return resolved
    with file_lock(resolved):
        existing = _read_workspace_text(resolved, path)
        if isinstance(existing, ToolResult):
            return existing
        try:
            table = _find_table(existing, section)
            _validate_row_keys(table.headers, [match, updates])
        except ValueError as exc:
            return ToolResult(text=f"[error: {exc}]")

        match_values = {key: _stringify_cell(value) for key, value in match.items()}
        update_values = {key: _stringify_cell(value) for key, value in updates.items()}
        updated_rows: list[dict[str, str]] = []
        updated_count = 0
        for row in table.rows:
            row_copy = dict(row)
            if all(row_copy.get(key, "") == value for key, value in match_values.items()):
                row_copy.update(update_values)
                updated_count += 1
            updated_rows.append(row_copy)

        if updated_count == 0:
            return ToolResult(text=f"[error: no rows matched in table section {table.section.heading}]")

        rendered = _render_table(table.headers, updated_rows, _detect_newline(existing))
        updated = _replace_line_range(existing, table.start_line, table.end_line, rendered)
        resolved.write_text(updated, newline="")
        invalidate_workspace_file_cache(ctx.config)
    return ToolResult(
        text=f"Updated {updated_count} row(s) in table section {table.section.heading} of {path}",
        data={"path": path, "section": table.section.heading, "updated": updated_count, "rows": updated_rows},
    )


def markdown_checklist_get(ctx: "Context", path: str, section: str | None = None, status: str = "all") -> ToolResult:
    """Return structured checklist items from a markdown file."""
    if status not in {"all", "open", "completed"}:
        return ToolResult(text="[error: status must be one of: all, open, completed]")
    resolved = _resolve_workspace_path(ctx, path)
    if isinstance(resolved, ToolResult):
        return resolved
    content = _read_workspace_text(resolved, path)
    if isinstance(content, ToolResult):
        return content
    try:
        items = _collect_checklist_items(content, section)
    except ValueError as exc:
        return ToolResult(text=f"[error: {exc}]")
    if status == "open":
        items = [item for item in items if not item.checked]
    elif status == "completed":
        items = [item for item in items if item.checked]
    payload = [item.as_dict() for item in items]
    scope = f"section {section}" if section else "file"
    return ToolResult(
        text=f"Found {len(payload)} checklist item(s) in {scope} of {path}",
        data={"path": path, "section": section, "status": status, "items": payload, "count": len(payload)},
    )


def markdown_checklist_toggle(ctx: "Context", path: str, text: str, checked: bool = True) -> ToolResult:
    """Toggle a single checklist item by exact task text."""
    if not text.strip():
        return ToolResult(text="[error: text is required]")
    resolved = _resolve_workspace_path(ctx, path)
    if isinstance(resolved, ToolResult):
        return resolved
    with file_lock(resolved):
        existing = _read_workspace_text(resolved, path)
        if isinstance(existing, ToolResult):
            return existing
        try:
            matches = [item for item in _collect_checklist_items(existing) if item.text == text]
        except ValueError as exc:
            return ToolResult(text=f"[error: {exc}]")
        if not matches:
            return ToolResult(text=f"[error: checklist item not found: {text}]")
        if len(matches) > 1:
            return ToolResult(text=f"[error: checklist item is ambiguous: {text}]")

        item = matches[0]
        desired_marker = "x" if checked else " "
        if item.marker.lower() == desired_marker.lower():
            return ToolResult(
                text=f"Checklist item already {'checked' if checked else 'unchecked'} in {path}",
                data={"path": path, "text": text, "checked": checked, "changed": False},
            )

        lines = existing.splitlines(keepends=True)
        line_index = item.line_number - 1
        original_line = lines[line_index]
        body = original_line.rstrip("\r\n")
        line_ending = original_line[len(body) :]
        match = _CHECKLIST_LINE_RE.match(body)
        if match is None:
            return ToolResult(text=f"[error: checklist item could not be rewritten: {text}]")
        lines[line_index] = (
            f"{match.group('indent')}{match.group('bullet')} [{desired_marker}] {match.group('text')}" + line_ending
        )
        resolved.write_text("".join(lines), newline="")
        invalidate_workspace_file_cache(ctx.config)
    return ToolResult(
        text=f"Marked checklist item as {'checked' if checked else 'unchecked'} in {path}",
        data={"path": path, "text": text, "checked": checked, "changed": True},
    )


TOOLS = {
    "markdown_get_section": markdown_get_section,
    "markdown_replace_section": markdown_replace_section,
    "markdown_table_read": markdown_table_read,
    "markdown_table_append": markdown_table_append,
    "markdown_table_update": markdown_table_update,
    "markdown_checklist_get": markdown_checklist_get,
    "markdown_checklist_toggle": markdown_checklist_toggle,
}


TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "markdown_get_section",
            "description": "Return the matching markdown heading section, including nested subsections, from a workspace file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Workspace-relative path to the markdown file."},
                    "heading": {
                        "type": "string",
                        "description": "Heading text or full ATX heading string to match, for example 'Plan' or '## Plan'.",
                    },
                },
                "required": ["path", "heading"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "markdown_replace_section",
            "description": "Replace the body of a markdown heading section in a workspace file without touching the rest of the document.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Workspace-relative path to the markdown file."},
                    "heading": {
                        "type": "string",
                        "description": "Heading text or full ATX heading string to match, for example 'Plan' or '## Plan'.",
                    },
                    "content": {"type": "string", "description": "Replacement section body text."},
                },
                "required": ["path", "heading", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "markdown_table_read",
            "description": "Read the single markdown table inside a section and return it as structured row objects.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Workspace-relative path to the markdown file."},
                    "section": {
                        "type": "string",
                        "description": "Section heading text or ATX heading string containing exactly one markdown table.",
                    },
                },
                "required": ["path", "section"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "markdown_table_append",
            "description": "Append row objects to the markdown table inside a section and re-render the ASCII table cleanly.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Workspace-relative path to the markdown file."},
                    "section": {
                        "type": "string",
                        "description": "Section heading text or ATX heading string containing exactly one markdown table.",
                    },
                    "rows": {
                        "type": "array",
                        "description": "Row objects keyed by table column names.",
                        "items": {
                            "type": "object",
                            "additionalProperties": {"type": ["string", "number", "boolean", "null"]},
                        },
                    },
                },
                "required": ["path", "section", "rows"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "markdown_table_update",
            "description": "Update matching rows in the markdown table inside a section and preserve clean ASCII alignment.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Workspace-relative path to the markdown file."},
                    "section": {
                        "type": "string",
                        "description": "Section heading text or ATX heading string containing exactly one markdown table.",
                    },
                    "match": {
                        "type": "object",
                        "description": "Exact cell values that select rows to update.",
                        "additionalProperties": {"type": ["string", "number", "boolean", "null"]},
                    },
                    "updates": {
                        "type": "object",
                        "description": "Replacement cell values for the matched rows.",
                        "additionalProperties": {"type": ["string", "number", "boolean", "null"]},
                    },
                },
                "required": ["path", "section", "match", "updates"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "markdown_checklist_get",
            "description": "Return structured markdown checklist items from a whole workspace file or a selected section.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Workspace-relative path to the markdown file."},
                    "section": {
                        "type": "string",
                        "description": "Optional heading text or ATX heading string that scopes the checklist search.",
                    },
                    "status": {
                        "type": "string",
                        "enum": ["all", "open", "completed"],
                        "description": "Filter results by completion status. Open includes non-x markers such as '[ ]' and '[>]'.",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "markdown_checklist_toggle",
            "description": "Toggle a single markdown checklist item in a workspace file by exact task text.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Workspace-relative path to the markdown file."},
                    "text": {"type": "string", "description": "Exact checklist item text to match."},
                    "checked": {
                        "type": "boolean",
                        "description": "True to mark the item complete, false to mark it incomplete.",
                    },
                },
                "required": ["path", "text"],
            },
        },
    },
]
