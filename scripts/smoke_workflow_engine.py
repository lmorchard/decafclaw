#!/usr/bin/env python3
"""Smoke test for the workflow engine rework.

Exercises as much of the workflow runtime as possible without
requiring a live LLM. Stubs out subagent dispatch so the gather phase
"succeeds" by writing sources.md directly. Then walks through the
remaining phases the way the LLM would.

Run from the worktree root:
    uv run python scripts/smoke_workflow_engine.py

Prints a step-by-step trace. Exits 0 on success, 1 on any failure.
"""

from __future__ import annotations

import asyncio
import secrets
import shutil
import sys
import tempfile
from pathlib import Path

# Ensure src/ is on path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from decafclaw.media import ToolResult
from decafclaw.tools.workflow_tools import (
    build_phase_advance_definition,
    refresh_workflow_tools,
    tool_phase_advance,
    tool_workflow_abort,
    tool_workflow_artifact_read,
    tool_workflow_artifact_write,
    tool_workflow_start,
    tool_workflow_status,
)
from decafclaw.workflow import engine, registry, subagent as wf_subagent
from decafclaw.workflow.conv_state import (
    artifacts_dir,
    load_workflow_state,
)
from decafclaw.workflow.loader import load_workflow
from decafclaw.workflow.types import RunStatus


GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
RESET = "\033[0m"


def ok(msg: str) -> None:
    print(f"  {GREEN}✓{RESET} {msg}")


def fail(msg: str) -> None:
    print(f"  {RED}✗{RESET} {msg}")
    sys.exit(1)


def info(msg: str) -> None:
    print(f"  {YELLOW}•{RESET} {msg}")


def section(title: str) -> None:
    print(f"\n{title}")


class FakeCtx:
    """Minimal ctx for runtime testing."""

    def __init__(self, workspace: Path, conv_id: str):
        from types import SimpleNamespace
        self.config = SimpleNamespace(
            workspace_path=workspace,
            discovered_skills=[],
            agent=SimpleNamespace(
                child_max_tool_iterations=8,
                child_timeout_sec=60,
            ),
            skill_tool_owners={},
        )
        self.conv_id = conv_id
        self.channel_id = conv_id
        self.tools = SimpleNamespace(
            extra={},
            extra_definitions=[],
            allowed=None,
            workflow_restricted=False,
            preempt_matches=set(),
        )
        self.skills = SimpleNamespace(
            data={},
            activated=set(),
        )
        self.manager = None  # subagent dispatch stubbed
        self.event_context_id = "smoke-evt"
        self.context_id = "smoke-ctx"
        self.cancelled = None
        self.request_confirmation = None
        self.active_model = ""


async def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="smoke_workflow_"))
    workspace = tmp / "workspace"
    workspace.mkdir(parents=True)
    print(f"Workspace: {workspace}")

    try:
        await run_smoke(workspace)
    finally:
        # Leave it for inspection if anything goes wrong
        if "--keep" not in sys.argv:
            shutil.rmtree(tmp)
            print(f"\nCleaned up {tmp}")
        else:
            print(f"\nKept {tmp} for inspection")


async def run_smoke(workspace: Path) -> None:
    section("[1] Load the demo workflow")
    wf = load_workflow(ROOT / "src" / "decafclaw" / "skills" / "workflow_demo")
    registry.register(wf)
    ok(f"Loaded '{wf.name}' with {len(wf.phases)} phases, "
       f"required_skills={wf.required_skills}")

    section("[2] Build the smoke ctx + stub skill activation")
    conv_id = f"smoke-{secrets.token_hex(4)}"
    ctx = FakeCtx(workspace, conv_id)
    ok(f"ctx.conv_id = {conv_id}")

    # Monkeypatch the required-skills activation path so we don't
    # spin up tabstack (it would need real env vars + config).
    import decafclaw.tools.workflow_tools as wt

    activated_skills: list[str] = []

    async def fake_activate(ctx, name):
        activated_skills.append(name)
        # Simulate the skill adding tools to ctx.tools.extra
        if name == "tabstack":
            ctx.tools.extra["tabstack_research"] = lambda *a, **kw: None
            ctx.tools.extra_definitions.append({
                "type": "function",
                "function": {"name": "tabstack_research"},
            })
        return ToolResult(text=f"Activated {name}")

    wt._activate_skill_for_workflow = fake_activate
    ok("Stubbed _activate_skill_for_workflow")

    # Monkeypatch _run_child so the gather subagent "succeeds" by
    # writing sources.md to the parent's artifacts dir (which is what
    # the real subagent should do after our conv_id override fix).
    async def fake_run_child(*, ctx, state, phase):
        art_dir = artifacts_dir(ctx) / phase.id
        art_dir.mkdir(parents=True, exist_ok=True)
        (art_dir / "sources.md").write_text(
            "# Sources for the smoke topic\n\n"
            "## Source: stub\nA stubbed source.\n"
        )
        return "subagent done"

    wf_subagent._run_child = fake_run_child
    ok("Stubbed wf_subagent._run_child (writes sources.md to "
       "parent's artifacts/)")

    section("[3] workflow_start (initial phase is subagent → dispatch)")
    result = await tool_workflow_start(ctx, name="research_brief")
    text = result.text if isinstance(result, ToolResult) else result
    print(f"  result: {text}")

    if activated_skills != ["tabstack"]:
        fail(f"required-skills activation: expected ['tabstack'], "
             f"got {activated_skills}")
    ok("required-skills activated: ['tabstack']")

    state = load_workflow_state(ctx)
    if state is None:
        fail("workflow.json was not written")
    ok("workflow.json exists")

    if state.current_phase != "draft":
        fail(f"expected current_phase='draft' after subagent dispatch, "
             f"got '{state.current_phase}'")
    ok(f"current_phase advanced past gather → {state.current_phase}")

    if state.status != RunStatus.RUNNING:
        fail(f"expected status=RUNNING, got {state.status.value}")
    ok(f"status: {state.status.value}")

    # Verify sources.md exists in conv-scoped artifacts dir
    sources_path = (workspace / "conversations" / conv_id
                    / "artifacts" / "gather" / "sources.md")
    if not sources_path.is_file():
        fail(f"sources.md not at {sources_path}")
    ok(f"sources.md at {sources_path.relative_to(workspace)}")

    section("[4] refresh_workflow_tools — verify catalog gating + phase_advance")
    refresh_workflow_tools(ctx)

    if "phase_advance" not in ctx.tools.extra:
        fail("phase_advance not injected into ctx.tools.extra")
    ok("phase_advance in ctx.tools.extra")

    pa_defs = [d for d in ctx.tools.extra_definitions
               if d.get("function", {}).get("name") == "phase_advance"]
    if not pa_defs:
        fail("phase_advance definition not in ctx.tools.extra_definitions")
    pa_def = pa_defs[0]
    if pa_def.get("priority") != "critical":
        fail(f"phase_advance priority should be critical, got "
             f"{pa_def.get('priority')!r}")
    ok("phase_advance priority: critical")

    enum_vals = pa_def["function"]["parameters"]["properties"][
        "target_phase_id"]["enum"]
    if set(enum_vals) != {"review", "gather"}:
        fail(f"phase_advance enum: expected {{review, gather}}, "
             f"got {enum_vals}")
    ok(f"phase_advance enum: {sorted(enum_vals)} (draft phase's edges)")

    if not ctx.tools.workflow_restricted:
        fail("workflow_restricted should be True in an inline phase")
    if ctx.tools.allowed is None:
        fail("ctx.tools.allowed should be set in an inline phase")
    ok(f"ctx.tools.allowed set (|allowed| = {len(ctx.tools.allowed)})")
    if "vault_read" not in ctx.tools.allowed:
        fail("vault_read should be in allowed (phase whitelist)")
    if "phase_advance" not in ctx.tools.allowed:
        fail("phase_advance should be in allowed (admin baseline)")
    ok("Phase whitelist + admin baseline in allowed")

    section("[5] workflow_status reports the current state")
    status_text = await tool_workflow_status(ctx)
    if isinstance(status_text, ToolResult):
        status_text = status_text.text
    if "draft" not in status_text:
        fail("workflow_status did not mention draft phase")
    if "review" not in status_text or "gather" not in status_text:
        fail("workflow_status did not list both next-phases")
    ok("workflow_status shows phase + transitions")

    section("[6] phase_advance to review (inline transition)")
    result = await tool_phase_advance(
        ctx, target_phase_id="review", reason="draft complete")
    text = result.text if isinstance(result, ToolResult) else result
    if "review" not in text.lower():
        fail(f"phase_advance result did not mention review: {text}")
    ok(f"phase_advance: {text}")

    state = load_workflow_state(ctx)
    if state.current_phase != "review":
        fail(f"expected current_phase=review, got {state.current_phase}")
    ok(f"current_phase: {state.current_phase}")

    section("[7] workflow_artifact_write + read in review phase")
    draft_path = "draft/brief.md"
    write_result = await tool_workflow_artifact_write(
        ctx, relative_path=draft_path,
        content="# Brief\n\nA short test brief.\n")
    info(str(write_result if isinstance(write_result, str)
         else write_result.text))

    read_result = await tool_workflow_artifact_read(
        ctx, relative_path=draft_path)
    text = read_result if isinstance(read_result, str) else read_result.text
    if "A short test brief" not in text:
        fail(f"artifact read didn't return what was written: {text}")
    ok("artifact write/read round-trip works")

    # Verify it landed in the conv-scoped artifacts dir
    expected = (workspace / "conversations" / conv_id / "artifacts"
                / "draft" / "brief.md")
    if not expected.is_file():
        fail(f"brief.md not at {expected}")
    ok(f"brief.md at {expected.relative_to(workspace)}")

    section("[8] phase_advance review → publish (gated edge)")
    refresh_workflow_tools(ctx)  # re-fetch for review phase's enum

    pa_def = build_phase_advance_definition(ctx)
    enum_vals = set(pa_def["function"]["parameters"]["properties"][
        "target_phase_id"]["enum"])
    ok(f"review phase enum: {sorted(enum_vals)}")

    result = await tool_phase_advance(
        ctx, target_phase_id="publish", reason="approving")
    if not isinstance(result, ToolResult):
        fail(f"gate transition should return ToolResult, got {type(result)}")
    if result.end_turn is None or result.end_turn is False:
        fail("gate transition should set end_turn to EndTurnConfirm")
    ok(f"Gate fired: end_turn={type(result.end_turn).__name__}")

    state = load_workflow_state(ctx)
    if state.status != RunStatus.PAUSED_GATE:
        fail(f"expected PAUSED_GATE, got {state.status.value}")
    ok(f"status: {state.status.value}")

    section("[9] approve the gate → publish phase")
    await engine.finalize_gate_response(ctx, state, approved=True)
    state = load_workflow_state(ctx)
    if state.current_phase != "publish":
        fail(f"after approve, expected current_phase=publish, "
             f"got {state.current_phase}")
    if state.status != RunStatus.DONE:
        fail(f"publish is terminal → expected DONE, got {state.status.value}")
    ok(f"current_phase: {state.current_phase}, status: {state.status.value}")

    section("[10] workflow_start fails when one is active (state ABORTED)")
    # Force-make the run live again for the start-while-active test
    state.status = RunStatus.RUNNING
    from decafclaw.workflow.conv_state import save_workflow_state
    save_workflow_state(ctx, state)

    second = await tool_workflow_start(ctx, name="research_brief")
    if not isinstance(second, ToolResult):
        fail("expected ToolResult error from second workflow_start")
    if "already active" not in second.text:
        fail(f"expected 'already active' in error, got {second.text}")
    ok(f"Second start blocked: {second.text[:90]}...")

    section("[11] workflow_abort archives state")
    result = await tool_workflow_abort(ctx, reason="smoke test cleanup")
    text = result.text if isinstance(result, ToolResult) else result
    if "abort" not in text.lower():
        fail(f"abort result didn't mention abort: {text}")
    ok(f"workflow_abort: {text}")

    state = load_workflow_state(ctx)
    if state is not None:
        fail("workflow.json should be archived → load_workflow_state returns None")
    ok("workflow.json archived; load_workflow_state returns None")

    archived = list((workspace / "conversations" / conv_id).glob(
        "workflow-*.json"))
    if not archived:
        fail("no archived workflow-*.json file found")
    ok(f"archive present: {archived[0].name}")

    section("[12] workflow_start succeeds again after abort (sequential)")
    third = await tool_workflow_start(ctx, name="research_brief")
    text = third.text if isinstance(third, ToolResult) else third
    info(text)
    state = load_workflow_state(ctx)
    if state is None:
        fail("expected new workflow.json after restart")
    if state.workflow != "research_brief":
        fail(f"new state should be research_brief, got {state.workflow}")
    ok(f"Fresh workflow started: {state.workflow}, phase={state.current_phase}")

    print(f"\n{GREEN}All smoke checks passed.{RESET}")


if __name__ == "__main__":
    asyncio.run(main())
