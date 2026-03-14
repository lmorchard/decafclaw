"""Per-conversation to-do list — markdown checkbox files on disk."""

import logging
from pathlib import Path

log = logging.getLogger(__name__)


def _todo_path(config, conv_id: str) -> Path:
    """Path to the to-do file for a conversation."""
    return config.workspace_path / "todos" / f"{conv_id}.md"


def _read_todos(config, conv_id: str) -> list[dict]:
    """Read to-do items from disk. Returns list of {text, done} dicts."""
    path = _todo_path(config, conv_id)
    if not path.exists():
        return []
    items = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line.startswith("- [x] "):
            items.append({"text": line[6:], "done": True})
        elif line.startswith("- [ ] "):
            items.append({"text": line[6:], "done": False})
    return items


def _write_todos(config, conv_id: str, items: list[dict]):
    """Write to-do items to disk as markdown checkboxes."""
    path = _todo_path(config, conv_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for item in items:
        marker = "x" if item["done"] else " "
        lines.append(f"- [{marker}] {item['text']}")
    path.write_text("\n".join(lines) + "\n" if lines else "")


def todo_add(config, conv_id: str, item: str) -> str:
    """Add a to-do item."""
    items = _read_todos(config, conv_id)
    items.append({"text": item, "done": False})
    _write_todos(config, conv_id, items)
    log.info(f"[todo:add] {item}")
    return f"Added: {item} ({len(items)} total)"


def todo_complete(config, conv_id: str, index: int) -> str:
    """Mark a to-do item as complete (1-indexed)."""
    items = _read_todos(config, conv_id)
    if index < 1 or index > len(items):
        return f"[error: invalid index {index}, have {len(items)} items]"
    items[index - 1]["done"] = True
    _write_todos(config, conv_id, items)
    log.info(f"[todo:complete] #{index}: {items[index - 1]['text']}")
    return f"Completed: {items[index - 1]['text']}"


def todo_list(config, conv_id: str) -> str:
    """List all to-do items."""
    items = _read_todos(config, conv_id)
    if not items:
        return "No to-do items."
    lines = []
    for i, item in enumerate(items, 1):
        marker = "x" if item["done"] else " "
        lines.append(f"{i}. [{marker}] {item['text']}")
    done = sum(1 for i in items if i["done"])
    lines.append(f"\n{done}/{len(items)} complete")
    return "\n".join(lines)


def todo_clear(config, conv_id: str) -> str:
    """Clear all to-do items."""
    path = _todo_path(config, conv_id)
    if path.exists():
        path.unlink()
    log.info("[todo:clear]")
    return "To-do list cleared."
