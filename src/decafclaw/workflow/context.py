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
from pathlib import Path

from . import registry
from .runs import load_run


@dataclass
class WorkflowOverlay:
    """Per-turn overlay returned by ``consult_workflow_overlay``."""

    phase_prompt_section: str
    context_profile_overrides: dict = field(default_factory=dict)
    phase_boundary: bool = False
    phase_id: str = ""
    workflow_name: str = ""
    run_id: str = ""


def _format_phase_section(state, phase, wf) -> str:
    """Render the ``<workflow_phase>`` block appended to the system prompt.

    Mirrors the existing XML-section convention (``<skill_catalog>``,
    ``<loaded_skills>``, etc.). Lists each outgoing edge with its
    ``when:`` annotation so the model has all the routing context inline
    before it reaches for ``phase_advance``.
    """
    parts = [
        f"<workflow_phase run=\"{state.run_id}\" "
        f"phase=\"{phase.id}\" kind=\"{phase.kind.value}\">",
        f"You are in phase '{phase.id}' of workflow '{wf.name}'.",
        "",
        "Phase prompt:",
        phase.prompt,
        "",
    ]
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
    """Return the workflow overlay for the current turn, or ``None``
    when no run is active.

    Fail-open: missing/corrupt run, missing workflow def, or missing
    phase all return ``None`` so the composer falls through to its
    default behavior.
    """
    skills = getattr(ctx, "skills", None)
    if skills is None:
        return None
    data = getattr(skills, "data", None) or {}
    run_id = data.get("current_workflow_run")
    if not run_id:
        return None

    workspace: Path = ctx.config.workspace_path
    state = load_run(workspace, run_id)
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
        phase_prompt_section=_format_phase_section(state, phase, wf),
        context_profile_overrides=dict(phase.context_profile),
        phase_boundary=phase_boundary,
        phase_id=phase.id,
        workflow_name=wf.name,
        run_id=state.run_id,
    )
