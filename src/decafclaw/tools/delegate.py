"""Sub-agent delegation — fork child agents for focused subtasks."""

import asyncio
import logging
from dataclasses import replace

from ..config import EFFORT_LEVELS
from ..media import ToolResult

log = logging.getLogger(__name__)

DEFAULT_CHILD_SYSTEM_PROMPT = (
    "Complete the following task. Be concise and focused. "
    "Return your result directly.\n\n"
    "IMPORTANT: You have tools available — check your tool list and USE them. "
    "Do NOT say you lack capabilities without first checking your available tools. "
    "When a skill below shows bash/curl commands, run them with the shell tool."
)


async def _run_child_turn(parent_ctx, task, effort: str = "",
                          max_iterations: int = 0):
    """Run a single child agent turn, inheriting parent's tools and skills.

    Args:
        effort: Override effort level for the child. Empty = inherit parent's.
        max_iterations: Override max tool iterations. 0 = use child_max_tool_iterations.

    Returns the child's text response, or an error string on failure.
    """
    from ..agent import run_agent_turn  # deferred: circular dep
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

    # Fork context with fresh ID, propagate cancel event
    parent_conv = getattr(parent_ctx, "conv_id", "") or getattr(parent_ctx, "channel_id", "")
    child_config = replace(
        config,
        agent=replace(config.agent, max_tool_iterations=(
            max_iterations or config.agent.child_max_tool_iterations)),
        system_prompt="\n".join(prompt_parts),
    )
    # Children don't discover or activate skills — they inherit parent's
    child_config.discovered_skills = []
    child_ctx = parent_ctx.fork(config=child_config)
    child_ctx.conv_id = f"{parent_conv}--child-{child_ctx.context_id[:8]}"
    child_ctx.cancelled = getattr(parent_ctx, "cancelled", None)

    # Route child events to the parent's UI subscriber so confirmations
    # and tool progress are visible in the parent conversation
    parent_event_id = getattr(parent_ctx, "event_context_id", "") or parent_ctx.context_id
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
    child_ctx.skip_memory_context = True

    # Set effort level: explicit override > parent's level > default
    child_ctx.effort = effort if effort else getattr(parent_ctx, "effort", "default")

    timeout = config.agent.child_timeout_sec

    try:
        result = await asyncio.wait_for(
            run_agent_turn(child_ctx, task, []),
            timeout=timeout,
        )
        return result.text if hasattr(result, "text") else str(result)
    except asyncio.TimeoutError:
        return ToolResult(text=f"[error: subtask timed out after {timeout}s]")
    except Exception as e:
        return ToolResult(text=f"[error: subtask failed: {e}]")


async def tool_delegate_task(ctx, task: str, effort: str = "") -> str | ToolResult:
    """Delegate a subtask to a child agent."""
    log.info(f"[tool:delegate_task] effort={effort or 'inherit'} {task[:80]}...")

    if not task or not task.strip():
        return ToolResult(text="[error: task description is required]")

    return await _run_child_turn(ctx, task, effort=effort)


DELEGATE_TOOLS = {
    "delegate_task": tool_delegate_task,
}

DELEGATE_TOOL_DEFINITIONS = [
    {
        "type": "function",
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
                    "effort": {
                        "type": "string",
                        "enum": sorted(EFFORT_LEVELS),
                        "description": (
                            "Effort level for the subtask. 'fast' for "
                            "procedural/compliant tasks, 'strong' for "
                            "complex reasoning. Omit to inherit parent's level."
                        ),
                    },
                },
                "required": ["task"],
            },
        },
    },
]
