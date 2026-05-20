"""Workflow-aware subagent dispatcher.

Implementation lives here; the tools/workflow_tools.py layer is what
calls dispatch_subagent_phase() during a transition into a subagent
phase. See Task 7 for the wiring.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .types import RunState

log = logging.getLogger(__name__)


async def dispatch_subagent_phase(ctx, workspace: Path,
                                   state: RunState, phase_id: str
                                   ) -> None:
    """Spawn a child agent to execute a subagent phase.

    Wires into delegate.py's _run_child_turn primitives with:
    - prompt = phase body (or activate `subagent-skill:` if set)
    - tool whitelist = phase.tools resolved against the registry
    - working dir hint = artifacts/{phase_id}/

    On completion, calls verify_subagent_outputs and, if outputs are
    present, applies the auto-advance transition (single edge).
    On missing outputs, sets state.status = ERROR.

    See Task 7 for the full implementation that ties this into the
    workflow_tools layer and the agent loop.
    """
    raise NotImplementedError("subagent dispatch — see Task 7")
