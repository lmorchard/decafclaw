"""Newsletter bundled skill — composes and delivers periodic activity digests."""

import html
import json
import logging
import re
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from decafclaw.context import TaskMode
from decafclaw.conversation_paths import iter_conversation_archives
from decafclaw.mail import send_mail
from decafclaw.media import ToolResult
from decafclaw.scheduled_activity import (
    collect_scheduled_activity,
    extract_activity,
    is_status_token,
    parse_scheduled_conv_id,
)
from decafclaw.skill_state import skill_state
from decafclaw.skills.vault.tools import collect_recent_pages, resolve_page

if TYPE_CHECKING:
    from decafclaw.context import Context

log = logging.getLogger(__name__)

# The email HTML/CSS shell lives beside this module so it can be edited as
# plain HTML instead of a brace-escaped f-string. It is read once at import;
# `_CONTENT_PLACEHOLDER` marks where the rendered markdown body goes.
_TEMPLATE_PATH = Path(__file__).with_name("template.html")
_NEWSLETTER_TEMPLATE = _TEMPLATE_PATH.read_text(encoding="utf-8")
_CONTENT_PLACEHOLDER = "<!--NEWSLETTER_CONTENT-->"


@dataclass
class SkillConfig:
    window_hours: int = field(default=24, metadata={"env_alias": "NEWSLETTER_WINDOW_HOURS"})
    email_enabled: bool = field(default=False, metadata={"env_alias": "NEWSLETTER_EMAIL_ENABLED"})
    email_recipients: list[str] = field(
        default_factory=list,
        metadata={"env_alias": "NEWSLETTER_EMAIL_RECIPIENTS"},
    )
    email_subject_prefix: str = field(
        default="[decafclaw newsletter]",
        metadata={"env_alias": "NEWSLETTER_EMAIL_SUBJECT_PREFIX"},
    )
    vault_page_enabled: bool = field(default=True, metadata={"env_alias": "NEWSLETTER_VAULT_PAGE_ENABLED"})
    vault_folder: str = field(
        default="agent/journal/newsletters",
        metadata={"env_alias": "NEWSLETTER_VAULT_FOLDER"},
    )
    web_base_url: str = field(
        default="http://decafclaw:18880",
        metadata={"env_alias": "NEWSLETTER_WEB_BASE_URL"},
    )


# Shared state holder (#1373): the dataclass defaults are the fallback until
# init() stores the resolved config, so read sites need no None guards.
_skill_config = skill_state(SkillConfig())


def init(config, skill_config: SkillConfig) -> None:
    """Initialize the newsletter skill. Called by the skill loader on activation."""
    _skill_config.init(skill_config)


_SELF_SKILL_NAME = "newsletter"
_WINDOW_RE = re.compile(r"^\s*(\d+)\s*([hdw])\s*$", re.IGNORECASE)
_WINDOW_UNIT_HOURS = {"h": 1, "d": 24, "w": 24 * 7}

# Shared scheduled-activity helpers (aliased for test backwards-compatibility)
_parse_conv_id = parse_scheduled_conv_id
_is_status_token = is_status_token
_extract_activity = extract_activity


def _parse_window(spec: str) -> int:
    """Parse a compact window spec like ``"24h"``, ``"7d"``, or ``"2w"``
    into an integer number of hours. Raises ValueError on malformed input."""
    match = _WINDOW_RE.match(spec)
    if not match:
        raise ValueError(f"invalid window spec {spec!r} — use forms like '24h', '7d', '2w'")
    n = int(match.group(1))
    if n <= 0:
        raise ValueError(f"invalid window spec {spec!r} — must be positive")
    unit = match.group(2).lower()
    return n * _WINDOW_UNIT_HOURS[unit]


def _resolve_hours(window: str, hours: int | None) -> tuple[int, str]:
    """Resolve the active window. ``window`` takes precedence when non-empty;
    otherwise ``hours`` (or the SkillConfig default) is used. Returns
    ``(hours, display)`` where ``display`` is used in tool summary text."""
    if window:
        h = _parse_window(window)
        return h, window.strip()
    if hours is None:
        resolved = _skill_config.window_hours  # SkillConfig default via the holder
        return resolved, f"{resolved}h"
    return hours, f"{hours}h"


_WIKILINK_RE = re.compile(r"(`{0,2})\[\[([^\]|`]+)(?:\|([^\]`]+))?\]\]\1")
_BACKTICK_MDLINK_RE = re.compile(r"`\[([^\]]+)\]\((https?://[^\)]+)\)`")


def _resolve_page_link(config, page_name: str, display_text: str | None, base_url: str) -> tuple[str, str]:
    """Resolve a wiki-link target into (label, url_or_text).

    Returns (label, url) if a link can be constructed to the internal vault page,
    or (label, "") if it should be rendered as unlinked text / styled pill.
    """
    clean_target = page_name.strip()
    label = (display_text or clean_target).strip()

    resolved_path = resolve_page(config, clean_target)
    if resolved_path is None or not resolved_path.exists():
        # Target not in vault — return label with empty url
        return label, ""

    # DecafClaw web UI deep link to the internal vault page
    if base_url:
        vault_root = config.vault_root.resolve()
        try:
            rel_path = str(resolved_path.resolve().relative_to(vault_root))
            if rel_path.endswith(".md"):
                rel_path = rel_path[:-3]
        except ValueError:
            rel_path = clean_target

        encoded_path = urllib.parse.quote(rel_path, safe="")
        base = base_url.rstrip("/")
        return label, f"{base}/?vault={encoded_path}"

    return label, ""


def render_newsletter_email(markdown: str, config, base_url: str = "http://decafclaw:18880") -> tuple[str, str]:
    """Convert raw newsletter markdown into (plain_text, html_body) with resolved links."""
    # Unwrap any markdown links that were inadvertently wrapped in backticks
    clean_markdown = _BACKTICK_MDLINK_RE.sub(r"[\1](\2)", markdown)

    # Cache resolved links across substitutions to avoid duplicate vault I/O
    link_cache: dict[tuple[str, str | None], tuple[str, str]] = {}

    def _get_resolved(target: str, display: str | None) -> tuple[str, str]:
        key = (target.strip(), display.strip() if display else None)
        if key not in link_cache:
            link_cache[key] = _resolve_page_link(config, key[0], key[1], base_url)
        return link_cache[key]

    # 1. Transform [[wiki-links]] for plain text
    def _plain_sub(match: re.Match) -> str:
        target = match.group(2)
        display = match.group(3)
        label, url = _get_resolved(target, display)
        if url:
            if label != url:
                return f"{label} ({url})"
            return url
        return label

    plain_text = _WIKILINK_RE.sub(_plain_sub, clean_markdown)

    # 2. Transform [[wiki-links]] for HTML markdown conversion
    pills: list[str] = []

    def _html_sub(match: re.Match) -> str:
        target = match.group(2)
        display = match.group(3)
        label, url = _get_resolved(target, display)
        if url:
            escaped_label = label.replace("[", r"\[").replace("]", r"\]")
            return f"[{escaped_label}]({url})"
        # Fallback to tag/pill representation with properly escaped text
        escaped_label = html.escape(label)
        pill_idx = len(pills)
        pills.append(
            '<span style="display:inline-block;padding:1px 6px;margin:0 1px;border-radius:4px;'
            'background-color:#e2e8f0;color:#334155;font-size:0.9em;font-weight:500;">'
            f"#{escaped_label}</span>"
        )
        return f"%%NEWSLETTER_PILL_{pill_idx}%%"

    html_markdown = _WIKILINK_RE.sub(_html_sub, clean_markdown)

    try:
        from markdown_it import MarkdownIt

        # Disable raw HTML so agent/vault-generated HTML cannot inject arbitrary tags/scripts
        md = MarkdownIt("commonmark", {"html": False})
        content_html = md.render(html_markdown)
        for idx, pill_html in enumerate(pills):
            content_html = content_html.replace(f"%%NEWSLETTER_PILL_{idx}%%", pill_html)
    except Exception as exc:
        log.warning("Failed to render markdown to HTML with markdown-it: %s", exc)
        content_html = f"<pre>{html.escape(plain_text)}</pre>"

    email_html = _NEWSLETTER_TEMPLATE.replace(_CONTENT_PLACEHOLDER, content_html)
    return plain_text, email_html


def _collect_scheduled_activity(ctx: "Context", hours: int = 24) -> list[dict]:
    """Gather scheduled-task activity records. Returns the raw list
    used by newsletter_list_scheduled_activity."""
    return collect_scheduled_activity(ctx.config, hours=hours, exclude_skill_name=_SELF_SKILL_NAME)


async def newsletter_list_scheduled_activity(ctx, hours: int | None = None, window: str = "") -> ToolResult:
    """List scheduled-task activity within the given time window.

    ``window`` is a compact string like ``"7d"``, ``"48h"``, ``"2w"`` and
    takes precedence when provided. ``hours`` is a numeric fallback;
    when neither is provided, the SkillConfig ``window_hours`` default
    (24h) is used.

    Returns one entry per scheduled-task conversation (excluding newsletter's own),
    with skill_name, conv_id, started_at, final_message, vault_pages_touched, workspace_files_touched, has_error, and errors.
    """
    try:
        resolved_hours, display = _resolve_hours(window, hours)
    except ValueError as exc:
        return ToolResult(text=f"[error: {exc}]")
    activity = _collect_scheduled_activity(ctx, resolved_hours)
    degraded_count = sum(1 for item in activity if item.get("degraded"))
    summary = f"Found {len(activity)} scheduled-task conversation(s) in the last {display}."
    if degraded_count:
        summary += f" {degraded_count} degraded run(s) need review."
    return ToolResult(
        text=summary,
        data={
            "activity": activity,
            "hours": resolved_hours,
            "window": display,
            "degraded_count": degraded_count,
        },
    )


async def newsletter_scheduled_faults(ctx, hours: int | None = None, window: str = "") -> ToolResult:
    """Return a Markdown checklist of scheduled runs with silent degradation.

    Degradation means a tool refusal followed by a later non-error tool result:
    the run hit a boundary, worked around it, and continued. The checklist is
    evidence only — refusal, tool, later tools, and a final-message excerpt.
    """
    try:
        resolved_hours, display = _resolve_hours(window, hours)
    except ValueError as exc:
        return ToolResult(text=f"[error: {exc}]")
    activity = _collect_scheduled_activity(ctx, resolved_hours)
    faults = [item for item in activity if item.get("degraded")]
    if not faults:
        return ToolResult(
            text=f"No degraded scheduled runs in the last {display}.",
            data={"faults": [], "degraded_count": 0, "hours": resolved_hours, "window": display},
        )
    lines = [f"# Scheduled runs needing review ({display})", ""]
    for item in faults:
        lines.append(f"- [ ] **{item['skill_name']}** — `{item['conv_id']}`")
        for degradation in item.get("degradations") or []:
            later = ", ".join(degradation.get("later_tools") or []) or "none"
            lines.append(f"  - Refusal: {degradation.get('refusal')}")
            lines.append(f"  - Tool: `{degradation.get('tool')}`; later tools: {later}")
        excerpt = (item.get("final_message") or "").strip().replace("\n", " ")[:200]
        if excerpt:
            lines.append(f"  - Final: {excerpt}")
    return ToolResult(
        text="\n".join(lines),
        data={
            "faults": faults,
            "degraded_count": len(faults),
            "hours": resolved_hours,
            "window": display,
        },
    )


def _collect_vault_changes(ctx: "Context", hours: int = 24) -> list[dict]:
    """Gather vault markdown files modified in the last `hours` (mtime-based).

    Returns a list of {path (str, relative to vault root), mtime (ISO-8601), size (int)}.
    Excludes files under the newsletter output folder (self-references).
    """
    cutoff_ts = (datetime.now(timezone.utc) - timedelta(hours=hours)).timestamp()
    newsletter_folder = _skill_config.vault_folder

    out: list[dict] = []
    # Shared, symlink-safe recency scan (see vault.tools.collect_recent_pages).
    for row in collect_recent_pages(ctx.config, cutoff_ts):
        rel = row["path"]
        if newsletter_folder and rel.startswith(newsletter_folder.rstrip("/") + "/"):
            continue
        out.append(
            {
                "path": rel,
                "mtime": datetime.fromtimestamp(row["mtime"], tz=timezone.utc).isoformat(),
                "size": row["size"],
            }
        )
    out.sort(key=lambda r: r["mtime"])
    return out


async def newsletter_list_vault_changes(ctx, hours: int | None = None, window: str = "") -> ToolResult:
    """List vault markdown files modified in the given window (mtime-based).

    ``window`` is a compact string like ``"7d"``, ``"48h"``, ``"2w"`` and
    takes precedence when provided. ``hours`` is a numeric fallback;
    when neither is provided, the SkillConfig ``window_hours`` default
    (24h) is used.

    Excludes files under the newsletter's own output folder so subsequent
    newsletters don't self-reference.
    """
    try:
        resolved_hours, display = _resolve_hours(window, hours)
    except ValueError as exc:
        return ToolResult(text=f"[error: {exc}]")
    changes = _collect_vault_changes(ctx, resolved_hours)
    summary = f"Found {len(changes)} vault page change(s) in the last {display}."
    return ToolResult(
        text=summary,
        data={"changes": changes, "hours": resolved_hours, "window": display},
    )


def _collect_recent_memories(ctx: "Context", hours: int = 24) -> tuple[bool, list[dict], str | None]:
    """Collect memories created in the last `hours` from Qdrant if Mem0 is enabled.

    Returns (enabled, memories_list, error_message_or_None).
    Fails safe if Mem0 is not configured, disabled, uninstalled, or unreachable.
    """
    mem0_ext = ctx.config.extensions.get("mem0")
    if not mem0_ext or not getattr(mem0_ext, "enabled", True):
        return False, [], "Mem0 extension is not enabled"

    try:
        from qdrant_client import QdrantClient  # pyright: ignore[reportMissingImports]
        from qdrant_client.models import (  # pyright: ignore[reportMissingImports]
            DatetimeRange,
            FieldCondition,
            Filter,
        )
    except ImportError:
        return False, [], "qdrant-client package is not installed"

    opts = dict(getattr(mem0_ext, "options", None) or {})
    qdrant_url = opts.get("qdrant_url")
    qdrant_host = opts.get("qdrant_host")
    qdrant_port = opts.get("qdrant_port", 6333)
    qdrant_path = opts.get("qdrant_path")

    try:
        if qdrant_url:
            qc = QdrantClient(url=qdrant_url, timeout=3)
        elif qdrant_host:
            qc = QdrantClient(host=qdrant_host, port=int(qdrant_port), timeout=3)
        elif qdrant_path:
            qc = QdrantClient(path=qdrant_path, timeout=3)
        else:
            return False, [], "No valid Qdrant URL, host, or path configured"

        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        scroll_filter = Filter(
            must=[
                FieldCondition(
                    key="created_at",
                    range=DatetimeRange(gte=cutoff),
                )
            ]
        )

        points, _ = qc.scroll(
            collection_name="mem0",
            scroll_filter=scroll_filter,
            limit=200,
            with_payload=True,
        )

        memories: list[dict] = []
        for p in points:
            payload = p.payload or {}
            fact = payload.get("data")
            created_at = payload.get("created_at")
            if fact:
                memories.append(
                    {
                        "id": str(p.id),
                        "memory": fact,
                        "created_at": created_at,
                        "user_id": payload.get("user_id", "default"),
                    }
                )

        memories.sort(key=lambda m: m.get("created_at") or "")
        return True, memories, None
    except Exception as exc:
        log.warning("newsletter: failed to query Qdrant memories: %s", exc)
        return False, [], str(exc)


async def newsletter_list_recent_memories(ctx, hours: int | None = None, window: str = "") -> ToolResult:
    """List semantic memory facts absorbed into Mem0 / Qdrant in the given window.

    Fails safe if Mem0 is not configured, disabled, or unreachable.
    """
    try:
        resolved_hours, display = _resolve_hours(window, hours)
    except ValueError as exc:
        return ToolResult(text=f"[error: {exc}]")

    enabled, memories, err = _collect_recent_memories(ctx, resolved_hours)
    if not enabled:
        reason = f" ({err})" if err else ""
        return ToolResult(
            text=f"Mem0 memory extension is not active{reason}; 0 memories available.",
            data={"enabled": False, "memories": [], "total": 0, "hours": resolved_hours, "window": display},
        )

    summary = f"Found {len(memories)} memory fact(s) absorbed in the last {display}."
    return ToolResult(
        text=summary,
        data={
            "enabled": True,
            "memories": memories,
            "total": len(memories),
            "hours": resolved_hours,
            "window": display,
        },
    )


async def newsletter_publish(
    ctx: "Context",
    markdown: str,
    subject_hint: str | None = None,
    has_content: bool = True,
    force_delivery: bool = False,
) -> ToolResult:
    """Publish the composed newsletter.

    If invoked interactively (``ctx.task_mode is not TaskMode.SCHEDULED``) and
    ``force_delivery`` is False, returns the markdown verbatim — no
    archive, no delivery, no state change.

    Under a scheduled run *or* when ``force_delivery`` is True, archives
    locally, delivers to all enabled targets, and advances
    ``last_run.json``. ``force_delivery`` exists for smoke-testing the
    delivery path from ``!newsletter send`` without waiting for cron.
    """
    if ctx.task_mode is not TaskMode.SCHEDULED and not force_delivery:
        return ToolResult(text=markdown)

    # Scheduled branch
    now = datetime.now(timezone.utc)
    workspace = Path(ctx.config.workspace_path)
    newsletter_dir = workspace / "newsletter"
    archive_dir = newsletter_dir / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)

    if has_content:
        archive_body = markdown
    else:
        archive_body = f"# {now.strftime('%Y-%m-%d')}\n\n_Nothing to report._\n"

    # Date-suffixed filename with collision handling
    date_str = now.strftime("%Y-%m-%d")
    archive_path = archive_dir / f"{date_str}.md"
    suffix = 0
    while archive_path.exists():
        suffix += 1
        archive_path = archive_dir / f"{date_str}-{suffix}.md"
    archive_path.write_text(archive_body, encoding="utf-8")

    # Advance state
    last_run_path = newsletter_dir / "last_run.json"
    last_run_path.write_text(
        json.dumps(
            {
                "last_run_utc": now.isoformat(),
                "window_end_utc": now.isoformat(),
            }
        ),
        encoding="utf-8",
    )

    delivered_targets: list[str] = []
    if has_content:
        # Email delivery
        if _skill_config.email_enabled and _skill_config.email_recipients:
            subject_suffix = subject_hint or now.strftime("%Y-%m-%d")
            subject = f"{_skill_config.email_subject_prefix} {subject_suffix}".strip()
            plain_body, html_body = render_newsletter_email(
                markdown,
                ctx.config,
                base_url=_skill_config.web_base_url,
            )
            try:
                await send_mail(
                    ctx.config,
                    to=list(_skill_config.email_recipients),
                    subject=subject,
                    body=plain_body,
                    html_body=html_body,
                )
                delivered_targets.append("email")
            except Exception as exc:  # noqa: BLE001 — isolation boundary
                log.warning("Newsletter email delivery failed: %s", exc)
        # Vault page delivery
        if _skill_config.vault_page_enabled:
            vault_root_path = ctx.config.vault_root
            if vault_root_path.is_dir():
                vault_folder = _skill_config.vault_folder
                # Sandbox check: reject empty, absolute, or path-traversal values.
                folder_ok = True
                if not vault_folder:
                    log.warning("Newsletter vault_folder is empty — skipping vault-page delivery")
                    folder_ok = False
                elif Path(vault_folder).is_absolute():
                    log.warning(
                        "Newsletter vault_folder %r is absolute — skipping vault-page delivery",
                        vault_folder,
                    )
                    folder_ok = False
                elif ".." in Path(vault_folder).parts:
                    log.warning(
                        "Newsletter vault_folder %r contains '..' — skipping vault-page delivery",
                        vault_folder,
                    )
                    folder_ok = False
                else:
                    resolved_dir = (vault_root_path / vault_folder).resolve()
                    if not resolved_dir.is_relative_to(vault_root_path.resolve()):
                        log.warning(
                            "Newsletter vault_folder %r resolves outside vault root — skipping vault-page delivery",
                            vault_folder,
                        )
                        folder_ok = False

                if folder_ok:
                    try:
                        target_dir = vault_root_path / vault_folder
                        target_dir.mkdir(parents=True, exist_ok=True)
                        target_path = target_dir / f"{date_str}.md"
                        suffix = 0
                        while target_path.exists():
                            suffix += 1
                            target_path = target_dir / f"{date_str}-{suffix}.md"
                        target_path.write_text(markdown, encoding="utf-8")
                        delivered_targets.append("vault_page")
                    except Exception as exc:  # noqa: BLE001
                        log.warning("Newsletter vault-page delivery failed: %s", exc)

    if delivered_targets:
        summary_text = f"Newsletter archived to {archive_path.name}. Delivered to: {', '.join(delivered_targets)}."
    elif has_content:
        summary_text = f"Newsletter archived to {archive_path.name}. No delivery targets enabled."
    else:
        summary_text = f"Nothing to report; window advanced. Archive: {archive_path.name}."
    return ToolResult(
        text=summary_text,
        data={
            "archive_path": str(archive_path),
            "has_content": has_content,
            "delivered_targets": delivered_targets,
        },
    )


# -- Registration -------------------------------------------------------------

TOOLS = {
    "newsletter_list_scheduled_activity": newsletter_list_scheduled_activity,
    "newsletter_scheduled_faults": newsletter_scheduled_faults,
    "newsletter_list_vault_changes": newsletter_list_vault_changes,
    "newsletter_list_recent_memories": newsletter_list_recent_memories,
    "newsletter_publish": newsletter_publish,
}

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "writes": False,
        "function": {
            "name": "newsletter_list_scheduled_activity",
            "description": (
                "List scheduled-task conversations within a time window (excludes "
                "newsletter's own runs). Returns skill_name, conv_id, started_at, "
                "final_message, vault_pages_touched, workspace_files_touched, has_error, and errors per conversation."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "window": {
                        "type": "string",
                        "description": (
                            "Compact time-range string like '24h', '7d', '2w'. "
                            "Takes precedence over 'hours' when non-empty."
                        ),
                    },
                    "hours": {
                        "type": "integer",
                        "description": (
                            "Numeric window fallback in hours. Defaults to 24 (or SkillConfig.window_hours)."
                        ),
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "writes": False,
        "function": {
            "name": "newsletter_scheduled_faults",
            "description": (
                "Return a Markdown checklist of scheduled runs with silent degradation: "
                "a tool refusal followed by a later non-error tool result. Each item "
                "carries the refusal, tool, later tools, and a final-message excerpt. "
                "Use this for the 'Needs review' section; do not infer faults from prose."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "window": {
                        "type": "string",
                        "description": (
                            "Compact time-range string like '24h', '7d', '2w'. "
                            "Takes precedence over 'hours' when non-empty."
                        ),
                    },
                    "hours": {
                        "type": "integer",
                        "description": (
                            "Numeric window fallback in hours. Defaults to 24 (or SkillConfig.window_hours)."
                        ),
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "writes": False,
        "function": {
            "name": "newsletter_list_vault_changes",
            "description": (
                "List vault markdown pages created or modified within a time window "
                "(mtime-based). Excludes files under the newsletter's own output folder."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "window": {
                        "type": "string",
                        "description": (
                            "Compact time-range string like '24h', '7d', '2w'. "
                            "Takes precedence over 'hours' when non-empty."
                        ),
                    },
                    "hours": {
                        "type": "integer",
                        "description": (
                            "Numeric window fallback in hours. Defaults to 24 (or SkillConfig.window_hours)."
                        ),
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "writes": False,
        "function": {
            "name": "newsletter_list_recent_memories",
            "description": (
                "List semantic memory facts absorbed into Mem0 / Qdrant in the given "
                "window. Fails safe if Mem0 is not configured, disabled, or unreachable."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "window": {
                        "type": "string",
                        "description": (
                            "Compact time-range string like '24h', '7d', '2w'. "
                            "Takes precedence over 'hours' when non-empty."
                        ),
                    },
                    "hours": {
                        "type": "integer",
                        "description": (
                            "Numeric window fallback in hours. Defaults to 24 (or SkillConfig.window_hours)."
                        ),
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "writes": True,
        "function": {
            "name": "newsletter_publish",
            "description": (
                "Publish the composed newsletter. Under a scheduled run, archives "
                "locally, dispatches to configured delivery targets (email, vault page), "
                "and advances last_run.json. Under interactive invocation "
                "(`!newsletter`), returns the markdown as-is with no side effects — "
                "unless `force_delivery` is True, which smoke-tests the full "
                "archive + delivery path on demand (used by `!newsletter send`). "
                "Call this as the final step."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "markdown": {
                        "type": "string",
                        "description": "Composed newsletter markdown body.",
                    },
                    "subject_hint": {
                        "type": "string",
                        "description": (
                            "Optional single-line highlight used in the email subject. Falls back to the date."
                        ),
                    },
                    "has_content": {
                        "type": "boolean",
                        "description": (
                            "True if the window had real activity to narrate. "
                            'False to record a "nothing to report" stub and skip '
                            "delivery. Defaults to True."
                        ),
                    },
                    "force_delivery": {
                        "type": "boolean",
                        "description": (
                            "Set True only when the user explicitly asked to send "
                            "(`!newsletter send`). Bypasses the interactive "
                            "short-circuit so archive + email/vault delivery run "
                            "even outside a scheduled task. Defaults to False."
                        ),
                    },
                },
                "required": ["markdown"],
            },
        },
    },
]
