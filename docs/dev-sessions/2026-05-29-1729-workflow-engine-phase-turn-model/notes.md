# Session Notes — Phase-Turn Model Iteration

This is the third iteration on issue #255 (workflow engine). Capturing session state for context survival across compaction.

## Where we are

**Branch:** `feat/255-workflow-engine` (PR #557, NOT yet merged, on "needs changes")
**Worktree:** `/Users/lorchard/devel/decafclaw/.claude/worktrees/feat-255-workflow-engine/`
**Status:** Spec written for phase-turn model. Plan NOT yet written. Implementation NOT yet started.
**Current artifact:** [`spec.md`](spec.md) in this directory.

## Journey across the three iterations

1. **Cross-conv workflow engine** ([`../2026-05-19-2121-workflow-engine/`](../2026-05-19-2121-workflow-engine/)) — original spec + plan + 22 implementation commits. Workflows had run-ids, lived at `workspace/workflows/{name}/runs/{run-id}/`, had `workflow_list`/`workflow_switch` for cross-conv discovery. Demo smoke against live LLM exposed cascading wiring bugs.

2. **Conv-scoped rework** ([`../2026-05-21-1732-workflow-engine-rework/`](../2026-05-21-1732-workflow-engine-rework/)) — spec + plan + 12 commits rewriting the engine. State moved to `conversations/{conv_id}/workflow.json` + `artifacts/`. Dropped `workflow_list`/`workflow_switch`. Added `workflow_abort`, `required-skills`, `phase_advance` priority:critical. **Live smoke (this session) proved the engine plumbing works correctly but the LLM stalls in iteration 2** — same failure mode as before the rework. Architecture passed every test that didn't require an LLM and failed every test that did.

3. **Phase-turn model** (this directory) — flip the relationship. Engine drives flow (mechanical turn scheduling, transitions, dispatch); LLM drives routing decisions (`phase_advance(target)`). Phase-internal loop nudges the LLM if it stops without `phase_advance`. Spec just written.

## Key decisions captured

- **Drop cross-conversation goal** (decided in iteration 2, holds here). State is conv-scoped.
- **Drop synchronous subagent dispatch** (the rework's Task 6 conv_id override caused Bug 2: subagent's archive mixed into parent's JSONL). Replace with engine-enqueued `CHILD_AGENT` turns.
- **Engine drives flow / LLM drives routing.** `phase_advance(target_phase_id, reason)` is the seam. Engine handles mechanics; LLM picks routes.
- **Phase-turn model, Option A** (each phase = its own `WORKFLOW_PHASE` turn, engine enqueues). Option B (parallel engine LLM loop) ruled out — Les wants user-interruption capability that requires phases to live in the conversation's turn timeline, not hidden inside the engine.
- **Phase-internal loop** (Les's idea, added today). Inside a `WORKFLOW_PHASE` turn, if the LLM ends without `phase_advance`, inject a synthetic "you stopped without advancing, signal `phase_advance` or finish" nudge, continue the loop. Bounded by `max_phase_continuations` (default 2, configurable per phase via frontmatter).
- **`parent_conv_id` field on `Context`** separates archive identity (child's own) from workflow path resolution (parent's). Resolves Bug 2 cleanly.
- **Phase prompt replaces system prompt** in `WORKFLOW_PHASE` turns (not appended as overlay). Layer 1 from the original brainstorming, finally implemented as replacement.

## Open questions in spec — triage status

Triaged 2026-05-30 after the cheap experiment ([../2026-05-29-1803-workflow-tool-result-framing/notes.md](../2026-05-29-1803-workflow-tool-result-framing/notes.md)) concluded prompt-only is insufficient.

### All settled (2026-05-30)

1. **USER-turn context** — **same as WORKFLOW_PHASE — phase context dominates.** User interjection is treated as a user message inside the current phase frame. Agent stays in worker mode. Off-topic chat works through `workflow_abort` if needed.
2. **`paused-subagent` timeout** — **rely on existing child timeout.** `delegate.py`'s `child_timeout_sec` already bounds CHILD_AGENT turns; engine relies on that. No separate engine-level wall-clock timeout. User can `workflow_abort` if state seems stuck.
3. **`params:` arg on `workflow_start`** — **land in this PR.** `workflow_start(name, params={...})` stored on `WorkflowState`, exposed to phase prompts via `{{params.X}}` interpolation. Bug 1 has been deferred through two iterations and bit every smoke. The WIP's `_latest_parent_user_message` heuristic gets superseded.
4. **Phase-prompt-as-system-prompt** — **full replace.** General preamble (SOUL.md, AGENT.md, USER.md) is suppressed inside a WORKFLOW_PHASE turn. System prompt is the phase body wrapped in a `<workflow_phase>` block with a one-line "you are operating in workflow mode" frame.
5. **Token cost for many-phase workflows** — **premature; measure first.** Ship without per-workflow consolidation directives. ~3-4K tokens of system prompt per turn for a typical phase; prompt caching amortizes. Revisit if real workflows hit cost ceilings.
6. **Default `max_phase_continuations`** — **2** (= 3 total LLM attempts at a phase). Configurable per-phase via `max_continuations:` frontmatter.
7. **Phase-internal nudges archival** — **visible user-role messages.** Nudges appear in the JSONL archive and the UI transcript as user-role messages so the loop mechanism is honest to the user. Slight archive bloat accepted.

## What survives across iterations (will remain in any v1)

- Workflows authored as `kind: workflow` skills with `phases/*.md`
- `phase_advance(target_phase_id, reason)` as the agent-LLM seam
- Edge gates via `EndTurnConfirm`
- Phase prompt format (frontmatter: `kind`, `tools`, `next-phases`, `context-profile`, optional `outputs`/`subagent-skill`/`gate`)
- Loader + validation
- `required-skills:` auto-activation on `workflow_start`
- Dynamic `phase_advance` schema with `priority: critical` + `enum` of valid targets + `when:` clauses in description
- Per-phase tool catalog hard-gate via `ctx.tools.allowed`
- `workflow_start` / `workflow_status` / `workflow_abort` / `workflow_artifact_read/write`
- One active workflow per conversation; sequential after `workflow_abort` or terminal state
- Conv-scoped state at `conversations/{conv_id}/workflow.json`

## Carry-forward issues (not solved by this iteration)

- **#562** — Engine writes `workflow_phase_boundary` markers (composer reads them; engine writes none yet). Phase-boundary tool clearing currently a no-op.
- **#563** — `decision-slice: off` context-profile override (cross-subsystem with compaction).
- **#564** — `subagent-skill:` integration test.
- **Bug 1 from smoke** — `workflow_start` accepts no args; topic from `$ARGUMENTS` doesn't reach subagent. Mentioned in spec's open questions as item 3.

## File locations

- Current spec: `docs/dev-sessions/2026-05-29-1729-workflow-engine-phase-turn-model/spec.md`
- Conv-scoped rework spec: `docs/dev-sessions/2026-05-21-1732-workflow-engine-rework/spec.md`
- Original spec: `docs/dev-sessions/2026-05-19-2121-workflow-engine/spec.md`
- Smoke test script (conv-scoped era, exercises engine surface): `scripts/smoke_workflow_engine.py`
- Demo workflow: `src/decafclaw/skills/workflow_demo/`
- Engine code (conv-scoped, pre-phase-turn): `src/decafclaw/workflow/{types.py,conv_state.py,engine.py,loader.py,subagent.py,context.py,registry.py}`
- Tools: `src/decafclaw/tools/workflow_tools.py`
- Tests: `tests/test_workflow_*.py` (79 tests, all passing on conv-scoped HEAD)

## Code state on disk

Conv-scoped rework is fully landed and tested (79 workflow tests pass; `make check` clean). The phase-turn model is NOT yet implemented — only the spec exists. Implementation work, when it begins, will:

1. Add `TurnKind.WORKFLOW_PHASE` + manager scheduling
2. Add `Context.parent_conv_id` field; update `conv_state` path helpers
3. Rewrite `tool_workflow_start` + `tool_phase_advance` to enqueue rather than synchronously dispatch
4. Replace `_run_child` / `dispatch_subagent_if_needed` with manager-driven child turn enqueue
5. Add `WORKFLOW_PHASE` composer mode (phase prompt replaces system prompt)
6. Extend `TurnRunner` agent loop with phase-aware exit condition (the phase-internal nudge loop)
7. Update demo workflow if needed; rewrite tests

## PR #557 state

- Branch pushed; last commit `a140edf` (phase-turn spec).
- Final code review on the conv-scoped rework said NEEDS CHANGES on two stale references → fixed in `7c45c20`. After that, the conv-scoped state is solid but the smoke test revealed the LLM stall.
- PR description currently reflects the conv-scoped rework. Will need updating when phase-turn model lands.
- Issues #561 closed (priority fix in conv-scoped rework). #562/#563/#564 still open.

## Next action

User (Les) asked to write the phase-turn spec, then review and consider what it gets us. Spec is written + refined with the phase-internal loop. **Next is review and decisions on the open questions.** Plan-writing comes after that alignment.

Do NOT start implementing without explicit user direction. The previous iteration (conv-scoped rework) was a full implementation cycle that ended at "doesn't actually work end-to-end" — a similar cycle on the phase-turn model without alignment on open questions risks the same outcome.
