"""Always-loaded workflow engine tools.

- workflow_start / list / switch / status — run lifecycle
- phase_advance — canonical transition (dynamically regenerated per turn
  with a current-phase enum + when: clause descriptions)
- workflow_artifact_read / write — scoped artifact I/O

The dynamic provider ``refresh_workflow_tools(ctx)`` is called from
tool_definitions.refresh_dynamic_tools() to inject the per-turn
phase_advance schema reflecting the current run's current phase.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..media import EndTurnConfirm, ToolResult
from ..workflow import engine, registry
from ..workflow.runs import create_run, list_runs, load_run
from ..workflow.types import PhaseKind

log = logging.getLogger(__name__)

_BASE_ADVANCE_DESC = (
    "Advance the current workflow run to its next phase. You MUST "
    "pick a target_phase_id from the enum — other values will be "
    "rejected by the engine. The 'reason' parameter is a 1-2 "
    "sentence justification for the routing choice."
)


def _get_run(ctx):
    run_id = (ctx.skills.data or {}).get("current_workflow_run")
    if not run_id:
        return None, None
    state = load_run(ctx.config.workspace_path, run_id)
    if state is None:
        return None, None
    wf = registry.get(state.workflow)
    return state, wf


def _set_current_run(ctx, run_id: str) -> None:
    if ctx.skills.data is None:
        ctx.skills.data = {}
    ctx.skills.data["current_workflow_run"] = run_id


def build_phase_advance_definition(ctx) -> dict | None:
    """Return the per-turn JSON-Schema function definition for
    phase_advance, with the enum + descriptions populated from the
    current run's current phase. Returns None when no run is active
    (the tool is hidden until a workflow starts).
    """
    state, wf = _get_run(ctx)
    if state is None or wf is None:
        return None
    phase = wf.phase(state.current_phase)
    if phase is None or not phase.next_phases:
        return None

    enum_vals = [e.id for e in phase.next_phases]
    parts = [
        f"You are currently in phase '{phase.id}' of workflow "
        f"'{wf.name}'. Pick the target that matches your situation:"
    ]
    for edge in phase.next_phases:
        when = edge.when.strip() or "(no annotation — only option)"
        parts.append(
            f"\n  - target_phase_id=\"{edge.id}\"\n"
            f"    Pick this when: {when}")
    parts.append(
        "\n\nIf you're not sure which applies, call workflow_status "
        "for a recap.")
    description = _BASE_ADVANCE_DESC + "\n\n" + "\n".join(parts)

    return {
        "type": "function",
        "function": {
            "name": "phase_advance",
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {
                    "target_phase_id": {
                        "type": "string",
                        "enum": enum_vals,
                    },
                    "reason": {
                        "type": "string",
                        "description": (
                            "Brief justification (1-2 sentences) "
                            "for choosing this target."),
                    },
                },
                "required": ["target_phase_id"],
            },
        },
    }


def refresh_workflow_tools(ctx) -> None:
    """Per-iteration dynamic refresh, called from
    tool_definitions.refresh_dynamic_tools() after the skill providers
    have run.

    Two responsibilities:

    1. **Inject the dynamic phase_advance** into ctx.tools.extra /
       ctx.tools.extra_definitions when a workflow run is active and
       its current phase has outgoing edges; remove it otherwise.

    2. **Restrict the tool catalog per phase**: when an inline phase
       is active, set ctx.tools.allowed to the union of the phase's
       declared tool whitelist, the workflow admin tools, and the
       critical-priority baseline. Clears the restriction when no
       workflow is active (only if it was set by this function).
    """
    state, wf = _get_run(ctx)

    # Always handle phase_advance injection/removal first
    definition = build_phase_advance_definition(ctx)
    if definition is None:
        if hasattr(ctx, "tools"):
            ctx.tools.extra.pop("phase_advance", None)
            ctx.tools.extra_definitions = [
                d for d in ctx.tools.extra_definitions
                if d["function"]["name"] != "phase_advance"
            ]
    else:
        ctx.tools.extra["phase_advance"] = tool_phase_advance
        ctx.tools.extra_definitions = [
            d for d in ctx.tools.extra_definitions
            if d["function"]["name"] != "phase_advance"
        ] + [definition]

    # Now handle per-phase tool catalog restriction
    phase = wf.phase(state.current_phase) if state and wf else None

    # We restrict only when in an INLINE phase. Subagent phases run
    # synchronously via dispatch from the tool layer, so the main
    # agent shouldn't normally see one. If something has us in a
    # subagent phase between iterations (e.g. ERROR / paused dispatch),
    # the safer default is NOT to restrict so the agent can recover.
    should_restrict = (
        state is not None and wf is not None
        and phase is not None
        and phase.kind == PhaseKind.INLINE
    )

    if should_restrict:
        ctx.tools.allowed = _build_phase_allowed_set(ctx, phase)
        ctx.tools.workflow_restricted = True
    elif getattr(ctx.tools, "workflow_restricted", False):
        # Only clear if WE set it — don't clobber an unrelated
        # restriction (e.g. from delegate_task's child setup).
        ctx.tools.allowed = None
        ctx.tools.workflow_restricted = False


def _build_phase_allowed_set(ctx, phase) -> set[str]:
    """Compute the tool-allowed set for an inline workflow phase:
    phase whitelist (glob-expanded) ∪ workflow admin tools ∪
    critical-priority baseline.

    Critical-priority tools (notes_*, checklist_*, etc.) are always
    included so conversation-infra tools still work inside a workflow.
    """
    import fnmatch

    from . import TOOLS as ALL_TOOLS
    from .tool_registry import get_critical_names

    all_tool_names = set(ALL_TOOLS.keys()) | set(
        getattr(ctx.tools, "extra", {}).keys())

    # 1. Phase whitelist. Literal names pass through as-is (they may
    # come from a skill that isn't activated at this exact moment but
    # could be later in the turn). Glob patterns expand against the
    # currently-registered tools.
    phase_tools: set[str] = set()
    for pat in phase.tools:
        if "*" in pat or "?" in pat:
            phase_tools |= {n for n in all_tool_names
                            if fnmatch.fnmatch(n, pat)}
        else:
            phase_tools.add(pat)

    # 2. Workflow admin baseline (always available so the agent can
    # check status, read/write artifacts, advance, etc.)
    admin = set(WORKFLOW_TOOLS.keys()) | {"phase_advance"}

    # 3. Critical-priority infra (notes_*, checklist_*) — these are
    # conversation infrastructure that should survive any restriction.
    try:
        critical = get_critical_names(ctx.config)
    except Exception:  # noqa: BLE001 — fail-open
        critical = set()

    return phase_tools | admin | critical


# --------------------------------------------------------------- tools

async def tool_workflow_start(ctx, name: str, slug: str = ""
                              ) -> str | ToolResult:
    """Create a new run of a workflow.

    If the workflow's initial phase is a subagent phase, the engine
    dispatches the subagent synchronously before this tool returns —
    the run advances to the next inline phase before the LLM sees the
    tool result. Subagent dispatch may chain if its target is also a
    subagent (bounded).
    """
    wf = registry.get(name)
    if wf is None:
        return ToolResult(
            text=f"[error: workflow '{name}' not found]")
    slug = slug or "run"
    state = create_run(
        ctx.config.workspace_path,
        workflow=name,
        slug=slug,
        initial_phase=wf.initial_phase,
    )
    _set_current_run(ctx, state.run_id)

    # If we landed on a subagent phase, dispatch synchronously so the
    # run advances past it before the LLM continues.
    state = await engine.dispatch_subagent_if_needed(ctx, state)

    return (
        f"Started workflow '{name}' (run {state.run_id}). "
        f"Current phase: {state.current_phase}. "
        f"Status: {state.status.value}. "
        f"Use phase_advance to move forward."
    )


async def tool_workflow_list(ctx, workflow: str = "",
                             status: str = "") -> str | ToolResult:
    """List workflow runs across all conversations."""
    runs = list_runs(ctx.config.workspace_path,
                     workflow=workflow, status=status)
    if not runs:
        return "No workflow runs."
    lines = ["| Run ID | Workflow | Phase | Status | Updated |",
             "| --- | --- | --- | --- | --- |"]
    for r in runs:
        lines.append(
            f"| {r.run_id} | {r.workflow} | {r.current_phase} "
            f"| {r.status.value} | {r.updated_at} |")
    return "\n".join(lines)


async def tool_workflow_switch(ctx, run_id: str) -> str | ToolResult:
    """Set the current workflow run for this conversation."""
    state = load_run(ctx.config.workspace_path, run_id)
    if state is None:
        return ToolResult(text=f"[error: run '{run_id}' not found]")
    _set_current_run(ctx, run_id)
    return f"Switched to run {run_id} (phase: {state.current_phase})."


async def tool_workflow_status(ctx) -> str | ToolResult:
    """Show the current run's state, valid next phases with when:
    annotations, and recent transition history."""
    state, wf = _get_run(ctx)
    if state is None or wf is None:
        return "No workflow run active. Use workflow_start to begin."
    phase = wf.phase(state.current_phase)
    lines = [
        f"# Workflow: {state.workflow}",
        f"**Run:** {state.run_id}",
        f"**Phase:** {state.current_phase}",
        f"**Status:** {state.status.value}",
        f"**Updated:** {state.updated_at}",
    ]
    if phase and phase.next_phases:
        lines.append("\n**Available transitions:**")
        for edge in phase.next_phases:
            when = edge.when.strip() or "(only option)"
            gated = " [gated]" if edge.gate else ""
            lines.append(f"  - `{edge.id}`{gated} — {when}")
    elif phase and phase.is_terminal:
        lines.append("\n**Terminal phase** — no transitions available.")
    if state.history:
        lines.append("\n**Recent history:**")
        for h in state.history[-5:]:
            arrow = f"{h.get('from', '∅')} → {h['to']}"
            lines.append(f"  - {arrow} ({h.get('reason', '')})")
    return "\n".join(lines)


async def tool_phase_advance(ctx, target_phase_id: str,
                              reason: str = "") -> str | ToolResult:
    """Canonical workflow transition. Dynamically gated per turn — the
    schema only allows current-phase target ids."""
    state, wf = _get_run(ctx)
    if state is None or wf is None:
        return ToolResult(text="[error: no active workflow run]")
    try:
        result = await engine.advance(
            ctx.config.workspace_path, state, target=target_phase_id,
            reason=reason)
    except ValueError as exc:
        return ToolResult(text=f"[error: {exc}]")

    if result.end_turn_signal is not None:
        confirm = result.end_turn_signal
        if not isinstance(confirm, EndTurnConfirm):
            log.warning(
                "[workflow] unexpected end_turn_signal type %r from "
                "engine.advance — expected EndTurnConfirm",
                type(confirm).__name__)
            return ToolResult(
                text="[error: unexpected gate signal type from engine]")
        run_id = state.run_id
        workspace = ctx.config.workspace_path

        async def _on_approve():
            s = load_run(workspace, run_id)
            if s is not None:
                await engine.finalize_gate_response(workspace, s,
                                                   approved=True)

        async def _on_deny():
            s = load_run(workspace, run_id)
            if s is not None:
                await engine.finalize_gate_response(workspace, s,
                                                   approved=False)

        confirm.on_approve = _on_approve
        confirm.on_deny = _on_deny
        return ToolResult(text="Submitted for review.",
                          end_turn=confirm)

    # If the transition landed on a subagent phase, dispatch
    # synchronously so the run advances past it before the LLM sees
    # the tool result.
    fresh = load_run(ctx.config.workspace_path, state.run_id)
    if fresh is not None:
        fresh = await engine.dispatch_subagent_if_needed(ctx, fresh)
        return ToolResult(
            text=f"Advanced to phase '{fresh.current_phase}' "
                 f"(status: {fresh.status.value}).",
            end_turn=False)
    return ToolResult(
        text=f"Advanced to phase '{result.new_phase}'.",
        end_turn=False)


def _resolve_artifact_path(ctx, relative_path: str) -> Path | None:
    state, _wf = _get_run(ctx)
    if state is None:
        return None
    base = (ctx.config.workspace_path / "workflows" / state.workflow
            / "runs" / state.run_id / "artifacts").resolve()
    candidate = (base / relative_path).resolve()
    try:
        candidate.relative_to(base)
    except ValueError:
        return None
    return candidate


async def tool_workflow_artifact_write(ctx, relative_path: str,
                                        content: str) -> str | ToolResult:
    """Write content to a path under the current run's artifacts/."""
    path = _resolve_artifact_path(ctx, relative_path)
    if path is None:
        state, _ = _get_run(ctx)
        if state is None:
            return ToolResult(text="[error: no active workflow run]")
        return ToolResult(
            text=f"[error: '{relative_path}' is outside the run's "
            "artifacts directory]")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return f"Wrote {len(content)} chars to {relative_path}."


async def tool_workflow_artifact_read(ctx, relative_path: str
                                       ) -> str | ToolResult:
    """Read content from a path under the current run's artifacts/."""
    path = _resolve_artifact_path(ctx, relative_path)
    if path is None:
        return ToolResult(
            text=f"[error: '{relative_path}' is outside the run's "
            "artifacts directory]")
    if not path.is_file():
        return ToolResult(text=f"[error: '{relative_path}' not found]")
    return path.read_text()


# ----------------------------------------------------- registry exports

WORKFLOW_TOOLS = {
    "workflow_start": tool_workflow_start,
    "workflow_list": tool_workflow_list,
    "workflow_switch": tool_workflow_switch,
    "workflow_status": tool_workflow_status,
    "workflow_artifact_write": tool_workflow_artifact_write,
    "workflow_artifact_read": tool_workflow_artifact_read,
    # phase_advance is dynamic — injected per turn by
    # refresh_workflow_tools when a run is active.
}

WORKFLOW_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "priority": "normal",
        "function": {
            "name": "workflow_start",
            "description": (
                "Start a new run of a workflow. The workflow must be "
                "registered (i.e., a kind:workflow skill is installed)."),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "slug": {"type": "string"},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "priority": "normal",
        "function": {
            "name": "workflow_list",
            "description": (
                "List workflow runs across all conversations. Filter "
                "by workflow name or status."),
            "parameters": {
                "type": "object",
                "properties": {
                    "workflow": {"type": "string"},
                    "status": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "priority": "normal",
        "function": {
            "name": "workflow_switch",
            "description": (
                "Set the current workflow run for this conversation."),
            "parameters": {
                "type": "object",
                "properties": {
                    "run_id": {"type": "string"},
                },
                "required": ["run_id"],
            },
        },
    },
    {
        "type": "function",
        "priority": "normal",
        "function": {
            "name": "workflow_status",
            "description": (
                "Show the current run: phase, status, valid next "
                "phases with their when: annotations, recent history."),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "priority": "normal",
        "function": {
            "name": "workflow_artifact_write",
            "description": (
                "Write content to a relative path under the current "
                "run's artifacts/ directory."),
            "parameters": {
                "type": "object",
                "properties": {
                    "relative_path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["relative_path", "content"],
            },
        },
    },
    {
        "type": "function",
        "priority": "normal",
        "function": {
            "name": "workflow_artifact_read",
            "description": (
                "Read content from a relative path under the current "
                "run's artifacts/ directory."),
            "parameters": {
                "type": "object",
                "properties": {
                    "relative_path": {"type": "string"},
                },
                "required": ["relative_path"],
            },
        },
    },
]
