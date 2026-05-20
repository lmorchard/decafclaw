"""Workflow state-machine operations.

advance() is the canonical transition entrypoint. Gate dispatch returns
an EndTurnConfirm; finalize_gate_response completes the gated edge once
the user has answered. verify_subagent_outputs is called by the
subagent dispatcher after a child completes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..media import EndTurnConfirm
from . import registry
from .runs import run_lock, save_run
from .types import (
    EdgeDef,
    PhaseDef,
    PhaseKind,
    RunState,
    RunStatus,
    WorkflowDef,
)

log = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class AdvanceResult:
    """Returned by advance(): new phase, optional end_turn signal."""

    new_phase: str
    end_turn_signal: EndTurnConfirm | bool | None = None


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
    """Apply a gate's approve/deny response and resume."""
    wf = registry.get(state.workflow)
    if wf is None:
        raise ValueError(f"workflow '{state.workflow}' not registered")
    if state.status != RunStatus.PAUSED_GATE or state.pending_gate is None:
        raise ValueError("run is not paused on a gate")

    async with run_lock(state.run_id):
        pending = state.pending_gate
        target = pending["edge_target"] if approved else pending["on_deny"]
        # Find edge index for history (approve path uses original edge)
        phase = wf.phase(state.current_phase)
        edge_idx = -1
        if phase is not None:
            for i, e in enumerate(phase.next_phases):
                if e.id == pending["edge_target"]:
                    edge_idx = i
                    break
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


def verify_subagent_outputs(workspace: Path, state: RunState,
                            phase_id: str) -> list[str]:
    """Return the list of expected outputs that are MISSING from artifacts.

    Empty list means all outputs are present.
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
