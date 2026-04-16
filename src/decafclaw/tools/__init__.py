"""Tool registry — combines core and built-in tools. Skill tools loaded on demand."""

import asyncio
import difflib
import logging

from ..media import ToolResult
from .attachment_tools import ATTACHMENT_TOOL_DEFINITIONS, ATTACHMENT_TOOLS
from .checklist_tools import CHECKLIST_TOOL_DEFINITIONS, CHECKLIST_TOOLS
from .conversation_tools import CONVERSATION_TOOL_DEFINITIONS, CONVERSATION_TOOLS
from .core import CORE_TOOL_DEFINITIONS, CORE_TOOLS
from .delegate import DELEGATE_TOOL_DEFINITIONS, DELEGATE_TOOLS
from .health import HEALTH_TOOL_DEFINITIONS, HEALTH_TOOLS
from .heartbeat_tools import HEARTBEAT_TOOL_DEFINITIONS, HEARTBEAT_TOOLS
from .http_tools import HTTP_TOOL_DEFINITIONS, HTTP_TOOLS
from .shell_tools import SHELL_TOOL_DEFINITIONS, SHELL_TOOLS
from .skill_tools import SKILL_TOOL_DEFINITIONS, SKILL_TOOLS
from .workspace_tools import WORKSPACE_TOOL_DEFINITIONS, WORKSPACE_TOOLS

log = logging.getLogger(__name__)

# Combined registry. External MCP server tools are registered via
# decafclaw.mcp_client. The `background` and `mcp` skills carry their
# own tools — loaded on activation.
TOOLS = {**CORE_TOOLS, **CHECKLIST_TOOLS,
         **CONVERSATION_TOOLS, **WORKSPACE_TOOLS, **SHELL_TOOLS,
         **HTTP_TOOLS,
         **SKILL_TOOLS,
         **HEARTBEAT_TOOLS, **HEALTH_TOOLS,
         **DELEGATE_TOOLS, **ATTACHMENT_TOOLS}
TOOL_DEFINITIONS = (CORE_TOOL_DEFINITIONS
                    + CHECKLIST_TOOL_DEFINITIONS
                    + CONVERSATION_TOOL_DEFINITIONS + WORKSPACE_TOOL_DEFINITIONS
                    + SHELL_TOOL_DEFINITIONS
                    + HTTP_TOOL_DEFINITIONS + SKILL_TOOL_DEFINITIONS
                    + HEARTBEAT_TOOL_DEFINITIONS
                    + HEALTH_TOOL_DEFINITIONS
                    + DELEGATE_TOOL_DEFINITIONS + ATTACHMENT_TOOL_DEFINITIONS)


async def _run_with_cancel(coro, cancel_event):
    """Run a coroutine, cancelling it if cancel_event fires first.

    Returns (task, interrupted) where interrupted is a ToolResult if the
    turn was cancelled, or None if the coroutine completed normally.
    """
    tool_task = asyncio.create_task(coro)
    if cancel_event:
        cancel_task = asyncio.create_task(cancel_event.wait())
        done, _ = await asyncio.wait(
            [tool_task, cancel_task], return_when=asyncio.FIRST_COMPLETED
        )
        cancel_task.cancel()
        if tool_task not in done:
            tool_task.cancel()
            try:
                await tool_task
            except (asyncio.CancelledError, Exception):
                pass
            return tool_task, ToolResult(text="[tool interrupted: agent turn cancelled]")
    else:
        await tool_task
    return tool_task, None


def _to_tool_result(value) -> ToolResult:
    """Normalize a tool return value to ToolResult."""
    if isinstance(value, ToolResult):
        return value
    if isinstance(value, str):
        return ToolResult(text=value)
    if value is None:
        return ToolResult(text="")
    return ToolResult(text=str(value))


def _suggest_tool_names(name: str, candidates: set[str], max_results: int = 5) -> list[str]:
    """Suggest tool names close to ``name`` using difflib + suffix match.

    Returns up to ``max_results`` suggestions, most likely first. Used to
    give the agent a "did you mean?" hint when it calls a tool that
    doesn't exist. No correction happens — the agent must retry with the
    exact name on the next turn.
    """
    if not candidates:
        return []
    suggestions: list[str] = []
    # Suffix match catches the common "dropped prefix" case (e.g. Gemini
    # truncating `mcp__oblique-strategies__get_strategy` to
    # `strategies__get_strategy`).
    for cand in candidates:
        if cand.endswith(f"__{name}") or cand.endswith(name):
            if cand != name and cand not in suggestions:
                suggestions.append(cand)
    # difflib fuzzy match for general typos
    for cand in difflib.get_close_matches(name, list(candidates), n=max_results, cutoff=0.6):
        if cand not in suggestions:
            suggestions.append(cand)
    return suggestions[:max_results]


def _format_suggestions(suggestions: list[str]) -> str:
    """Format suggestion list as 'Did you mean: a, b, c.' for error messages."""
    if not suggestions:
        return ""
    return f" Did you mean: {', '.join(suggestions)}."


async def execute_tool(ctx, name: str, arguments: dict) -> ToolResult:
    """Execute a tool by name and return the result.

    Returns a ToolResult with text (for LLM history) and optional media
    (for file attachments). Checks ctx.tools.extra (from activated skills)
    first, then the global registry.
    """
    # Check allowed tools list (used by eval runner)
    allowed = ctx.tools.allowed
    if allowed is not None and name not in allowed:
        return ToolResult(text=f"[error: tool '{name}' is not available in this context]")

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
            return ToolResult(
                text=f"[error: MCP tool '{name}' not found; no MCP servers are connected.]"
            )
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
        # Check if tool is in the deferred pool — auto-fetch if so
        deferred_pool = ctx.tools.deferred_pool
        deferred_names = {td.get("function", {}).get("name") for td in deferred_pool}
        if name in deferred_names:
            log.debug(f"Auto-fetching deferred tool: {name}")
            from .tool_registry import add_fetched_tools
            add_fetched_tools(ctx, {name})
            fn = extra_tools.get(name) or TOOLS.get(name)
        if fn is None:
            # Unknown tool — suggest close matches from everything the
            # agent could reach: active, skill, deferred pool, and MCP.
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
            except Exception:
                pass
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
    try:
        coro = fn(ctx, **arguments) if asyncio.iscoroutinefunction(fn) else asyncio.to_thread(fn, ctx, **arguments)
        tool_task, interrupted = await _run_with_cancel(coro, cancel_event)
        if interrupted:
            return interrupted
        return _to_tool_result(tool_task.result())
    except TypeError as e:
        # Common: model guesses wrong parameter names (e.g. 'path' instead of 'file').
        # Include expected params in the error to help the model self-correct.
        import inspect
        try:
            sig = inspect.signature(fn)
            params = [p for p in sig.parameters if p != "ctx"]
            return ToolResult(text=f"[error executing {name}: {e}. Expected parameters: {', '.join(params)}]")
        except (ValueError, TypeError):
            return ToolResult(text=f"[error executing {name}: {e}]")
    except Exception as e:
        return ToolResult(text=f"[error executing {name}: {e}]")
