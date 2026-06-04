"""Workflow-aware subagent dispatcher.

Built on the same low-level primitives as ``tools/delegate.py``'s
``_run_child_turn`` (parent_ctx.manager.enqueue_turn with a custom
setup callback), but with workflow-specific setup: the child's system
prompt is the step prompt (or the activated ``skill:``), and the
child's ``tools.allowed`` is exactly the step's tool whitelist minus a
small blocklist of orchestration tools children must never receive.

The child runs to completion before ``run_subagent_step`` returns
(awaited with a configurable timeout), so the caller can synchronously
verify outputs and apply the step transition.

Public entry: ``run_subagent_step(ctx, step, state) -> SubagentResult``
"""

from __future__ import annotations

import asyncio
import fnmatch
import logging
import secrets
from dataclasses import dataclass, replace

from ..archive import read_archive
from .conv_state import artifacts_dir
from .types import (
    StepDef,
    StepKind,
    WorkflowState,
)

log = logging.getLogger(__name__)


# Tools children must never receive, even if the step whitelist
# includes them. Children are workflow workers, not orchestrators:
# they don't activate skills, don't fan out further sub-tasks, and
# don't manipulate workflow state.
_BLOCKED_FOR_CHILDREN = frozenset({
    # Children must not orchestrate further work
    "delegate_task", "delegate_tasks",
    # Children inherit parent's activated skill set; they don't manage it
    "activate_skill", "refresh_skills", "tool_search",
    # Children must not start or abort the workflow they run inside.
    "workflow_start", "workflow_abort", "workflow_status",
    # phase_advance is the old engine's dynamic transition tool; blocked
    # here for completeness in case old tools are in the registry.
    "phase_advance",
})


@dataclass
class SubagentResult:
    """Result from run_subagent_step.

    ``suspended`` is False in all current code paths — the child always
    awaits synchronously. It is included to support a future async
    resumption model where the child runs as a background turn and the
    engine is woken when it completes.
    """
    suspended: bool
    child_conv_id: str
    text: str
    output_paths: dict[str, str]   # filename → relative path in artifacts/


def _latest_parent_user_message(parent_ctx) -> str:
    """Best-effort: pull the parent's most-recent user-role message text.

    Used to convey the user's task (e.g. the workflow topic) to a
    subagent that would otherwise have no way to receive it. Falls back
    to "" on any failure (no archive, malformed entry, IO error).
    """
    parent_conv = getattr(parent_ctx, "conv_id", "") or ""
    config = getattr(parent_ctx, "config", None)
    if not parent_conv or config is None:
        return ""
    try:
        messages = read_archive(config, parent_conv)
    except Exception as exc:  # noqa: BLE001 — fail-open
        log.debug("[workflow] couldn't read parent archive for "
                  "subagent context: %s", exc)
        return ""
    for m in reversed(messages):
        if m.get("role") != "user":
            continue
        text = m.get("content") or ""
        if isinstance(text, str) and text.strip():
            return text.strip()
    return ""


def _render_subagent_user_prompt(parent_ctx, state: WorkflowState,
                                  step: StepDef, body: str) -> str:
    """Wrap the step body (or skill body) with strong framing for the
    child's user-role initial message.

    Three jobs:

    1. **Strong framing** — explicit "you are a subagent, no human to
       query, execute the task, do not narrate" directive.

    2. **Topic passing** — inject the parent's most-recent user message
       so the child can pick up the actual research topic (or
       equivalent input).

    3. **Output checklist** — list the declared ``outputs:`` so the LLM
       has a concrete "you are done when" signal beyond the procedural
       prose of the step body.
    """
    user_msg = _latest_parent_user_message(parent_ctx)
    outputs = step.config.get("outputs") or []

    parts = [
        f"=== WORKFLOW SUBAGENT — step '{step.id}' of '{state.workflow}' ===",
        "",
        "You are an autonomous subagent. There is no human user "
        "reachable during this turn. Do not ask questions or wait "
        "for clarification — act on the information provided below.",
        "",
    ]

    if user_msg:
        parts.extend([
            "PARENT AGENT'S MOST-RECENT USER MESSAGE (use this as the "
            "input/topic for the step task):",
            "",
            "```",
            user_msg,
            "```",
            "",
        ])

    parts.extend([
        "YOUR TASK FOR THIS STEP:",
        "",
        body.strip(),
        "",
    ])

    if outputs:
        parts.append(
            "REQUIRED OUTPUTS (call `workflow_artifact_write` once per "
            "file; paths are relative to the workflow's `artifacts/` "
            "root, so pass them exactly as listed):")
        for o in outputs:
            parts.append(f'  - relative_path="{step.id}/{o}"')
        parts.append("")

    parts.append(
        "IMPORTANT: Do not narrate what you plan to do — call the "
        "tools and do it. Do not end the turn without producing the "
        "required outputs. If a tool you'd prefer is unavailable, "
        "make a reasonable substitute (e.g. write a brief stub note "
        "via `workflow_artifact_write` rather than ending with no "
        "tool call). Begin now.")

    return "\n".join(parts)


def _resolve_step_tools(all_names: set[str],
                        patterns: list[str]) -> set[str]:
    """Expand glob patterns against the live tool registry.

    Exact names that don't appear in the registry are silently
    dropped — they'll surface as a load-time loader error elsewhere,
    not at dispatch time.
    """
    if not patterns:
        return set()
    matched: set[str] = set()
    for pat in patterns:
        if "*" in pat or "?" in pat or "[" in pat:
            matched |= {n for n in all_names if fnmatch.fnmatch(n, pat)}
        elif pat in all_names:
            matched.add(pat)
    return matched


async def _run_child(*, ctx, state: WorkflowState,
                     step: StepDef, prompt: str) -> tuple[str, str]:
    """Spawn a child agent to execute the step.

    Returns ``(child_conv_id, result_text)`` so the caller has a single
    source of truth for the child's conversation ID — no second token
    generation needed.

    Public-ish entry for tests — monkeypatching this avoids spinning up
    the real LLM client. The real implementation enqueues a CHILD_AGENT
    turn on the parent's ConversationManager with a setup callback that
    locks down the child's prompt and tools to the step definition.

    The child runs to completion before this function returns — the
    caller relies on that to verify outputs synchronously.
    """
    # Function-local: tools/__init__.py imports workflow_tools which imports
    # workflow.engine; if engine imports subagent at module level and subagent
    # imports tools at module level, the chain is a circular import.
    # Both this import and the one in step_executors must remain function-local.
    from ..conversation_manager import TurnKind  # noqa: PLC0415
    from ..tools import TOOLS  # noqa: PLC0415

    config = ctx.config
    tools_list: list[str] = step.config.get("tools") or []

    # Resolve the tool whitelist at setup time, against the live registry
    # plus any parent-provided extras (skill-attached tools).
    parent_extra = getattr(ctx.tools, "extra", {}) if hasattr(ctx, "tools") else {}
    all_tool_names = set(TOOLS) | set(parent_extra)
    allowed = _resolve_step_tools(all_tool_names, tools_list)
    allowed -= _BLOCKED_FOR_CHILDREN

    parent_conv = (getattr(ctx, "conv_id", "")
                   or getattr(ctx, "channel_id", ""))
    child_conv_id = (
        f"{parent_conv}--wf-{state.workflow}-{step.id}-"
        f"{secrets.token_hex(4)}"
    )

    child_config = replace(
        config,
        agent=replace(
            config.agent,
            max_tool_iterations=config.agent.child_max_tool_iterations,
        ),
        system_prompt=prompt,
    )
    # Children don't discover or activate skills — their tool surface
    # is exactly the step whitelist.
    child_config.discovered_skills = []

    parent_event_id = (
        getattr(ctx, "event_context_id", "")
        or getattr(ctx, "context_id", "")
    )

    def setup(child_ctx):
        # Swap in the child-specific config (smaller iteration budget,
        # step-derived system prompt).
        child_ctx.config = child_config
        child_ctx.cancelled = getattr(ctx, "cancelled", None)
        child_ctx.request_confirmation = getattr(
            ctx, "request_confirmation", None)
        # Route child events to the parent's UI subscriber.
        child_ctx.event_context_id = parent_event_id

        # Override the child's conv_id to the parent's so that
        # workflow_artifact_write (and other conv-scoped tools) called
        # from inside the subagent resolve against the parent's
        # conversations/{conv_id}/artifacts/ directory.
        # The child's archive (separate JSONL) is still keyed by the
        # child_conv_id passed to manager.enqueue_turn; this override
        # only affects ctx.conv_id-dependent path resolution in tools.
        child_ctx.conv_id = getattr(ctx, "conv_id", "")

        # Lock the child to the step whitelist (minus orchestration tools).
        child_ctx.tools.allowed = allowed
        # Carry over the parent's dynamic (skill-attached) tool extras.
        child_ctx.tools.extra = dict(getattr(ctx.tools, "extra", {}))
        child_ctx.tools.extra_definitions = list(
            getattr(ctx.tools, "extra_definitions", []))
        # Carry skill data too but reset the activated set — children
        # should not activate new skills mid-step.
        child_ctx.skills.activated = set(
            getattr(ctx.skills, "activated", set()))
        child_ctx.skills.data = dict(
            getattr(ctx.skills, "data", {}))

        child_ctx.on_stream_chunk = None
        child_ctx.is_child = True
        child_ctx.skip_reflection = True
        # Workflow children get no proactive memory injection.
        child_ctx.skip_vault_retrieval = True

        # Inherit the parent's active model unless overridden.
        child_ctx.active_model = getattr(ctx, "active_model", "") or ""

    manager = getattr(ctx, "manager", None)
    if manager is None:
        raise RuntimeError(
            "workflow subagent dispatch requires a "
            "ConversationManager; no manager on parent ctx")

    timeout = config.agent.child_timeout_sec

    log.info(
        "[workflow] dispatching subagent for conv=%s workflow=%s "
        "step=%s (tools=%d, timeout=%ds)",
        parent_conv, state.workflow, step.id, len(allowed), timeout,
    )

    kickoff_prompt = _render_subagent_user_prompt(
        parent_ctx=ctx, state=state, step=step, body=prompt)

    future = await manager.enqueue_turn(
        child_conv_id,
        kind=TurnKind.CHILD_AGENT,
        prompt=kickoff_prompt,
        history=[],
        context_setup=setup,
        user_id=getattr(ctx, "user_id", ""),
    )
    try:
        result_text = await asyncio.wait_for(future, timeout=timeout)
    except asyncio.TimeoutError as exc:
        raise RuntimeError(
            f"workflow subagent timed out after {timeout}s") from exc

    return child_conv_id, result_text or ""


def _verify_subagent_outputs(ctx, step: StepDef) -> dict[str, str]:
    """Verify that declared output files exist in the artifacts dir.

    Returns a dict mapping each filename to its relative path
    (relative to workspace root) for storage in state.

    Raises RuntimeError if any declared output is missing.
    """
    outputs: list[str] = step.config.get("outputs") or []
    if not outputs:
        return {}

    art_dir = artifacts_dir(ctx)
    step_dir = art_dir / step.id
    output_paths: dict[str, str] = {}

    missing = []
    for filename in outputs:
        path = step_dir / filename
        if not path.is_file():
            missing.append(str(path))
        else:
            # Store as relative path from workspace root
            try:
                rel = path.relative_to(ctx.config.workspace_path)
            except ValueError:
                rel = path
            output_paths[filename] = str(rel)

    if missing:
        raise RuntimeError(
            f"subagent step '{step.id}' did not produce required "
            f"outputs: {missing!r}"
        )

    return output_paths


async def run_subagent_step(
    ctx,
    *,
    state: WorkflowState,
    step_id: str,
    skill: str | None,
    tools: list[str],
    outputs: list[str],
    context_profile: dict,
    prompt: str,
) -> SubagentResult:
    """Public entry point for the subagent step executor.

    Dispatches a child agent loop, waits for it to complete, verifies
    declared output files exist, and returns a SubagentResult.

    This always runs synchronously (suspended=False) — the child awaits
    completion before returning. The suspended=True path is reserved for
    a future async resumption model.

    Parameters
    ----------
    ctx:
        Parent context (must have a ConversationManager attached).
    state:
        Current workflow state (used for framing the child prompt).
    step_id:
        The step ID — used to look up the StepDef from state for framing.
    skill:
        Skill name to load as the child's system prompt body. If None,
        ``prompt`` is used as the system prompt directly.
    tools:
        List of tool names (or glob patterns) to whitelist for the child.
    outputs:
        Declared output filenames (relative to step's artifacts subdir).
    context_profile:
        Currently unused but passed through for future context-profile
        overrides (e.g. memory-retrieval: off).
    prompt:
        The rendered step prompt — used as the child's system prompt if
        ``skill`` is None, and always included in the kickoff message.
    """
    # Reconstruct a minimal StepDef for _run_child / framing helpers.
    # We use only config fields that subagent internals need.
    step = StepDef(
        id=step_id,
        kind=StepKind.SUBAGENT,
        config={
            "prompt": prompt,
            "skill": skill,
            "tools": tools,
            "outputs": outputs,
            "context-profile": context_profile,
        },
    )

    # Resolve the system prompt: skill body if skill is set, else prompt.
    system_prompt: str = prompt
    if skill:
        config = ctx.config
        discovered = getattr(config, "discovered_skills", []) or []
        skill_map = {s.name: s for s in discovered}
        skill_def = skill_map.get(skill)
        if skill_def is None:
            raise ValueError(
                f"subagent skill '{skill}' not found in discovered skills")
        if not skill_def.body:
            raise ValueError(
                f"subagent skill '{skill}' has an empty body — "
                "skill may be lazy-loaded and not yet activated")
        system_prompt = skill_def.body

    # child_conv_id comes from _run_child — single source of truth so
    # SubagentResult.child_conv_id matches the actual child conversation.
    child_conv_id, result_text = await _run_child(
        ctx=ctx, state=state, step=step, prompt=system_prompt)

    output_paths = _verify_subagent_outputs(ctx, step)

    return SubagentResult(
        suspended=False,
        child_conv_id=child_conv_id,
        text=result_text,
        output_paths=output_paths,
    )
