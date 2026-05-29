"""Workflow-aware subagent dispatcher.

Built on the same low-level primitives as ``tools/delegate.py``'s
``_run_child_turn`` (parent_ctx.manager.enqueue_turn with a custom
setup callback), but with workflow-specific setup: the child's system
prompt is the phase body (or the activated ``subagent-skill:``), and
the child's ``tools.allowed`` is exactly the phase's tool whitelist
minus a small blocklist of orchestration tools children must never
receive.

The child runs to completion before this function returns, so the
caller (``engine.dispatch_and_finalize_subagent``) can synchronously
verify outputs and apply the auto-advance transition.
"""

from __future__ import annotations

import asyncio
import fnmatch
import logging
import secrets
from dataclasses import replace

from .types import PhaseDef, WorkflowState

log = logging.getLogger(__name__)


# Tools children must never receive, even if the phase whitelist
# includes them. Children are workflow workers, not orchestrators:
# they don't activate skills, don't fan out further sub-tasks, and
# don't manipulate workflow state.
_BLOCKED_FOR_CHILDREN = frozenset({
    # Children must not orchestrate further work
    "delegate_task", "delegate_tasks",
    # Children inherit parent's activated skill set; they don't manage it
    "activate_skill", "refresh_skills", "tool_search",
    # Children must not start or abort the workflow they run inside,
    # and must not advance the parent's transition state machine —
    # phase_advance is the dynamic transition tool injected per turn.
    "workflow_start", "workflow_abort", "workflow_status",
    "phase_advance",
})


def _resolve_phase_tools(all_names: set[str],
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
                     phase: PhaseDef) -> str:
    """Spawn a child agent to execute the phase. Returns child's text.

    Public entrypoint — tests monkeypatch this to avoid spinning up
    the real LLM client. The real implementation enqueues a
    CHILD_AGENT turn on the parent's ConversationManager with a
    setup callback that locks down the child's prompt and tools to
    the phase definition.

    If ``phase.subagent_skill`` is set, the child boots with that
    skill's body as its system prompt; otherwise, ``phase.prompt``
    (the phase markdown body) is the prompt.

    The child runs to completion before this function returns — the
    caller relies on that to verify outputs synchronously.
    """
    # Function-local: tools/__init__.py imports workflow_tools which imports
    # workflow.engine; if engine imports subagent at module level and subagent
    # imports tools at module level, the chain is:
    #   tools -> workflow_tools -> workflow.engine -> workflow.subagent -> tools
    # Python sees tools as a partial module at that point — ImportError.
    # Both this import and the `from . import subagent` in engine.py's
    # dispatch_and_finalize_subagent must remain function-local.
    from ..conversation_manager import TurnKind
    from ..tools import TOOLS

    config = ctx.config

    # Resolve the prompt: skill body if subagent-skill is set, else
    # the phase markdown body.
    skill_to_activate: str | None = phase.subagent_skill
    if skill_to_activate:
        discovered = getattr(config, "discovered_skills", []) or []
        skill_map = {s.name: s for s in discovered}
        skill = skill_map.get(skill_to_activate)
        if skill is None:
            raise ValueError(
                f"subagent-skill '{skill_to_activate}' not found in "
                "discovered skills")
        if not skill.body:
            raise ValueError(
                f"subagent-skill '{skill_to_activate}' has an empty "
                "body — skill may be lazy-loaded and not yet activated")
        prompt = skill.body
    else:
        prompt = phase.prompt or ""

    parent_conv = (getattr(ctx, "conv_id", "")
                   or getattr(ctx, "channel_id", ""))
    child_conv_id = (
        f"{parent_conv}--wf-{state.workflow}-{phase.id}-"
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
    # is exactly the phase whitelist.
    child_config.discovered_skills = []

    # Resolve the tool whitelist at setup time, against the live
    # registry plus any parent-provided extras (skill-attached tools).
    parent_extra = getattr(ctx.tools, "extra", {}) if hasattr(ctx, "tools") else {}
    all_tool_names = set(TOOLS) | set(parent_extra)
    allowed = _resolve_phase_tools(all_tool_names, phase.tools)
    allowed -= _BLOCKED_FOR_CHILDREN

    parent_event_id = (
        getattr(ctx, "event_context_id", "")
        or getattr(ctx, "context_id", "")
    )

    def setup(child_ctx):
        # Swap in the child-specific config (smaller iteration budget,
        # phase-derived system prompt). Context.for_task built the ctx
        # with the parent's config; we overwrite here.
        child_ctx.config = child_config
        child_ctx.cancelled = getattr(ctx, "cancelled", None)
        child_ctx.request_confirmation = getattr(
            ctx, "request_confirmation", None)
        # Route child events to the parent's UI subscriber so progress
        # is visible in the parent conversation.
        child_ctx.event_context_id = parent_event_id

        # Override the child's conv_id to the parent's so that
        # workflow_artifact_write (and other conv-scoped tools) called
        # from inside the subagent resolve against the parent's
        # conversations/{conv_id}/artifacts/ directory — which is where
        # verify_subagent_outputs reads after the child returns.
        # Without this override the child would write to its own
        # child_conv_id-scoped directory and the engine would never find
        # the declared outputs, causing "subagent did not produce
        # required outputs" failures.
        # The child's archive (separate JSONL) is still keyed by the
        # child_conv_id passed to manager.enqueue_turn; this override
        # only affects ctx.conv_id-dependent path resolution in tools.
        child_ctx.conv_id = getattr(ctx, "conv_id", "")

        # Lock the child to the phase whitelist (minus orchestration
        # tools). The phase loader already validated that every name
        # resolves to at least one registered tool.
        child_ctx.tools.allowed = allowed
        # Carry over the parent's dynamic (skill-attached) tool extras.
        # Skills like `tabstack` register tools via `get_tools(ctx)` on
        # the parent at activate-time; the child needs those tool
        # callables and definitions to be in its catalog, otherwise a
        # phase whitelist that references e.g. `tabstack_research`
        # would be a paper tiger (name in allowed, no implementation).
        # Pattern matches tools/delegate.py:_run_child_turn.
        child_ctx.tools.extra = dict(getattr(ctx.tools, "extra", {}))
        child_ctx.tools.extra_definitions = list(
            getattr(ctx.tools, "extra_definitions", []))
        # Carry skill data too (e.g. tabstack config) but reset the
        # activated set — children should not be able to activate new
        # skills mid-phase. `allow_tools` already excludes
        # `activate_skill`.
        child_ctx.skills.activated = set(
            getattr(ctx.skills, "activated", set()))
        child_ctx.skills.data = dict(
            getattr(ctx.skills, "data", {}))

        child_ctx.on_stream_chunk = None
        child_ctx.is_child = True
        child_ctx.skip_reflection = True
        # Workflow children get no proactive memory injection — the
        # phase prompt and any tool calls are the entire context.
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
        "phase=%s (tools=%d, timeout=%ds)",
        parent_conv, state.workflow, phase.id, len(allowed), timeout,
    )

    future = await manager.enqueue_turn(
        child_conv_id,
        kind=TurnKind.CHILD_AGENT,
        prompt=prompt,
        history=[],
        context_setup=setup,
        user_id=getattr(ctx, "user_id", ""),
    )
    try:
        result_text = await asyncio.wait_for(future, timeout=timeout)
    except asyncio.TimeoutError as exc:
        raise RuntimeError(
            f"workflow subagent timed out after {timeout}s") from exc

    return result_text or ""
