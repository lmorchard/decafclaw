"""Always-loaded workflow engine tools.

- workflow_start / abort / status — run lifecycle
- phase_advance — canonical transition (dynamically regenerated per turn
  with a current-phase enum + when: clause descriptions). Declared at
  priority "critical" so it stays in the active catalog even under
  deferral pressure (the previous "normal" priority caused the demo's
  "unknown tool 'phase_advance'" loop).
- workflow_artifact_read / write — scoped artifact I/O

The dynamic provider ``refresh_workflow_tools(ctx)`` is called from
tool_definitions.refresh_dynamic_tools() to inject the per-turn
phase_advance schema reflecting the current workflow's current phase.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..media import EndTurnConfirm, ToolResult
from ..workflow import engine, registry
from ..workflow.conv_state import (
    archive_workflow_state,
    artifacts_dir,
    init_workflow_state,
    load_workflow_state,
    save_workflow_state,
)
from ..workflow.types import PhaseKind, RunStatus

log = logging.getLogger(__name__)

_BASE_ADVANCE_DESC = (
    "Advance the current workflow to its next phase. You MUST "
    "pick a target_phase_id from the enum — other values will be "
    "rejected by the engine. The 'reason' parameter is a 1-2 "
    "sentence justification for the routing choice."
)


def _get_workflow(ctx):
    """Return (state, wf) or (None, None) if no workflow is active or
    the registered workflow definition is gone."""
    state = load_workflow_state(ctx)
    if state is None:
        return None, None
    wf = registry.get(state.workflow)
    if wf is None:
        return None, None
    return state, wf


async def _activate_skill_for_workflow(ctx, name: str) -> ToolResult:
    """Activate a skill required by a workflow definition.

    Delegates to the standard skill activation path so user-tier
    skills hit the same approval gate they would for a direct
    activate_skill call. Returns a ToolResult — success carries the
    skill body text; failure carries '[error: ...]'.

    Wrapped in a separate helper so tests can monkeypatch this
    function without touching tool_activate_skill internals.
    """
    from .skill_tools import tool_activate_skill
    result = await tool_activate_skill(ctx, name=name)
    if isinstance(result, ToolResult):
        return result
    return ToolResult(text=result)


def build_phase_advance_definition(ctx) -> dict | None:
    """Return the per-turn JSON-Schema function definition for
    phase_advance, with the enum + descriptions populated from the
    current workflow's current phase. Returns None when no workflow is
    active (the tool is hidden until a workflow starts).
    """
    state, wf = _get_workflow(ctx)
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
        "priority": "critical",
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
       ctx.tools.extra_definitions when a workflow is active and its
       current phase has outgoing edges; remove it otherwise.

    2. **Restrict the tool catalog per phase**: when an inline phase
       is active, set ctx.tools.allowed to the union of the phase's
       declared tool whitelist, the workflow admin tools, and the
       critical-priority baseline. Clears the restriction when no
       workflow is active (only if it was set by this function).
    """
    state, wf = _get_workflow(ctx)

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


def _render_phase_handoff(state, wf, lead_in: str) -> str:
    """Strongly-framed tool result for a workflow phase handoff.

    Used by tool_workflow_start (on first landing in an inline phase) and
    tool_phase_advance (after transitioning to a new inline phase). Gives
    the LLM enough framing to drive the phase forward in the same turn:
    phase identity, the phase body verbatim, the tool whitelist, the
    `next-phases` options with their `when:` annotations, and an
    imperative "do not stop" directive.

    Caller is responsible for the gate / DONE / ERROR branches — this
    function assumes ``state`` is in an inline phase with workflow
    status RUNNING.
    """
    phase = wf.phase(state.current_phase)
    if phase is None:
        return f"{lead_in} Current phase: {state.current_phase}."

    body = phase.prompt.strip()
    lines: list[str] = [lead_in.rstrip(".") + "."]
    lines.append("")
    lines.append(f"=== ACTIVE PHASE: '{state.current_phase}' ===")
    lines.append("")
    if body:
        lines.append("YOUR TASK FOR THIS PHASE:")
        lines.append("")
        lines.append(body)
        lines.append("")

    if phase.tools:
        lines.append("TOOLS AVAILABLE IN THIS PHASE:")
        for t in phase.tools:
            lines.append(f"  - {t}")
        lines.append("")

    if phase.next_phases:
        lines.append(
            "WHEN THE PHASE TASK IS COMPLETE, call `phase_advance` "
            "with one of these targets:")
        for edge in phase.next_phases:
            when = (edge.when or "").strip() or \
                "(only option — call this when the phase is done)"
            gated = " [REQUIRES USER REVIEW via gate]" if edge.gate else ""
            lines.append(f"  - target_phase_id=\"{edge.id}\"{gated}")
            for wl in when.splitlines():
                lines.append(f"      {wl}")
        lines.append("")
    else:
        lines.append(
            "This is a TERMINAL phase. Complete the work; no "
            "`phase_advance` call is needed — the workflow ends "
            "when this phase finishes.")
        lines.append("")

    lines.append(
        "IMPORTANT: Do not stop after reading this message. Begin "
        "executing the phase task immediately. End the turn only when "
        "the phase task is complete (then call `phase_advance`) or "
        "you need specific user input (then ask a specific question). "
        "Do not narrate what you plan to do — do it.")

    return "\n".join(lines)


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

async def tool_workflow_start(ctx, name: str) -> str | ToolResult:
    """Start a fresh workflow for the current conversation.

    Activates each skill in ``wf.required_skills`` first (errors if any
    fails); then initializes conversation-scoped state. If the initial
    phase is a subagent, dispatches it synchronously before returning.

    Errors if a workflow is already active in this conversation (the
    user must call workflow_abort first, or wait for the current
    workflow to finish).
    """
    wf = registry.get(name)
    if wf is None:
        return ToolResult(text=f"[error: workflow '{name}' not found]")

    existing = load_workflow_state(ctx)
    if existing is not None and existing.status not in (
            RunStatus.DONE, RunStatus.ERROR, RunStatus.ABORTED):
        return ToolResult(text=(
            f"[error: workflow '{existing.workflow}' is already "
            f"active in this conversation (status: "
            f"{existing.status.value}). Call workflow_abort first, or "
            f"wait for it to finish.]"))

    # Archive a previous terminal workflow so the new one starts clean.
    if existing is not None:
        archive_workflow_state(ctx)

    # Activate required skills BEFORE initializing state, so a partial
    # init doesn't leave a dead workflow.json on activation failure.
    for skill_name in wf.required_skills:
        result = await _activate_skill_for_workflow(ctx, skill_name)
        text = result.text if isinstance(result, ToolResult) else result
        if isinstance(text, str) and text.startswith("[error"):
            return ToolResult(text=(
                f"[error: required skill '{skill_name}' failed to "
                f"activate: {text}. Cannot start workflow '{name}'.]"))

    state = init_workflow_state(
        ctx, workflow=name, initial_phase=wf.initial_phase)

    # If the initial phase is a subagent, dispatch synchronously so the
    # workflow advances past it before the LLM continues.
    state = await engine.dispatch_subagent_if_needed(ctx, state)

    lead_in = f"Started workflow '{name}'."

    if state.status == RunStatus.DONE:
        return (
            f"{lead_in} The workflow reached a terminal phase "
            f"('{state.current_phase}') during startup and is complete.")
    if state.status == RunStatus.ERROR:
        return ToolResult(text=(
            f"[error: workflow '{name}' errored during startup: "
            f"{state.error or 'unknown'}]"))
    if state.status == RunStatus.PAUSED_SUBAGENT:
        # Dispatcher didn't complete — surface for diagnosis.
        return ToolResult(text=(
            f"[error: subagent dispatch did not complete for workflow "
            f"'{name}': {state.error or 'unknown'}]"))

    return _render_phase_handoff(state, wf, lead_in)


async def tool_workflow_abort(ctx, reason: str = "") -> str | ToolResult:
    """Abort the current workflow in this conversation.

    Marks the workflow as aborted, archives its workflow.json to
    workflow-<timestamp>.json in the same directory, and clears the
    conversation's active-workflow state. Artifacts remain on disk
    for reference but the workflow is no longer the conversation's
    active context.
    """
    state = load_workflow_state(ctx)
    if state is None:
        return ToolResult(text="[error: no workflow active to abort]")

    state.status = RunStatus.ABORTED
    state.error = reason.strip() or "user aborted"
    save_workflow_state(ctx, state)
    archive_workflow_state(ctx)
    return (
        f"Aborted workflow '{state.workflow}' "
        f"(was at phase '{state.current_phase}'). "
        f"Reason: {state.error}"
    )


async def tool_workflow_status(ctx) -> str | ToolResult:
    """Show the current workflow's state, valid next phases with when:
    annotations, and recent transition history."""
    state, wf = _get_workflow(ctx)
    if state is None or wf is None:
        return ("No workflow active in this conversation. "
                "Use workflow_start to begin.")
    phase = wf.phase(state.current_phase)
    lines = [
        f"# Workflow: {state.workflow}",
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
    state, wf = _get_workflow(ctx)
    if state is None or wf is None:
        return ToolResult(text="[error: no active workflow]")
    prior_phase = state.current_phase
    try:
        result = await engine.advance(
            ctx, state, target=target_phase_id, reason=reason)
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

        async def _on_approve():
            s = load_workflow_state(ctx)
            if s is not None:
                await engine.finalize_gate_response(ctx, s,
                                                   approved=True)

        async def _on_deny():
            s = load_workflow_state(ctx)
            if s is not None:
                await engine.finalize_gate_response(ctx, s,
                                                   approved=False)

        confirm.on_approve = _on_approve
        confirm.on_deny = _on_deny
        return ToolResult(text="Submitted for review.",
                          end_turn=confirm)

    # If the transition landed on a subagent phase, dispatch
    # synchronously so the workflow advances past it before the LLM
    # sees the tool result.
    fresh = load_workflow_state(ctx)
    if fresh is None:
        return ToolResult(
            text=f"Advanced to phase '{result.new_phase}'.",
            end_turn=False)
    fresh = await engine.dispatch_subagent_if_needed(ctx, fresh)

    lead_in = f"Advanced from phase '{prior_phase}' to '{fresh.current_phase}'."

    if fresh.status == RunStatus.DONE:
        return ToolResult(
            text=(f"{lead_in} Workflow '{fresh.workflow}' is complete "
                  f"(terminal phase)."),
            end_turn=False)
    if fresh.status == RunStatus.ERROR:
        return ToolResult(
            text=(f"[error: workflow '{fresh.workflow}' errored after "
                  f"transition to '{fresh.current_phase}': "
                  f"{fresh.error or 'unknown'}]"),
            end_turn=False)
    if fresh.status == RunStatus.PAUSED_SUBAGENT:
        return ToolResult(
            text=(f"[error: subagent dispatch did not complete after "
                  f"advance to '{fresh.current_phase}': "
                  f"{fresh.error or 'unknown'}]"),
            end_turn=False)

    return ToolResult(
        text=_render_phase_handoff(fresh, wf, lead_in),
        end_turn=False)


def _resolve_artifact_path(ctx, relative_path: str) -> Path | None:
    state, _wf = _get_workflow(ctx)
    if state is None:
        return None
    base = artifacts_dir(ctx).resolve()
    candidate = (base / relative_path).resolve()
    try:
        candidate.relative_to(base)
    except ValueError:
        return None
    return candidate


async def tool_workflow_artifact_write(ctx, relative_path: str,
                                        content: str) -> str | ToolResult:
    """Write content to a path under the current workflow's artifacts/."""
    path = _resolve_artifact_path(ctx, relative_path)
    if path is None:
        state, _ = _get_workflow(ctx)
        if state is None:
            return ToolResult(text="[error: no active workflow]")
        return ToolResult(
            text=f"[error: '{relative_path}' is outside the workflow's "
            "artifacts directory]")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return f"Wrote {len(content)} chars to {relative_path}."


async def tool_workflow_artifact_read(ctx, relative_path: str
                                       ) -> str | ToolResult:
    """Read content from a path under the current workflow's artifacts/."""
    path = _resolve_artifact_path(ctx, relative_path)
    if path is None:
        return ToolResult(
            text=f"[error: '{relative_path}' is outside the workflow's "
            "artifacts directory]")
    if not path.is_file():
        return ToolResult(text=f"[error: '{relative_path}' not found]")
    return path.read_text()


# ----------------------------------------------------- registry exports

WORKFLOW_TOOLS = {
    "workflow_start": tool_workflow_start,
    "workflow_status": tool_workflow_status,
    "workflow_abort": tool_workflow_abort,
    "workflow_artifact_write": tool_workflow_artifact_write,
    "workflow_artifact_read": tool_workflow_artifact_read,
    # phase_advance is dynamic — injected per turn by
    # refresh_workflow_tools when a workflow is active.
}

WORKFLOW_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "priority": "normal",
        "function": {
            "name": "workflow_start",
            "description": (
                "Start a fresh workflow in the current conversation. "
                "Activates the workflow's required-skills first, then "
                "initializes per-conversation state."),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "priority": "normal",
        "function": {
            "name": "workflow_status",
            "description": (
                "Show the current workflow: phase, status, valid next "
                "phases with their when: annotations, recent history."),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "priority": "normal",
        "function": {
            "name": "workflow_abort",
            "description": (
                "Abort the currently-active workflow in this "
                "conversation. Archives state and clears the active-"
                "workflow context. Errors if no workflow is active."),
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "Why the workflow is being aborted.",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "priority": "normal",
        "function": {
            "name": "workflow_artifact_write",
            "description": (
                "Write content to a relative path under the current "
                "workflow's artifacts/ directory."),
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
                "workflow's artifacts/ directory."),
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
