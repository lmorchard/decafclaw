"""improvement-inbox contrib skill — triage the improvement scout's JSONL inbox.

The improvement scout extension (#1482) silently buffers friction in
``workspace/improvement_inbox.jsonl``. This skill is the interactive and
scheduled counterpart: inspect, summarize, dismiss, archive, export, and run an
on-demand retrospective over a whole session.

Every tool takes ``ctx: "Context"`` first. Do not add
``from __future__ import annotations`` here: the skill loader executes this
module outside ``sys.modules``, and postponed dataclass annotations can crash
loading.
"""

import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from decafclaw.extensions.improvement_scout import evaluate_transcript
from decafclaw.improvement_inbox import (
    append_item,
    filter_items,
    get_item,
    inbox_path,
    new_item,
    read_items,
    remove_items,
    update_status,
)
from decafclaw.media import ToolResult
from decafclaw.toil import audit_toil, export_candidates, format_report

if TYPE_CHECKING:
    from decafclaw.context import Context

log = logging.getLogger(__name__)

_EXPORT_PAGE = ("pages", "decafclaw", "improvement-inbox.md")
_STOPWORDS = frozenset(
    {
        "about",
        "after",
        "again",
        "also",
        "because",
        "before",
        "being",
        "could",
        "does",
        "doesn",
        "from",
        "have",
        "into",
        "just",
        "like",
        "more",
        "must",
        "only",
        "over",
        "should",
        "some",
        "than",
        "that",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "this",
        "tool",
        "when",
        "where",
        "which",
        "while",
        "with",
        "would",
    }
)


def _items(ctx: "Context") -> list[dict[str, Any]]:
    return read_items(ctx.config)


def _parse_item_ids(raw: str) -> list[str]:
    return [part.strip() for part in re.split(r"[,\s]+", raw or "") if part.strip()]


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _item_line(item: dict[str, Any]) -> str:
    return (
        f"- `{item.get('id', '?')}` [{item.get('severity', '?')}] "
        f"{item.get('title', 'untitled')} — {item.get('subsystem', 'unknown')}"
    )


def _themes(items: list[dict[str, Any]], limit: int = 5) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for item in items:
        text = f"{item.get('title', '')} {item.get('observed_friction', '')}".lower()
        words = {
            word.strip("._/-")
            for word in re.findall(r"[a-z][a-z0-9_./-]{4,}", text)
            if word.strip("._/-") and word.strip("._/-") not in _STOPWORDS
        }
        for word in words:
            counts[word] = counts.get(word, 0) + 1
    recurring = [(word, count) for word, count in counts.items() if count >= 2]
    recurring.sort(key=lambda pair: (-pair[1], pair[0]))
    return recurring[:limit]


def _export_markdown(items: list[dict[str, Any]], exported_at: str) -> str:
    lines = [f"## Improvement inbox export — {exported_at}", ""]
    for item in items:
        lines.extend(
            [
                f"### {item.get('title', 'untitled')}",
                "",
                f"<!-- imp:{item.get('id', '')} -->",
                f"- **ID:** {item.get('id', '')}",
                f"- **Severity:** {item.get('severity', '')}",
                f"- **Subsystem:** {item.get('subsystem', '')}",
                f"- **Trigger:** {item.get('trigger', '')}",
                f"- **Conversation:** {item.get('conversation_id', '')}",
                f"- **Detected:** {item.get('timestamp', '')}",
                "",
                f"**Observed friction:** {item.get('observed_friction', '')}",
                "",
                f"**Suggested improvement:** {item.get('suggested_improvement', '')}",
                "",
            ]
        )
    return "\n".join(lines)


def improvement_inbox_list(
    ctx: "Context",
    status: str = "pending",
    subsystem: str = "",
    severity: str = "",
) -> ToolResult:
    """List inbox items filtered by status, subsystem, or severity."""
    selected_status = None if status in ("", "any", "all") else status
    items = filter_items(
        _items(ctx),
        status=selected_status,
        subsystem=subsystem or None,
        severity=severity or None,
    )
    if not items:
        return ToolResult(text="No improvement inbox items match.", data={"items": [], "count": 0})
    header = f"{len(items)} improvement item(s):"
    return ToolResult(
        text="\n".join([header, *(_item_line(item) for item in items)]),
        data={"items": items, "count": len(items)},
    )


def improvement_inbox_show(ctx: "Context", item_id: str) -> ToolResult:
    """Show one inbox item in full."""
    item = get_item(_items(ctx), item_id)
    if item is None:
        return ToolResult(text=f"[error: no improvement item with id {item_id}]")
    text = "\n".join(
        [
            f"# {item.get('title', 'untitled')}",
            f"ID: {item.get('id', '')}",
            f"Status: {item.get('status', '')}",
            f"Severity: {item.get('severity', '')}",
            f"Subsystem: {item.get('subsystem', '')}",
            f"Trigger: {item.get('trigger', '')}",
            f"Conversation: {item.get('conversation_id', '')}",
            f"Detected: {item.get('timestamp', '')}",
            "",
            f"Observed friction: {item.get('observed_friction', '')}",
            "",
            f"Suggested improvement: {item.get('suggested_improvement', '')}",
        ]
    )
    return ToolResult(text=text, data=item)


def improvement_inbox_summary(ctx: "Context", status: str = "pending") -> ToolResult:
    """Roll up inbox counts by subsystem and severity, with recurring themes."""
    selected_status = None if status in ("", "any", "all") else status
    items = filter_items(_items(ctx), status=selected_status)
    if not items:
        return ToolResult(text="No improvement inbox items match.", data={"count": 0})
    by_subsystem: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    for item in items:
        subsystem = str(item.get("subsystem") or "unknown")
        severity = str(item.get("severity") or "unknown")
        by_subsystem[subsystem] = by_subsystem.get(subsystem, 0) + 1
        by_severity[severity] = by_severity.get(severity, 0) + 1

    lines = [f"{len(items)} improvement item(s)"]
    lines.append("By subsystem:")
    lines.extend(f"- {name}: {count}" for name, count in sorted(by_subsystem.items(), key=lambda p: (-p[1], p[0])))
    lines.append("By severity:")
    lines.extend(f"- {name}: {count}" for name, count in sorted(by_severity.items(), key=lambda p: (-p[1], p[0])))
    themes = _themes(items)
    if themes:
        lines.append("Recurring themes:")
        lines.extend(f"- {word}: {count} item(s)" for word, count in themes)
    return ToolResult(
        text="\n".join(lines),
        data={"count": len(items), "by_subsystem": by_subsystem, "by_severity": by_severity},
    )


def improvement_inbox_dismiss(ctx: "Context", item_ids: str, reason: str = "") -> ToolResult:
    """Dismiss one or more items, preserving them with an optional reason."""
    ids = _parse_item_ids(item_ids)
    if not ids:
        return ToolResult(text="[error: item_ids is required]")
    updated = update_status(ctx.config, ids, "dismissed", reason=reason or None)
    if not updated:
        return ToolResult(text=f"No matching items to dismiss: {', '.join(ids)}")
    return ToolResult(
        text=f"Dismissed {len(updated)} item(s): {', '.join(updated)}",
        data={"updated": updated},
    )


def improvement_inbox_clear(
    ctx: "Context",
    older_than_days: int = 30,
    status: str = "pending",
    archive: bool = True,
) -> ToolResult:
    """Archive (default) or delete items older than N days."""
    try:
        days = max(0, int(older_than_days))
    except (TypeError, ValueError):
        return ToolResult(text="[error: older_than_days must be an integer]")
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    stale_ids: list[str] = []
    for item in filter_items(_items(ctx), status=status):
        timestamp = _parse_timestamp(item.get("timestamp"))
        if timestamp is not None and timestamp < cutoff:
            stale_ids.append(str(item.get("id")))
    if not stale_ids:
        return ToolResult(text=f"No {status} items older than {days} day(s).")

    if archive:
        changed = update_status(ctx.config, stale_ids, "archived")
        verb = "Archived"
    else:
        changed = remove_items(ctx.config, stale_ids)
        verb = "Deleted"
    return ToolResult(
        text=f"{verb} {len(changed)} item(s) older than {days} day(s): {', '.join(changed)}",
        data={"changed": changed, "archived": archive},
    )


def improvement_inbox_export(ctx: "Context") -> ToolResult:
    """Append pending items to the vault page and mark them exported."""
    pending = filter_items(_items(ctx), status="pending")
    if not pending:
        return ToolResult(text="No pending improvement items to export.")

    page = Path(ctx.config.vault_agent_dir).joinpath(*_EXPORT_PAGE)
    page.parent.mkdir(parents=True, exist_ok=True)
    existing = page.read_text(encoding="utf-8") if page.exists() else "# Improvement inbox\n\n"
    to_export = [item for item in pending if f"<!-- imp:{item.get('id', '')} -->" not in existing]
    if not to_export:
        update_status(ctx.config, [str(item.get("id")) for item in pending], "exported")
        return ToolResult(text=f"All {len(pending)} pending item(s) already on {page}.")

    exported_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    section = _export_markdown(to_export, exported_at)
    separator = "" if existing.endswith("\n\n") else ("\n" if existing.endswith("\n") else "\n\n")
    page.write_text(existing + separator + section, encoding="utf-8")
    changed = update_status(ctx.config, [str(item.get("id")) for item in to_export], "exported")
    return ToolResult(
        text=f"Exported {len(changed)} item(s) to {page}",
        data={"path": str(page), "exported": changed},
    )


async def improvement_retro(ctx: "Context", conversation_id: str = "") -> ToolResult:
    """Run an on-demand retrospective over the full session history.

    The evaluator model is the configured effective default; the tool exposes
    no model choice, so the agent cannot change its own model.
    """
    conv_id = conversation_id or str(getattr(ctx, "conv_id", "") or "")
    history: list[dict[str, Any]] = []
    manager = getattr(ctx, "manager", None)
    if manager is not None and conv_id:
        try:
            history = manager.load_history(conv_id) or []
        except Exception as e:  # noqa: BLE001 - fall back to the turn's own history
            log.warning("Improvement retro could not load history for %s: %s", conv_id, e)
    if not history:
        history = list(getattr(ctx, "history", None) or [])
    if not history:
        return ToolResult(text="[error: no conversation history available for a retrospective]")

    outcome = await evaluate_transcript(
        ctx.config,
        history,
        trigger="retro",
    )
    if not isinstance(outcome, dict):
        return ToolResult(text="No additional harness friction detected in this session.")
    item = new_item(
        conversation_id=conv_id,
        trigger="retro",
        title=outcome["title"],
        observed_friction=outcome["observed_friction"],
        suggested_improvement=outcome["suggested_improvement"],
        subsystem=outcome["subsystem"],
        severity=outcome["severity"],
    )
    append_item(ctx.config, item)
    return ToolResult(
        text=f"Buffered {item['id']}: {item['title']}",
        data=item,
    )


def toil_audit(ctx: "Context", days: int = 7, min_strikes: int = 3, export: bool = False) -> ToolResult:
    """Mine conversation archives for repeated procedural toil (Three Strikes)."""
    try:
        window = max(0, int(days))
        strikes = max(1, int(min_strikes))
    except (TypeError, ValueError):
        return ToolResult(text="[error: days and min_strikes must be integers]")

    report = audit_toil(ctx.config, days=None if window == 0 else window, min_strikes=strikes)
    exported: list[dict[str, Any]] = []
    skipped = 0
    if export:
        exported, skipped_candidates = export_candidates(ctx.config, report.candidates)
        skipped = len(skipped_candidates)

    data = report.to_dict()
    if export:
        data["exported"] = [str(item.get("id", "")) for item in exported]
        data["skipped_existing"] = skipped
    return ToolResult(text=format_report(report, exported=exported, skipped=skipped), data=data)


TOOLS = {
    "improvement_inbox_list": improvement_inbox_list,
    "improvement_inbox_show": improvement_inbox_show,
    "improvement_inbox_summary": improvement_inbox_summary,
    "improvement_inbox_dismiss": improvement_inbox_dismiss,
    "improvement_inbox_clear": improvement_inbox_clear,
    "improvement_inbox_export": improvement_inbox_export,
    "improvement_retro": improvement_retro,
    "toil_audit": toil_audit,
}

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "improvement_inbox_list",
            "description": (
                "List buffered improvement suggestions from the improvement scout. "
                "Filter by status (pending default; all/any for everything), subsystem, or severity."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "description": "pending (default), exported, dismissed, archived, or all/any.",
                    },
                    "subsystem": {"type": "string", "description": "Exact subsystem filter, e.g. shell."},
                    "severity": {"type": "string", "description": "low, medium, or high."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "improvement_inbox_show",
            "description": "Show one improvement inbox item in full, including observed friction and the suggested improvement.",
            "parameters": {
                "type": "object",
                "properties": {
                    "item_id": {"type": "string", "description": "Inbox item id, e.g. imp_20261117_142500_a1b2."},
                },
                "required": ["item_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "improvement_inbox_summary",
            "description": "Roll up inbox items by subsystem and severity, with recurring friction themes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "description": "pending (default) or all/any."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "improvement_inbox_dismiss",
            "description": "Dismiss one or more inbox items, keeping them for audit with an optional reason.",
            "parameters": {
                "type": "object",
                "properties": {
                    "item_ids": {
                        "type": "string",
                        "description": "Comma- or space-separated inbox item ids.",
                    },
                    "reason": {"type": "string", "description": "Why these items are not actionable."},
                },
                "required": ["item_ids"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "improvement_inbox_clear",
            "description": "Archive (default) or delete inbox items older than N days.",
            "parameters": {
                "type": "object",
                "properties": {
                    "older_than_days": {"type": "integer", "description": "Age threshold in days (default 30)."},
                    "status": {"type": "string", "description": "Only items in this status (default pending)."},
                    "archive": {
                        "type": "boolean",
                        "description": "True archives (default); false permanently deletes.",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "improvement_inbox_export",
            "description": (
                "Append pending inbox items to the vault page "
                "agent/pages/decafclaw/improvement-inbox.md and mark them exported."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "improvement_retro",
            "description": (
                "Run an on-demand retrospective over a full conversation history to find subtler "
                "multi-turn friction that the per-turn scout missed. Buffers findings in the inbox."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "conversation_id": {
                        "type": "string",
                        "description": "Conversation id; defaults to the current conversation.",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "toil_audit",
            "description": (
                "Mine conversation archives for repeated procedural toil (Three Strikes): "
                "defensive shell pipelines, read-after-write verification, and discovery or "
                "setup ceremony. Reaches across conversations; export appends strike-3 "
                "candidates to the improvement inbox."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {
                        "type": "integer",
                        "description": "Lookback window in days (default 7); 0 scans every archive.",
                    },
                    "min_strikes": {
                        "type": "integer",
                        "description": "Distinct-conversation threshold (default 3).",
                    },
                    "export": {
                        "type": "boolean",
                        "description": "Append new candidates to the improvement inbox (default false).",
                    },
                },
            },
        },
    },
]
