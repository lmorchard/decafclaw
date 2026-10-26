"""Structural outline parser and cache for workspace documents and code.

Extracts classes, functions, and methods for Python files via AST parsing,
and headings and sections for Markdown files. Supports targeted section
lookup and caching invalidated by content hash.
"""

from __future__ import annotations

import ast
import hashlib
import logging
import re
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

log = logging.getLogger(__name__)

# Max outline items shown before truncation
MAX_OUTLINE_ITEMS = 200
# Cache size for parsed outlines
MAX_CACHE_ENTRIES = 512

HEADING_RE = re.compile(r"^ {0,3}(#{1,6})\s+(.*?)\s*$")
FENCE_RE = re.compile(r"^ {0,3}(```|~~~)")


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


_OUTLINE_CACHE: OrderedDict[tuple[str, str], OutlineResult] = OrderedDict()


def invalidate_outline_cache(path: str | Path | None = None) -> None:
    """Invalidate cached outlines.

    If path is provided, removes entries matching that path.
    If path is None, clears the entire cache.
    """
    if path is None:
        _OUTLINE_CACHE.clear()
        return

    path_str = str(path)
    keys_to_remove = [k for k in _OUTLINE_CACHE if k[0] == path_str]
    for k in keys_to_remove:
        _OUTLINE_CACHE.pop(k, None)


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Python AST Parsing
# ---------------------------------------------------------------------------


def _extract_py_items(parent: ast.AST, depth: int = 0) -> list[OutlineItem]:
    """Recursively extract ClassDef, FunctionDef, and AsyncFunctionDef items."""
    items: list[OutlineItem] = []
    for node in getattr(parent, "body", []):
        if isinstance(node, ast.ClassDef):
            start = node.decorator_list[0].lineno if node.decorator_list else node.lineno
            end = getattr(node, "end_lineno", node.lineno)
            bases = [ast.unparse(b) for b in node.bases]
            base_str = f"({', '.join(bases)})" if bases else ""
            decs = [f"@{ast.unparse(d)}" for d in node.decorator_list]
            dec_prefix = f"{' '.join(decs)} " if decs else ""
            item = OutlineItem(
                name=node.name,
                kind="class",
                start_line=start,
                end_line=end,
                heading_line=node.lineno,
                signature=f"{dec_prefix}class {node.name}{base_str}:",
                level=depth,
            )
            item.children = _extract_py_items(node, depth + 1)
            items.append(item)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            start = node.decorator_list[0].lineno if node.decorator_list else node.lineno
            end = getattr(node, "end_lineno", node.lineno)
            prefix = "async def " if isinstance(node, ast.AsyncFunctionDef) else "def "
            args_str = ast.unparse(node.args)
            ret_str = f" -> {ast.unparse(node.returns)}" if node.returns else ""
            decs = [f"@{ast.unparse(d)}" for d in node.decorator_list]
            dec_prefix = f"{' '.join(decs)} " if decs else ""
            item = OutlineItem(
                name=node.name,
                kind="async_function" if isinstance(node, ast.AsyncFunctionDef) else "function",
                start_line=start,
                end_line=end,
                heading_line=node.lineno,
                signature=f"{dec_prefix}{prefix}{node.name}({args_str}){ret_str}:",
                level=depth,
            )
            item.children = _extract_py_items(node, depth + 1)
            items.append(item)
    return items


def parse_python_outline(content: str, path: str = "") -> tuple[list[OutlineItem], str | None]:
    """Parse Python source into structural outline items.

    Returns (items, error_string).
    """
    try:
        tree = ast.parse(content, filename=path or "<string>")
    except SyntaxError as e:
        return [], f"line {e.lineno or 1}: {e.msg}"
    except Exception as e:
        return [], str(e)

    items = _extract_py_items(tree, depth=0)
    return items, None


# ---------------------------------------------------------------------------
# Markdown Parsing
# ---------------------------------------------------------------------------


def parse_markdown_outline(content: str) -> list[OutlineItem]:
    """Parse Markdown headings into nested OutlineItem objects.

    Fenced code blocks are ignored to avoid treating comments as headings.
    """
    lines = content.splitlines()
    in_fence = False
    flat_headings: list[dict] = []

    for i, line in enumerate(lines):
        if FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
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

    # Compute content end lines
    for idx, sec in enumerate(flat_headings):
        for later in flat_headings[idx + 1 :]:
            if later["level"] <= sec["level"]:
                sec["end"] = later["start"] - 1
                break

    # Build hierarchy
    items: list[OutlineItem] = []
    stack: list[OutlineItem] = []

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
        while stack and stack[-1].level >= item.level:
            stack.pop()
        if stack:
            stack[-1].children.append(item)
        else:
            items.append(item)
        stack.append(item)

    return items


# ---------------------------------------------------------------------------
# Outline Resolution and Cache
# ---------------------------------------------------------------------------

_SUPPORTED_PY_EXTS = frozenset({".py"})
_SUPPORTED_MD_EXTS = frozenset({".md", ".markdown"})


def get_outline(resolved_path: Path, content: str) -> OutlineResult:
    """Retrieve or compute the structural outline for a file."""
    path_str = str(resolved_path)
    chash = _content_hash(content)
    cache_key = (path_str, chash)

    if cache_key in _OUTLINE_CACHE:
        _OUTLINE_CACHE.move_to_end(cache_key)
        return _OUTLINE_CACHE[cache_key]

    ext = resolved_path.suffix.lower()
    if ext in _SUPPORTED_PY_EXTS:
        items, error = parse_python_outline(content, path=path_str)
        if error:
            res = OutlineResult(
                path=path_str,
                items=[],
                is_supported=True,
                error=error,
                has_symbols=False,
            )
        else:
            res = OutlineResult(
                path=path_str,
                items=items,
                is_supported=True,
                error=None,
                has_symbols=bool(items),
            )
    elif ext in _SUPPORTED_MD_EXTS:
        items = parse_markdown_outline(content)
        res = OutlineResult(
            path=path_str,
            items=items,
            is_supported=True,
            error=None,
            has_symbols=bool(items),
        )
    else:
        res = OutlineResult(
            path=path_str,
            items=[],
            is_supported=False,
            error=None,
            has_symbols=False,
        )

    _OUTLINE_CACHE[cache_key] = res
    if len(_OUTLINE_CACHE) > MAX_CACHE_ENTRIES:
        _OUTLINE_CACHE.popitem(last=False)
    return res


# ---------------------------------------------------------------------------
# Formatting and Section Retrieval
# ---------------------------------------------------------------------------


def flatten_items(items: Sequence[OutlineItem], parent_path: str = "") -> list[tuple[OutlineItem, str]]:
    """Flatten items into document order with their full hierarchical paths."""
    result: list[tuple[OutlineItem, str]] = []
    for item in items:
        sep = "." if item.kind != "heading" else "/"
        current_path = f"{parent_path}{sep}{item.name}" if parent_path else item.name
        result.append((item, current_path))
        if item.children:
            result.extend(flatten_items(item.children, current_path))
    return result


def format_outline(items: Sequence[OutlineItem], max_items: int = MAX_OUTLINE_ITEMS) -> tuple[str, bool]:
    """Format outline items into a deterministic indented text representation.

    Returns (formatted_text, is_truncated).
    """
    flat = flatten_items(items)
    total_count = len(flat)
    truncated = total_count > max_items
    selected = flat[:max_items]

    lines = []
    for item, _path in selected:
        indent = "  " * item.level
        if item.start_line == item.end_line:
            range_str = f"{item.start_line}:"
        else:
            range_str = f"{item.start_line}-{item.end_line}:"

        # Clearly identify omitted body lines
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


def normalize_lookup(name: str) -> str:
    """Normalize a section lookup query."""
    parts = [p.strip().lstrip("#").strip() for p in name.split("/") if p.strip()]
    cleaned = ".".join(parts) if parts else name.strip().lstrip("#").strip()
    return cleaned.lower()


def find_section(items: Sequence[OutlineItem], query: str) -> tuple[OutlineItem | None, list[str], list[str]]:
    """Find a section by exact full path or unique suffix.

    Returns (matching_item, ambiguous_matches, known_sections):
    - Found: (matching_item, [], [])
    - Ambiguous: (None, ambiguous_matches, [])
    - Not found: (None, [], all_paths[:20])
    """
    flat = flatten_items(items)
    normalized_query = normalize_lookup(query)
    all_paths = [p for _, p in flat]

    # 1. Exact match (case-insensitive)
    for item, path in flat:
        if normalize_lookup(path) == normalized_query:
            return item, [], []

    # 2. Suffix match
    suffix_matches = [
        (item, path)
        for item, path in flat
        if normalize_lookup(path).endswith(f".{normalized_query}") or normalize_lookup(item.name) == normalized_query
    ]

    if len(suffix_matches) == 1:
        return suffix_matches[0][0], [], []
    if len(suffix_matches) > 1:
        return None, [p for _, p in suffix_matches], []

    # Not found — return known candidates (up to 20)
    return None, [], all_paths[:20]
