"""Sub-agent delegation — fork child agents for focused subtasks."""

import asyncio
import json
import logging
import re
import secrets
from dataclasses import replace
from typing import Any

from ..media import ToolResult

log = logging.getLogger(__name__)

DEFAULT_CHILD_SYSTEM_PROMPT = (
    "Complete the following task. Be concise and focused. "
    "Return your result directly.\n\n"
    "IMPORTANT: You have tools available — check your tool list and USE them. "
    "Do NOT say you lack capabilities without first checking your available tools. "
    "When a skill below shows bash/curl commands, run them with the shell tool."
)

# Vault-access policy for child agents (#396). Default is no-access;
# the parent opts the child in via flags on ``delegate_task``. Vault
# WRITE tools are categorically blocked — if a child's work should
# land in the vault, the parent does the write itself after the child
# returns. New vault tools should update these sets when added.
_VAULT_READ_TOOLS = frozenset({
    "vault_read",
    "vault_search",
    "vault_list",
    "vault_backlinks",
    "vault_show_sections",
})

_VAULT_WRITE_TOOLS = frozenset({
    "vault_write",
    "vault_delete",
    "vault_rename",
    "vault_journal_append",
    "vault_move_lines",
    "vault_section",
})

# Structured-return addendum (#395). Appended to the child system
# prompt when `delegate_task` is called with a `return_schema` hint.
# The schema is rendered as a JSON example; the child is instructed
# to emit prose first, then a fenced JSON block matching the shape.
_STRUCTURED_OUTPUT_INSTRUCTION = """\

You MUST return your output in the following form:

1. Any prose explanation, analysis, or context first.
2. Then a fenced JSON block matching this exact schema:

```json
{schema}
```

Replace placeholder values with actual data; keep the field shape
exactly as shown. Use `null` for missing values rather than
omitting fields."""

_FENCED_JSON_RE = re.compile(
    r"```json\s*\n(?P<body>.+?)\n```",
    re.DOTALL,
)


def _render_schema_addendum(schema: dict) -> str:
    """Render a JSON-schema-shaped dict into the structured-output
    prompt addendum. Returns "" on JSON-encoding failure (defensive
    — the caller short-circuits if the addendum is empty)."""
    try:
        rendered = json.dumps(schema, indent=2)
    except (TypeError, ValueError) as exc:
        log.warning(
            "delegate_task: failed to render return_schema as JSON; "
            "skipping addendum: %s", exc,
        )
        return ""
    return _STRUCTURED_OUTPUT_INSTRUCTION.format(schema=rendered)


def _parse_structured_output(text: str) -> tuple[Any | None, str]:
    """Extract a fenced ```json block from ``text``.

    Returns ``(parsed, prose)`` where ``parsed`` is the JSON-decoded
    object (any shape — list/dict/scalar — since the caller's schema
    is treated as a hint, not enforced). ``prose`` is ``text`` with
    the JSON block stripped so the tool result's prose half doesn't
    duplicate the auto-rendered ``ToolResult.data`` block.

    Returns ``(None, text)`` when there's no fenced block, the JSON
    is malformed, or the input is empty. Lenient — the caller treats
    None as a silent prose-only fallback.
    """
    if not text:
        return None, text
    match = _FENCED_JSON_RE.search(text)
    if not match:
        return None, text
    body = match.group("body").strip()
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return None, text
    prose = _FENCED_JSON_RE.sub("", text).strip()
    return parsed, prose


async def _run_child_turn(parent_ctx, task, model: str = "",
                          max_iterations: int = 0,
                          *,
                          allow_vault_retrieval: bool = False,
                          allow_vault_read: bool = False,
                          return_schema: dict | None = None,
                          event_context_id_override: str | None = None):
    """Run a child agent turn via ConversationManager, preserving the
    parent's tools, skills, and event routing.

    Args:
        model: Override model for the child. Empty = inherit parent's.
        max_iterations: Override max tool iterations. 0 = use child_max_tool_iterations.
        allow_vault_retrieval: When False (default), the child runs
            with ``skip_vault_retrieval=True`` — no proactive memory
            injection. Set True to opt the child INTO the parent's
            retrieval pipeline. See #396.
        allow_vault_read: When False (default), the child has no
            access to vault read tools. Set True to opt INTO the
            read set (``vault_read``, ``vault_search``,
            ``vault_list``, ``vault_backlinks``,
            ``vault_show_sections``). Vault WRITE tools are
            categorically blocked regardless.
        return_schema: Optional JSON-schema-shaped dict (#395). When
            supplied, the child system prompt gets an addendum
            instructing it to emit a fenced JSON block matching the
            shape after any prose. The caller is responsible for
            parsing the JSON out of the response.
        event_context_id_override: When non-None, the child's
            ``event_context_id`` is set to this value instead of
            inheriting the parent's subscriber id. Used by
            ``delegate_tasks`` (#397) to suppress per-child progress
            events from the parent UI.

    Returns the child's text response, or an error string on failure.
    """
    from ..conversation_manager import TurnKind  # deferred: circular dep
    from . import TOOLS  # deferred: circular dep

    config = parent_ctx.config

    # Build child system prompt: base + activated skill bodies + optional
    # structured-output addendum.
    activated = parent_ctx.skills.activated
    skill_map = {s.name: s for s in config.discovered_skills}
    prompt_parts = [DEFAULT_CHILD_SYSTEM_PROMPT]
    for name in sorted(activated):
        skill = skill_map.get(name)
        if skill and skill.body:
            prompt_parts.append(f"\n\n--- Skill: {name} ---\n{skill.body}")
    if return_schema is not None:
        addendum = _render_schema_addendum(return_schema)
        if addendum:
            prompt_parts.append(addendum)
    child_system_prompt = "\n".join(prompt_parts)

    child_config = replace(
        config,
        agent=replace(config.agent, max_tool_iterations=(
            max_iterations or config.agent.child_max_tool_iterations)),
        system_prompt=child_system_prompt,
    )
    # Children don't discover or activate skills — they inherit parent's
    child_config.discovered_skills = []

    parent_conv = parent_ctx.conv_id or parent_ctx.channel_id
    # Per-call unique conv_id; short random suffix to avoid collisions.
    child_conv_id = f"{parent_conv}--child-{secrets.token_hex(4)}"
    parent_event_id = parent_ctx.event_context_id or parent_ctx.context_id

    def setup(child_ctx):
        # Swap in the child-specific config (smaller iteration budget + child
        # system prompt). Context was already built with parent's config by
        # Context.for_task, so we overwrite here.
        child_ctx.config = child_config
        child_ctx.cancelled = parent_ctx.cancelled
        child_ctx.request_confirmation = parent_ctx.request_confirmation
        # Route child events to the parent's UI subscriber so confirmations
        # and tool progress are visible in the parent conversation.
        # `event_context_id_override` lets `delegate_tasks` (#397) point
        # children at a separate id so per-child progress doesn't flood
        # the parent UI when running batches in parallel.
        child_ctx.event_context_id = (
            event_context_id_override if event_context_id_override is not None
            else parent_event_id
        )

        # Child inherits parent's tools minus delegation/activation.
        # If parent has restricted allowed_tools, respect that restriction.
        excluded = {"delegate_task", "activate_skill", "refresh_skills", "tool_search"}
        # Vault policy (#396): writes are categorically blocked for
        # children regardless of flags; reads require explicit opt-in.
        excluded |= _VAULT_WRITE_TOOLS
        if not allow_vault_read:
            excluded |= _VAULT_READ_TOOLS
        all_tools = set(TOOLS) | set(parent_ctx.tools.extra)
        parent_allowed = parent_ctx.tools.allowed
        if parent_allowed is not None:
            all_tools = all_tools & parent_allowed
        child_ctx.tools.allowed = all_tools - excluded

        # Carry over parent's activated skill tools and data
        child_ctx.tools.extra = parent_ctx.tools.extra
        child_ctx.tools.extra_definitions = parent_ctx.tools.extra_definitions
        child_ctx.skills.data = parent_ctx.skills.data

        # Clear skill state so children can't activate new skills
        child_ctx.skills.activated = set()
        # Propagate command pre-approved tools and scoped shell patterns to child
        child_ctx.tools.preapproved = parent_ctx.tools.preapproved
        child_ctx.tools.preapproved_shell_patterns = parent_ctx.tools.preapproved_shell_patterns

        # No streaming or reflection for child agents
        child_ctx.on_stream_chunk = None
        child_ctx.is_child = True
        child_ctx.skip_reflection = True
        # Default-deny vault retrieval (#396); the parent opts in via
        # `allow_vault_retrieval=True` on `delegate_task`.
        child_ctx.skip_vault_retrieval = not allow_vault_retrieval

        # Set active model: explicit override > parent's model
        child_ctx.active_model = model if model else parent_ctx.active_model

    manager = parent_ctx.manager
    if manager is None:
        return ToolResult(
            text="[error: delegate_task requires a ConversationManager; "
                 "no manager on parent ctx]"
        )

    timeout = config.agent.child_timeout_sec

    try:
        future = await manager.enqueue_turn(
            child_conv_id,
            kind=TurnKind.CHILD_AGENT,
            prompt=task,
            history=[],
            context_setup=setup,
            user_id=parent_ctx.user_id,
        )
        result_text = await asyncio.wait_for(future, timeout=timeout)
        return result_text or ""
    except asyncio.TimeoutError:
        return ToolResult(text=f"[error: subtask timed out after {timeout}s]")
    except Exception as e:
        return ToolResult(text=f"[error: subtask failed: {e}]")


async def tool_delegate_task(
    ctx,
    task: str,
    model: str = "",
    allow_vault_retrieval: bool = False,
    allow_vault_read: bool = False,
    return_schema: dict | None = None,
) -> ToolResult:
    """Delegate a subtask to a child agent.

    By default the child has NO vault access — no proactive
    retrieval, no read tools, no write tools. Opt the child into
    retrieval via ``allow_vault_retrieval=True`` and into the
    read-side vault tools via ``allow_vault_read=True``. Write
    tools are categorically blocked for children regardless. See
    #396.

    When ``return_schema`` is supplied, the child is instructed to
    return prose followed by a fenced JSON block matching the shape;
    the parsed object lands on ``ToolResult.data`` and the prose half
    on ``ToolResult.text``. Parse failures fall through silently with
    a debug log — the parent gets the raw response as text. See #395.
    """
    log.info(
        "[tool:delegate_task] model=%s vault_retrieval=%s vault_read=%s "
        "schema=%s %s...",
        model or "inherit",
        allow_vault_retrieval,
        allow_vault_read,
        "yes" if return_schema else "no",
        task[:80],
    )

    if not task or not task.strip():
        return ToolResult(text="[error: task description is required]")

    raw = await _run_child_turn(
        ctx, task, model=model,
        allow_vault_retrieval=allow_vault_retrieval,
        allow_vault_read=allow_vault_read,
        return_schema=return_schema,
    )
    # Error paths in _run_child_turn return ToolResult directly; pass through.
    if isinstance(raw, ToolResult):
        return raw

    raw_text = raw or ""
    if return_schema is None:
        return ToolResult(text=raw_text)

    parsed, prose = _parse_structured_output(raw_text)
    if parsed is None:
        log.debug(
            "delegate_task: child response had no parseable JSON block; "
            "falling back to prose-only return",
        )
        return ToolResult(text=raw_text)
    return ToolResult(text=prose or raw_text, data=parsed)


async def _run_one_delegated(
    parent_ctx,
    *,
    task: str,
    idx: int,
    semaphore: asyncio.Semaphore,
    progress: dict,
    progress_lock: asyncio.Lock,
    total: int,
    model: str,
    allow_vault_retrieval: bool,
    allow_vault_read: bool,
    return_schema: dict | None,
) -> dict:
    """Run one child of a `delegate_tasks` batch under a semaphore.

    Returns a dict matching the per-task entry shape on
    ``ToolResult.data["results"]``: index + ok + (text/data | error).

    Per-child events are routed to a dedicated id (the child's own
    conv id) so they don't flood the parent UI; the parent emits one
    aggregate ``tool_status`` event per completion via this helper.
    """
    async with semaphore:
        try:
            raw = await _run_child_turn(
                parent_ctx, task, model=model,
                allow_vault_retrieval=allow_vault_retrieval,
                allow_vault_read=allow_vault_read,
                return_schema=return_schema,
                event_context_id_override=f"delegate-tasks-child-{idx}",
            )
        except Exception as exc:
            log.warning(
                "delegate_tasks: child %d raised unexpectedly: %s",
                idx, exc, exc_info=True,
            )
            entry: dict = {
                "index": idx,
                "ok": False,
                "error": f"delegate_tasks internal error: {exc}",
            }
        else:
            if isinstance(raw, ToolResult):
                # _run_child_turn surfaces failure as a ToolResult with
                # an "[error: ...]" prefix; preserve it as the error
                # field so callers can inspect it.
                entry = {"index": idx, "ok": False, "error": raw.text or ""}
            else:
                raw_text = raw or ""
                if return_schema is None:
                    entry = {"index": idx, "ok": True, "text": raw_text}
                else:
                    parsed, prose = _parse_structured_output(raw_text)
                    if parsed is None:
                        log.debug(
                            "delegate_tasks: child %d had no parseable "
                            "JSON block; falling back to prose-only.",
                            idx,
                        )
                        entry = {"index": idx, "ok": True, "text": raw_text}
                    else:
                        entry = {
                            "index": idx,
                            "ok": True,
                            "text": prose or raw_text,
                            "data": parsed,
                        }

        async with progress_lock:
            progress["done"] += 1
            done = progress["done"]
        try:
            await parent_ctx.publish("tool_status", {
                "tool": "delegate_tasks",
                "message": f"{done}/{total} subtasks complete",
            })
        except Exception:
            log.debug(
                "delegate_tasks: failed to publish progress event "
                "(child %d)", idx, exc_info=True,
            )
        return entry


async def tool_delegate_tasks(
    ctx,
    tasks: list[str],
    model: str = "",
    allow_vault_retrieval: bool = False,
    allow_vault_read: bool = False,
    return_schema: dict | None = None,
) -> ToolResult:
    """Delegate a batch of subtasks to child agents in parallel (#397).

    Each task in ``tasks`` runs as its own forked child agent with
    the parent's tools and activated skills. Children run
    concurrently, capped by ``config.agent.max_parallel_delegates``.
    The total batch size is capped at
    ``config.agent.max_tasks_per_delegate_call``.

    All non-``tasks`` parameters are shared across the batch — same
    model, same vault flags, same return schema. (For per-task
    overrides, fall back to multiple ``delegate_task`` calls.)

    Per-child progress events are NOT routed to the parent UI; the
    parent emits one aggregate ``tool_status`` event per completion.

    Returns ``ToolResult(text=summary_line, data={"summary": ...,
    "results": [...]})``. Each per-task entry carries ``index``,
    ``ok``, and either ``text``/``data`` (success) or ``error``
    (failure). Results are ordered by input index.
    """
    config = ctx.config
    cap_count = config.agent.max_tasks_per_delegate_call
    cap_parallel = config.agent.max_parallel_delegates

    if not isinstance(tasks, list) or not tasks:
        return ToolResult(text="[error: tasks must be a non-empty list]")
    for i, t in enumerate(tasks):
        if not isinstance(t, str) or not t.strip():
            return ToolResult(text=(
                f"[error: tasks[{i}] must be a non-empty string]"
            ))
    if cap_count > 0 and len(tasks) > cap_count:
        return ToolResult(text=(
            f"[error: too many tasks ({len(tasks)}); cap is "
            f"{cap_count} per call. Split the batch or raise "
            "config.agent.max_tasks_per_delegate_call.]"
        ))

    log.info(
        "[tool:delegate_tasks] count=%d parallel<=%d model=%s "
        "vault_retrieval=%s vault_read=%s schema=%s",
        len(tasks), cap_parallel, model or "inherit",
        allow_vault_retrieval, allow_vault_read,
        "yes" if return_schema else "no",
    )

    semaphore = asyncio.Semaphore(max(1, cap_parallel))
    progress: dict = {"done": 0}
    progress_lock = asyncio.Lock()
    total = len(tasks)

    coros = [
        _run_one_delegated(
            ctx,
            task=t,
            idx=i,
            semaphore=semaphore,
            progress=progress,
            progress_lock=progress_lock,
            total=total,
            model=model,
            allow_vault_retrieval=allow_vault_retrieval,
            allow_vault_read=allow_vault_read,
            return_schema=return_schema,
        )
        for i, t in enumerate(tasks)
    ]
    raw_results = await asyncio.gather(*coros, return_exceptions=True)

    # Replace any leaked exception with a structured failure entry.
    # `_run_one_delegated` already swallows child errors, so this is
    # purely defensive against bugs in the gather/setup path.
    results: list[dict] = []
    for i, r in enumerate(raw_results):
        if isinstance(r, BaseException):
            log.warning(
                "delegate_tasks: gather slot %d raised unexpectedly: %s",
                i, r, exc_info=r,
            )
            results.append({
                "index": i,
                "ok": False,
                "error": f"delegate_tasks internal error: {r}",
            })
        else:
            results.append(r)

    results.sort(key=lambda e: e["index"])
    ok_count = sum(1 for e in results if e["ok"])
    fail_count = total - ok_count
    summary_line = (
        f"{total} subtasks: {ok_count} succeeded, {fail_count} failed"
    )
    return ToolResult(
        text=summary_line,
        data={
            "summary": {
                "total": total, "ok": ok_count, "failed": fail_count,
            },
            "results": results,
        },
    )


DELEGATE_TOOLS = {
    "delegate_task": tool_delegate_task,
    "delegate_tasks": tool_delegate_tasks,
}

DELEGATE_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "priority": "critical",
        # Owns its own child-agent timeout via asyncio.wait_for(child_timeout_sec).
        "timeout": None,
        "function": {
            "name": "delegate_task",
            "description": (
                "Delegate a SINGLE subtask to a child agent (a separate sub-agent / "
                "fork) that runs as an independent agent turn with access to "
                "the same tools and skills. **Use this whenever the user asks "
                "you to spin up, fork off, or hand off a task to a sub-agent, "
                "child agent, or separate agent**, and whenever a request has "
                "an independent part that benefits from running in its own "
                "context (e.g. exploration / summarization that would clutter "
                "the main conversation). For parallel work over a known list "
                "of similar subtasks, prefer `delegate_tasks` (plural). "
                "**Do not just do the work yourself with workspace_read / "
                "vault_read** when the user explicitly asked for a sub-agent."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": (
                            "Task description with enough context for the "
                            "child agent to work independently"
                        ),
                    },
                    "model": {
                        "type": "string",
                        "description": (
                            "Named model config for the subtask. "
                            "Omit to inherit parent's model."
                        ),
                    },
                    "allow_vault_retrieval": {
                        "type": "boolean",
                        "description": (
                            "When true, the child runs the proactive memory "
                            "retrieval at turn start. Default false — the "
                            "child has no auto-injected memory context "
                            "unless you opt in. Use when the child needs "
                            "to draw on past conversations or vault "
                            "knowledge to do its task."
                        ),
                    },
                    "allow_vault_read": {
                        "type": "boolean",
                        "description": (
                            "When true, the child can call read-side vault "
                            "tools (vault_read, vault_search, vault_list, "
                            "vault_backlinks, vault_show_sections). Default "
                            "false — the child can't read the vault unless "
                            "you opt in. Vault WRITE tools (vault_write, "
                            "vault_journal_append, vault_delete, etc.) are "
                            "NEVER available to children regardless of this "
                            "flag; if the child's work should land in the "
                            "vault, do the write yourself after the child "
                            "returns."
                        ),
                    },
                    "return_schema": {
                        "type": "object",
                        "description": (
                            "Optional JSON-schema-shaped object describing "
                            "the structured return shape you want from the "
                            "child. When supplied, the child is instructed "
                            "to emit prose followed by a fenced JSON block "
                            "matching this shape; the parsed object arrives "
                            "on this tool result's structured-data block. "
                            "Use for subtasks where you need specific fields "
                            "(counts, lists, scores) rather than just prose. "
                            "Treat as a hint — no validation is performed; "
                            "parse failures fall back to prose-only."
                        ),
                    },
                },
                "required": ["task"],
            },
        },
    },
    {
        "type": "function",
        "priority": "critical",
        # Owns child timeouts internally; gather-bounded fan-out.
        "timeout": None,
        "function": {
            "name": "delegate_tasks",
            "description": (
                "Dispatch a BATCH of independent subtasks to child agents "
                "in PARALLEL. Use this when you have a known list of "
                "similar investigations (per-page, per-file, per-topic) "
                "where the children don't need to talk to each other and "
                "can run concurrently. Each task runs as its own forked "
                "child with the same tools and skills. Returns one "
                "structured result containing per-task status (ok/error) "
                "in input order. Concurrency is capped by config; the "
                "batch size is also capped per call. For a single "
                "subtask, use `delegate_task` (singular) instead — the "
                "ergonomics are simpler."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "tasks": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "List of task descriptions, one per child. "
                            "Each description should be self-contained "
                            "with enough context to work independently. "
                            "All tasks share the same model, vault flags, "
                            "and return schema — for per-task overrides, "
                            "fall back to multiple `delegate_task` calls."
                        ),
                    },
                    "model": {
                        "type": "string",
                        "description": (
                            "Named model config for every subtask in the "
                            "batch. Omit to inherit parent's model."
                        ),
                    },
                    "allow_vault_retrieval": {
                        "type": "boolean",
                        "description": (
                            "When true, every child runs the proactive "
                            "memory retrieval at turn start. Default "
                            "false. Same semantics as `delegate_task`."
                        ),
                    },
                    "allow_vault_read": {
                        "type": "boolean",
                        "description": (
                            "When true, every child can call read-side "
                            "vault tools (vault_read, vault_search, "
                            "vault_list, vault_backlinks, "
                            "vault_show_sections). Default false. Vault "
                            "WRITE tools are categorically blocked. "
                            "Same semantics as `delegate_task`."
                        ),
                    },
                    "return_schema": {
                        "type": "object",
                        "description": (
                            "Optional JSON-schema-shaped object applied "
                            "to every child in the batch. Each successful "
                            "per-task entry's `data` field will be the "
                            "parsed JSON; `text` will be the prose with "
                            "the JSON block stripped. Treat as a hint — "
                            "no validation is performed."
                        ),
                    },
                },
                "required": ["tasks"],
            },
        },
    },
]
