"""Markdown vault skill — section-aware note reading and editing.

Combines the document model (parsing, checklist ops, tag ops, section ops)
with the tool wrappers that the skill loader exposes to the agent.
"""

import datetime
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from decafclaw.media import ToolResult

# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
CHECKBOX_RE = re.compile(r"^(\s*- \[)([ xX])(\]\s+)(.*)")
TAG_RE = re.compile(r"(?:^|(?<=\s))#([a-zA-Z][a-zA-Z0-9-]*)")
WIKILINK_RE = re.compile(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def extract_tags(text: str) -> list[str]:
    """Extract all #tags from a line of text."""
    return TAG_RE.findall(text)


def daily_path(date: datetime.date | str | None = None, offset: int = 0) -> str:
    """Return the relative vault path for a daily journal.

    Args:
        date: A date object, ISO string (YYYY-MM-DD), or None for today.
        offset: Days to shift (-1 = yesterday, 1 = tomorrow). Applied after date.

    Returns:
        Relative path like 'journals/2026/2026-03-17.md'
    """
    if date is None:
        d = datetime.date.today()
    elif isinstance(date, str):
        d = datetime.date.fromisoformat(date)
    else:
        d = date
    d += datetime.timedelta(days=offset)
    return f"journals/{d.year}/{d.isoformat()}.md"


def normalize_title(raw: str) -> str:
    """Strip wiki-links and lowercase for matching."""
    stripped = WIKILINK_RE.sub(r"\1", raw)
    return stripped.strip().lower()


def _ensure_newlines(text: str) -> list[str]:
    """Split text into lines, each ending with newline."""
    lines = text.splitlines()
    return [line + "\n" for line in lines]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class Section:
    """A heading and its line range within the document."""

    title: str
    level: int
    heading_line: int
    content_start: int
    content_end: int
    children: list["Section"] = field(default_factory=list)

    @property
    def normalized_title(self) -> str:
        return normalize_title(self.title)

    def content_lines(self, lines: list[str]) -> list[str]:
        return lines[self.content_start : self.content_end]

    def all_lines(self, lines: list[str]) -> list[str]:
        return lines[self.heading_line : self.content_end]


@dataclass
class ChecklistItem:
    """A checkbox item within a section."""

    line_index: int
    checked: bool
    text: str
    raw: str


# ---------------------------------------------------------------------------
# Document model
# ---------------------------------------------------------------------------


class Document:
    """A parsed markdown document that supports surgical edits."""

    def __init__(self, text: str):
        self.lines: list[str] = text.splitlines(keepends=True)
        self.sections: list[Section] = []
        self._parse()

    @classmethod
    def from_file(cls, path: str | Path) -> "Document":
        return cls(Path(path).read_text())

    def save(self, path: str | Path) -> None:
        Path(path).write_text("".join(self.lines))

    def __str__(self) -> str:
        return "".join(self.lines)

    # --- Parsing ---

    def _parse(self) -> None:
        flat: list[Section] = []
        for i, line in enumerate(self.lines):
            m = HEADING_RE.match(line)
            if m:
                level = len(m.group(1))
                title = m.group(2)
                flat.append(
                    Section(
                        title=title,
                        level=level,
                        heading_line=i,
                        content_start=i + 1,
                        content_end=len(self.lines),
                    )
                )

        for idx, sec in enumerate(flat):
            for later in flat[idx + 1 :]:
                if later.level <= sec.level:
                    sec.content_end = later.heading_line
                    break

        self.sections = _build_tree(flat)

    # --- Section lookup ---

    def find_section(self, path: str) -> Section | None:
        parts = [p.strip().lower() for p in path.split("/")]
        return _walk_path(self.sections, parts)

    def list_sections(self, depth: int = 0) -> list[tuple[int, Section]]:
        result: list[tuple[int, Section]] = []
        _flatten_sections(self.sections, 0, result)
        return result

    # --- Checklist operations ---

    def get_items(self, section: Section) -> list[ChecklistItem]:
        items = []
        end = section.children[0].heading_line if section.children else section.content_end
        for i in range(section.content_start, end):
            m = CHECKBOX_RE.match(self.lines[i])
            if m:
                items.append(
                    ChecklistItem(
                        line_index=i,
                        checked=m.group(2) in ("x", "X"),
                        text=m.group(4),
                        raw=self.lines[i],
                    )
                )
        return items

    def check_item(self, section: Section, match: str | None = None, index: int | None = None) -> bool:
        return self._set_check(section, True, match, index)

    def uncheck_item(self, section: Section, match: str | None = None, index: int | None = None) -> bool:
        return self._set_check(section, False, match, index)

    def _set_check(self, section: Section, checked: bool, match: str | None, index: int | None) -> bool:
        item = self._find_item(section, match, index)
        if not item:
            return False
        mark = "x" if checked else " "
        m = CHECKBOX_RE.match(self.lines[item.line_index])
        if m:
            self.lines[item.line_index] = f"{m.group(1)}{mark}{m.group(3)}{m.group(4)}"
            if item.raw.endswith("\n") and not self.lines[item.line_index].endswith("\n"):
                self.lines[item.line_index] += "\n"
        return True

    def _find_item(self, section: Section, match: str | None, index: int | None) -> ChecklistItem | None:
        items = self.get_items(section)
        if not items:
            return None
        if match is not None:
            match_lower = match.lower()
            for item in items:
                if match_lower in item.text.lower():
                    return item
            return None
        if index is not None:
            if -len(items) <= index < len(items):
                return items[index]
            return None
        return None

    # --- Content operations ---

    def append(self, section: Section, text: str) -> None:
        new_lines = _ensure_newlines(text)
        insert_at = section.content_end
        while insert_at > section.content_start and not self.lines[insert_at - 1].strip():
            insert_at -= 1
        trailing_blank = insert_at < section.content_end
        self._insert_lines(insert_at, new_lines)
        if not trailing_blank:
            reparse_end = insert_at + len(new_lines)
            if reparse_end < len(self.lines):
                self.lines.insert(reparse_end, "\n")
                self._parse()

    def prepend(self, section: Section, text: str) -> None:
        insert_at = section.content_start
        new_lines = _ensure_newlines(text)
        if insert_at < section.content_end and not self.lines[insert_at].strip():
            insert_at += 1
        self._insert_lines(insert_at, new_lines)

    def insert_item(self, section: Section, index: int, text: str) -> None:
        items = self.get_items(section)
        new_lines = _ensure_newlines(text)
        if not items or index >= len(items):
            self.append(section, text)
            return
        if index < 0:
            index = max(0, len(items) + index)
        insert_at = items[index].line_index
        self._insert_lines(insert_at, new_lines)

    def replace_item(
        self, section: Section, new_text: str,
        match: str | None = None, index: int | None = None,
    ) -> bool:
        item = self._find_item(section, match, index)
        if not item:
            return False
        m = CHECKBOX_RE.match(self.lines[item.line_index])
        if m:
            self.lines[item.line_index] = f"{m.group(1)}{m.group(2)}{m.group(3)}{new_text}"
            if item.raw.endswith("\n") and not self.lines[item.line_index].endswith("\n"):
                self.lines[item.line_index] += "\n"
        return True

    def delete_item(self, section: Section, match: str | None = None, index: int | None = None) -> bool:
        item = self._find_item(section, match, index)
        if not item:
            return False
        self._delete_lines(item.line_index, 1)
        return True

    def move_item(
        self, from_section: Section, to_section: Section,
        match: str | None = None, index: int | None = None,
    ) -> bool:
        item = self._find_item(from_section, match, index)
        if not item:
            return False
        raw = self.lines[item.line_index]
        self._delete_lines(item.line_index, 1)
        to_section_refreshed = self.find_section(_section_path(to_section, self.sections))
        if not to_section_refreshed:
            return False
        self.append(to_section_refreshed, raw.rstrip("\n"))
        return True

    def bulk_check(self, section: Section) -> int:
        items = self.get_items(section)
        count = 0
        for item in items:
            if not item.checked:
                m = CHECKBOX_RE.match(self.lines[item.line_index])
                if m:
                    self.lines[item.line_index] = f"{m.group(1)}x{m.group(3)}{m.group(4)}"
                    if item.raw.endswith("\n") and not self.lines[item.line_index].endswith("\n"):
                        self.lines[item.line_index] += "\n"
                    count += 1
        return count

    def bulk_uncheck(self, section: Section) -> int:
        items = self.get_items(section)
        count = 0
        for item in items:
            if item.checked:
                m = CHECKBOX_RE.match(self.lines[item.line_index])
                if m:
                    self.lines[item.line_index] = f"{m.group(1)} {m.group(3)}{m.group(4)}"
                    if item.raw.endswith("\n") and not self.lines[item.line_index].endswith("\n"):
                        self.lines[item.line_index] += "\n"
                    count += 1
        return count

    def find_items(self, query: str) -> list[tuple[Section, ChecklistItem]]:
        results: list[tuple[Section, ChecklistItem]] = []
        for _, sec in self.list_sections():
            for item in self.get_items(sec):
                if query.lower() in item.text.lower():
                    results.append((sec, item))
        return results

    # --- Tag operations ---

    def list_tags(self) -> list[str]:
        tags: set[str] = set()
        for line in self.lines:
            if not HEADING_RE.match(line):
                tags.update(extract_tags(line))
        return sorted(tags)

    def find_tagged(self, tag: str) -> list[tuple[Section | None, int, str]]:
        tag_lower = tag.lstrip("#").lower()
        results: list[tuple[Section | None, int, str]] = []
        for i, line in enumerate(self.lines):
            if HEADING_RE.match(line):
                continue
            line_tags = [t.lower() for t in extract_tags(line)]
            if tag_lower in line_tags:
                sec = self._section_for_line(i)
                results.append((sec, i, line.rstrip("\n")))
        return results

    def add_tag(
        self, section: Section, tag: str,
        match: str | None = None, index: int | None = None,
    ) -> bool:
        tag = tag.lstrip("#")
        line_idx = self._find_content_line(section, match, index)
        if line_idx is None:
            return False
        existing = [t.lower() for t in extract_tags(self.lines[line_idx])]
        if tag.lower() in existing:
            return True
        line = self.lines[line_idx].rstrip("\n")
        self.lines[line_idx] = f"{line} #{tag}\n"
        return True

    def remove_tag(
        self, section: Section, tag: str,
        match: str | None = None, index: int | None = None,
    ) -> bool:
        tag = tag.lstrip("#")
        line_idx = self._find_content_line(section, match, index)
        if line_idx is None:
            return False
        tag_lower = tag.lower()
        line = self.lines[line_idx]
        new_line = TAG_RE.sub(
            lambda m: "" if m.group(1).lower() == tag_lower else m.group(0),
            line,
        )
        new_line = re.sub(r"  +", " ", new_line)
        new_line = new_line.rstrip(" ")
        if not new_line.endswith("\n") and line.endswith("\n"):
            new_line += "\n"
        self.lines[line_idx] = new_line
        return True

    def _find_content_line(
        self, section: Section, match: str | None, index: int | None,
    ) -> int | None:
        if index is not None:
            item = self._find_item(section, match=None, index=index)
            return item.line_index if item else None
        if match is not None:
            match_lower = match.lower()
            item = self._find_item(section, match=match, index=None)
            if item:
                return item.line_index
            end = section.children[0].heading_line if section.children else section.content_end
            for i in range(section.content_start, end):
                line = self.lines[i]
                if line.strip() and not HEADING_RE.match(line):
                    if match_lower in line.lower():
                        return i
        return None

    def _section_for_line(self, line_index: int) -> Section | None:
        for _, sec in self.list_sections():
            if sec.heading_line <= line_index < sec.content_end:
                best = sec
                for child in sec.children:
                    if child.heading_line <= line_index < child.content_end:
                        best = child
                        break
                return best
        return None

    # --- Section operations ---

    def add_section(
        self,
        title: str,
        level: int = 1,
        content: str = "",
        after: str | None = None,
        before: str | None = None,
        parent: str | None = None,
    ) -> bool:
        heading = f"{'#' * level} {title}\n"
        new_lines = ["\n", heading]
        if content:
            new_lines.extend(_ensure_newlines(content))
        if new_lines[-1].strip():
            new_lines.append("\n")

        if after:
            sec = self.find_section(after)
            if not sec:
                return False
            self._insert_lines(sec.content_end, new_lines)
        elif before:
            sec = self.find_section(before)
            if not sec:
                return False
            self._insert_lines(sec.heading_line, new_lines)
        elif parent:
            sec = self.find_section(parent)
            if not sec:
                return False
            self._insert_lines(sec.content_end, new_lines)
        else:
            self._insert_lines(len(self.lines), new_lines)
        return True

    def rename_section(self, path: str, new_title: str) -> bool:
        sec = self.find_section(path)
        if not sec:
            return False
        self.lines[sec.heading_line] = f"{'#' * sec.level} {new_title}\n"
        self._parse()
        return True

    def replace_section_content(self, path: str, new_content: str) -> bool:
        sec = self.find_section(path)
        if not sec:
            return False
        end = sec.children[0].heading_line if sec.children else sec.content_end
        new_lines = _ensure_newlines(new_content)
        if new_lines and new_lines[-1].strip():
            new_lines.append("\n")
        del self.lines[sec.content_start : end]
        self.lines[sec.content_start : sec.content_start] = new_lines
        self._parse()
        return True

    def remove_section(self, path: str) -> list[str] | None:
        sec = self.find_section(path)
        if not sec:
            return None
        removed = self.lines[sec.heading_line : sec.content_end]
        self._delete_lines(sec.heading_line, sec.content_end - sec.heading_line)
        self._collapse_blank_lines()
        return removed

    def move_section(
        self,
        path: str,
        after: str | None = None,
        before: str | None = None,
    ) -> bool:
        sec = self.find_section(path)
        if not sec:
            return False
        extracted = self.lines[sec.heading_line : sec.content_end]
        self._delete_lines(sec.heading_line, sec.content_end - sec.heading_line)
        block = ["\n"] + extracted
        if block[-1].strip():
            block.append("\n")
        if after:
            target = self.find_section(after)
            if not target:
                return False
            self._insert_lines(target.content_end, block)
        elif before:
            target = self.find_section(before)
            if not target:
                return False
            self._insert_lines(target.heading_line, block)
        else:
            self._insert_lines(len(self.lines), block)
        self._collapse_blank_lines()
        return True

    # --- Low-level line operations ---

    def _insert_lines(self, at: int, new_lines: list[str]) -> None:
        self.lines[at:at] = new_lines
        self._parse()

    def _delete_lines(self, at: int, count: int) -> None:
        del self.lines[at : at + count]
        self._parse()

    def _collapse_blank_lines(self) -> None:
        i = 0
        while i < len(self.lines):
            if not self.lines[i].strip():
                j = i
                while j < len(self.lines) and not self.lines[j].strip():
                    j += 1
                if j - i > 2:
                    del self.lines[i + 2 : j]
            i += 1
        self._parse()


# ---------------------------------------------------------------------------
# Tree building helpers
# ---------------------------------------------------------------------------


def _build_tree(flat: list[Section]) -> list[Section]:
    root: list[Section] = []
    stack: list[Section] = []
    for sec in flat:
        while stack and stack[-1].level >= sec.level:
            stack.pop()
        if stack:
            stack[-1].children.append(sec)
        else:
            root.append(sec)
        stack.append(sec)
    return root


def _walk_path(sections: list[Section], parts: list[str]) -> Section | None:
    if not parts:
        return None
    target = parts[0]
    for sec in sections:
        if sec.normalized_title == target:
            if len(parts) == 1:
                return sec
            return _walk_path(sec.children, parts[1:])
    return None


def _flatten_sections(
    sections: list[Section], depth: int, result: list[tuple[int, Section]]
) -> None:
    for sec in sections:
        result.append((depth, sec))
        _flatten_sections(sec.children, depth + 1, result)


def _section_path(sec: Section, top_sections: list[Section]) -> str:
    def _find(sections: list[Section], target_line: int, prefix: str) -> str | None:
        for s in sections:
            current = f"{prefix}/{s.normalized_title}" if prefix else s.normalized_title
            if s.heading_line == target_line:
                return current
            found = _find(s.children, target_line, current)
            if found:
                return found
        return None
    return _find(top_sections, sec.heading_line, "") or sec.normalized_title


# ---------------------------------------------------------------------------
# Cross-file operations
# ---------------------------------------------------------------------------


def move_item_across_files(
    from_doc: Document, from_section_path: str,
    to_doc: Document, to_section_path: str,
    match: str | None = None, index: int | None = None,
) -> bool:
    from_sec = from_doc.find_section(from_section_path)
    if not from_sec:
        return False
    to_sec = to_doc.find_section(to_section_path)
    if not to_sec:
        return False
    item = from_doc._find_item(from_sec, match, index)
    if not item:
        return False
    raw = from_doc.lines[item.line_index].rstrip("\n")
    from_doc._delete_lines(item.line_index, 1)
    to_doc.append(to_sec, raw)
    return True


def bulk_move_items(
    from_doc: Document, from_section_path: str,
    to_doc: Document, to_section_path: str,
    indices: list[int] | None = None,
    checked_only: bool = False,
    unchecked_only: bool = False,
) -> int:
    from_sec = from_doc.find_section(from_section_path)
    if not from_sec:
        return 0
    to_sec = to_doc.find_section(to_section_path)
    if not to_sec:
        return 0
    items = from_doc.get_items(from_sec)
    if not items:
        return 0
    to_move = []
    for i, item in enumerate(items):
        if indices is not None and i not in indices:
            continue
        if checked_only and not item.checked:
            continue
        if unchecked_only and item.checked:
            continue
        to_move.append(item)
    if not to_move:
        return 0
    raw_lines = [from_doc.lines[item.line_index].rstrip("\n") for item in to_move]
    for item in reversed(to_move):
        from_doc._delete_lines(item.line_index, 1)
        from_doc._parse()
    for raw in raw_lines:
        to_sec = to_doc.find_section(to_section_path)
        if not to_sec:
            break
        to_doc.append(to_sec, raw)
    return len(raw_lines)

log = logging.getLogger(__name__)

_workspace_path: Path | None = None


def init(config):
    """Initialize using the agent workspace path. Called by the skill loader on activation."""
    global _workspace_path
    _workspace_path = config.workspace_path.resolve()
    log.info(f"Markdown vault initialized at workspace {_workspace_path}")


def _resolve(file: str) -> Path:
    """Resolve a relative file path within the workspace, with safety check."""
    if _workspace_path is None:
        raise RuntimeError("Vault not initialized — skill not activated?")
    resolved = (_workspace_path / file).resolve()
    if not resolved.is_relative_to(_workspace_path):
        raise ValueError(f"Path escapes workspace: {file}")
    return resolved


# -- Tool implementations ---------------------------------------------------


def tool_vault_read(ctx, file: str) -> ToolResult:
    """Read an entire markdown file as text."""
    log.info(f"[tool:vault_read] file={file!r}")
    try:
        path = _resolve(file)
        if not path.is_file():
            return ToolResult(text=f"[error: file not found: {file}]")
        return ToolResult(text=path.read_text())
    except Exception as e:
        return ToolResult(text=f"[error: {e}]")


def tool_vault_create_file(
    ctx, file: str, content: str = "", template: str | None = None,
) -> ToolResult:
    """Create a new markdown file, optionally from a template.

    If template is given, its content is used as the starting point
    (with {{date}} replaced by today's date). content is ignored if
    template is provided. Will not overwrite an existing file.
    """
    log.info(f"[tool:vault_create_file] file={file!r} template={template!r}")
    try:
        path = _resolve(file)
        if path.exists():
            return ToolResult(text=f"[error: file already exists: {file}]")

        if template:
            tmpl_path = _resolve(template)
            if not tmpl_path.is_file():
                return ToolResult(text=f"[error: template not found: {template}]")
            import datetime
            body = tmpl_path.read_text()
            today = datetime.date.today().isoformat()
            body = body.replace("{{date}}", today)
        else:
            body = content

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
        return ToolResult(text="Done.")
    except Exception as e:
        return ToolResult(text=f"[error: {e}]")


def tool_vault_list(ctx, path: str = "") -> ToolResult:
    """List markdown files at a path within the vault."""
    log.info(f"[tool:vault_list] path={path!r}")
    try:
        target = _resolve(path)
        if not target.is_dir():
            return ToolResult(text=f"[error: not a directory: {path}]")

        entries = sorted(target.iterdir())
        lines = []
        for entry in entries:
            rel = entry.relative_to(_workspace_path)  # type: ignore[arg-type]  # guarded by _resolve
            if entry.is_dir():
                lines.append(f"  {rel}/")
            elif entry.suffix in (".md", ".markdown"):
                lines.append(f"  {rel}")
        if not lines:
            return ToolResult(text="(no markdown files found)")
        return ToolResult(text="\n".join(lines))
    except Exception as e:
        return ToolResult(text=f"[error: {e}]")


def tool_vault_show(ctx, file: str, section: str | None = None) -> ToolResult:
    """Show a section's content, or the document outline if no section given."""
    log.info(f"[tool:vault_show] file={file!r} section={section!r}")
    try:
        doc = Document.from_file(_resolve(file))
        if section:
            sec = doc.find_section(section)
            if not sec:
                return ToolResult(text=f"[error: section not found: {section}]")
            return ToolResult(text="".join(sec.all_lines(doc.lines)))
        else:
            lines = []
            for depth, sec in doc.list_sections():
                indent = "  " * depth
                lines.append(f"{indent}{'#' * sec.level} {sec.title}")
            return ToolResult(text="\n".join(lines))
    except Exception as e:
        return ToolResult(text=f"[error: {e}]")


def tool_vault_items(ctx, file: str, section: str) -> ToolResult:
    """List checklist items in a section with their indices."""
    log.info(f"[tool:vault_items] file={file!r} section={section!r}")
    try:
        doc = Document.from_file(_resolve(file))
        sec = doc.find_section(section)
        if not sec:
            return ToolResult(text=f"[error: section not found: {section}]")
        items = doc.get_items(sec)
        if not items:
            return ToolResult(text="(no checklist items)")
        lines = []
        for i, item in enumerate(items):
            mark = "x" if item.checked else " "
            lines.append(f"  {i}: [{mark}] {item.text}")
        return ToolResult(text="\n".join(lines))
    except Exception as e:
        return ToolResult(text=f"[error: {e}]")


def tool_vault_check(ctx, file: str, section: str, match: str | None = None, index: int | None = None) -> ToolResult:
    """Mark a checklist item as done."""
    log.info(f"[tool:vault_check] file={file!r} section={section!r} match={match!r} index={index!r}")
    try:
        path = _resolve(file)
        doc = Document.from_file(path)
        sec = doc.find_section(section)
        if not sec:
            return ToolResult(text=f"[error: section not found: {section}]")
        if doc.check_item(sec, match=match, index=index):
            doc.save(path)
            return ToolResult(text="Done.")
        return ToolResult(text="[error: item not found]")
    except Exception as e:
        return ToolResult(text=f"[error: {e}]")


def tool_vault_uncheck(ctx, file: str, section: str, match: str | None = None, index: int | None = None) -> ToolResult:
    """Mark a checklist item as not done."""
    log.info(f"[tool:vault_uncheck] file={file!r} section={section!r} match={match!r} index={index!r}")
    try:
        path = _resolve(file)
        doc = Document.from_file(path)
        sec = doc.find_section(section)
        if not sec:
            return ToolResult(text=f"[error: section not found: {section}]")
        if doc.uncheck_item(sec, match=match, index=index):
            doc.save(path)
            return ToolResult(text="Done.")
        return ToolResult(text="[error: item not found]")
    except Exception as e:
        return ToolResult(text=f"[error: {e}]")


def tool_vault_append(ctx, file: str, section: str, text: str) -> ToolResult:
    """Append text to the end of a section."""
    log.info(f"[tool:vault_append] file={file!r} section={section!r}")
    try:
        path = _resolve(file)
        doc = Document.from_file(path)
        sec = doc.find_section(section)
        if not sec:
            return ToolResult(text=f"[error: section not found: {section}]")
        doc.append(sec, text)
        doc.save(path)
        return ToolResult(text="Done.")
    except Exception as e:
        return ToolResult(text=f"[error: {e}]")


def tool_vault_prepend(ctx, file: str, section: str, text: str) -> ToolResult:
    """Prepend text to the start of a section."""
    log.info(f"[tool:vault_prepend] file={file!r} section={section!r}")
    try:
        path = _resolve(file)
        doc = Document.from_file(path)
        sec = doc.find_section(section)
        if not sec:
            return ToolResult(text=f"[error: section not found: {section}]")
        doc.prepend(sec, text)
        doc.save(path)
        return ToolResult(text="Done.")
    except Exception as e:
        return ToolResult(text=f"[error: {e}]")


def tool_vault_insert(ctx, file: str, section: str, index: int, text: str) -> ToolResult:
    """Insert text at a specific item index within a section."""
    log.info(f"[tool:vault_insert] file={file!r} section={section!r} index={index}")
    try:
        path = _resolve(file)
        doc = Document.from_file(path)
        sec = doc.find_section(section)
        if not sec:
            return ToolResult(text=f"[error: section not found: {section}]")
        doc.insert_item(sec, index, text)
        doc.save(path)
        return ToolResult(text="Done.")
    except Exception as e:
        return ToolResult(text=f"[error: {e}]")


def tool_vault_delete(ctx, file: str, section: str, match: str | None = None, index: int | None = None) -> ToolResult:
    """Delete a checklist item from a section."""
    log.info(f"[tool:vault_delete] file={file!r} section={section!r} match={match!r} index={index!r}")
    try:
        path = _resolve(file)
        doc = Document.from_file(path)
        sec = doc.find_section(section)
        if not sec:
            return ToolResult(text=f"[error: section not found: {section}]")
        if doc.delete_item(sec, match=match, index=index):
            doc.save(path)
            return ToolResult(text="Done.")
        return ToolResult(text="[error: item not found]")
    except Exception as e:
        return ToolResult(text=f"[error: {e}]")


def tool_vault_add_section(
    ctx, file: str, title: str, level: int = 1, content: str = "",
    after: str | None = None, before: str | None = None, parent: str | None = None,
) -> ToolResult:
    """Add a new section to a markdown file."""
    log.info(f"[tool:vault_add_section] file={file!r} title={title!r} level={level}")
    try:
        path = _resolve(file)
        doc = Document.from_file(path)
        if doc.add_section(title, level=level, content=content, after=after, before=before, parent=parent):
            doc.save(path)
            return ToolResult(text="Done.")
        return ToolResult(text="[error: target section not found]")
    except Exception as e:
        return ToolResult(text=f"[error: {e}]")


def tool_vault_remove_section(ctx, file: str, section: str) -> ToolResult:
    """Remove a section (heading + all content including children)."""
    log.info(f"[tool:vault_remove_section] file={file!r} section={section!r}")
    try:
        path = _resolve(file)
        doc = Document.from_file(path)
        removed = doc.remove_section(section)
        if removed is not None:
            doc.save(path)
            return ToolResult(text="Done.")
        return ToolResult(text=f"[error: section not found: {section}]")
    except Exception as e:
        return ToolResult(text=f"[error: {e}]")


def tool_vault_move_section(
    ctx, file: str, section: str,
    after: str | None = None, before: str | None = None,
) -> ToolResult:
    """Move a section (heading + content + children) to a new position."""
    log.info(f"[tool:vault_move_section] file={file!r} section={section!r}")
    try:
        path = _resolve(file)
        doc = Document.from_file(path)
        if doc.move_section(section, after=after, before=before):
            doc.save(path)
            return ToolResult(text="Done.")
        return ToolResult(text="[error: section or target not found]")
    except Exception as e:
        return ToolResult(text=f"[error: {e}]")


def tool_vault_replace_item(
    ctx, file: str, section: str, text: str,
    match: str | None = None, index: int | None = None,
) -> ToolResult:
    """Replace a checklist item's text, preserving its checked state."""
    log.info(f"[tool:vault_replace_item] file={file!r} section={section!r}")
    try:
        path = _resolve(file)
        doc = Document.from_file(path)
        sec = doc.find_section(section)
        if not sec:
            return ToolResult(text=f"[error: section not found: {section}]")
        if doc.replace_item(sec, text, match=match, index=index):
            doc.save(path)
            return ToolResult(text="Done.")
        return ToolResult(text="[error: item not found]")
    except Exception as e:
        return ToolResult(text=f"[error: {e}]")


def tool_vault_move_item(
    ctx, file: str, from_section: str, to_section: str,
    match: str | None = None, index: int | None = None,
) -> ToolResult:
    """Move a checklist item from one section to another."""
    log.info(f"[tool:vault_move_item] file={file!r} from={from_section!r} to={to_section!r}")
    try:
        path = _resolve(file)
        doc = Document.from_file(path)
        from_sec = doc.find_section(from_section)
        if not from_sec:
            return ToolResult(text=f"[error: source section not found: {from_section}]")
        to_sec = doc.find_section(to_section)
        if not to_sec:
            return ToolResult(text=f"[error: target section not found: {to_section}]")
        if doc.move_item(from_sec, to_sec, match=match, index=index):
            doc.save(path)
            return ToolResult(text="Done.")
        return ToolResult(text="[error: item not found]")
    except Exception as e:
        return ToolResult(text=f"[error: {e}]")


def tool_vault_bulk_check(ctx, file: str, section: str) -> ToolResult:
    """Mark all checklist items in a section as done."""
    log.info(f"[tool:vault_bulk_check] file={file!r} section={section!r}")
    try:
        path = _resolve(file)
        doc = Document.from_file(path)
        sec = doc.find_section(section)
        if not sec:
            return ToolResult(text=f"[error: section not found: {section}]")
        count = doc.bulk_check(sec)
        doc.save(path)
        return ToolResult(text=f"Checked {count} item(s).")
    except Exception as e:
        return ToolResult(text=f"[error: {e}]")


def tool_vault_bulk_uncheck(ctx, file: str, section: str) -> ToolResult:
    """Mark all checklist items in a section as not done."""
    log.info(f"[tool:vault_bulk_uncheck] file={file!r} section={section!r}")
    try:
        path = _resolve(file)
        doc = Document.from_file(path)
        sec = doc.find_section(section)
        if not sec:
            return ToolResult(text=f"[error: section not found: {section}]")
        count = doc.bulk_uncheck(sec)
        doc.save(path)
        return ToolResult(text=f"Unchecked {count} item(s).")
    except Exception as e:
        return ToolResult(text=f"[error: {e}]")


def tool_vault_find_items(ctx, file: str, query: str) -> ToolResult:
    """Search all sections for checklist items matching a substring."""
    log.info(f"[tool:vault_find_items] file={file!r} query={query!r}")
    try:
        path = _resolve(file)
        doc = Document.from_file(path)
        results = doc.find_items(query)
        if not results:
            return ToolResult(text="(no matching items)")
        lines = []
        for sec, item in results:
            mark = "x" if item.checked else " "
            lines.append(f"  {sec.title}: [{mark}] {item.text}")
        return ToolResult(text="\n".join(lines))
    except Exception as e:
        return ToolResult(text=f"[error: {e}]")


def tool_vault_rename_section(ctx, file: str, section: str, title: str) -> ToolResult:
    """Rename a section's heading text."""
    log.info(f"[tool:vault_rename_section] file={file!r} section={section!r} title={title!r}")
    try:
        path = _resolve(file)
        doc = Document.from_file(path)
        if doc.rename_section(section, title):
            doc.save(path)
            return ToolResult(text="Done.")
        return ToolResult(text=f"[error: section not found: {section}]")
    except Exception as e:
        return ToolResult(text=f"[error: {e}]")


def tool_vault_replace_section(ctx, file: str, section: str, content: str) -> ToolResult:
    """Replace a section's body content, preserving heading and children."""
    log.info(f"[tool:vault_replace_section] file={file!r} section={section!r}")
    try:
        path = _resolve(file)
        doc = Document.from_file(path)
        if doc.replace_section_content(section, content):
            doc.save(path)
            return ToolResult(text="Done.")
        return ToolResult(text=f"[error: section not found: {section}]")
    except Exception as e:
        return ToolResult(text=f"[error: {e}]")


def tool_vault_tags(ctx, file: str) -> ToolResult:
    """List all unique tags in a markdown file."""
    log.info(f"[tool:vault_tags] file={file!r}")
    try:
        path = _resolve(file)
        doc = Document.from_file(path)
        tags = doc.list_tags()
        if not tags:
            return ToolResult(text="(no tags)")
        return ToolResult(text="\n".join(f"  #{t}" for t in tags))
    except Exception as e:
        return ToolResult(text=f"[error: {e}]")


def tool_vault_tagged(ctx, file: str, tag: str) -> ToolResult:
    """Find all lines with a given tag."""
    log.info(f"[tool:vault_tagged] file={file!r} tag={tag!r}")
    try:
        path = _resolve(file)
        doc = Document.from_file(path)
        results = doc.find_tagged(tag)
        if not results:
            return ToolResult(text="(no matching lines)")
        lines = []
        for sec, _line_idx, text in results:
            section_name = sec.title if sec else "(top)"
            lines.append(f"  {section_name}: {text}")
        return ToolResult(text="\n".join(lines))
    except Exception as e:
        return ToolResult(text=f"[error: {e}]")


def tool_vault_add_tag(
    ctx, file: str, section: str, tag: str,
    match: str | None = None, index: int | None = None,
) -> ToolResult:
    """Add a tag to a checklist item."""
    log.info(f"[tool:vault_add_tag] file={file!r} section={section!r} tag={tag!r}")
    try:
        path = _resolve(file)
        doc = Document.from_file(path)
        sec = doc.find_section(section)
        if not sec:
            return ToolResult(text=f"[error: section not found: {section}]")
        if doc.add_tag(sec, tag, match=match, index=index):
            doc.save(path)
            return ToolResult(text="Done.")
        return ToolResult(text="[error: item not found]")
    except Exception as e:
        return ToolResult(text=f"[error: {e}]")


def tool_vault_remove_tag(
    ctx, file: str, section: str, tag: str,
    match: str | None = None, index: int | None = None,
) -> ToolResult:
    """Remove a tag from a checklist item."""
    log.info(f"[tool:vault_remove_tag] file={file!r} section={section!r} tag={tag!r}")
    try:
        path = _resolve(file)
        doc = Document.from_file(path)
        sec = doc.find_section(section)
        if not sec:
            return ToolResult(text=f"[error: section not found: {section}]")
        if doc.remove_tag(sec, tag, match=match, index=index):
            doc.save(path)
            return ToolResult(text="Done.")
        return ToolResult(text="[error: item not found]")
    except Exception as e:
        return ToolResult(text=f"[error: {e}]")


def tool_vault_daily_path(ctx, date: str | None = None, offset: int = 0) -> ToolResult:
    """Return the vault path for a daily journal file."""
    log.info(f"[tool:vault_daily_path] date={date!r} offset={offset}")
    try:
        return ToolResult(text=daily_path(date=date, offset=offset))
    except Exception as e:
        return ToolResult(text=f"[error: {e}]")


def tool_vault_move_items(
    ctx, from_file: str, from_section: str,
    to_file: str, to_section: str,
    indices: str | None = None,
    checked_only: bool = False,
    unchecked_only: bool = False,
) -> ToolResult:
    """Move multiple checklist items between files/sections."""
    log.info(f"[tool:vault_move_items] {from_file!r}:{from_section!r} -> {to_file!r}:{to_section!r}")
    try:
        from_path = _resolve(from_file)
        to_path = _resolve(to_file)
        from_doc = Document.from_file(from_path)
        to_doc = Document.from_file(to_path) if to_path != from_path else from_doc

        idx_list = None
        if indices:
            idx_list = [int(i.strip()) for i in indices.split(",")]

        count = bulk_move_items(
            from_doc, from_section,
            to_doc, to_section,
            indices=idx_list,
            checked_only=checked_only,
            unchecked_only=unchecked_only,
        )
        if count > 0:
            from_doc.save(from_path)
            if to_path != from_path:
                to_doc.save(to_path)
        return ToolResult(text=f"Moved {count} item(s).")
    except Exception as e:
        return ToolResult(text=f"[error: {e}]")


# -- Registry ---------------------------------------------------------------

TOOLS = {
    "vault_daily_path": tool_vault_daily_path,
    "vault_move_items": tool_vault_move_items,
    "vault_read": tool_vault_read,
    "vault_create_file": tool_vault_create_file,
    "vault_list": tool_vault_list,
    "vault_show": tool_vault_show,
    "vault_items": tool_vault_items,
    "vault_check": tool_vault_check,
    "vault_uncheck": tool_vault_uncheck,
    "vault_append": tool_vault_append,
    "vault_prepend": tool_vault_prepend,
    "vault_insert": tool_vault_insert,
    "vault_delete": tool_vault_delete,
    "vault_replace_item": tool_vault_replace_item,
    "vault_move_item": tool_vault_move_item,
    "vault_bulk_check": tool_vault_bulk_check,
    "vault_bulk_uncheck": tool_vault_bulk_uncheck,
    "vault_find_items": tool_vault_find_items,
    "vault_add_section": tool_vault_add_section,
    "vault_remove_section": tool_vault_remove_section,
    "vault_move_section": tool_vault_move_section,
    "vault_rename_section": tool_vault_rename_section,
    "vault_replace_section": tool_vault_replace_section,
    "vault_tags": tool_vault_tags,
    "vault_tagged": tool_vault_tagged,
    "vault_add_tag": tool_vault_add_tag,
    "vault_remove_tag": tool_vault_remove_tag,
}

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "vault_daily_path",
            "description": "Get the vault file path for a daily journal. Returns a path like 'journals/2026/2026-03-17.md'. Use this to avoid constructing date-based paths manually.",
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "ISO date string (YYYY-MM-DD). Omit for today.",
                    },
                    "offset": {
                        "type": "integer",
                        "description": "Days to shift: -1 for yesterday, 1 for tomorrow, etc. Applied after date. Default: 0.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vault_move_items",
            "description": "Move multiple checklist items between files and/or sections. Can move all items, only checked, only unchecked, or specific indices. Use for daily task migration (e.g. unchecked items from yesterday's 'today' to today's 'today').",
            "parameters": {
                "type": "object",
                "properties": {
                    "from_file": {
                        "type": "string",
                        "description": "Source file (relative path)",
                    },
                    "from_section": {
                        "type": "string",
                        "description": "Source section path",
                    },
                    "to_file": {
                        "type": "string",
                        "description": "Target file (relative path, can be same as from_file)",
                    },
                    "to_section": {
                        "type": "string",
                        "description": "Target section path",
                    },
                    "indices": {
                        "type": "string",
                        "description": "Comma-separated item indices to move (e.g. '0,2,3'). Omit to move all.",
                    },
                    "checked_only": {
                        "type": "boolean",
                        "description": "Only move checked items (default: false)",
                    },
                    "unchecked_only": {
                        "type": "boolean",
                        "description": "Only move unchecked items (default: false)",
                    },
                },
                "required": ["from_file", "from_section", "to_file", "to_section"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vault_read",
            "description": "Read an entire markdown file as text. Use when you need the full file content, not just a section.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file": {
                        "type": "string",
                        "description": "Relative path to the markdown file within the workspace",
                    },
                },
                "required": ["file"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vault_create_file",
            "description": "Create a new markdown file. Optionally use a template (with {{date}} substitution). Will not overwrite existing files. Creates parent directories as needed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file": {
                        "type": "string",
                        "description": "Relative path for the new file (e.g. 'journals/2026/2026-03-17.md')",
                    },
                    "content": {
                        "type": "string",
                        "description": "Initial file content (ignored if template is provided)",
                    },
                    "template": {
                        "type": "string",
                        "description": "Relative path to a template file (e.g. 'templates/daily.md'). {{date}} in template is replaced with today's date.",
                    },
                },
                "required": ["file"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vault_list",
            "description": "List markdown files at a path within the vault.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative directory path within the vault (empty string for root)",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vault_show",
            "description": "Show a section's content from a markdown file, or the document's heading outline if no section is given.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file": {
                        "type": "string",
                        "description": "Relative path to the markdown file within the vault",
                    },
                    "section": {
                        "type": "string",
                        "description": "Section path (e.g. 'today' or 'notes/standup'). Omit to see the document outline.",
                    },
                },
                "required": ["file"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vault_items",
            "description": "List checklist items in a section with their indices and checked state.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file": {
                        "type": "string",
                        "description": "Relative path to the markdown file within the vault",
                    },
                    "section": {
                        "type": "string",
                        "description": "Section path (e.g. 'today')",
                    },
                },
                "required": ["file", "section"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vault_check",
            "description": "Mark a checklist item as done. Select by substring match or index.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file": {
                        "type": "string",
                        "description": "Relative path to the markdown file",
                    },
                    "section": {
                        "type": "string",
                        "description": "Section path",
                    },
                    "match": {
                        "type": "string",
                        "description": "Substring to match in item text",
                    },
                    "index": {
                        "type": "integer",
                        "description": "Item index (0-based, negatives ok)",
                    },
                },
                "required": ["file", "section"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vault_uncheck",
            "description": "Mark a checklist item as not done. Select by substring match or index.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file": {
                        "type": "string",
                        "description": "Relative path to the markdown file",
                    },
                    "section": {
                        "type": "string",
                        "description": "Section path",
                    },
                    "match": {
                        "type": "string",
                        "description": "Substring to match in item text",
                    },
                    "index": {
                        "type": "integer",
                        "description": "Item index (0-based, negatives ok)",
                    },
                },
                "required": ["file", "section"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vault_append",
            "description": "Append text (e.g. a new checklist item) to the end of a section.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file": {
                        "type": "string",
                        "description": "Relative path to the markdown file",
                    },
                    "section": {
                        "type": "string",
                        "description": "Section path",
                    },
                    "text": {
                        "type": "string",
                        "description": "Text to append (e.g. '- [ ] new task')",
                    },
                },
                "required": ["file", "section", "text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vault_prepend",
            "description": "Prepend text to the start of a section (right after the heading).",
            "parameters": {
                "type": "object",
                "properties": {
                    "file": {
                        "type": "string",
                        "description": "Relative path to the markdown file",
                    },
                    "section": {
                        "type": "string",
                        "description": "Section path",
                    },
                    "text": {
                        "type": "string",
                        "description": "Text to prepend (e.g. '- [ ] urgent task')",
                    },
                },
                "required": ["file", "section", "text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vault_insert",
            "description": "Insert text at a specific item index within a section.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file": {
                        "type": "string",
                        "description": "Relative path to the markdown file",
                    },
                    "section": {
                        "type": "string",
                        "description": "Section path",
                    },
                    "index": {
                        "type": "integer",
                        "description": "Item index to insert before (0-based)",
                    },
                    "text": {
                        "type": "string",
                        "description": "Text to insert",
                    },
                },
                "required": ["file", "section", "index", "text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vault_delete",
            "description": "Delete a checklist item from a section. Select by substring match or index.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file": {
                        "type": "string",
                        "description": "Relative path to the markdown file",
                    },
                    "section": {
                        "type": "string",
                        "description": "Section path",
                    },
                    "match": {
                        "type": "string",
                        "description": "Substring to match in item text",
                    },
                    "index": {
                        "type": "integer",
                        "description": "Item index (0-based, negatives ok)",
                    },
                },
                "required": ["file", "section"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vault_add_section",
            "description": "Add a new section to a markdown file. Position with after/before/parent, or omit to append at end.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file": {
                        "type": "string",
                        "description": "Relative path to the markdown file",
                    },
                    "title": {
                        "type": "string",
                        "description": "Heading text for the new section",
                    },
                    "level": {
                        "type": "integer",
                        "description": "Heading level 1-6 (default: 1)",
                    },
                    "content": {
                        "type": "string",
                        "description": "Initial content for the section",
                    },
                    "after": {
                        "type": "string",
                        "description": "Insert after this section",
                    },
                    "before": {
                        "type": "string",
                        "description": "Insert before this section",
                    },
                    "parent": {
                        "type": "string",
                        "description": "Insert as last child of this section",
                    },
                },
                "required": ["file", "title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vault_remove_section",
            "description": "Remove an entire section (heading + all content + children). ALWAYS confirm with the user before calling this.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file": {
                        "type": "string",
                        "description": "Relative path to the markdown file",
                    },
                    "section": {
                        "type": "string",
                        "description": "Section path to remove (e.g. 'notes/standup')",
                    },
                },
                "required": ["file", "section"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vault_move_section",
            "description": "Move a section (heading + content + children) to a new position. Specify after or before to position relative to another section, or omit both to move to end.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file": {
                        "type": "string",
                        "description": "Relative path to the markdown file",
                    },
                    "section": {
                        "type": "string",
                        "description": "Section path to move",
                    },
                    "after": {
                        "type": "string",
                        "description": "Place after this section",
                    },
                    "before": {
                        "type": "string",
                        "description": "Place before this section",
                    },
                },
                "required": ["file", "section"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vault_replace_item",
            "description": "Replace a checklist item's text, preserving its checked/unchecked state. Select by match or index.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file": {
                        "type": "string",
                        "description": "Relative path to the markdown file",
                    },
                    "section": {
                        "type": "string",
                        "description": "Section path",
                    },
                    "text": {
                        "type": "string",
                        "description": "New text for the item (without the checkbox prefix)",
                    },
                    "match": {
                        "type": "string",
                        "description": "Substring to match in current item text",
                    },
                    "index": {
                        "type": "integer",
                        "description": "Item index (0-based, negatives ok)",
                    },
                },
                "required": ["file", "section", "text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vault_move_item",
            "description": "Move a checklist item from one section to another. The item is appended to the target section.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file": {
                        "type": "string",
                        "description": "Relative path to the markdown file",
                    },
                    "from_section": {
                        "type": "string",
                        "description": "Source section path",
                    },
                    "to_section": {
                        "type": "string",
                        "description": "Target section path",
                    },
                    "match": {
                        "type": "string",
                        "description": "Substring to match in item text",
                    },
                    "index": {
                        "type": "integer",
                        "description": "Item index (0-based, negatives ok)",
                    },
                },
                "required": ["file", "from_section", "to_section"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vault_bulk_check",
            "description": "Mark ALL checklist items in a section as done.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file": {
                        "type": "string",
                        "description": "Relative path to the markdown file",
                    },
                    "section": {
                        "type": "string",
                        "description": "Section path",
                    },
                },
                "required": ["file", "section"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vault_bulk_uncheck",
            "description": "Mark ALL checklist items in a section as not done.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file": {
                        "type": "string",
                        "description": "Relative path to the markdown file",
                    },
                    "section": {
                        "type": "string",
                        "description": "Section path",
                    },
                },
                "required": ["file", "section"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vault_find_items",
            "description": "Search all sections in a file for checklist items matching a substring. Returns section name and item text for each match.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file": {
                        "type": "string",
                        "description": "Relative path to the markdown file",
                    },
                    "query": {
                        "type": "string",
                        "description": "Substring to search for in item text",
                    },
                },
                "required": ["file", "query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vault_rename_section",
            "description": "Rename a section's heading text, preserving its level, content, and position.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file": {
                        "type": "string",
                        "description": "Relative path to the markdown file",
                    },
                    "section": {
                        "type": "string",
                        "description": "Section path to rename",
                    },
                    "title": {
                        "type": "string",
                        "description": "New heading text",
                    },
                },
                "required": ["file", "section", "title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vault_replace_section",
            "description": "Replace a section's body content while preserving the heading and any child sections.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file": {
                        "type": "string",
                        "description": "Relative path to the markdown file",
                    },
                    "section": {
                        "type": "string",
                        "description": "Section path",
                    },
                    "content": {
                        "type": "string",
                        "description": "New content for the section body",
                    },
                },
                "required": ["file", "section", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vault_tags",
            "description": "List all unique #tags in a markdown file. Use for tag discovery.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file": {
                        "type": "string",
                        "description": "Relative path to the markdown file",
                    },
                },
                "required": ["file"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vault_tagged",
            "description": "Find all lines with a given #tag. Returns section name and line text for each match.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file": {
                        "type": "string",
                        "description": "Relative path to the markdown file",
                    },
                    "tag": {
                        "type": "string",
                        "description": "Tag to search for (with or without # prefix)",
                    },
                },
                "required": ["file", "tag"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vault_add_tag",
            "description": "Add a #tag to a checklist item. The tag is appended to the end of the item text.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file": {
                        "type": "string",
                        "description": "Relative path to the markdown file",
                    },
                    "section": {
                        "type": "string",
                        "description": "Section path",
                    },
                    "tag": {
                        "type": "string",
                        "description": "Tag to add (with or without # prefix)",
                    },
                    "match": {
                        "type": "string",
                        "description": "Substring to match in item text",
                    },
                    "index": {
                        "type": "integer",
                        "description": "Item index (0-based, negatives ok)",
                    },
                },
                "required": ["file", "section", "tag"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vault_remove_tag",
            "description": "Remove a #tag from a checklist item.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file": {
                        "type": "string",
                        "description": "Relative path to the markdown file",
                    },
                    "section": {
                        "type": "string",
                        "description": "Section path",
                    },
                    "tag": {
                        "type": "string",
                        "description": "Tag to remove (with or without # prefix)",
                    },
                    "match": {
                        "type": "string",
                        "description": "Substring to match in item text",
                    },
                    "index": {
                        "type": "integer",
                        "description": "Item index (0-based, negatives ok)",
                    },
                },
                "required": ["file", "section", "tag"],
            },
        },
    },
]
