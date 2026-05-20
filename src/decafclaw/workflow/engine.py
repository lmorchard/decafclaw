"""Workflow state-machine operations.

advance() is the canonical transition entrypoint. Gate dispatch returns
an EndTurnConfirm; finalize_gate_response completes the gated edge once
the user has answered. verify_subagent_outputs is called by the
subagent dispatcher after a child completes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from ..media import EndTurnConfirm
from . import registry
from .runs import _now_iso, load_run, run_lock, save_run
from .types import (
    EdgeDef,
    PhaseDef,
    PhaseKind,
    RunState,
    RunStatus,
    WorkflowDef,
)

log = logging.getLogger(__name__)


@dataclass
class AdvanceResult:
    """Returned by advance(): new phase, optional end_turn signal.

    end_turn_signal is None for non-gated transitions, or an
    EndTurnConfirm for gated edges (caller wires on_approve/on_deny
    to finalize_gate_response and surfaces the buttons).
    """

    new_phase: str
    end_turn_signal: EndTurnConfirm | None = None


def _find_edge(phase: PhaseDef, target: str) -> tuple[int, EdgeDef] | None:
    for i, edge in enumerate(phase.next_phases):
        if edge.id == target:
            return i, edge
    return None


async def advance(workspace: Path, state: RunState, target: str,
                  reason: str) -> AdvanceResult:
    """Advance the run along the matching edge.

    If the edge has a gate, returns an AdvanceResult with an
    EndTurnConfirm in end_turn_signal — the caller surfaces the
    buttons. Otherwise applies the transition and persists.
    """
    wf = registry.get(state.workflow)
    if wf is None:
        raise ValueError(f"workflow '{state.workflow}' not registered")

    async with run_lock(state.run_id):
        phase = wf.phase(state.current_phase)
        if phase is None:
            raise ValueError(
                f"current phase '{state.current_phase}' not in workflow")
        found = _find_edge(phase, target)
        if found is None:
            valid = ", ".join(e.id for e in phase.next_phases) or "(none)"
            raise ValueError(
                f"'{target}' is not a valid next phase from "
                f"'{state.current_phase}'. Valid: {valid}")
        edge_idx, edge = found

        if edge.gate is not None:
            return _enter_gate(workspace, state, edge_idx, edge, reason)

        return _apply_transition(
            workspace, wf, state, edge_idx, target, reason,
            gate_response=None)


def _enter_gate(workspace: Path, state: RunState, edge_idx: int,
                edge: EdgeDef, reason: str) -> AdvanceResult:
    gate = edge.gate
    assert gate is not None
    on_deny = gate.on_deny or state.current_phase
    state.status = RunStatus.PAUSED_GATE
    state.pending_gate = {"edge_target": edge.id, "on_deny": on_deny}
    save_run(workspace, state)

    confirm = EndTurnConfirm(
        message=gate.message,
        approve_label=gate.approve_label,
        deny_label=gate.deny_label,
        on_approve=None,  # filled in by tool layer with finalize_gate_response
        on_deny=None,
    )
    return AdvanceResult(new_phase=state.current_phase,
                         end_turn_signal=confirm)


async def finalize_gate_response(workspace: Path, state: RunState,
                                 approved: bool) -> AdvanceResult:
    """Apply a gate's approve/deny response and resume.

    Re-loads state from disk inside the lock to avoid TOCTOU between
    the caller's load_run and the lock acquisition: a concurrent
    advance could otherwise have cleared pending_gate already.
    """
    wf = registry.get(state.workflow)
    if wf is None:
        raise ValueError(f"workflow '{state.workflow}' not registered")

    async with run_lock(state.run_id):
        fresh = load_run(workspace, state.run_id)
        if fresh is None:
            raise ValueError(f"run '{state.run_id}' not found")
        if fresh.status != RunStatus.PAUSED_GATE \
                or fresh.pending_gate is None:
            raise ValueError("run is not paused on a gate")
        state = fresh

        # Guard above ensures pending_gate is non-None; assign to a
        # local so pyright can narrow the type for the dict accesses.
        pending = state.pending_gate
        assert pending is not None  # invariant from guard above
        target = pending["edge_target"] if approved else pending["on_deny"]
        phase = wf.phase(state.current_phase)
        if phase is None:
            raise ValueError(
                f"current phase '{state.current_phase}' not in workflow")
        edge_idx = -1
        for i, e in enumerate(phase.next_phases):
            if e.id == pending["edge_target"]:
                edge_idx = i
                break
        if edge_idx < 0:
            raise ValueError(
                f"gate edge target '{pending['edge_target']}' is no "
                f"longer in phase '{state.current_phase}' — workflow "
                "definition changed mid-run?")
        state.pending_gate = None
        return _apply_transition(
            workspace, wf, state, edge_idx, target,
            reason=("user approved" if approved else "user denied"),
            gate_response=("approved" if approved else "denied"))


def _apply_transition(workspace: Path, wf: WorkflowDef,
                      state: RunState, edge_idx: int, target: str,
                      reason: str, gate_response: str | None
                      ) -> AdvanceResult:
    prev = state.current_phase
    next_phase = wf.phase(target)
    if next_phase is None:
        raise ValueError(
            f"transition target '{target}' not in workflow")
    state.current_phase = target
    state.history.append({
        "from": prev,
        "to": target,
        "edge_index": edge_idx if edge_idx >= 0 else None,
        "gate_response": gate_response,
        "reason": reason,
        "timestamp": _now_iso(),
    })
    if next_phase.is_terminal:
        state.status = RunStatus.DONE
    elif next_phase.kind == PhaseKind.SUBAGENT:
        state.status = RunStatus.PAUSED_SUBAGENT
    else:
        state.status = RunStatus.RUNNING
    save_run(workspace, state)
    return AdvanceResult(new_phase=target, end_turn_signal=None)


async def dispatch_and_finalize_subagent(ctx, workspace: Path,
                                         state: RunState,
                                         phase_id: str) -> None:
    """Run a subagent phase end-to-end: spawn the child, verify its
    outputs, and either auto-advance along the single ``next-phases``
    edge or set ``RunStatus.ERROR``.

    Called by the tool layer (workflow_advance) when a transition
    lands on a SUBAGENT phase. Holds the per-run lock for the full
    dispatch + verify + advance sequence so concurrent operations on
    the same run serialize correctly.

    Failure modes (all persisted to ``state.error``):
    - workflow / phase not registered: ``ValueError`` propagated to caller
    - child crashes / times out: ``RunStatus.ERROR``, current_phase unchanged
    - declared outputs missing after child returns: ``RunStatus.ERROR``
    - subagent phase doesn't have exactly one edge: ``RunStatus.ERROR``
      (loader should already enforce this, but we double-check)
    """
    # Function-local: see subagent.py for the full cycle explanation.
    # tools -> workflow_tools -> workflow.engine -> workflow.subagent -> tools
    from . import subagent as wf_subagent

    wf = registry.get(state.workflow)
    if wf is None:
        raise ValueError(f"workflow '{state.workflow}' not registered")
    phase = wf.phase(phase_id)
    if phase is None:
        raise ValueError(
            f"phase '{phase_id}' not in workflow '{state.workflow}'")

    async with run_lock(state.run_id):
        try:
            await wf_subagent._run_child(
                ctx=ctx, workspace=workspace,
                state=state, phase=phase,
            )
        except Exception as exc:
            log.exception(
                "[workflow] subagent crashed for run=%s phase=%s",
                state.run_id, phase_id)
            state.status = RunStatus.ERROR
            state.error = f"subagent crashed: {exc}"
            save_run(workspace, state)
            return

        missing = verify_subagent_outputs(workspace, state, phase_id)
        if missing:
            state.status = RunStatus.ERROR
            state.error = (
                "subagent did not produce required outputs: "
                + ", ".join(missing)
            )
            save_run(workspace, state)
            log.warning(
                "[workflow] subagent for run=%s phase=%s missing "
                "outputs: %s",
                state.run_id, phase_id, missing)
            return

        # Subagent phases must have exactly one next edge (loader
        # enforces this; check again here so a hand-edited workflow
        # surfaces a clear error rather than an arbitrary pick).
        if len(phase.next_phases) != 1:
            state.status = RunStatus.ERROR
            state.error = (
                f"subagent phase '{phase_id}' must have exactly one "
                f"next-phases edge for auto-advance "
                f"(found {len(phase.next_phases)})"
            )
            save_run(workspace, state)
            log.error(
                "[workflow] subagent phase=%s has %d edges, expected 1",
                phase_id, len(phase.next_phases))
            return

        target = phase.next_phases[0].id
        _apply_transition(
            workspace, wf, state, edge_idx=0,
            target=target,
            reason="subagent complete",
            gate_response=None,
        )
        log.info(
            "[workflow] subagent complete for run=%s phase=%s → %s",
            state.run_id, phase_id, target)


def verify_subagent_outputs(workspace: Path, state: RunState,
                            phase_id: str) -> list[str]:
    """Return the list of expected outputs that are MISSING from artifacts.

    Empty list means all outputs are present (or the phase isn't a
    subagent phase / the workflow isn't registered — fail-open by
    design, since the caller is the subagent dispatcher which already
    knows it landed on a SUBAGENT phase. Out-of-band callers get a
    no-op rather than an exception.).
    """
    wf = registry.get(state.workflow)
    if wf is None:
        return []
    phase = wf.phase(phase_id)
    if phase is None or phase.kind != PhaseKind.SUBAGENT:
        return []
    artifacts = (workspace / "workflows" / state.workflow / "runs"
                 / state.run_id / "artifacts" / phase_id)
    missing: list[str] = []
    for output in phase.outputs:
        if not (artifacts / output).is_file():
            missing.append(output)
    return missing
