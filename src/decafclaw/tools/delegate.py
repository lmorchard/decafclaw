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
                          return_schema: dict | None = None):
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
        child_ctx.event_context_id = parent_event_id

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


DELEGATE_TOOLS = {
    "delegate_task": tool_delegate_task,
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
                "Delegate a subtask to a child agent (a separate sub-agent / "
                "fork) that runs as an independent agent turn with access to "
                "the same tools and skills. **Use this whenever the user asks "
                "you to spin up, fork off, or hand off a task to a sub-agent, "
                "child agent, or separate agent**, and whenever a request has "
                "an independent part that benefits from running in its own "
                "context (e.g. exploration / summarization that would clutter "
                "the main conversation). For parallel work, call "
                "delegate_task multiple times in the same response. "
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
]
