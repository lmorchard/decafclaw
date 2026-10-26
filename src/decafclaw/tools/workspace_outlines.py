"""Structural outline parser for workspace documents and code.

Extracts classes, functions, and methods for Python files via AST parsing,
and headings and sections for Markdown files. Supports targeted section
lookup and formatting without caching or synthetic AST unparsing.
"""

from __future__ import annotations

import ast
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Sequence

log = logging.getLogger(__name__)

# Max outline items shown before truncation
MAX_OUTLINE_ITEMS = 200

HEADING_RE = re.compile(r"^ {0,3}(#{1,6})\s+(.*?)\s*$")
OPEN_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
CLOSE_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})\s*$")

_PHYSICAL_LINE_SPLIT_RE = re.compile(r"(\r\n|\r|\n)")


def split_physical_lines(text: str, keepends: bool = False) -> list[str]:
    """Split text on physical newlines (\r\n, \r, \n) only, preserving 1-based source line numbering."""
    if not text:
        return []
    parts = _PHYSICAL_LINE_SPLIT_RE.split(text)
    result: list[str] = []
    for i in range(0, len(parts) - 1, 2):
        result.append(parts[i] + parts[i + 1] if keepends else parts[i])
    if len(parts) % 2 == 1 and parts[-1]:
        result.append(parts[-1])
    return result


@dataclass
class OutlineItem:
    """A structural item (class, function, method, or markdown heading)."""

    name: str
    kind: str  # "class", "function", "async_function", "heading"
    start_line: int  # 1-based, inclusive (includes leading decorators)
    end_line: int  # 1-based, inclusive
    heading_line: int  # 1-based line of def/class/heading
    signature: str
    level: int = 0
    children: list["OutlineItem"] = field(default_factory=list)


@dataclass
class OutlineResult:
    """The outcome of parsing a file for outline structure."""

    path: str
    items: list[OutlineItem]
    is_supported: bool = True
    error: str | None = None
    has_symbols: bool = True


# ---------------------------------------------------------------------------
# Python AST Parsing (Raw-line based extraction)
# ---------------------------------------------------------------------------


def _iter_statements(statements: Sequence[ast.stmt]) -> Iterator[ast.stmt]:
    """Yield statements, recursively expanding compound blocks without nesting depth."""
    for stmt in statements:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            yield stmt
        elif isinstance(stmt, ast.If):
            yield from _iter_statements(stmt.body)
            yield from _iter_statements(stmt.orelse)
        elif isinstance(stmt, ast.Try):
            yield from _iter_statements(stmt.body)
            for handler in stmt.handlers:
                yield from _iter_statements(handler.body)
            yield from _iter_statements(stmt.orelse)
            yield from _iter_statements(stmt.finalbody)
        elif hasattr(ast, "TryStar") and isinstance(stmt, ast.TryStar):
            yield from _iter_statements(stmt.body)
            for handler in stmt.handlers:
                yield from _iter_statements(handler.body)
            yield from _iter_statements(stmt.orelse)
            yield from _iter_statements(stmt.finalbody)
        elif isinstance(stmt, (ast.With, ast.AsyncWith)):
            yield from _iter_statements(stmt.body)
        elif isinstance(stmt, (ast.For, ast.AsyncFor, ast.While)):
            yield from _iter_statements(stmt.body)
            yield from _iter_statements(stmt.orelse)
        elif isinstance(stmt, ast.Match):
            for case in stmt.cases:
                yield from _iter_statements(case.body)


def _get_start_line(lines: list[str], node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    if not node.decorator_list:
        return node.lineno
    d_first = node.decorator_list[0]
    d_start = d_first.lineno
    if d_start > 1 and lines[d_start - 2].strip().startswith("@"):
        return d_start - 1
    return d_start


def _get_python_header(lines: list[str], node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> str:
    """Extract declaration header directly from source lines, preserving syntax as-is."""
    start_idx = node.lineno - 1
    if node.decorator_list:
        d_first = node.decorator_list[0]
        d_start = d_first.lineno - 1
        if d_start > 0 and lines[d_start - 1].strip().startswith("@"):
            d_start = d_start - 1
        dec_parts = [lines[i].strip() for i in range(d_start, max(d_start, start_idx)) if lines[i].strip()]
        if dec_parts:
            dec_text = " ".join(dec_parts)
            if not dec_text.startswith("@"):
                dec_text = "@" + dec_text
            dec_prefix = f"{dec_text} "
        else:
            dec_prefix = ""
    else:
        dec_prefix = ""

    if start_idx >= len(lines):
        return f"{dec_prefix}{node.name}"

    if not node.body:
        return f"{dec_prefix}{lines[start_idx].strip()}"

    first_body = node.body[0]
    # Handle single-line definitions (e.g. def foo(): return 1 or class Foo: pass)
    if first_body.lineno == node.lineno:
        col = getattr(first_body, "col_offset", None)
        if col is not None and col > 0:
            raw_bytes = lines[start_idx].encode("utf-8")[:col]
            header = raw_bytes.decode("utf-8", errors="replace").strip()
            return f"{dec_prefix}{header}"
        return f"{dec_prefix}{lines[start_idx].strip()}"

    # Multiline or standard header: lines from def/class up to first body statement (or its decorators)
    body_start = (
        _get_start_line(lines, first_body)
        if getattr(first_body, "decorator_list", None)
        and isinstance(first_body, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        else first_body.lineno
    )
    end_idx = body_start - 1
    parts: list[str] = []
    for i in range(start_idx, end_idx):
        line_str = lines[i].strip()
        if line_str and not line_str.startswith("#"):
            parts.append(line_str)

    # If first body statement shares line with closing colon: ): return x
    col = getattr(first_body, "col_offset", None)
    if col is not None and col > 0 and end_idx < len(lines):
        raw_prefix = lines[end_idx].encode("utf-8")[:col].decode("utf-8", errors="replace").strip()
        if raw_prefix and not raw_prefix.startswith("#"):
            parts.append(raw_prefix)

    header = " ".join(parts)
    return f"{dec_prefix}{header}"


def _extract_py_items(parent: ast.AST, lines: list[str], depth: int = 0) -> list[OutlineItem]:
    """Recursively extract ClassDef, FunctionDef, and AsyncFunctionDef items."""
    items: list[OutlineItem] = []
    body = getattr(parent, "body", [])
    if not isinstance(body, list):
        return items

    for node in _iter_statements(body):
        if isinstance(node, ast.ClassDef):
            start = _get_start_line(lines, node)
            end = getattr(node, "end_lineno", node.lineno)
            header = _get_python_header(lines, node)
            item = OutlineItem(
                name=node.name,
                kind="class",
                start_line=start,
                end_line=end,
                heading_line=node.lineno,
                signature=header,
                level=depth,
            )
            item.children = _extract_py_items(node, lines, depth + 1)
            items.append(item)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            start = _get_start_line(lines, node)
            end = getattr(node, "end_lineno", node.lineno)
            kind = "async_function" if isinstance(node, ast.AsyncFunctionDef) else "function"
            header = _get_python_header(lines, node)
            item = OutlineItem(
                name=node.name,
                kind=kind,
                start_line=start,
                end_line=end,
                heading_line=node.lineno,
                signature=header,
                level=depth,
            )
            item.children = _extract_py_items(node, lines, depth + 1)
            items.append(item)
    return items


def parse_python_outline(content: str, path: str = "") -> tuple[list[OutlineItem], str | None]:
    """Parse Python source into structural outline items using AST line ranges."""
    try:
        tree = ast.parse(content, filename=path or "<string>")
    except SyntaxError as e:
        return [], f"line {e.lineno or 1}: {e.msg}"
    except Exception as e:
        return [], str(e)

    lines = split_physical_lines(content)
    items = _extract_py_items(tree, lines, depth=0)
    return items, None


# ---------------------------------------------------------------------------
# Markdown Parsing
# ---------------------------------------------------------------------------


def parse_markdown_outline(content: str) -> list[OutlineItem]:
    """Parse Markdown headings into nested OutlineItem objects."""
    lines = split_physical_lines(content)
    active_fence_char: str | None = None
    active_fence_len: int = 0
    flat_headings: list[dict] = []

    for i, line in enumerate(lines):
        if active_fence_char is not None:
            m_close = CLOSE_FENCE_RE.match(line)
            if m_close:
                fence = m_close.group(1)
                if fence[0] == active_fence_char and len(fence) >= active_fence_len:
                    active_fence_char = None
                    active_fence_len = 0
            continue

        m_open = OPEN_FENCE_RE.match(line)
        if m_open:
            fence = m_open.group(1)
            active_fence_char = fence[0]
            active_fence_len = len(fence)
            continue

        m = HEADING_RE.match(line)
        if m:
            level = len(m.group(1))
            title = m.group(2).strip()
            flat_headings.append(
                {
                    "level": level,
                    "title": title,
                    "start": i + 1,
                    "heading_line": i + 1,
                    "end": len(lines),
                }
            )

    # Compute content end lines in linear time via a monotonic stack
    end_stack: list[dict] = []
    for h in flat_headings:
        while end_stack and end_stack[-1]["level"] >= h["level"]:
            popped = end_stack.pop()
            popped["end"] = h["start"] - 1
        end_stack.append(h)
    while end_stack:
        end_stack.pop()["end"] = len(lines)

    # Build hierarchy tree
    items: list[OutlineItem] = []
    tree_stack: list[OutlineItem] = []

    for h in flat_headings:
        hashes = "#" * h["level"]
        item = OutlineItem(
            name=h["title"],
            kind="heading",
            start_line=h["start"],
            end_line=h["end"],
            heading_line=h["heading_line"],
            signature=f"{hashes} {h['title']}",
            level=h["level"] - 1,
        )
        while tree_stack and tree_stack[-1].level >= item.level:
            tree_stack.pop()
        if tree_stack:
            tree_stack[-1].children.append(item)
        else:
            items.append(item)
        tree_stack.append(item)

    return items


# ---------------------------------------------------------------------------
# Outline Entry Point (On-demand, no cache)
# ---------------------------------------------------------------------------

_SUPPORTED_PY_EXTS = frozenset({".py"})
_SUPPORTED_MD_EXTS = frozenset({".md", ".markdown"})


def get_outline(resolved_path: Path, content: str) -> OutlineResult:
    """Compute the structural outline for a file on demand."""
    path_str = str(resolved_path)
    ext = resolved_path.suffix.lower()

    if ext in _SUPPORTED_PY_EXTS:
        items, error = parse_python_outline(content, path=path_str)
        if error:
            return OutlineResult(
                path=path_str,
                items=[],
                is_supported=True,
                error=error,
                has_symbols=False,
            )
        return OutlineResult(
            path=path_str,
            items=items,
            is_supported=True,
            error=None,
            has_symbols=bool(items),
        )

    if ext in _SUPPORTED_MD_EXTS:
        items = parse_markdown_outline(content)
        return OutlineResult(
            path=path_str,
            items=items,
            is_supported=True,
            error=None,
            has_symbols=bool(items),
        )

    return OutlineResult(
        path=path_str,
        items=[],
        is_supported=False,
        error=None,
        has_symbols=False,
    )


# ---------------------------------------------------------------------------
# Formatting and Section Retrieval
# ---------------------------------------------------------------------------


def flatten_items(
    items: Sequence[OutlineItem], parent_components: tuple[str, ...] = ()
) -> list[tuple[OutlineItem, str, tuple[str, ...]]]:
    """Flatten items into document order with display paths and component tuples."""
    result: list[tuple[OutlineItem, str, tuple[str, ...]]] = []
    for item in items:
        current_components = parent_components + (item.name,)
        sep = "." if item.kind != "heading" else "/"
        current_path = sep.join(current_components)
        result.append((item, current_path, current_components))
        if item.children:
            result.extend(flatten_items(item.children, current_components))
    return result


def format_outline(items: Sequence[OutlineItem], max_items: int = MAX_OUTLINE_ITEMS) -> tuple[str, bool]:
    """Format outline items into a deterministic indented text representation."""
    flat = flatten_items(items)
    total_count = len(flat)
    truncated = total_count > max_items
    selected = flat[:max_items]

    lines = []
    for item, _, _ in selected:
        indent = "  " * item.level
        if item.start_line == item.end_line:
            range_str = f"{item.start_line}:"
        else:
            range_str = f"{item.start_line}-{item.end_line}:"

        if item.kind in ("class", "function", "async_function"):
            has_omitted_body = True
        else:
            has_omitted_body = item.end_line > item.heading_line
        omitted = " ..." if has_omitted_body else ""
        lines.append(f"{indent}{range_str} {item.signature}{omitted}")

    text = "\n".join(lines)
    if truncated:
        text += (
            f"\n[Outline truncated: showing first {max_items} of {total_count} items. "
            f"Use targeted section retrieval to inspect specific classes, functions, or headings.]"
        )
    return text, truncated


def find_section(items: Sequence[OutlineItem], query: str) -> tuple[OutlineItem | None, list[str], list[str]]:
    """Find a section by exact full path or unique suffix.

    Returns (matching_item, ambiguous_matches, known_sections):
    - Found: (matching_item, [], [])
    - Ambiguous: (None, ambiguous_matches, [])
    - Not found: (None, [], known_sections)
    """
    flat = flatten_items(items)
    if not items or not flat:
        return None, [], []

    def _cap_list(candidates: list[str], max_items: int = 20) -> list[str]:
        if len(candidates) > max_items:
            omitted = len(candidates) - max_items
            return candidates[:max_items] + [f"... and {omitted} more omitted"]
        return candidates

    all_paths = [p for _, p, _ in flat]
    # Check for direct exact match on item name or display path (preserving literal slashes/dots)
    norm_query_raw = query.strip().lstrip("#").strip().lower()
    raw_exact = [
        (item, path)
        for item, path, _ in flat
        if item.name.strip().lstrip("#").strip().lower() == norm_query_raw
        or path.strip().lstrip("#").strip().lower() == norm_query_raw
    ]
    if len(raw_exact) == 1:
        return raw_exact[0][0], [], []
    if len(raw_exact) > 1:
        ambig_cands = [f"{path} (lines {item.start_line}-{item.end_line})" for item, path in raw_exact]
        return None, _cap_list(ambig_cands), []

    is_markdown = any(item.kind == "heading" for item, _, _ in flat)

    if is_markdown:
        parts = [p.strip().lstrip("#").strip().lower() for p in query.split("/") if p.strip()]
        query_components = tuple(parts) if parts else (query.strip().lstrip("#").strip().lower(),)
        norm_item_components = [
            (item, path, tuple(c.strip().lstrip("#").strip().lower() for c in comps)) for item, path, comps in flat
        ]
    else:
        parts = [p.strip().lower() for p in query.replace("/", ".").split(".") if p.strip()]
        query_components = tuple(parts) if parts else (query.strip().lower(),)
        norm_item_components = [(item, path, tuple(c.strip().lower() for c in comps)) for item, path, comps in flat]

    # 1. Exact match on full components
    exact_matches = [(item, path) for item, path, comps in norm_item_components if comps == query_components]
    if len(exact_matches) == 1:
        return exact_matches[0][0], [], []
    if len(exact_matches) > 1:
        ambig_cands = [f"{path} (lines {item.start_line}-{item.end_line})" for item, path in exact_matches]
        return None, _cap_list(ambig_cands), []

    # 2. Suffix match on components tuple
    q_len = len(query_components)
    suffix_matches = [
        (item, path)
        for item, path, comps in norm_item_components
        if len(comps) >= q_len and comps[-q_len:] == query_components
    ]

    if len(suffix_matches) == 1:
        return suffix_matches[0][0], [], []
    if len(suffix_matches) > 1:
        paths_seen = set()
        has_dup_paths = False
        for _, path in suffix_matches:
            if path in paths_seen:
                has_dup_paths = True
                break
            paths_seen.add(path)

        ambig_cands = [
            f"{path} (lines {item.start_line}-{item.end_line})" if has_dup_paths else path
            for item, path in suffix_matches
        ]
        return None, _cap_list(ambig_cands), []

    return None, [], _cap_list(all_paths)
