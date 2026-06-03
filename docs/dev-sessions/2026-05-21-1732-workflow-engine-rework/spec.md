# Workflow Engine Rework — Spec

**Issue:** [#255 — Design: first-class workflow abstraction (LangGraph-inspired)](https://github.com/lmorchard/decafclaw/issues/255)
**Original spec:** [`../2026-05-19-2121-workflow-engine/spec.md`](../2026-05-19-2121-workflow-engine/spec.md)
**Branch:** `feat/255-workflow-engine` (rewriting in place; PR #557)
**Date:** 2026-05-21
**Status:** Spec — pending plan

## Why a rework

The original implementation made workflow runs durable, conversation-independent objects (with `run_id`, cross-conversation `workflow_list`/`workflow_switch`, separate `workspace/workflows/{name}/runs/{run-id}/` storage). Two demo runs against the original architecture surfaced cascading wiring bugs:

1. Subagent dispatch wasn't wired into the tool layer (fixed in `2a83053`)
2. Per-phase tool catalog hard-gate wasn't applied to inline phases (fixed in `2a83053`)
3. Workflow overlay was built once per turn — invisible to iteration 2 when `workflow_start` ran in iteration 1 (fixed in `2ff9e13`)
4. Subagent prompt was rendered as main agent's instructions during paused-subagent states (fixed in `2ff9e13`)
5. `tool_search` was added to catalog when ALL deferred tools were filtered out by `allowed` (fixed in `7425996`)
6. Subagent's `tools.extra` was cleared, severing it from parent-activated skill tools (fixed in `7425996`)
7. **`required-skills` for workflow phases is unmodeled.** The `research_brief` workflow needs `tabstack` to be activated, but nothing in the workflow definition or the engine ensures that.

Each fix exposed coupling somewhere else. The pattern matches the systematic-debugging skill's "3+ fixes failed = question the architecture" gate.

The bigger architectural cost: the cross-conversation feature ("resume Thursday from a different conversation") was a stated goal at design time but in practice nobody exercised it, while its complexity caused the failure modes above. The conversation IS the natural unit of state — confirming activation, threading history, owning sidecar files. Workflow state ought to live there too.

This rework scopes workflows to a single conversation and deletes the cross-conversation machinery.

## Goals

A workflow author should be able to:

1. Declare a multi-phase task in markdown (frontmatter + body) — **unchanged**
2. List `required-skills:` in SKILL.md frontmatter; engine auto-activates them on `workflow_start` **(new)**
3. Constrain the agent per phase: tool catalog hard-gate, system prompt overlay, context-composer profile — **unchanged**
4. Route between phases as a graph with annotated `when:` clauses, via dynamically-regenerated `phase_advance` — **unchanged**
5. Gate edges with user-facing review confirmations — **unchanged**
6. Delegate entire phases to subagents — **unchanged**
7. Have workflow state survive across turns within the same conversation — **unchanged** (conversation persistence carries it)

A workflow runs to completion within a single conversation. Once it reaches DONE / ERROR / aborted, the user can start a fresh workflow in the same conversation.

## Non-goals (changed from original)

- **Cross-conversation workflow resumption** — explicitly dropped. State lives at conversation scope.
- **Cross-conversation `workflow_list` / `workflow_switch`** — both tools deleted.
- **Concurrent workflows within one conversation** — at most one active workflow per conversation.
- **Workflow runs as standalone durable objects** — they're sidecar state belonging to a conversation.

Non-goals unchanged from original:

- Conditional edge logic evaluated by code (LLM-routed `when:` only)
- Nested workflows
- Input-widget gates (only review gates in v1)
- Visual workflow editor / debugger
- Migration of existing `workspace/workflows/` data from the original architecture (the branch hasn't merged; data on disk is throwaway demo state)

## Architecture overview

### Module layout (unchanged except runs.py → conv_state.py)

```
src/decafclaw/workflow/
  __init__.py
  types.py        # WorkflowDef, PhaseDef, EdgeDef, GateDef, WorkflowState
  loader.py       # parse SKILL.md + phases/*.md → WorkflowDef; required-skills parsing
  conv_state.py   # conversation-scoped state persistence (replaces runs.py)
  engine.py       # transitions, gate dispatch, subagent dispatch
  subagent.py     # subagent dispatcher (artifacts paths change; logic unchanged)
  context.py      # WorkflowOverlay for composer integration
  registry.py     # in-memory WorkflowDef registry

src/decafclaw/tools/
  workflow_tools.py  # workflow_start, status, abort, advance, artifact_*
```

`runs.py` and its module-global `_run_locks` dict are deleted.

### Workflow state at conversation scope

```
data/{agent_id}/workspace/
  conversations/
    web-lmorchard-abc123.jsonl                 # archive (existing)
    web-lmorchard-abc123.notes.md              # notes sidecar (existing)
    web-lmorchard-abc123.context.json          # diagnostics (existing)
    web-lmorchard-abc123/                      # workflow-owned directory (new)
      workflow.json                            # phase, status, history, error
      artifacts/
        gather/sources.md
        draft/brief.md
```

The conversation directory `conversations/{conv_id}/` is created on `workflow_start` and persists for the conversation's lifetime. When a workflow completes/aborts, `workflow.json` is updated to terminal state but the directory is preserved (referenceable from chat history). Starting a fresh workflow in the same conversation overwrites `workflow.json` and may keep prior artifacts (named per workflow run for diff-ability) or wipe them — see "Sequential workflows" below.

### Sequential workflows in one conversation

The state machine for a conversation: `NO_WORKFLOW ↔ ACTIVE`. Transitions:

- `NO_WORKFLOW → ACTIVE` via `workflow_start`
- `ACTIVE → NO_WORKFLOW` via:
  - Workflow reaches a terminal phase (status `done`)
  - Workflow lands in `error` state and user/agent calls `workflow_abort`
  - Explicit `workflow_abort` at any time

When transitioning to `NO_WORKFLOW`, prior `workflow.json` is renamed to `workflow-<terminated-timestamp>.json` (archive in same dir) so the next `workflow_start` writes a fresh `workflow.json` without losing the past. Artifacts from prior workflows stay in `artifacts/` namespaced per-workflow (`artifacts/{workflow_name}-{terminated_timestamp}/{phase}/...`) on transition. This is mostly historical record; the agent's tool surface only deals with the current workflow's artifacts.

(Implementation note: this naming convention is a v1 simplification. If artifacts accumulate uncomfortably, a future iteration could prune or compress.)

### Workflow definition format

**Unchanged from original** for `phases/{phase}.md` files. SKILL.md adds optional `required-skills:` in frontmatter:

```yaml
---
name: research_brief
description: Research a topic and produce a short written brief.
kind: workflow
user-invocable: true
required-skills: [tabstack]
argument-hint: "[start|status|abort] <topic>"
workflow:
  initial-phase: gather
---
User said: $ARGUMENTS
```

`required-skills:` is a list of skill names. On `workflow_start`, the engine calls the standard `activate_skill(name)` path for each. If activation fails (permission denied, skill not found, env vars missing), `workflow_start` returns an error and no workflow run is created.

Skills stay activated for the rest of the conversation (same lifecycle as any other activation).

### Engine tools (simplified)

The tool surface contracts:

| Tool | Behavior |
|---|---|
| `workflow_start(name)` | Activate required-skills, then initialize workflow state for the conversation. Synchronously dispatches the initial phase if it's a subagent. Error if a workflow is already active. |
| `workflow_status` | Show current workflow's phase, status, valid next phases with `when:` clauses, history. |
| `workflow_abort(reason="")` | Mark current workflow aborted. Archives `workflow.json` and resets to `NO_WORKFLOW`. Errors if no workflow is active. |
| `phase_advance(target_phase_id, reason="")` | Canonical transition. Dynamically regenerated per turn with phase-specific enum. **Priority: critical** (was missing in original — likely root cause of "unknown tool" issue). |
| `workflow_artifact_read(relative_path)` | Read from `conversations/{conv_id}/artifacts/<path>`. |
| `workflow_artifact_write(relative_path, content)` | Write under `conversations/{conv_id}/artifacts/<path>`. Rejects path traversal. |

**Deleted from original:** `workflow_list`, `workflow_switch`. No cross-conversation discovery.

### Per-phase tool catalog hard-gate (unchanged in concept)

When the current phase is inline, `refresh_workflow_tools` sets `ctx.tools.allowed` to:
- Phase's `tools:` whitelist (literals pass through; globs expand against the live registry)
- Workflow admin baseline (`workflow_*`, `phase_advance`)
- Critical-priority baseline (`notes_*`, `checklist_*`, etc.)

Subagent phases don't restrict the main agent's catalog (subagent runs inside the tool call; main agent shouldn't normally see one). The `ToolState.workflow_restricted` flag tracking unchanged.

### Per-iteration overlay refresh (unchanged from `2ff9e13`)

`TurnRunner._refresh_workflow_msg` continues to manage a `workflow_msg` system-prompt slot per iteration, parallel to `deferred_msg`.

### Subagent dispatch (unchanged in concept, paths change)

`dispatch_and_finalize_subagent` runs the child via `manager.enqueue_turn(kind=CHILD_AGENT)` with phase-derived setup. Child inherits parent's `tools.extra` (so skill tools like `tabstack_research` are available). Artifacts directory passed to the child is now `conversations/{conv_id}/artifacts/{phase}/`. Output verification reads from the same path.

### Context composer integration (unchanged in concept)

`consult_workflow_overlay(ctx)` reads state from `conversations/{conv_id}/workflow.json` instead of from `workspace/workflows/.../runs/{run_id}/state.json`. Otherwise identical: phase prompt section, context-profile overrides, phase-boundary clearing flag.

## What changes vs. the original

### Deleted

- `src/decafclaw/workflow/runs.py` (entirely)
- `tests/test_workflow_runs.py` (entirely)
- `_run_locks` module-global lock registry
- `RunState.run_id` field redundancy (the conv_id IS the implicit identifier)
- `workflow_list` and `workflow_switch` tools, their tests, and their entries in `WORKFLOW_TOOLS` / `WORKFLOW_TOOL_DEFINITIONS`
- Cross-conversation discovery glob walks
- `slug` parameter on `workflow_start`

### Added

- `src/decafclaw/workflow/conv_state.py` — `load_workflow_state(ctx) -> WorkflowState | None`, `save_workflow_state(ctx, state)`, `init_workflow_state(ctx, workflow_name, initial_phase)`, `archive_workflow_state(ctx)` (rename `workflow.json` to `workflow-<terminated-timestamp>.json` and move artifacts). Per-conversation `asyncio.Lock` keyed by `conv_id`.
- `tests/test_workflow_conv_state.py`
- `tool_workflow_abort` tool
- `required-skills:` parsing in `loader.py` and validation
- `WorkflowDef.required_skills: list[str]` field
- Engine logic to activate required-skills on `workflow_start`
- `"priority": "critical"` on the dynamic `phase_advance` definition (was missing — likely the "unknown tool" root cause)

### Modified

- `types.py`: `RunState` → `WorkflowState`. Drops `run_id` (conv_id is implicit). Add fields if needed.
- `engine.py`: Replace `runs.run_lock(run_id)` with `conv_state.conv_lock(ctx)`; replace `load_run`/`save_run` calls with the conv_state equivalents.
- `subagent.py`: Artifact directory hint becomes `conversations/{conv_id}/artifacts/{phase}/`. Logic unchanged.
- `workflow/context.py`: `consult_workflow_overlay` loads from conv_state.
- `tools/workflow_tools.py`: Major simplification. New `tool_workflow_abort`. Drop `workflow_list`, `workflow_switch`. Add priority field to dynamic phase_advance definition.
- `loader.py`: parse `required-skills:` from SKILL.md frontmatter; validate it's a list of strings.
- `tools/__init__.py`: Update `WORKFLOW_TOOLS` / `WORKFLOW_TOOL_DEFINITIONS` (remove deleted tools, add `workflow_abort`).
- `skills/workflow_demo/SKILL.md`: add `required-skills: [tabstack]`.
- All workflow tests: update for new state API and dropped tools.

### Unchanged

- `phases/{phase}.md` authoring format
- `kind: workflow` skill loader branch
- Dynamic `phase_advance` enum generation (just add the priority field)
- Edge gates with `EndTurnConfirm`
- Phase-boundary tool clearing (still no engine writes markers; same deferred state)
- Per-phase context profile overrides
- Per-iteration `workflow_msg` in agent loop

## State shape

`conversations/{conv_id}/workflow.json`:

```json
{
  "workflow": "research_brief",
  "current_phase": "draft",
  "status": "running",
  "started_at": "2026-05-21T17:32:00+00:00",
  "updated_at": "2026-05-21T17:38:12+00:00",
  "history": [
    {"from": null, "to": "gather", "edge_index": null, "gate_response": null,
     "reason": "initial", "timestamp": "2026-05-21T17:32:00+00:00"},
    {"from": "gather", "to": "draft", "edge_index": 0, "gate_response": null,
     "reason": "subagent complete", "timestamp": "2026-05-21T17:32:18+00:00"}
  ],
  "pending_gate": null,
  "pending_subagent": null,
  "error": null
}
```

Statuses (unchanged set, just no longer "per-run"): `running`, `paused-gate`, `paused-subagent`, `done`, `error`, `aborted`. (`aborted` is new — replaces the implicit "user gave up" path.)

## Engine tool details

### `workflow_start(name)`

No `slug` parameter. With conv-scoped storage the conversation IS the workflow's identity — a slug doesn't add value. Skill bodies that previously included a slug arg are updated as part of the rework.

Sequence:
1. Check if a workflow is already active for this conversation. If so, return error suggesting `workflow_abort` first.
2. Look up the workflow definition by name; error if not found.
3. For each skill in `wf.required_skills`: call the standard skill activation path. If any fail, return error citing the failed skill (no partial state written).
4. Initialize `conversations/{conv_id}/` directory and `workflow.json` with `current_phase = wf.initial_phase`, `status = running`, initial history entry.
5. If initial phase is a subagent, call `engine.dispatch_subagent_if_needed`. State may advance / land in ERROR.
6. Return text summarizing the post-dispatch state.

### `workflow_abort(reason="")`

1. Load current state. If no workflow active, return error.
2. Archive `workflow.json` to `workflow-<aborted-timestamp>.json` in the same directory.
3. Set conversation back to NO_WORKFLOW state.

Artifacts: stay in `artifacts/` under their phase subdirs. (Don't bother renaming on abort — the directory is auto-namespaced if a new workflow starts. If accumulating clutter becomes a concern, future iteration can add a cleanup tool.)

### `phase_advance(target_phase_id, reason="")` (priority fix)

The dynamic schema returned from `build_phase_advance_definition` now includes `"priority": "critical"`. This was missing from the original and is the suspected root cause of the "unknown tool 'phase_advance'" loop in demos (when classify_tools deferred phase_advance with no explicit priority, the tool became unreachable from the active catalog even though it was in `ctx.tools.extra`).

## Error handling

| Failure | Behavior |
|---|---|
| `workflow_start` with workflow already active | Error: "workflow X already active in this conversation; call workflow_abort first" |
| `workflow_start` with required-skills activation failing | Error citing skill name and reason. No partial state. |
| Subagent crashes during dispatch | `state.status = error`, error message saved, state persisted |
| Subagent produced incomplete outputs | `state.status = error`, list of missing outputs in `error` field |
| `phase_advance` with invalid target | Tool returns error with list of valid targets and `when:` clauses |
| `workflow_status` with no workflow active | "No workflow active in this conversation. Use workflow_start to begin." |
| `workflow_abort` with no workflow active | "No workflow active to abort." |
| Two iterations try to advance simultaneously (rare in user flow but possible via tool concurrency) | Per-conv `asyncio.Lock` in `conv_state` serializes; second waiter sees post-transition state. |

## Testing

### Unit tests

- `tests/test_workflow_types.py` — minimal changes (rename `RunState` → `WorkflowState` if we rename; drop `run_id` field references)
- `tests/test_workflow_conv_state.py` — REPLACES `test_workflow_runs.py`. Covers: init, save, load, archive (terminal transition), lock serialization
- `tests/test_workflow_loader.py` — add `required-skills` parsing tests (valid + invalid shape)
- `tests/test_workflow_engine.py` — update API calls to use conv-scoped state. Test list unchanged in concept: transitions, gate approve/deny, subagent dispatch, output verification, etc.
- `tests/test_workflow_tools.py` — drop tests for `workflow_list`/`workflow_switch`. Add tests for `workflow_abort` and for `workflow_start` activating required-skills. Update tests that referenced the old `current_workflow_run` pointer.
- `tests/test_workflow_context.py` — update overlay tests for conv-scoped state.
- `tests/test_workflow_skill_loader.py` — verify `required-skills` parses through `parse_skill_md`.

### Eval

`evals/workflow_routing.yaml` — unchanged in spirit. The setup framing primes the LLM with the post-gather state; the routing assertions remain valid.

## V1 scope

### Ships

- Conversation-scoped state at `conversations/{conv_id}/workflow.json` + `artifacts/`
- `required-skills` auto-activation on `workflow_start`
- `workflow_abort` tool
- Priority fix on dynamic `phase_advance` schema
- Demo workflow updated with `required-skills: [tabstack]`
- All unit tests updated
- `docs/workflows.md` updated for the new scope

### Does not ship (deferred items, mostly carried from original)

- Phase-boundary markers written by engine (composer reads them; engine writes only at transition time — still TODO, tracked in #562)
- `decision-slice: off` context-profile override (cross-subsystem with compaction, tracked in #563)
- Input-widget gates (only review gates)
- Nested workflows
- Workflow-scoped notes

## Notes on the existing PR branch

The branch `feat/255-workflow-engine` will be updated in place. Commits 22+ (the original implementation) stay; the rework adds new commits that delete/replace specific modules. PR #557 description should be updated to reflect the conversation-scoped architecture once the rework lands.

The original spec at `../2026-05-19-2121-workflow-engine/spec.md` stays as historical record. This rework spec supersedes it.

## Open questions for planning

- **Engine activation of required-skills:** the standard `activate_skill` path requires user approval for some skill tiers. For workflows, requiring user approval before each phase fires might be too noisy. Open question for plan time: should `required-skills` skip approval (workflow author trusted) or always go through approval (consistent with skill conventions)? Recommendation: respect skill tier — bundled/admin auto-activate, workspace/extra prompt for approval, denied skills fail the workflow.
- **Concurrent `workflow_start` calls in one conversation:** the `ConversationManager.busy` lock should serialize turns, so two concurrent starts shouldn't really happen. Conv-scoped `asyncio.Lock` in conv_state.py covers in-tool race window.

## References

- Original spec: [`../2026-05-19-2121-workflow-engine/spec.md`](../2026-05-19-2121-workflow-engine/spec.md)
- Original plan: [`../2026-05-19-2121-workflow-engine/plan.md`](../2026-05-19-2121-workflow-engine/plan.md)
- Issue #255 — Design: first-class workflow abstraction
- Issue #561 — Priority decision (closes with this rework — sets `phase_advance` to critical)
- Issue #562 — Phase-boundary markers (still open; carries forward)
- Issue #563 — Decision-slice override (still open; carries forward)
- Issue #564 — Subagent-skill smoke test (still open; carries forward)
