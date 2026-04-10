"""Plan.md parser and manipulator.

Parses the structured step checklist format used in project plans,
and provides functions to query and modify the step tree.
"""

import re
from dataclasses import dataclass, field


@dataclass
class Step:
    number: str  # e.g. "1", "1.2", "2.3.1"
    description: str
    status: str = "pending"  # pending, in_progress, done, skipped
    note: str = ""
    children: list["Step"] = field(default_factory=list)


# Checkbox markers → status
_MARKER_TO_STATUS = {
    " ": "pending",
    ">": "in_progress",
    "x": "done",
    "-": "skipped",
}

_STATUS_TO_MARKER = {v: k for k, v in _MARKER_TO_STATUS.items()}

# Matches: "- [x] 1.2. Description text" (numbered)
_STEP_RE = re.compile(
    r"^(\s*)- \[([x >-])\] (\d+(?:\.\d+)*)\.\s+(.*)"
)

# Matches: "- [x] Description text" (unnumbered — will be auto-numbered)
_UNNUMBERED_STEP_RE = re.compile(
    r"^(\s*)- \[([x >-])\]\s+(.*)"
)

# Matches: "  > Note text" (blockquote note after a step)
_NOTE_RE = re.compile(r"^(\s*)>\s+(.*)")


def parse_plan(content: str) -> tuple[str, list[Step], str]:
    """Parse plan markdown into overview text and a step tree.

    Returns (overview, steps) where overview is everything before
    the step list and steps is a flat-then-nested structure.
    Any content after the step list is preserved in the overview
    as a trailing section.
    """
    lines = content.split("\n")
    overview_lines: list[str] = []
    tail_lines: list[str] = []
    step_entries: list[tuple[int, Step]] = []  # (indent_level, step)
    in_steps = False
    past_steps = False

    i = 0
    has_unnumbered = False
    while i < len(lines):
        line = lines[i]
        m = _STEP_RE.match(line)
        is_unnumbered = False
        if not m and not past_steps:
            # Try unnumbered checkbox as fallback
            m_un = _UNNUMBERED_STEP_RE.match(line)
            if m_un:
                m = m_un
                is_unnumbered = True
                has_unnumbered = True
        if m and not past_steps:
            in_steps = True
            indent = len(m.group(1))
            marker = m.group(2)
            if is_unnumbered:
                # Unnumbered: placeholder number, will be fixed after parsing
                number = "_unnumbered"
                desc = m.group(3)
            else:
                number = m.group(3)
                desc = m.group(4)
            status = _MARKER_TO_STATUS.get(marker, "pending")

            # Check for note on next line(s)
            note_parts: list[str] = []
            while i + 1 < len(lines):
                nm = _NOTE_RE.match(lines[i + 1])
                if nm:
                    note_parts.append(nm.group(2))
                    i += 1
                else:
                    break

            step = Step(
                number=number,
                description=desc,
                status=status,
                note="\n".join(note_parts),
            )
            step_entries.append((indent, step))
        elif not in_steps:
            overview_lines.append(line)
        else:
            # We were in steps but this line isn't a step or note —
            # everything from here is trailing content
            past_steps = True
            tail_lines.append(line)
        i += 1

    # Build tree from indent levels
    steps = _build_tree(step_entries)
    # Auto-number when any unnumbered steps are present.
    # Renumbers all steps for consistency.
    if has_unnumbered:
        _renumber_list(steps, "")
    overview = "\n".join(overview_lines).rstrip()
    tail = "\n".join(tail_lines).rstrip() if tail_lines else ""
    return overview, steps, tail


def _build_tree(entries: list[tuple[int, Step]]) -> list[Step]:
    """Nest steps based on indent levels."""
    if not entries:
        return []

    root: list[Step] = []
    # Stack of (indent_level, step) for building parent-child relationships
    stack: list[tuple[int, Step]] = []

    for indent, step in entries:
        # Pop stack until we find a parent with less indent
        while stack and stack[-1][0] >= indent:
            stack.pop()

        if stack:
            stack[-1][1].children.append(step)
        else:
            root.append(step)

        stack.append((indent, step))

    return root


def render_plan(overview: str, steps: list[Step], tail: str = "") -> str:
    """Serialize overview and step tree back to markdown."""
    lines = [overview, ""] if overview else []
    _render_steps(steps, lines, indent=0)
    if tail:
        lines.append("")
        lines.append(tail)
    return "\n".join(lines) + "\n"


def _render_steps(steps: list[Step], lines: list[str], indent: int) -> None:
    """Recursively render steps as markdown checkboxes."""
    prefix = "  " * indent
    for step in steps:
        marker = _STATUS_TO_MARKER.get(step.status, " ")
        lines.append(f"{prefix}- [{marker}] {step.number}. {step.description}")
        if step.note:
            for note_line in step.note.split("\n"):
                lines.append(f"{prefix}  > {note_line}")
        if step.children:
            _render_steps(step.children, lines, indent + 1)


def find_step(steps: list[Step], number: str) -> Step | None:
    """Find a step by number in the tree."""
    for step in steps:
        if step.number == number:
            return step
        found = find_step(step.children, number)
        if found:
            return found
    return None


def next_actionable(steps: list[Step]) -> Step | None:
    """Find the next actionable step.

    Returns the first pending step. If a parent is in_progress,
    returns the first pending child within it. Skips done/skipped steps.
    """
    for step in steps:
        if step.status in ("done", "skipped"):
            continue
        if step.children:
            # If parent is in_progress, look for pending children
            child = next_actionable(step.children)
            if child:
                return child
            # If all children are done/skipped but parent isn't marked done,
            # the parent itself is actionable (needs to be marked done)
            if step.status == "in_progress" and all(
                c.status in ("done", "skipped") for c in step.children
            ):
                return step
        if step.status == "pending":
            return step
        if step.status == "in_progress" and not step.children:
            return step
    return None


def update_step_status(
    steps: list[Step], number: str, status: str, note: str = ""
) -> bool:
    """Update a step's status and optional note. Returns True if found."""
    step = find_step(steps, number)
    if not step:
        return False
    step.status = status
    if note:
        step.note = note
    return True


def insert_steps(
    steps: list[Step], after_number: str, new_descriptions: list[str]
) -> bool:
    """Insert new steps after the given step number.

    If after_number is a top-level step (e.g. "2"), inserts new top-level
    steps after it. If it's a sub-step (e.g. "2.1"), inserts sibling
    sub-steps after it within the same parent.

    Auto-numbers the new steps sequentially after the target.
    Returns True if the insertion point was found.
    """
    parts = after_number.split(".")
    if len(parts) == 1:
        # Top-level insertion
        return _insert_in_list(steps, after_number, new_descriptions, prefix="")
    else:
        # Find parent, insert among its children
        parent_number = ".".join(parts[:-1])
        parent = find_step(steps, parent_number)
        if not parent:
            return False
        return _insert_in_list(
            parent.children, after_number, new_descriptions, prefix=parent_number
        )


def _insert_in_list(
    step_list: list[Step],
    after_number: str,
    descriptions: list[str],
    prefix: str,
) -> bool:
    """Insert steps into a list after the step with the given number."""
    idx = None
    for i, step in enumerate(step_list):
        if step.number == after_number:
            idx = i
            break
    if idx is None:
        return False

    # Determine numbering: after_number's last component + 1
    parts = after_number.split(".")
    base = int(parts[-1])
    new_steps = []
    for j, desc in enumerate(descriptions):
        num = base + 1 + j
        number = f"{prefix}.{num}" if prefix else str(num)
        new_steps.append(Step(number=number, description=desc))

    # Insert after idx
    for j, step in enumerate(new_steps):
        step_list.insert(idx + 1 + j, step)

    # Renumber all subsequent steps in this list
    _renumber_list(step_list, prefix)
    return True


def _renumber_list(step_list: list[Step], prefix: str) -> None:
    """Renumber steps in a list sequentially, updating children too."""
    for i, step in enumerate(step_list):
        new_number = f"{prefix}.{i + 1}" if prefix else str(i + 1)
        step.number = new_number
        if step.children:
            _renumber_children(step.children, new_number)


def _renumber_children(children: list[Step], parent_number: str) -> None:
    """Recursively renumber child steps."""
    for i, child in enumerate(children):
        child.number = f"{parent_number}.{i + 1}"
        if child.children:
            _renumber_children(child.children, child.number)


def plan_progress(steps: list[Step]) -> tuple[int, int]:
    """Count (completed, total) leaf steps.

    Only counts leaf steps (no children). Done and skipped count as completed.
    """
    completed = 0
    total = 0
    for step in steps:
        if step.children:
            c, t = plan_progress(step.children)
            completed += c
            total += t
        else:
            total += 1
            if step.status in ("done", "skipped"):
                completed += 1
    return completed, total
