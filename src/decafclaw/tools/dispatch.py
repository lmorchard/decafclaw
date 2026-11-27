"""Tool dispatch helpers — cancel/timeout racing, timeout resolution, and error formatting.

Moved out of the package ``__init__`` (#1337) so the registry module stays
focused on registry derivation. ``decafclaw.tools`` re-imports these names,
so existing imports keep working.
"""

import asyncio
import difflib
import inspect
import logging
from typing import TYPE_CHECKING

from ..media import ToolResult

if TYPE_CHECKING:
    from decafclaw.context import Context

log = logging.getLogger(__name__)


def _progress_suffix(ctx) -> str:
    """Name a tool's recorded partial progress in an interrupt/timeout result."""
    if ctx is None:
        return ""
    progress = ctx.tools.progress_text
    return f" ({progress})" if progress else ""


async def _run_with_cancel(coro, cancel_event, timeout_sec=None, tool_name="", ctx=None):
    """Run a coroutine, racing it against optional cancel + timeout signals.

    Returns (task, terminated) where terminated is a ToolResult if the
    tool was interrupted or timed out, or None if the coroutine completed
    normally. On near-simultaneous fire, precedence is cancel > timeout.

    `timeout_sec` of None disables the timer. `<=0` is the caller's
    responsibility to normalize (see `_resolve_tool_timeout`); we treat
    anything non-positive here as "no timer" as a defensive guard.
    """
    tool_task = asyncio.create_task(coro)
    if not cancel_event and (timeout_sec is None or timeout_sec <= 0):
        await tool_task
        return tool_task, None

    cancel_task: asyncio.Task | None = None
    if cancel_event:
        cancel_task = asyncio.create_task(cancel_event.wait())

    while True:
        aux_tasks: list[asyncio.Task] = []
        if cancel_task is not None:
            aux_tasks.append(cancel_task)

        timer_task: asyncio.Task | None = None
        if timeout_sec is not None and timeout_sec > 0:
            timer_task = asyncio.create_task(asyncio.sleep(timeout_sec))
            aux_tasks.append(timer_task)

        done, _pending = await asyncio.wait([tool_task, *aux_tasks], return_when=asyncio.FIRST_COMPLETED)

        # Cancel leftover timer task for this round
        if timer_task is not None and not timer_task.done():
            timer_task.cancel()

        # Tool task finished
        if tool_task in done:
            if cancel_task is not None and not cancel_task.done():
                cancel_task.cancel()
            return tool_task, None

        # Cancel beats timeout on tie. Check cancel first.
        if cancel_task is not None and cancel_task in done:
            tool_task.cancel()
            try:
                await tool_task
            except (asyncio.CancelledError, Exception):
                pass
            return tool_task, ToolResult(text=f"[tool interrupted: agent turn cancelled{_progress_suffix(ctx)}]")

        # Timer task fired
        if timer_task is not None and timer_task in done:
            # Check if the tool is currently waiting for interactive confirmation.
            # While the user is considering the prompt, pause the watchdog rather
            # than killing the tool.
            conf_active = getattr(ctx, "confirmation_active", None)
            if conf_active is not None and conf_active.is_set():
                conf_poll = asyncio.create_task(asyncio.sleep(0.5))
                while conf_active.is_set() and not tool_task.done():
                    wait_tasks = [tool_task, conf_poll]
                    if cancel_task is not None:
                        wait_tasks.append(cancel_task)
                    wait_done, _ = await asyncio.wait(wait_tasks, return_when=asyncio.FIRST_COMPLETED)
                    if cancel_task is not None and cancel_task in wait_done:
                        if not conf_poll.done():
                            conf_poll.cancel()
                        tool_task.cancel()
                        try:
                            await tool_task
                        except (asyncio.CancelledError, Exception):
                            pass
                        return tool_task, ToolResult(
                            text=f"[tool interrupted: agent turn cancelled{_progress_suffix(ctx)}]"
                        )
                    if tool_task in wait_done:
                        if not conf_poll.done():
                            conf_poll.cancel()
                        if cancel_task is not None and not cancel_task.done():
                            cancel_task.cancel()
                        return tool_task, None
                    if conf_poll in wait_done:
                        conf_poll = asyncio.create_task(asyncio.sleep(0.5))
                if not conf_poll.done():
                    conf_poll.cancel()
                if tool_task.done():
                    if cancel_task is not None and not cancel_task.done():
                        cancel_task.cancel()
                    return tool_task, None
                continue

            tool_task.cancel()
            try:
                await tool_task
            except (asyncio.CancelledError, Exception):
                pass
            if cancel_task is not None and not cancel_task.done():
                cancel_task.cancel()
            log.warning(
                "[watchdog] Tool '%s' timed out after %ss (conv=%s)",
                tool_name,
                timeout_sec,
                getattr(ctx, "conv_id", ""),
            )
            return tool_task, ToolResult(
                text=f"[error: tool {tool_name} timed out after {timeout_sec}s{_progress_suffix(ctx)}]",
                timed_out=True,
                timeout_sec=timeout_sec,
            )


_MISSING = object()


def _resolve_tool_timeout(ctx: "Context", name: str) -> int | None:
    """Resolve the wall-clock timeout for `name`.

    Walks skill-provided defs first, then the global TOOL_DEFINITIONS,
    then SEARCH_TOOL_DEFINITIONS. Returns the first explicit `timeout`
    found (including `None`). Falls back to `config.agent.tool_timeout_sec`.
    Any resolved value `<= 0` normalizes to None (disabled).
    """
    # Lazy: the package __init__ imports this module, so a top-level import
    # of TOOL_DEFINITIONS would cycle.
    from . import TOOL_DEFINITIONS
    from .search_tools import SEARCH_TOOL_DEFINITIONS

    def _normalize_default() -> int | None:
        default = ctx.config.agent.tool_timeout_sec
        if default is None or default <= 0:
            return None
        return int(default)

    sources = (
        ctx.tools.extra_definitions,
        TOOL_DEFINITIONS,
        SEARCH_TOOL_DEFINITIONS,
    )
    for source in sources:
        for entry in source:
            func = entry.get("function") or {}
            if func.get("name") == name:
                # First match wins. Skill defs and global defs don't overlap
                # in practice, so we don't fall through on a missing key.
                val = entry.get("timeout", _MISSING)
                if val is _MISSING:
                    return _normalize_default()
                if val is None:
                    return None
                if isinstance(val, (int, float)) and val > 0:
                    return int(val)
                return None  # 0, negative, or unexpected type → disabled

    return _normalize_default()


def _suggest_tool_names(name: str, candidates: set[str], max_results: int = 5) -> list[str]:
    """Suggest tool names close to ``name`` using difflib + suffix match.

    Returns up to ``max_results`` suggestions, most likely first. Used to
    give the agent a "did you mean?" hint when it calls a tool that
    doesn't exist. No correction happens — the agent must retry with the
    exact name on the next turn.
    """
    if not candidates:
        return []
    # Never suggest the unknown name back to the caller (#355): if it's
    # present in the candidate pool (e.g. a deferred/unactivated tool),
    # difflib would return it as an exact ratio-1.0 match. Case-insensitive
    # so a candidate differing only in case is treated as the same name.
    # `isinstance(c, str)` guards against a malformed tool def leaving a
    # None in the pool (deferred_names uses `.get("name")` with no default)
    # — a suggestion hint must never crash the unknown-tool error path.
    name_lower = name.lower()
    candidates = {c for c in candidates if isinstance(c, str) and c.lower() != name_lower}
    if not candidates:
        return []
    suggestions: list[str] = []
    # Suffix match catches the common "dropped prefix" case (e.g. Gemini
    # truncating `mcp__oblique-strategies__get_strategy` to
    # `strategies__get_strategy`).
    for cand in candidates:
        if cand.endswith(f"__{name}") or cand.endswith(name):
            if cand not in suggestions:
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


def _typeerror_result(ctx: "Context", name: str, fn, arguments: dict, exc: TypeError) -> ToolResult:
    """Attribute a TypeError to either the call arguments or the tool body.

    The call and the entire tool body sit inside one `except TypeError`, so
    two very different failures land here:

    1. The model guessed wrong parameter names ('path' instead of 'file') —
       the caller is wrong, and listing the expected params lets it
       self-correct.
    2. The tool's own code raised TypeError — the *caller* is fine and the
       bug is in the implementation.

    Before this split, case 2 was reported with case 1's wording, so a bad
    `ToolResult(tool_code=...)` inside a skill tool came back as "Expected
    parameters: " (empty, for a zero-arg tool) and sent the author to audit
    the call site for a bug that was on line 60 of their own file.

    `sig.bind` is the discriminator: if the arguments bind cleanly, the
    TypeError cannot have come from argument binding.
    """
    try:
        sig = inspect.signature(fn)
    except (ValueError, TypeError):
        # Builtins and some C-implemented callables have no introspectable
        # signature; there's nothing to say beyond the error itself.
        return ToolResult(text=f"[error executing {name}: {exc}]")

    try:
        sig.bind(ctx, **arguments)
    except TypeError:
        params = [p for p in sig.parameters if p != "ctx"]
        return ToolResult(text=f"[error executing {name}: {exc}. Expected parameters: {', '.join(params)}]")

    owner = ctx.config.skill_tool_owners.get(name)
    where = f" of skill '{owner}'" if owner else ""
    return ToolResult(
        text=(
            f"[error executing {name}: {exc}. This TypeError was raised inside "
            f"the tool's own code{where} — the arguments you passed matched its "
            f"signature, so calling it differently will not help. Fix the tool's "
            f"implementation.]"
        )
    )
