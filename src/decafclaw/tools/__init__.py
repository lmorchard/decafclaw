"""Tool registry — combines core and built-in tools. Skill tools loaded on demand."""

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from ..media import ToolResult
from ..reminders import check as check_reminders
from ..reminders import reject_text
from .admin_tools import ADMIN_TOOL_DEFINITIONS, ADMIN_TOOLS
from .attachment_tools import ATTACHMENT_TOOL_DEFINITIONS, ATTACHMENT_TOOLS
from .calendar_tools import CALENDAR_TOOL_DEFINITIONS, CALENDAR_TOOLS
from .canvas_tools import CANVAS_TOOL_DEFINITIONS, CANVAS_TOOLS
from .checklist_tools import CHECKLIST_TOOL_DEFINITIONS, CHECKLIST_TOOLS
from .conversation_tools import CONVERSATION_TOOL_DEFINITIONS, CONVERSATION_TOOLS
from .core import CORE_TOOL_DEFINITIONS, CORE_TOOLS
from .delegate import DELEGATE_TOOL_DEFINITIONS, DELEGATE_TOOLS
from .dispatch import (
    _format_suggestions,
    _progress_suffix,
    _resolve_tool_timeout,
    _run_with_cancel,
    _suggest_tool_names,
    _typeerror_result,
)
from .email_tools import EMAIL_TOOL_DEFINITIONS, EMAIL_TOOLS
from .health import HEALTH_TOOL_DEFINITIONS, HEALTH_TOOLS
from .heartbeat_tools import HEARTBEAT_TOOL_DEFINITIONS, HEARTBEAT_TOOLS
from .http_tools import HTTP_TOOL_DEFINITIONS, HTTP_TOOLS
from .llm_tools import LLM_TOOL_DEFINITIONS, LLM_TOOLS
from .notes_tools import NOTES_TOOL_DEFINITIONS, NOTES_TOOLS
from .notification_tools import (
    NOTIFICATION_TOOL_DEFINITIONS,
    NOTIFICATION_TOOLS,
)
from .prompt_tools import PROMPT_TOOL_DEFINITIONS, PROMPT_TOOLS
from .schedule_tools import SCHEDULE_TOOL_DEFINITIONS, SCHEDULE_TOOLS
from .session_tools import SESSION_TOOL_DEFINITIONS, SESSION_TOOLS
from .shell_tools import SHELL_TOOL_DEFINITIONS, SHELL_TOOLS
from .skill_tools import SKILL_TOOL_DEFINITIONS, SKILL_TOOLS
from .ssh_tools import SSH_TOOL_DEFINITIONS, SSH_TOOLS
from .sticky_tools import STICKY_TOOL_DEFINITIONS, STICKY_TOOLS
from .subagent_tools import SUBAGENT_TOOL_DEFINITIONS, SUBAGENT_TOOLS
from .tool_output_tools import TOOL_OUTPUT_TOOL_DEFINITIONS, TOOL_OUTPUT_TOOLS
from .watch_tools import WATCH_TOOL_DEFINITIONS, WATCH_TOOLS
from .workspace_tools import WORKSPACE_TOOL_DEFINITIONS, WORKSPACE_TOOLS

if TYPE_CHECKING:
    from decafclaw.context import Context

log = logging.getLogger(__name__)

# Combined registry. External MCP server tools are registered via
# decafclaw.mcp_client. The `background` and `mcp` skills carry their
# own tools — loaded on activation.
#
# One entry per module, pairing its tool functions with its schema
# definitions (#1338). TOOLS and TOOL_DEFINITIONS are both derived from this
# single source, so the two collections cannot silently diverge in coverage
# or order. Entry order is the registry order: later modules override earlier
# ones in TOOLS, and TOOL_DEFINITIONS concatenates entries in this order.
_TOOL_SOURCES = (
    (CORE_TOOLS, CORE_TOOL_DEFINITIONS),
    (ADMIN_TOOLS, ADMIN_TOOL_DEFINITIONS),
    (CHECKLIST_TOOLS, CHECKLIST_TOOL_DEFINITIONS),
    (CONVERSATION_TOOLS, CONVERSATION_TOOL_DEFINITIONS),
    (WORKSPACE_TOOLS, WORKSPACE_TOOL_DEFINITIONS),
    (SHELL_TOOLS, SHELL_TOOL_DEFINITIONS),
    (TOOL_OUTPUT_TOOLS, TOOL_OUTPUT_TOOL_DEFINITIONS),
    (HTTP_TOOLS, HTTP_TOOL_DEFINITIONS),
    (LLM_TOOLS, LLM_TOOL_DEFINITIONS),
    (SKILL_TOOLS, SKILL_TOOL_DEFINITIONS),
    (HEARTBEAT_TOOLS, HEARTBEAT_TOOL_DEFINITIONS),
    (HEALTH_TOOLS, HEALTH_TOOL_DEFINITIONS),
    (DELEGATE_TOOLS, DELEGATE_TOOL_DEFINITIONS),
    (SUBAGENT_TOOLS, SUBAGENT_TOOL_DEFINITIONS),
    (ATTACHMENT_TOOLS, ATTACHMENT_TOOL_DEFINITIONS),
    (EMAIL_TOOLS, EMAIL_TOOL_DEFINITIONS),
    (NOTIFICATION_TOOLS, NOTIFICATION_TOOL_DEFINITIONS),
    (CANVAS_TOOLS, CANVAS_TOOL_DEFINITIONS),
    (CALENDAR_TOOLS, CALENDAR_TOOL_DEFINITIONS),
    (SSH_TOOLS, SSH_TOOL_DEFINITIONS),
    (NOTES_TOOLS, NOTES_TOOL_DEFINITIONS),
    (PROMPT_TOOLS, PROMPT_TOOL_DEFINITIONS),
    (SCHEDULE_TOOLS, SCHEDULE_TOOL_DEFINITIONS),
    (SESSION_TOOLS, SESSION_TOOL_DEFINITIONS),
    (STICKY_TOOLS, STICKY_TOOL_DEFINITIONS),
    (WATCH_TOOLS, WATCH_TOOL_DEFINITIONS),
)

# Registered for dispatch but intentionally without a schema definition: the
# LLM never sees them in its tool list. #1003 consolidated delegate_tasks into
# delegate_task (a 501-token schema saving) but kept the alias registered so
# skills and workflows that name it keep working. Tests assert this allowlist
# is the only TOOLS/TOOL_DEFINITIONS divergence (test_tool_registry.py).
_DISPATCH_ONLY_TOOLS = frozenset({"delegate_tasks"})

# Values are heterogeneous sync/async callables with no common signature, so
# the value type is Any (as in context.ToolState.extra) rather than a callable
# union pyright cannot collapse.
TOOLS: dict[str, Any] = {name: fn for tools, _defs in _TOOL_SOURCES for name, fn in tools.items()}
TOOL_DEFINITIONS = [defn for _tools, defs in _TOOL_SOURCES for defn in defs]


def _to_tool_result(value) -> ToolResult:
    """Normalize a tool return value to ToolResult."""
    if isinstance(value, ToolResult):
        return value
    if isinstance(value, str):
        return ToolResult(text=value)
    if value is None:
        return ToolResult(text="")
    return ToolResult(text=str(value))


# Tools whose successful result points at a file or page the web UI can open
# in the in-session editor/wiki panel. The link comes from the validated
# arguments, never from parsing the result text (#1829).
_LINK_TOOLS = {
    "workspace_read": ("workspace", "path"),
    "workspace_write": ("workspace", "path"),
    "workspace_edit": ("workspace", "path"),
    "vault_read": ("vault", "page"),
    "vault_write": ("vault", "page"),
    "vault_edit": ("vault", "page"),
    "vault_append": ("vault", "page"),
}


def _attach_links(result: ToolResult, name: str, arguments: dict) -> None:
    spec = _LINK_TOOLS.get(name)
    if spec is None or result.text.lstrip().startswith("[error"):
        return
    kind, key = spec
    value = arguments.get(key)
    if not isinstance(value, str) or not value:
        return
    result.links = [{"kind": kind, key: value, "label": value}]


async def execute_tool(ctx: "Context", name: str, arguments: dict) -> ToolResult:
    """Execute a tool by name and return the result.

    Returns a ToolResult with text (for LLM history) and optional media
    (for file attachments). Checks ctx.tools.extra (from activated skills)
    first, then the global registry.
    """
    # Check allowed tools list (used by eval runner)
    allowed = ctx.tools.allowed
    if allowed is not None and name not in allowed:
        return ToolResult(text=f"[error: tool '{name}' is not available in this context]")

    # Check disallowed tools blocklist
    disallowed = ctx.tools.disallowed or set()
    if name in disallowed:
        return ToolResult(text=f"[error: tool '{name}' is blocked by this context's allowlist/blocklist]")

    # Scoped reminders (#1127): admin-authored rules over the finalized
    # arguments, checked at this shared dispatch entry so the agent loop and
    # workflow `wf.tool_call` path get the same boundary. A reject stops the
    # call before dispatch; a remind queues an ephemeral message the agent loop
    # injects after the batch (workflows record but do not inject). Fail-open:
    # a malformed rules file yields no hits and never breaks execution.
    reminder_hit = await check_reminders(ctx, name, arguments)
    if reminder_hit is not None:
        if reminder_hit.action == "reject":
            return ToolResult(text=reject_text(reminder_hit))
        ctx.reminder_hits.append(reminder_hit)

    # Route MCP tools to the MCP registry (different call signature — no ctx)
    if name.startswith("mcp__"):
        from ..mcp_client import get_registry

        registry = get_registry()
        mcp_tools = registry.get_tools() if registry else {}
        fn = mcp_tools.get(name)
        if fn:
            try:
                cancel_event = ctx.cancelled
                tool_task, interrupted = await _run_with_cancel(fn(arguments), cancel_event)
                if interrupted:
                    return interrupted
                return _to_tool_result(tool_task.result())
            except Exception as e:
                return ToolResult(text=f"[error executing {name}: {e}]")
        # Tool not found — suggest close matches, require the agent to retry
        # with the exact name. No auto-correction.
        suggestions = _suggest_tool_names(name, set(mcp_tools.keys()))
        hint = _format_suggestions(suggestions)
        if not mcp_tools:
            return ToolResult(text=f"[error: MCP tool '{name}' not found; no MCP servers are connected.]")
        return ToolResult(
            text=(
                f"[error: MCP tool '{name}' not found.{hint} "
                f"Use the exact name from your tool list. To discover "
                f"available tools, call tool_search.]"
            )
        )

    # Check skill-provided tools first, then global registry, then search tools
    from .search_tools import SEARCH_TOOLS

    extra_tools = ctx.tools.extra
    fn = extra_tools.get(name) or TOOLS.get(name) or SEARCH_TOOLS.get(name)
    if fn is None:
        # Check if tool is in the deferred pool — auto-fetch if so.
        # Exception: skill-owned tools are NOT auto-fetched. The skill
        # body documents how to use them; bypassing activation would
        # let the agent call a tool without ever reading the body.
        # Phase 3 hides skill tools from the visible deferred list;
        # this guard hides them from auto-fetch too.
        deferred_pool = ctx.tools.deferred_pool
        deferred_names = {td.get("function", {}).get("name") for td in deferred_pool}
        skill_tool_owners = getattr(ctx.config, "skill_tool_owners", {}) or {}
        is_skill_tool = name in skill_tool_owners
        if name in deferred_names and not is_skill_tool:
            log.debug(f"Auto-fetching deferred tool: {name}")
            from .tool_registry import add_fetched_tools

            add_fetched_tools(ctx, {name})
            fn = extra_tools.get(name) or TOOLS.get(name)
        if fn is None:
            # Tool name belongs to a discovered skill — surface that
            # specifically rather than suggesting close matches, since
            # the agent guessed the right name but the skill isn't
            # loaded yet.
            if is_skill_tool:
                owning_skill = skill_tool_owners[name]
                # Determine the skill's status for a tailored message.
                from ..tools.skill_tools import _load_permissions

                perms = _load_permissions(ctx.config)
                if perms.get(owning_skill) == "deny":
                    return ToolResult(
                        text=(
                            f"[error: '{name}' is provided by skill "
                            f"'{owning_skill}', which has been denied. "
                            f"Tool unavailable.]"
                        )
                    )
                # Look up tier to phrase the recovery accurately.
                tier = "workspace"
                for s in ctx.config.discovered_skills:
                    if s.name == owning_skill:
                        tier = s.trust_tier
                        break
                if tier == "workspace" and perms.get(owning_skill) != "always":
                    return ToolResult(
                        text=(
                            f"[error: '{name}' is provided by the workspace "
                            f"skill '{owning_skill}', which has not been "
                            f"approved. Call activate_skill('{owning_skill}') "
                            f"to request user approval.]"
                        )
                    )
                return ToolResult(
                    text=(
                        f"[error: '{name}' is provided by the "
                        f"'{owning_skill}' skill, which is not activated. "
                        f"Call activate_skill('{owning_skill}') first.]"
                    )
                )

            # Unknown tool name — suggest close matches from everything
            # the agent could reach: active, skill, deferred pool, MCP.
            candidates: set[str] = set()
            candidates.update(extra_tools.keys())
            candidates.update(TOOLS.keys())
            candidates.update(SEARCH_TOOLS.keys())
            candidates.update(deferred_names)
            try:
                from ..mcp_client import get_registry

                reg = get_registry()
                if reg:
                    candidates.update(reg.get_tools().keys())
            except Exception as exc:
                log.debug("MCP registry probe failed while suggesting tools: %s", exc)
            suggestions = _suggest_tool_names(name, candidates)
            hint = _format_suggestions(suggestions)
            return ToolResult(
                text=(
                    f"[error: unknown tool '{name}'.{hint} "
                    f"Use the exact name from your tool list. To discover "
                    f"available tools, call tool_search.]"
                )
            )
    cancel_event = ctx.cancelled
    timeout_sec = _resolve_tool_timeout(ctx, name)
    try:
        coro = fn(ctx, **arguments) if asyncio.iscoroutinefunction(fn) else asyncio.to_thread(fn, ctx, **arguments)
        tool_task, interrupted = await _run_with_cancel(
            coro, cancel_event, timeout_sec=timeout_sec, tool_name=name, ctx=ctx
        )
        if interrupted:
            return interrupted
        result = _to_tool_result(tool_task.result())
        _attach_links(result, name, arguments)
        return result
    except TypeError as e:
        return _typeerror_result(ctx, name, fn, arguments, e)
    except Exception as e:
        return ToolResult(text=f"[error executing {name}: {e}]")
