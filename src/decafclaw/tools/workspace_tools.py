"""Workspace file tools — sandboxed to the agent's workspace directory."""

from __future__ import annotations

import difflib
import fnmatch
import logging
import re
from pathlib import Path

from ..media import ToolResult, WidgetRequest

log = logging.getLogger(__name__)


def _file_error(e: Exception, path: str) -> ToolResult:
    """Convert common file exceptions to a ToolResult error."""
    if isinstance(e, FileNotFoundError):
        return ToolResult(text=f"[error: file not found: {path}]")
    if isinstance(e, IsADirectoryError):
        return ToolResult(text=f"[error: path is a directory, not a file: {path}]")
    if isinstance(e, PermissionError):
        return ToolResult(text=f"[error: permission denied: {path}]")
    if isinstance(e, UnicodeDecodeError):
        return ToolResult(text=f"[error: file is not valid UTF-8 text: {path}]")
    return ToolResult(text=f"[error: {e}: {path}]")


# Max lines returned by workspace_read when no line range is specified
MAX_READ_LINES = 200
# Context lines shown in edit tool mini-diffs
EDIT_CONTEXT_LINES = 3


def _mini_diff(old_text: str, new_text: str, path: str = "") -> str:
    """Generate a compact unified diff for edit tool output."""
    old_lines = old_text.splitlines(keepends=True)
    new_lines = new_text.splitlines(keepends=True)
    diff = list(difflib.unified_diff(
        old_lines, new_lines,
        fromfile=f"a/{path}" if path else "before",
        tofile=f"b/{path}" if path else "after",
        n=EDIT_CONTEXT_LINES,
    ))
    if not diff:
        return ""
    return "".join(diff)


def _resolve_safe(config, path_str: str) -> Path | None:
    """Resolve a path within the workspace, rejecting escapes.

    Uses Path.is_relative_to for containment check (not string prefix),
    which correctly handles cases like '/tmp/ws' vs '/tmp/ws2/...'.
    """
    workspace = config.workspace_path.resolve()
    target = (workspace / path_str).resolve()
    if not target.is_relative_to(workspace):
        return None
    return target


def tool_workspace_read(ctx, path: str, start_line: int | None = None,
                        end_line: int | None = None) -> str | ToolResult:
    """Read a file from the agent's workspace, optionally a line range."""
    log.info(f"[tool:workspace_read] {path}")
    resolved = _resolve_safe(ctx.config, path)
    if resolved is None:
        return ToolResult(text=f"[error: path '{path}' is outside the workspace]")
    try:
        content = resolved.read_text()
    except (FileNotFoundError, IsADirectoryError, PermissionError, UnicodeDecodeError) as e:
        return _file_error(e, path)

    all_lines = content.splitlines()
    total = len(all_lines)
    partial = start_line is not None or end_line is not None

    # Large file guard: cap full reads at MAX_READ_LINES
    if not partial and total > MAX_READ_LINES:
        end = MAX_READ_LINES
        selected = all_lines[:end]
        width = len(str(end))
        numbered = [f"{str(i + 1).rjust(width)}| {line}"
                    for i, line in enumerate(selected)]
        header = (f"File has {total} lines, showing first {MAX_READ_LINES}. "
                  f"Use start_line/end_line to read specific sections.\n")
        return header + "\n".join(numbered)

    # Determine range (1-based, inclusive)
    start = max(1, start_line or 1)
    end = min(total, end_line or total)
    selected = all_lines[start - 1:end]
    width = len(str(end))
    numbered = [f"{str(start + i).rjust(width)}| {line}"
                for i, line in enumerate(selected)]
    if partial:
        header = f"Lines {start}-{end} of {total}:\n"
        return header + "\n".join(numbered)
    return "\n".join(numbered)


_MARKDOWN_EXTS = (".md", ".markdown")


def tool_workspace_preview_markdown(ctx, path: str) -> ToolResult:
    """Read a workspace markdown file and return it as an inline markdown widget.

    The web UI renders the content as rich markdown via the
    ``markdown_document`` widget; non-web channels see the raw markdown
    text. Use this when you want to show the user a formatted preview
    of a markdown file (notes, docs, drafts) rather than dump raw
    markdown into chat.

    Capped at ``MAX_READ_LINES`` lines (same as ``workspace_read``); for
    larger documents the widget shows the first N lines and a notice.
    """
    config = ctx.config
    if not any(path.lower().endswith(ext) for ext in _MARKDOWN_EXTS):
        return ToolResult(
            text=f"[error: workspace_preview_markdown requires a .md or .markdown file; got '{path}']"
        )
    safe = _resolve_safe(config, path)
    if safe is None:
        return ToolResult(text=f"[error: invalid path '{path}']")
    if not safe.exists() or not safe.is_file():
        return ToolResult(text=f"[error: file not found: '{path}']")
    try:
        content = safe.read_text()
    except OSError as e:
        return _file_error(e, path)
    lines = content.splitlines()
    total = len(lines)
    if total > MAX_READ_LINES:
        truncated = "\n".join(lines[:MAX_READ_LINES])
        notice = (
            f"\n\n_(file has {total} lines; showing first {MAX_READ_LINES}. "
            f"Use `workspace_read` with `start_line`/`end_line` for specific ranges.)_"
        )
        widget_content = truncated + notice
        text = (f"[file truncated: {total} lines, showing first {MAX_READ_LINES}]\n"
                + truncated)
    else:
        widget_content = content
        text = content
    return ToolResult(
        text=text,
        widget=WidgetRequest(
            widget_type="markdown_document",
            data={"content": widget_content},
            target="inline",
        ),
    )


def tool_workspace_write(ctx, path: str, content: str) -> str | ToolResult:
    """Write content to a file in the agent's workspace."""
    log.info(f"[tool:workspace_write] {path}")
    resolved = _resolve_safe(ctx.config, path)
    if resolved is None:
        return ToolResult(text=f"[error: path '{path}' is outside the workspace]")
    try:
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content)
        return f"Wrote {len(content)} characters to {path}"
    except PermissionError as e:
        return _file_error(e, path)


def tool_workspace_list(ctx, path: str = ".") -> str | ToolResult:
    """List files and directories in the agent's workspace."""
    log.info(f"[tool:workspace_list] {path}")
    resolved = _resolve_safe(ctx.config, path)
    if resolved is None:
        return ToolResult(text=f"[error: path '{path}' is outside the workspace]")
    if not resolved.exists():
        return ToolResult(text=f"[error: path not found: {path}]")
    if not resolved.is_dir():
        return ToolResult(text=f"[error: '{path}' is not a directory]")
    try:
        entries = sorted(resolved.iterdir())
        lines = []
        for entry in entries:
            rel = entry.relative_to(resolved)
            suffix = "/" if entry.is_dir() else ""
            size = f" ({entry.stat().st_size}B)" if entry.is_file() else ""
            lines.append(f"{rel}{suffix}{size}")
        return "\n".join(lines) if lines else "(empty directory)"
    except PermissionError as e:
        return _file_error(e, path)


def tool_file_share(ctx, path: str, message: str = "") -> "ToolResult":
    """Share a file from the workspace as an attachment."""
    import mimetypes

    log.info(f"[tool:file_share] {path}")
    resolved = _resolve_safe(ctx.config, path)
    if resolved is None:
        return ToolResult(text=f"[error: path '{path}' is outside the workspace]")
    if not resolved.exists():
        return ToolResult(text=f"[error: file not found: {path}]")
    if resolved.is_dir():
        return ToolResult(text=f"[error: '{path}' is a directory, not a file]")

    try:
        data = resolved.read_bytes()
        content_type = mimetypes.guess_type(str(resolved))[0] or "application/octet-stream"
        return ToolResult(
            text=message or f"Sharing {path}",
            media=[{
                "type": "file",
                "filename": resolved.name,
                "data": data,
                "content_type": content_type,
            }],
        )
    except PermissionError as e:
        return _file_error(e, path)


def tool_workspace_move(ctx, path: str, destination: str) -> str | ToolResult:
    """Move or rename a file within the workspace."""
    log.info(f"[tool:workspace_move] {path} -> {destination}")
    resolved_src = _resolve_safe(ctx.config, path)
    if resolved_src is None:
        return ToolResult(text=f"[error: path '{path}' is outside the workspace]")
    resolved_dst = _resolve_safe(ctx.config, destination)
    if resolved_dst is None:
        return ToolResult(text=f"[error: destination '{destination}' is outside the workspace]")
    if not resolved_src.exists():
        return ToolResult(text=f"[error: file not found: {path}]")
    if resolved_dst.exists():
        return ToolResult(text=f"[error: destination already exists: {destination}]")
    try:
        resolved_dst.parent.mkdir(parents=True, exist_ok=True)
        resolved_src.rename(resolved_dst)
        return f"Moved {path} -> {destination}"
    except PermissionError as e:
        return _file_error(e, path)


def tool_workspace_delete(ctx, path: str) -> str | ToolResult:
    """Delete a file from the workspace."""
    log.info(f"[tool:workspace_delete] {path}")
    resolved = _resolve_safe(ctx.config, path)
    if resolved is None:
        return ToolResult(text=f"[error: path '{path}' is outside the workspace]")
    if not resolved.exists():
        return ToolResult(text=f"[error: file not found: {path}]")
    if resolved.is_dir():
        return ToolResult(text=f"[error: '{path}' is a directory. Use shell to remove directories.]")
    try:
        resolved.unlink()
        return f"Deleted {path}"
    except PermissionError as e:
        return _file_error(e, path)


def tool_workspace_edit(ctx, path: str, old_text: str, new_text: str,
                       replace_all: bool = False) -> str | ToolResult:
    """Edit a file by replacing exact text matches."""
    log.info(f"[tool:workspace_edit] {path}")
    resolved = _resolve_safe(ctx.config, path)
    if resolved is None:
        return ToolResult(text=f"[error: path '{path}' is outside the workspace]")
    try:
        content = resolved.read_text()
    except (FileNotFoundError, PermissionError, UnicodeDecodeError) as e:
        return _file_error(e, path)

    count = content.count(old_text)
    if count == 0:
        return ToolResult(text=f"[error: text not found in {path}. "
                "Make sure old_text matches exactly, including whitespace and indentation.]")
    if count > 1 and not replace_all:
        return ToolResult(text=f"[error: found {count} matches in {path}. "
                "Use replace_all=true for bulk replacement, "
                "or provide more surrounding context to make old_text unique.]")

    if replace_all:
        new_content = content.replace(old_text, new_text)
    else:
        new_content = content.replace(old_text, new_text, 1)
    resolved.write_text(new_content)
    summary = f"Edited {path}: replaced {count} occurrence(s)"
    diff = _mini_diff(content, new_content, path)
    if diff:
        return f"{summary}\n\n{diff}"
    return summary


def tool_workspace_insert(ctx, path: str, line_number: int, content: str) -> str | ToolResult:
    """Insert text at a specific line number in a workspace file."""
    log.info(f"[tool:workspace_insert] {path} at line {line_number}")
    resolved = _resolve_safe(ctx.config, path)
    if resolved is None:
        return ToolResult(text=f"[error: path '{path}' is outside the workspace]")
    try:
        existing = resolved.read_text()
    except (FileNotFoundError, PermissionError) as e:
        return _file_error(e, path)

    lines = existing.splitlines(keepends=True)
    if line_number < 1 or line_number > len(lines) + 1:
        return ToolResult(text=f"[error: line_number {line_number} is out of range. "
                f"File has {len(lines)} lines, valid range is 1-{len(lines) + 1}.]")

    # Ensure content ends with newline for clean insertion
    if content and not content.endswith("\n"):
        content += "\n"
    insert_lines = content.splitlines(keepends=True)
    new_lines = list(lines)
    new_lines[line_number - 1:line_number - 1] = insert_lines
    resolved.write_text("".join(new_lines))
    summary = f"Inserted {len(insert_lines)} line(s) at line {line_number} in {path}"
    diff = _mini_diff(existing, "".join(new_lines), path)
    if diff:
        return f"{summary}\n\n{diff}"
    return summary


def tool_workspace_replace_lines(ctx, path: str, start_line: int, end_line: int,
                                 content: str = "") -> str | ToolResult:
    """Replace a range of lines in a workspace file."""
    log.info(f"[tool:workspace_replace_lines] {path} lines {start_line}-{end_line}")
    resolved = _resolve_safe(ctx.config, path)
    if resolved is None:
        return ToolResult(text=f"[error: path '{path}' is outside the workspace]")
    try:
        existing = resolved.read_text()
    except (FileNotFoundError, PermissionError) as e:
        return _file_error(e, path)

    lines = existing.splitlines(keepends=True)
    if start_line < 1 or end_line < start_line or end_line > len(lines):
        return ToolResult(text=f"[error: invalid line range {start_line}-{end_line}. "
                f"File has {len(lines)} lines.]")

    if content:
        if not content.endswith("\n"):
            content += "\n"
        replacement = content.splitlines(keepends=True)
    else:
        replacement = []
    new_lines = list(lines)
    new_lines[start_line - 1:end_line] = replacement
    resolved.write_text("".join(new_lines))
    if not content:
        summary = f"Deleted lines {start_line}-{end_line} from {path}"
    else:
        summary = f"Replaced lines {start_line}-{end_line} with {len(replacement)} line(s) in {path}"
    diff = _mini_diff(existing, "".join(new_lines), path)
    if diff:
        return f"{summary}\n\n{diff}"
    return summary


def tool_workspace_append(ctx, path: str, content: str) -> str | ToolResult:
    """Append content to a file in the agent's workspace."""
    log.info(f"[tool:workspace_append] {path}")
    resolved = _resolve_safe(ctx.config, path)
    if resolved is None:
        return ToolResult(text=f"[error: path '{path}' is outside the workspace]")
    try:
        resolved.parent.mkdir(parents=True, exist_ok=True)
        if resolved.exists():
            existing = resolved.read_text()
            if existing and not existing.endswith("\n"):
                content = "\n" + content
            resolved.write_text(existing + content)
        else:
            resolved.write_text(content)
        return f"Appended {len(content)} characters to {path}"
    except PermissionError as e:
        return _file_error(e, path)


def tool_workspace_diff(ctx, path1: str, path2: str, context_lines: int = 3) -> str | ToolResult:
    """Show a unified diff between two workspace files."""
    log.info(f"[tool:workspace_diff] {path1} vs {path2}")
    resolved1 = _resolve_safe(ctx.config, path1)
    if resolved1 is None:
        return ToolResult(text=f"[error: path '{path1}' is outside the workspace]")
    resolved2 = _resolve_safe(ctx.config, path2)
    if resolved2 is None:
        return ToolResult(text=f"[error: path '{path2}' is outside the workspace]")
    try:
        lines1 = resolved1.read_text().splitlines(keepends=True)
    except (FileNotFoundError, PermissionError, UnicodeDecodeError) as e:
        return _file_error(e, path1)
    try:
        lines2 = resolved2.read_text().splitlines(keepends=True)
    except (FileNotFoundError, PermissionError, UnicodeDecodeError) as e:
        return _file_error(e, path2)

    diff = list(difflib.unified_diff(
        lines1, lines2,
        fromfile=path1, tofile=path2,
        n=context_lines,
    ))
    if not diff:
        return f"Files are identical: {path1} and {path2}"
    return "".join(diff)


def tool_workspace_search(ctx, pattern: str, path: str = ".",
                          glob: str = "*", context_lines: int = 2) -> str | ToolResult:
    """Search for a regex pattern across workspace files."""
    log.info(f"[tool:workspace_search] pattern={pattern!r} path={path} glob={glob}")
    resolved = _resolve_safe(ctx.config, path)
    if resolved is None:
        return ToolResult(text=f"[error: path '{path}' is outside the workspace]")
    if not resolved.exists():
        return ToolResult(text=f"[error: path not found: {path}]")

    try:
        regex = re.compile(pattern)
    except re.error as e:
        return ToolResult(text=f"[error: invalid regex pattern: {e}]")

    workspace = ctx.config.workspace_path.resolve()
    max_matches = 50
    total_matches = 0
    output_sections = []

    # Collect files to search
    if resolved.is_file():
        files = [resolved]
    else:
        files = sorted(f for f in resolved.rglob("*") if f.is_file()
                       and fnmatch.fnmatch(f.name, glob))

    for fpath in files:
        try:
            content = fpath.read_text(encoding="utf-8")
        except (UnicodeDecodeError, PermissionError):
            continue

        lines = content.splitlines()
        file_matches = []
        for i, line in enumerate(lines):
            if regex.search(line):
                file_matches.append(i)

        if not file_matches:
            continue

        rel_path = fpath.relative_to(workspace)
        section_lines = [f"--- {rel_path} ---"]
        shown = set()  # track which lines we've already output

        for match_idx in file_matches:
            if total_matches >= max_matches:
                break
            total_matches += 1
            start = max(0, match_idx - context_lines)
            end = min(len(lines), match_idx + context_lines + 1)
            # Add separator if there's a gap from previous context
            if shown and start > max(shown) + 1:
                section_lines.append("  ...")
            for j in range(start, end):
                if j in shown:
                    continue
                shown.add(j)
                lineno = j + 1
                prefix = ">" if j == match_idx else " "
                section_lines.append(f"{prefix} {lineno:>4}| {lines[j]}")

        output_sections.append("\n".join(section_lines))
        if total_matches >= max_matches:
            break

    if not output_sections:
        return "(no matches)"

    result = "\n\n".join(output_sections)
    if total_matches >= max_matches:
        result += f"\n\n(truncated — showing first {max_matches} matches)"
    return result


def tool_workspace_glob(ctx, pattern: str, path: str = ".") -> str | ToolResult:
    """Find files by glob pattern in the workspace."""
    log.info(f"[tool:workspace_glob] pattern={pattern!r} path={path}")
    resolved = _resolve_safe(ctx.config, path)
    if resolved is None:
        return ToolResult(text=f"[error: path '{path}' is outside the workspace]")
    if not resolved.exists():
        return ToolResult(text=f"[error: path not found: {path}]")
    if not resolved.is_dir():
        return ToolResult(text=f"[error: '{path}' is not a directory]")

    workspace = ctx.config.workspace_path.resolve()
    max_results = 200
    matches = []
    for fpath in sorted(resolved.rglob(pattern)):
        rel = fpath.relative_to(workspace)
        if fpath.is_file():
            size = fpath.stat().st_size
            matches.append(f"{rel} ({size}B)")
        else:
            matches.append(f"{rel}/")
        if len(matches) >= max_results:
            break

    if not matches:
        return "(no matches)"

    result = "\n".join(matches)
    if len(matches) >= max_results:
        result += f"\n\n(truncated — showing first {max_results} results)"
    return result


WORKSPACE_TOOLS = {
    "workspace_read": tool_workspace_read,
    "workspace_preview_markdown": tool_workspace_preview_markdown,
    "workspace_write": tool_workspace_write,
    "workspace_list": tool_workspace_list,
    "file_share": tool_file_share,
    "workspace_append": tool_workspace_append,
    "workspace_edit": tool_workspace_edit,
    "workspace_insert": tool_workspace_insert,
    "workspace_replace_lines": tool_workspace_replace_lines,
    "workspace_search": tool_workspace_search,
    "workspace_glob": tool_workspace_glob,
    "workspace_move": tool_workspace_move,
    "workspace_delete": tool_workspace_delete,
    "workspace_diff": tool_workspace_diff,
}

WORKSPACE_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "priority": "critical",
        "function": {
            "name": "workspace_read",
            "description": "Read a file from your workspace filesystem (blog posts, code, configs, scripts, project files). NOT for vault knowledge pages (use vault_read for those). Returns content with line numbers. Optionally read a specific line range with start_line/end_line (1-based, inclusive). Paths are relative to the workspace root — do NOT prefix with 'workspace/'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path within the workspace",
                    },
                    "start_line": {
                        "type": "integer",
                        "description": "First line to read (1-based, inclusive). Omit to start from beginning.",
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "Last line to read (1-based, inclusive). Omit to read to end of file.",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "priority": "normal",
        "function": {
            "name": "workspace_preview_markdown",
            "description": (
                "Read a workspace markdown file (.md or .markdown) and "
                "show it to the user as a rendered preview. The agent and "
                "non-web channels see the raw markdown text; the web UI "
                "renders it richly via the markdown_document widget. Use "
                "for showing formatted notes, docs, or drafts rather than "
                "dumping raw markdown into chat."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Workspace-relative path to a markdown file.",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "priority": "critical",
        "function": {
            "name": "workspace_write",
            "description": "Write content to a file in your workspace filesystem. Use this for blog posts, code, configs, scripts, and any project files. NOT for vault knowledge pages (use vault_write for those). Creates parent directories as needed. Paths are relative to the workspace root — do NOT prefix with 'workspace/' (use 'blog/post.md' not 'workspace/blog/post.md').",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path within the workspace",
                    },
                    "content": {
                        "type": "string",
                        "description": "Content to write to the file",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "priority": "normal",
        "function": {
            "name": "workspace_append",
            "description": "Append content to the end of a file in your workspace. Creates the file (and parent directories) if it doesn't exist. Adds a newline separator if the file doesn't end with one.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path within the workspace",
                    },
                    "content": {
                        "type": "string",
                        "description": "Content to append to the file",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "priority": "normal",
        "function": {
            "name": "workspace_edit",
            "description": (
                "Replace an exact string with another exact string in a file. "
                "USE SPARINGLY — prefer workspace_replace_lines for multi-line "
                "edits, workspace_insert for additions, workspace_write for "
                "full rewrites.\n\n"
                "When to use: small, targeted changes (typo fix, URL swap, "
                "single identifier rename) where you have the exact current "
                "content fresh from workspace_read. The old_text must match "
                "CHARACTER-FOR-CHARACTER including every whitespace "
                "character.\n\n"
                "Common failure: reconstructing text from memory instead of "
                "a fresh read — your mental model drifts from the file on "
                "disk and the match fails. If you don't have the current "
                "content in front of you, use workspace_replace_lines with "
                "line numbers instead.\n\n"
                "Fails if old_text is not found or matches multiple locations. "
                "Set replace_all=true for intentional bulk replacement."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path within the workspace",
                    },
                    "old_text": {
                        "type": "string",
                        "description": (
                            "Exact text currently in the file. Must match "
                            "character-for-character including every space, "
                            "tab, and newline. If you can't see the current "
                            "content, use workspace_replace_lines instead."
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
                },
                "required": ["path", "old_text", "new_text"],
            },
        },
    },
    {
        "type": "function",
        "priority": "normal",
        "function": {
            "name": "workspace_insert",
            "description": "Insert text at a specific line number in a workspace file, pushing existing content down. Line numbers are 1-based. Use workspace_read first to see line numbers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path within the workspace",
                    },
                    "line_number": {
                        "type": "integer",
                        "description": "Line number to insert at (1-based). Existing content at this line moves down.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Text to insert",
                    },
                },
                "required": ["path", "line_number", "content"],
            },
        },
    },
    {
        "type": "function",
        "priority": "normal",
        "function": {
            "name": "workspace_replace_lines",
            "description": "Replace a range of lines (1-based, inclusive) with new content. Pass empty content to delete lines. Use workspace_read first to see line numbers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path within the workspace",
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
                "required": ["path", "start_line", "end_line"],
            },
        },
    },
    {
        "type": "function",
        "priority": "normal",
        "function": {
            "name": "workspace_search",
            "description": "Search for a regex pattern across files in the workspace. Returns matching lines with line numbers and surrounding context, grouped by file. Use the glob parameter to filter file types (e.g. '*.py'). Pass a specific file path to search within one file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Regex pattern to search for",
                    },
                    "path": {
                        "type": "string",
                        "description": "Directory or file to search in (default: workspace root)",
                    },
                    "glob": {
                        "type": "string",
                        "description": "Filename glob filter (default: '*' for all files). Examples: '*.py', '*.md', '*.json'",
                    },
                    "context_lines": {
                        "type": "integer",
                        "description": "Lines of context to show around each match (default: 2)",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "priority": "normal",
        "function": {
            "name": "workspace_glob",
            "description": "Find files by name/glob pattern, recursively. Returns matching file paths relative to workspace root with file sizes. Useful for finding files by extension or name pattern.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Glob pattern to match filenames (e.g. '*.py', 'config*', '*.md')",
                    },
                    "path": {
                        "type": "string",
                        "description": "Directory to search from (default: workspace root)",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "priority": "normal",
        "function": {
            "name": "workspace_move",
            "description": "Move or rename a file within the workspace. Fails if the destination already exists.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Current relative path of the file",
                    },
                    "destination": {
                        "type": "string",
                        "description": "New relative path for the file",
                    },
                },
                "required": ["path", "destination"],
            },
        },
    },
    {
        "type": "function",
        "priority": "normal",
        "function": {
            "name": "workspace_delete",
            "description": "Delete a file from the workspace. Cannot delete directories.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path of the file to delete",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "priority": "normal",
        "function": {
            "name": "workspace_diff",
            "description": "Show a unified diff between two workspace files. Useful for comparing versions, checking what changed, or reviewing differences.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path1": {
                        "type": "string",
                        "description": "Relative path to the first file",
                    },
                    "path2": {
                        "type": "string",
                        "description": "Relative path to the second file",
                    },
                    "context_lines": {
                        "type": "integer",
                        "description": "Lines of context around each change (default: 3)",
                    },
                },
                "required": ["path1", "path2"],
            },
        },
    },
    {
        "type": "function",
        "priority": "normal",
        "function": {
            "name": "file_share",
            "description": "Share a file from the workspace as an attachment in the conversation. The file will be uploaded and displayed inline (images) or as a download (other files). Use this to share reports, images, logs, or any workspace file with the user.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path within the workspace",
                    },
                    "message": {
                        "type": "string",
                        "description": "Optional message to include with the file",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "priority": "normal",
        "function": {
            "name": "workspace_list",
            "description": "List files and directories in your workspace. Paths are relative to the workspace root — do NOT prefix with 'workspace/'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative directory path (default: workspace root)",
                    },
                },
                "required": [],
            },
        },
    },
]
