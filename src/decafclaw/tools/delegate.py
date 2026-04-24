"""Sub-agent delegation — fork child agents for focused subtasks."""

import asyncio
import logging
import secrets
from dataclasses import replace

from ..media import ToolResult

log = logging.getLogger(__name__)

DEFAULT_CHILD_SYSTEM_PROMPT = (
    "Complete the following task. Be concise and focused. "
    "Return your result directly.\n\n"
    "IMPORTANT: You have tools available — check your tool list and USE them. "
    "Do NOT say you lack capabilities without first checking your available tools. "
    "When a skill below shows bash/curl commands, run them with the shell tool."
)


async def _run_child_turn(parent_ctx, task, model: str = "",
                          max_iterations: int = 0):
    """Run a child agent turn via ConversationManager, preserving the
    parent's tools, skills, and event routing.

    Args:
        model: Override model for the child. Empty = inherit parent's.
        max_iterations: Override max tool iterations. 0 = use child_max_tool_iterations.

    Returns the child's text response, or an error string on failure.
    """
    from ..conversation_manager import TurnKind  # deferred: circular dep
    from . import TOOLS  # deferred: circular dep

    config = parent_ctx.config

    # Build child system prompt: base + activated skill bodies
    activated = parent_ctx.skills.activated
    skill_map = {s.name: s for s in getattr(config, "discovered_skills", [])}
    prompt_parts = [DEFAULT_CHILD_SYSTEM_PROMPT]
    for name in sorted(activated):
        skill = skill_map.get(name)
        if skill and skill.body:
            prompt_parts.append(f"\n\n--- Skill: {name} ---\n{skill.body}")
    child_system_prompt = "\n".join(prompt_parts)

    child_config = replace(
        config,
        agent=replace(config.agent, max_tool_iterations=(
            max_iterations or config.agent.child_max_tool_iterations)),
        system_prompt=child_system_prompt,
    )
    # Children don't discover or activate skills — they inherit parent's
    child_config.discovered_skills = []

    parent_conv = getattr(parent_ctx, "conv_id", "") or getattr(parent_ctx, "channel_id", "")
    # Per-call unique conv_id; short random suffix to avoid collisions.
    child_conv_id = f"{parent_conv}--child-{secrets.token_hex(4)}"
    parent_event_id = getattr(parent_ctx, "event_context_id", "") or parent_ctx.context_id

    def setup(child_ctx):
        # Swap in the child-specific config (smaller iteration budget + child
        # system prompt). Context was already built with parent's config by
        # Context.for_task, so we overwrite here.
        child_ctx.config = child_config
        child_ctx.cancelled = getattr(parent_ctx, "cancelled", None)
        child_ctx.request_confirmation = getattr(parent_ctx, "request_confirmation", None)
        # Route child events to the parent's UI subscriber so confirmations
        # and tool progress are visible in the parent conversation.
        child_ctx.event_context_id = parent_event_id

        # Child inherits parent's tools minus delegation/activation.
        # If parent has restricted allowed_tools, respect that restriction.
        excluded = {"delegate_task", "activate_skill", "refresh_skills", "tool_search"}
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
        child_ctx.skip_vault_retrieval = True

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


async def tool_delegate_task(ctx, task: str, model: str = "") -> str | ToolResult:
    """Delegate a subtask to a child agent."""
    log.info("[tool:delegate_task] model=%s %s...", model or "inherit", task[:80])

    if not task or not task.strip():
        return ToolResult(text="[error: task description is required]")

    return await _run_child_turn(ctx, task, model=model)


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
                "Delegate a subtask to a child agent. The child runs as an "
                "independent agent turn with access to the same tools and "
                "skills. Use when a request has an independent part that can "
                "be handled separately. For parallel work, call delegate_task "
                "multiple times in the same response."
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
                },
                "required": ["task"],
            },
        },
    },
]
