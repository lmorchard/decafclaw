"""Per-conversation checklist — markdown checkbox files on disk.

Provides a mechanical execution loop: create a checklist of steps,
work through them one at a time, mark each complete. Used by the
checklist tools for always-available step-by-step execution.
"""

import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

_UNCHECKED = "- [ ] "
_CHECKED = "- [x] "
_DONE_NOTE_RE = re.compile(r"^(.*?)\s*\[done:\s*(.*?)\]\s*$")


def _checklist_path(config, conv_id: str) -> Path:
    """Path to the checklist file for a conversation."""
    return config.workspace_path / "todos" / f"{conv_id}.md"


def _read_items(config, conv_id: str) -> list[dict]:
    """Read checklist items from disk.

    Returns list of {text, done, note} dicts.
    """
    path = _checklist_path(config, conv_id)
    if not path.exists():
        return []
    items = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line.startswith(_CHECKED):
            raw = line[len(_CHECKED):]
            m = _DONE_NOTE_RE.match(raw)
            if m:
                items.append({"text": m.group(1), "done": True,
                              "note": m.group(2)})
            else:
                items.append({"text": raw, "done": True, "note": ""})
        elif line.startswith(_UNCHECKED):
            items.append({"text": line[len(_UNCHECKED):], "done": False,
                          "note": ""})
    return items


def _sanitize_line(text: str) -> str:
    """Normalize text to a single line safe for the markdown format."""
    return text.replace("\n", " ").replace("\r", "").strip()


def _write_items(config, conv_id: str, items: list[dict]):
    """Write checklist items to disk as markdown checkboxes."""
    path = _checklist_path(config, conv_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for item in items:
        text = _sanitize_line(item["text"])
        if item["done"]:
            note = _sanitize_line(item.get("note", ""))
            suffix = f" [done: {note}]" if note else ""
            lines.append(f"{_CHECKED}{text}{suffix}")
        else:
            lines.append(f"{_UNCHECKED}{text}")
    path.write_text("\n".join(lines) + "\n" if lines else "")


def checklist_create(config, conv_id: str, steps: list[str]) -> list[dict]:
    """Create a checklist from a list of step descriptions.

    Overwrites any existing checklist for this conversation.
    Returns the list of items.
    """
    items = [{"text": step, "done": False, "note": ""} for step in steps]
    _write_items(config, conv_id, items)
    log.info("[checklist:create] %d steps for %s", len(steps), conv_id[:8])
    return items


def checklist_get_current(config, conv_id: str) -> dict | None:
    """Return the first unchecked item with its 1-based index, or None."""
    items = _read_items(config, conv_id)
    for i, item in enumerate(items, 1):
        if not item["done"]:
            return {"index": i, "total": len(items), **item}
    return None


def checklist_complete_current(config, conv_id: str,
                               note: str = "") -> dict | None:
    """Mark the current (first unchecked) step as done.

    Returns the next unchecked item, or None if all steps are complete.
    """
    items = _read_items(config, conv_id)
    for i, item in enumerate(items):
        if not item["done"]:
            item["done"] = True
            item["note"] = note
            _write_items(config, conv_id, items)
            log.info("[checklist:done] step %d/%d: %s",
                     i + 1, len(items), item["text"][:60])
            # Return next unchecked item
            for j, next_item in enumerate(items[i + 1:], i + 2):
                if not next_item["done"]:
                    return {"index": j, "total": len(items), **next_item}
            return None  # all done
    return None  # nothing to complete


def checklist_abort(config, conv_id: str) -> None:
    """Delete the checklist file."""
    path = _checklist_path(config, conv_id)
    if path.exists():
        path.unlink()
    log.info("[checklist:abort] %s", conv_id[:8])


def checklist_status(config, conv_id: str) -> list[dict]:
    """Read all checklist items with their status."""
    return _read_items(config, conv_id)
