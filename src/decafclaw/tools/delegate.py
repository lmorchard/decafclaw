"""Sub-agent delegation — fork child agents for focused subtasks."""

import asyncio
import logging
from dataclasses import replace

log = logging.getLogger(__name__)

DEFAULT_CHILD_SYSTEM_PROMPT = (
    "Complete the following task. Be concise and focused. "
    "Return your result directly."
)


async def _run_child_turn(parent_ctx, task, tools, system_prompt=None):
    """Run a single child agent turn with restricted tools and config.

    Returns the child's text response, or an error string on failure.
    """
    from ..agent import run_agent_turn

    config = parent_ctx.config
    child_prompt = system_prompt or DEFAULT_CHILD_SYSTEM_PROMPT

    # Fork context with fresh ID, propagate cancel event
    parent_conv = getattr(parent_ctx, "conv_id", "") or getattr(parent_ctx, "channel_id", "")
    child_config = replace(
        config,
        max_tool_iterations=config.child_max_tool_iterations,
        system_prompt=child_prompt,
    )
    # Children don't discover or activate skills — they get a flat tool list
    child_config.discovered_skills = []
    child_ctx = parent_ctx.fork(config=child_config)
    child_ctx.conv_id = f"{parent_conv}--child-{child_ctx.context_id[:8]}"
    child_ctx.cancelled = getattr(parent_ctx, "cancelled", None)

    # Restrict tools — exclude delegate, skill tools, and confirmation-prone tools.
    # Children see a flat tool list, no skills concept.
    excluded = {"delegate", "activate_skill", "refresh_skills"}
    allowed = set(tools) - excluded
    child_ctx.allowed_tools = allowed

    # Carry over parent's activated skill tools (callables + definitions)
    child_ctx.extra_tools = getattr(parent_ctx, "extra_tools", {})
    child_ctx.extra_tool_definitions = getattr(parent_ctx, "extra_tool_definitions", [])

    # Clear skill state so children can't activate or see skills
    child_ctx.activated_skills = set()

    # No streaming for child agents
    child_ctx.on_stream_chunk = None

    timeout = config.child_timeout_sec

    try:
        result = await asyncio.wait_for(
            run_agent_turn(child_ctx, task, []),
            timeout=timeout,
        )
        return result.text if hasattr(result, "text") else str(result)
    except asyncio.TimeoutError:
        return f"[subtask timed out after {timeout}s]"
    except Exception as e:
        return f"[subtask failed: {e}]"


async def tool_delegate(ctx, tasks: list) -> str:
    """Delegate subtasks to child agents, running them concurrently."""
    log.info(f"[tool:delegate] {len(tasks)} task(s)")

    if not tasks:
        return "[error: no tasks provided]"

    # Validate
    for i, t in enumerate(tasks):
        if not isinstance(t, dict) or "task" not in t or "tools" not in t:
            return f"[error: task {i + 1} must have 'task' and 'tools' fields]"
        if not isinstance(t["task"], str) or not t["task"].strip():
            return f"[error: task {i + 1} 'task' must be a non-empty string]"
        if not isinstance(t["tools"], list) or not all(isinstance(x, str) for x in t["tools"]):
            return f"[error: task {i + 1} 'tools' must be a list of strings]"

    # Publish progress: what we're delegating
    task_summaries = []
    for i, t in enumerate(tasks):
        tools_str = ", ".join(t["tools"]) if t["tools"] else "none"
        preview = t["task"][:80] + ("..." if len(t["task"]) > 80 else "")
        task_summaries.append(f"{i + 1}. {preview} (tools: {tools_str})")
    status_msg = f"Delegating {len(tasks)} subtask(s):\n" + "\n".join(task_summaries)
    await ctx.publish("tool_status", tool_name="delegate", message=status_msg)

    if len(tasks) == 1:
        # Single task — run directly, return result
        t = tasks[0]
        result = await _run_child_turn(
            ctx, t["task"], t["tools"], t.get("system_prompt"),
        )
        return result

    # Multiple tasks — run concurrently
    coros = [
        _run_child_turn(ctx, t["task"], t["tools"], t.get("system_prompt"))
        for t in tasks
    ]
    results = await asyncio.gather(*coros, return_exceptions=True)

    parts = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            parts.append(f"Task {i + 1}: [error: {result}]")
        else:
            parts.append(f"Task {i + 1}: {result}")
    return "\n\n".join(parts)


DELEGATE_TOOLS = {
    "delegate": tool_delegate,
}

DELEGATE_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "delegate",
            "description": (
                "Delegate subtasks to child agents. Each task runs as an independent "
                "agent turn with its own tool set. Multiple tasks run concurrently. "
                "Use this when a request has independent parts that can be handled "
                "in parallel (e.g. researching multiple topics, searching different "
                "sources). Provide enough context in each task description for the "
                "child agent to work independently."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "tasks": {
                        "type": "array",
                        "description": "List of subtasks to delegate",
                        "items": {
                            "type": "object",
                            "properties": {
                                "task": {
                                    "type": "string",
                                    "description": "Task description — becomes the child agent's input",
                                },
                                "tools": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "Tool names the child agent can use",
                                },
                                "system_prompt": {
                                    "type": "string",
                                    "description": "Optional system prompt override for this child",
                                },
                            },
                            "required": ["task", "tools"],
                        },
                    },
                },
                "required": ["tasks"],
            },
        },
    },
]
