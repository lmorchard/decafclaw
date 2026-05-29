"""ContextComposer integration for workflow runs.

The composer calls ``consult_workflow_overlay(ctx)`` once per compose().
The overlay returns:

- the phase-prompt section to append to the system prompt, formatted
  as a ``<workflow_phase>`` XML block matching the existing system-prompt
  section convention;
- a dict of context-profile overrides to apply during composition (e.g.
  ``memory-retrieval: off``, ``notes-injection: off``);
- a phase-boundary flag for tool-result clearing.

The overlay is consulted only in INTERACTIVE mode (the only mode that
drives workflow turns). When no workflow run is active, returns ``None``
and the composer behaves as if the workflow engine weren't present.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import registry
from .conv_state import load_workflow_state
from .types import PhaseKind, RunStatus


@dataclass
class WorkflowOverlay:
    """Per-turn overlay returned by ``consult_workflow_overlay``."""

    phase_prompt_section: str
    context_profile_overrides: dict = field(default_factory=dict)
    phase_boundary: bool = False
    phase_id: str = ""
    workflow_name: str = ""
    run_id: str = ""


def _format_phase_section(state, phase, wf, run_id: str = "") -> str:
    """Render the ``<workflow_phase>`` block appended to the system prompt.

    ``run_id`` is the identifier rendered into the XML opening tag —
    callers pass ``ctx.conv_id`` (the conv-scoped identifier in the
    new architecture).

    For INLINE phases: includes the phase prompt body so the LLM has
    its phase-specific instructions, plus a list of outgoing edges with
    their ``when:`` annotations.

    For SUBAGENT phases: shows a status note rather than the prompt
    body — the prompt is meant for the subagent worker, not for the
    main agent. The main agent shouldn't normally see a subagent phase
    here (engine dispatches synchronously from the tool layer), but if
    dispatch failed or the run is paused, this defensive rendering
    keeps the main agent oriented toward recovery rather than running
    the subagent's instructions itself.
    """
    parts = [
        f"<workflow_phase run=\"{run_id}\" "
        f"phase=\"{phase.id}\" kind=\"{phase.kind.value}\" "
        f"status=\"{state.status.value}\">",
        f"You are in phase '{phase.id}' of workflow '{wf.name}'.",
        "",
    ]

    if phase.kind == PhaseKind.SUBAGENT:
        parts.extend([
            "This is a SUBAGENT phase — the engine dispatches a child",
            "worker to run its prompt and write declared outputs. The",
            "phase body is NOT your instructions; do not act on it.",
            "",
            "If you are seeing this block, dispatch did not complete",
            "synchronously. Likely cases:",
            f"  - status is '{RunStatus.ERROR.value}': subagent crashed",
            "    or produced incomplete outputs. Use workflow_status",
            "    for the error message and consider phase_advance to a",
            "    recovery target if one is declared, or workflow_abort",
            "    to abandon this run.",
            f"  - status is '{RunStatus.PAUSED_SUBAGENT.value}': dispatch",
            "    is still in progress (rare — should resolve before",
            "    your turn). Wait for the next event.",
            "",
        ])
    else:
        parts.extend([
            "Phase prompt:",
            phase.prompt,
            "",
        ])

    if phase.next_phases:
        parts.append("Available transitions (use phase_advance):")
        for edge in phase.next_phases:
            when = edge.when.strip() or "(only option)"
            gated = " [gated]" if edge.gate else ""
            parts.append(f"  - {edge.id}{gated} - {when}")
        parts.append("")
        parts.append(
            "No other transition targets are available from this phase."
        )
    else:
        parts.append(
            "This is a terminal phase - no further transitions are possible."
        )
    parts.append("</workflow_phase>")
    return "\n".join(parts)


def consult_workflow_overlay(ctx) -> WorkflowOverlay | None:
    """Return the workflow overlay for the current conversation, or
    ``None`` when no workflow is active.

    Fail-open: missing/corrupt state, missing workflow def, or missing
    phase all return ``None`` so the composer falls through to its
    default behavior.
    """
    state = load_workflow_state(ctx)
    if state is None:
        return None
    wf = registry.get(state.workflow)
    if wf is None:
        return None
    phase = wf.phase(state.current_phase)
    if phase is None:
        return None

    phase_boundary = bool(
        phase.context_profile.get("clear-prior-phase-tools", True)
    )

    return WorkflowOverlay(
        phase_prompt_section=_format_phase_section(
            state, phase, wf, run_id=ctx.conv_id),
        context_profile_overrides=dict(phase.context_profile),
        phase_boundary=phase_boundary,
        phase_id=phase.id,
        workflow_name=wf.name,
        run_id=ctx.conv_id,  # conv_id serves as the run identifier
    )
