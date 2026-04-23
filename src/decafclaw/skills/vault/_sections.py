"""Section-aware markdown parser for vault pages.

Internal helpers for vault_show_sections, vault_move_lines, vault_section.
Ported from the retired markdown_vault skill — see #264.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

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
        self._sections: list[Section] = []
        self._dirty: bool = False
        self._parse()
        self._dirty = False

    @classmethod
    def from_text(cls, text: str) -> "Document":
        return cls(text)

    def to_text(self) -> str:
        return "".join(self.lines)

    @property
    def sections(self) -> list[Section]:
        self._ensure_parsed()
        return self._sections

    @sections.setter
    def sections(self, value: list[Section]) -> None:
        self._sections = value

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

        self._sections = _build_tree(flat)
        self._dirty = False

    def _ensure_parsed(self) -> None:
        if self._dirty:
            self._parse()

    # --- Section lookup ---

    def find_section(self, path: str) -> Section | None:
        self._ensure_parsed()
        parts = [p.strip().lower() for p in path.split("/")]
        return _walk_path(self._sections, parts)

    def list_sections(self, depth: int = 0) -> list[tuple[int, Section]]:
        self._ensure_parsed()
        result: list[tuple[int, Section]] = []
        _flatten_sections(self._sections, 0, result)
        return result

    # --- Checklist operations ---

    def get_items(self, section: Section) -> list[ChecklistItem]:
        self._ensure_parsed()
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
                self._dirty = True

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
        self._dirty = True
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
        self._dirty = True
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
        self._dirty = True
        self._collapse_blank_lines()

    def _delete_lines(self, at: int, count: int) -> None:
        del self.lines[at : at + count]
        self._dirty = True
        self._collapse_blank_lines()

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
    from_doc._ensure_parsed()
    for raw in raw_lines:
        to_sec = to_doc.find_section(to_section_path)
        if not to_sec:
            break
        to_doc.append(to_sec, raw)
    return len(raw_lines)


# ---------------------------------------------------------------------------
# List item insertion helpers
# ---------------------------------------------------------------------------


def _find_first_list_item(lines: list[str], start: int, end: int) -> int | None:
    """Find the index of the first checklist/list item in a line range."""
    for i in range(start, end):
        stripped = lines[i].lstrip()
        if stripped.startswith("- [") or stripped.startswith("- "):
            return i
    return None


def _insert_into_doc(
    doc: Document, lines_to_insert: list[str],
    to_section: str | None, position: str,
) -> str | None:
    """Insert lines into a Document at the specified location.

    Returns an error string on failure, or None on success.
    """
    new_lines = _ensure_newlines("".join(lines_to_insert))

    if to_section:
        sec = doc.find_section(to_section)
        if not sec:
            return f"section not found: {to_section}"
        if position == "prepend":
            # Insert before first list item in section, or at content start
            target = _find_first_list_item(doc.lines, sec.content_start, sec.content_end)
            if target is not None:
                doc._insert_lines(target, new_lines)
            else:
                doc.prepend(sec, "".join(ln.rstrip("\n") for ln in lines_to_insert))
        else:
            for line in lines_to_insert:
                sec = doc.find_section(to_section)
                if not sec:
                    break
                doc.append(sec, line.rstrip("\n"))
    else:
        # No section specified — operate on the whole file
        if position == "prepend":
            # Insert before first list item in file
            target = _find_first_list_item(doc.lines, 0, len(doc.lines))
            if target is not None:
                doc._insert_lines(target, new_lines)
            else:
                # No list items — append to end of file
                doc._insert_lines(len(doc.lines), new_lines)
        else:
            # Append to end of file, before trailing blank lines
            insert_at = len(doc.lines)
            while insert_at > 0 and not doc.lines[insert_at - 1].strip():
                insert_at -= 1
            doc._insert_lines(insert_at, new_lines)
    return None
