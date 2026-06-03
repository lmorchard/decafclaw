# Workflow Engine — Spec

**Issue:** [#255 — Design: first-class workflow abstraction (LangGraph-inspired)](https://github.com/lmorchard/decafclaw/issues/255)
**Date:** 2026-05-19
**Status:** Spec — pending plan

## Problem

Skills like `project` (brainstorm → spec → plan → execute → done) work by hand-rolling four separate mechanisms in concert: a state machine (`state.py`), a dynamic tool provider (`get_tools(ctx)`), turn-control signals (`EndTurnConfirm` / `end_turn=True`), and review-gate confirmation handlers. The pattern is reusable, but every new structured workflow forces an author to reinvent the plumbing in Python.

This pattern occurs anywhere we want **focused, multi-phase work**: composing weeknotes from multiple data sources, brainstorming and outlining a short story, assembling a self-assessment, running a structured research session. Each of these benefits from constrained tool surface per phase, scoped context, declarative review gates, and resumability across sessions. None of them want to be a Python skill.

The constraint that matters most: **focus the LLM**. Holistic conversation history is the wrong context for some steps; a phase wants to see a curated slice — sometimes via context-composer profile changes, sometimes via running as an isolated subagent. The current skill system has no first-class way to express "while in phase X, the agent sees only these tools, this system prompt, and this context shape."

## Goals

A workflow author should be able to:

1. **Declare** a multi-phase task in markdown (frontmatter + body), without writing Python.
2. **Constrain** the agent per phase: tool catalog, system prompt, context-composer profile.
3. **Route** between phases as a graph (not just a chain), with annotated decision criteria the LLM uses to choose edges.
4. **Gate** edges with user-facing confirmations (Approve / Needs Changes) declaratively.
5. **Delegate** entire phases to subagents when isolation is preferable to constraint.
6. **Resume** a run across sessions and conversations — the workflow run is a durable, named, on-disk thing.

A non-goal for v1: replacing the existing `project` skill. The workflow engine ships standalone; existing skills are untouched. Migration of `project` (or anything else) onto the engine is a separate, later decision.

## Non-goals

- Conditional edge logic evaluated by code (only LLM-routed `when:` annotations for v1)
- Nested workflows (a phase invoking another workflow as a sub-run)
- Input-widget gates (only review-style Approve/Deny gates for v1; input gates can be added later by extending `gate.type`)
- Backward-compatibility shim for existing workflow-like skills (none yet exist that use this engine)
- Migration of existing `workspace/projects/` data
- Visual workflow editor / debugger

## Architecture overview

### Module layout (new)

```
src/decafclaw/workflow/
  __init__.py
  types.py        # WorkflowDef, PhaseDef, EdgeDef, GateDef, RunState
  loader.py       # parse SKILL.md + phases/*.md → WorkflowDef
  runs.py         # RunState persistence: create, load, list, switch, save
  engine.py       # transitions, gate dispatch, subagent dispatch, validation
  context.py      # WorkflowOverlay for ContextComposer integration

src/decafclaw/tools/
  workflow_tools.py  # engine management tools, registered as always-loaded
```

Engine tools live in `tools/workflow_tools.py` and are registered as **always-loaded** through the existing priority classification (the same path that registers `notes_*`, `checklist_*`). The workflow engine reaches into core architecture (ContextComposer, tool registry, agent loop), so it is core, not a skill.

### Workflow recognition

The existing skill loader gets one new branch: if a `SKILL.md` has `kind: workflow` in frontmatter, the loader builds a `WorkflowDef` from `SKILL.md` + `phases/*.md` and registers a per-phase dynamic `get_tools(ctx)` provider. Workflow skills have no `tools.py`. All other skill mechanics (`user-invocable`, scheduling, permissions, scan order) work unchanged.

### Workflow runs (instances)

Each workflow invocation creates an **instance** with a durable ID, an on-disk state directory, and an artifacts folder. The skill is the cookie cutter; the run is the cookie.

```
workspace/
  workflows/
    weeknotes/
      style-reference.md     # workflow-author-owned, optional
      runs/
        2026-05-19-1402-w20/
          state.json
          artifacts/
            gather/
              sources.md
            draft/
              post.md
        2026-05-12-0930-w19/
          state.json
          artifacts/
    short-story/
      runs/
        2026-05-15-2100-shadowport/
          state.json
          artifacts/
```

The engine owns `workspace/workflows/{name}/runs/`. Workflow authors may drop other files at `workspace/workflows/{name}/<anything>` for shared/persistent state (config, style references); the engine does not touch them.

The current run for a conversation is tracked in `ctx.skills.data['current_workflow_run']` (analog of the project skill's `current_project`). The pointer is per-conversation; the run state is durable across conversations.

## Workflow definition format

### SKILL.md (workflow shell)

```yaml
---
name: weeknotes
description: Compose weeknotes from Mastodon + Linkding for a date range.
kind: workflow
user-invocable: true
argument-hint: "[start|list|switch|status]"

workflow:
  initial-phase: gather
---

User said: $ARGUMENTS

(Optional command handler prose — same convention as today's project skill.
Tells the LLM how to interpret !weeknotes start / !weeknotes status / etc.)
```

`SKILL.md` carries workflow-level config and the user-invocable command handler text. All phase-specific declarations live in `phases/`.

### phases/{phase-id}.md (one file per phase)

Each `phases/<stem>.md` file declares a phase with `id: <stem>`. Frontmatter holds wiring; body holds the prompt.

```yaml
# phases/draft.md
---
kind: inline                       # or 'subagent'; default 'inline'
tools: [vault_read, vault_write]   # glob patterns OK: tabstack_*
context-profile:
  memory-retrieval: off            # inherit | off
  vault-injection-mode: inherit    # inherit | always | headlines | on_demand
  notes-injection: inherit         # inherit | off
  decision-slice: inherit          # inherit | off
  clear-prior-phase-tools: true    # default true (opt-out)
next-phases:
  - id: research
    when: |
      The fetched sources are thin or one of the major themes lacks
      material. Fetch additional context first.
  - id: review
    when: |
      The draft is complete, covers the week's themes, and is ready
      for user review.
---

You are drafting this week's weeknotes blog post from the sources
gathered in `artifacts/gather/sources.md`.

Write in the user's voice — conversational, self-deprecating, with
parenthetical asides. Start with an inline "TL;DR: ..." followed by
`<!--more-->`. Use a Miscellanea section near the end for stragglers.

When the draft is complete, call `phase_advance` to move on:
`review` if it's ready for the user, `research` if you need more
sources. Before advancing, use `notes_append` to record one or two
sentences summarizing what this phase concluded — your notes survive
across phase boundaries even when tool outputs are cleared.
```

### Edges and gates

**Edges** (next-phases) are agent-directed: the LLM picks the target via `phase_advance(target_phase_id, reason="...")`. Each edge optionally has a `gate:` that mediates the transition with a user-facing confirmation.

```yaml
# phases/review.md (gate on the edge)
next-phases:
  - id: publish
    when: |
      The user has reviewed and approved the draft.
    gate:
      type: review
      message: "Approve weeknotes draft?"
      approve-label: "Looks good"
      deny-label: "Needs changes"
      on-deny: draft       # where to go if user denies; on-approve = edge target
```

Agent calls `phase_advance(publish, reason="...")` → engine sees the edge has a gate → fires the gate → on approve, transition to `publish` completes; on deny, transition to `draft` instead. The `on-approve` target is implicit (it's the edge's `id`); only `on-deny` needs to be declared.

Gates are **edge-level**, not phase-level. A phase can have multiple gated edges; each gate is a property of its edge.

### Subagent phases

A phase with `kind: subagent` runs as an isolated child agent. The phase prompt becomes the child's instructions; `tools:` is the child's whitelist; the child's working dir is scoped to the phase's artifacts subdir. The child uses the existing `CHILD_AGENT` composer mode.

```yaml
# phases/gather.md
---
kind: subagent
tools: [tabstack_*, vault_read]
outputs: [sources.md]              # required: files the subagent must produce
next-phases:
  - id: draft                      # single edge, no `when:` required
---

You are a research subagent. Fetch the user's Mastodon posts and
Linkding bookmarks for the past 7 days. Summarize into
`artifacts/gather/sources.md` — keep URLs, drop chatter. Return
when the artifact is written.
```

**Constraints on subagent phases (enforced by loader):**
- Must have exactly one `next-phases` entry (no agent choice — subagents auto-advance on completion)
- No gates on outgoing edges (gates are user-facing; users don't see the subagent)
- Must declare `outputs:` (the engine verifies the listed files exist before advancing)

**`subagent-skill:` escape hatch:** instead of an inline prompt body, point to an existing skill:

```yaml
---
kind: subagent
subagent-skill: research-worker
outputs: [report.md]
next-phases:
  - id: synthesize
---
```

The engine boots the child agent with that skill auto-activated; the phase file's body is unused.

### Loader validation (at load time, not run time)

- Every `next-phases.id` resolves to a defined phase in the same workflow
- Every gate's `on-deny` resolves to a defined phase
- `workflow.initial-phase` resolves to a defined phase
- Every phase has at least one `next-phases` edge OR is a terminal (no edges, no gates)
- Multi-edge phases require `when:` on every edge (forces the author to write routing hints)
- Subagent phases obey the constraints above
- Tool-list globs are validated against the current tool registry (warn if no matches; the registry is dynamic)

A workflow that fails validation logs a warning and does not appear in `workflow_list`; other workflows are unaffected.

## Engine tools (always-loaded)

Registered in `src/decafclaw/tools/workflow_tools.py` with `priority: critical` (always-loaded).

| Tool | Purpose |
|---|---|
| `workflow_start(name, slug="")` | Create a new run of workflow `name`. Returns the run-id and initial phase prompt. |
| `workflow_list(workflow="", status="")` | List runs across conversations, optionally filtered. |
| `workflow_switch(run_id)` | Change the conversation's current run. |
| `workflow_status` | Show current run's workflow, phase, valid `next-phases` with `when:` clauses, recent transition history. |
| `phase_advance(target_phase_id, reason="")` | Canonical transition tool. **Dynamically regenerated per turn** with a JSON-Schema `enum` of the current phase's valid targets and inlined `when:` descriptions. |
| `workflow_artifact_write(relative_path, content)` | Write to a path under the current run's `artifacts/`. |
| `workflow_artifact_read(relative_path)` | Read from a path under the current run's `artifacts/`. |

### Dynamic `phase_advance` schema

The engine constructs `phase_advance` per turn based on the current phase's `next-phases`:

```json
{
  "name": "phase_advance",
  "description": "Advance the workflow run to its next phase.\n\nYou are currently in phase 'draft' of 'weeknotes'. Pick the target that matches your situation:\n\n  - target_phase_id=\"research\"\n    Pick this when: The fetched sources are thin or one of the major themes lacks material. Fetch additional context first.\n\n  - target_phase_id=\"review\"\n    Pick this when: The draft is complete, covers the week's themes, and is ready for user review.\n\nIf you're not sure which applies, call workflow_status for a recap.",
  "parameters": {
    "properties": {
      "target_phase_id": {
        "type": "string",
        "enum": ["research", "review"]
      },
      "reason": {
        "type": "string",
        "description": "Brief justification (1-2 sentences) for choosing this target."
      }
    },
    "required": ["target_phase_id"]
  }
}
```

The `enum` is enforced by the provider's function-calling layer (OpenAI, Anthropic, Vertex all honor it). The engine also validates server-side as defense in depth.

## ContextComposer integration

### One new hook

`ContextComposer.compose()` calls `WorkflowOverlay.consult(ctx)` once per compose. The overlay returns `None` if no run is active, otherwise:

```python
@dataclass
class WorkflowOverlay:
    phase_prompt_section: str         # <workflow_phase ...>...</workflow_phase> block
    context_profile_overrides: dict   # see below
    phase_boundary: bool              # True on the turn immediately after a transition
```

### Context profile overrides

The overlay consults the current phase's `context-profile` block. Recognized keys:

| Key | Values | Effect |
|---|---|---|
| `memory-retrieval` | `inherit` (default), `off` | When `off`, the composer skips composite vault retrieval for this turn. |
| `vault-injection-mode` | `inherit`, `always`, `headlines`, `on_demand` | Overrides `vault_retrieval.mode` for this turn. |
| `notes-injection` | `inherit`, `off` | When `off`, the composer skips `conversation_notes` injection. |
| `decision-slice` | `inherit`, `off` | When `off`, the composer skips `<decision_slice>` injection. |
| `clear-prior-phase-tools` | `true` (default), `false` | When `true`, the existing `clear_old_tool_results` runs aggressively on tool messages between the previous phase boundary and this one. |

All keys are optional; `inherit` means use the conversation's defaults.

### Phase prompt section

```
<workflow_phase run="weeknotes-w20" phase="draft" kind="inline">
You are in phase 'draft' of workflow 'weeknotes'.

Phase prompt:
  [body of phases/draft.md]

Available transitions (use phase_advance):
  - research — Sources are thin, fetch more
  - review   — Draft complete, ready for user

No other transition targets are available from this phase.
</workflow_phase>
```

Appended after existing system-prompt sections (`<skill_catalog>`, `<loaded_skills>`, etc.).

### No new composer modes

Workflow runs piggyback on the existing `INTERACTIVE` mode with overlay-applied overrides. `HEARTBEAT` / `SCHEDULED` / `CHILD_AGENT` ignore the workflow overlay entirely.

## Cross-phase context preservation

Phase-boundary tool clearing (default on) discards prior-phase tool outputs from the composer's view. Agents that need to retain context across phases use the existing always-loaded `notes_append` / `notes_read` tools — notes are stored at `{workspace}/conversations/{conv_id}.notes.md`, auto-injected into context, and not affected by tool-result clearing.

**Convention:** phase prompts should instruct the agent to record a 1-2 sentence phase summary via `notes_append` before calling `phase_advance`. This documents what each phase concluded and seeds the next phase with portable context.

The engine does not introduce workflow-scoped notes in v1. If conversation-level notes prove insufficient across multiple concurrent runs, a `workflow_note_*` family scoped per-run can be added later.

## Run lifecycle and state

### State machine

| State | Meaning |
|---|---|
| `running` | Normal turn-by-turn execution |
| `paused-gate` | Waiting on user response to a gate |
| `paused-subagent` | Waiting on child-agent completion |
| `done` | Terminal phase reached; no outgoing edges |
| `error` | Last operation failed; user intervention needed |

### state.json shape

```json
{
  "workflow": "weeknotes",
  "slug": "w20",
  "run_id": "2026-05-19-1402-weeknotes-w20",
  "status": "running",
  "current_phase": "draft",
  "created_at": "2026-05-19T14:02:00+00:00",
  "updated_at": "2026-05-19T14:35:12+00:00",
  "history": [
    {
      "from": null,
      "to": "gather",
      "edge_index": null,
      "gate_response": null,
      "reason": "initial",
      "timestamp": "2026-05-19T14:02:00+00:00"
    },
    {
      "from": "gather",
      "to": "draft",
      "edge_index": 0,
      "gate_response": null,
      "reason": "subagent complete",
      "timestamp": "2026-05-19T14:28:43+00:00"
    }
  ],
  "pending_gate": null,
  "pending_subagent": null,
  "error": null
}
```

Persistence is atomic: write `.tmp`, fsync, rename. Per-run `asyncio.Lock` (lazily created, keyed by run-id) serializes concurrent advances against the same run.

### Per-turn flow

1. `ContextComposer.compose()` calls `WorkflowOverlay.consult(ctx)`.
2. Overlay reads `ctx.skills.data['current_workflow_run']`, loads `state.json`, finds current phase, returns overrides.
3. Tool-list builder calls the workflow engine's `get_tools(ctx)`:
   - Resolves the phase's `tools:` (glob expansion)
   - Dynamically constructs `phase_advance` with current-phase enum + descriptions
   - Adds always-on engine tools
4. LLM runs, eventually calls `phase_advance(target, reason)`.
5. Engine looks up the matching edge:
   - No gate → write state, append history, return success; agent loop continues with new phase active.
   - Gate → return `ToolResult(end_turn=EndTurnConfirm(...))`. State → `paused-gate`. On approve: advance to edge target. On deny: advance to `gate.on-deny`.
   - Target is a subagent phase → on the next iteration, engine dispatches the subagent.
6. Subagent dispatch: write `paused-subagent`, spawn child via existing `delegate_task` plumbing, wait for completion notification, verify `outputs:`, advance per `next-phases` (or → `error` if outputs missing).
7. On terminal phase: state → `done`, emit `workflow_complete` event.

### Cross-conversation discovery

`workflow_list` walks `workspace/workflows/*/runs/*/state.json` directly — the file system is the source of truth. The `current_workflow_run` pointer in `skills.data` is just a per-conversation focus; a user can `workflow_switch` to any run created from any conversation.

## V1 scope

### Ships

- `src/decafclaw/workflow/` module (types, loader, runs, engine, context overlay)
- `src/decafclaw/tools/workflow_tools.py` with engine management tools
- Skill loader branch for `kind: workflow`
- ContextComposer integration hook
- **Demo workflow skill** — small bundled workflow that exercises every engine code path (inline phase, subagent phase, gated edge, branching `next-phases`, backward edge, terminal phase). Acts as the "real downstream artifact" in the engine PR per the established harness-smoke-test convention.
- Tests (see below)
- Docs at `docs/workflows.md` describing the engine and authoring conventions

### Does not ship

- Migration of the `project` skill (separate decision, later PR)
- Migration of any external skill (weeknotes, dev-session, etc.)
- Conditional code-evaluated edges
- Input-widget gates (only review gates)
- Workflow-scoped notes (`workflow_note_*`)
- Nested sub-workflows

## Demo workflow

A small bundled skill at `src/decafclaw/skills/workflow_demo/` (name TBD at plan time) that exercises every engine feature. The demo should be useful enough to be more than a toy — final shape decided during planning. Candidate framings to consider:

- A focused "research + summarize" workflow: subagent gather → inline outline (branching back to gather if thin) → inline draft → review gate → terminal publish
- A simplified version of the dev-session protocol that does not replace `project`

Whichever framing wins, the demo's job is to prove the engine handles all the moving parts in one place. The exact prompts and phase shape are planning concerns, not spec concerns.

## Error handling

| Failure | Engine response |
|---|---|
| `phase_advance` called with invalid target | Tool error listing valid targets with `when:` clauses. State unchanged. |
| Gate denied | Transition to `gate.on-deny`. Normal flow continues. |
| Subagent phase completes, `outputs:` missing | State → `error`. Notification emitted. Run pauses; user can intervene. |
| Subagent phase fails (child errors) | Same as above. |
| Workflow definition fails to load | Loader logs warning; workflow does not appear in `workflow_list`. Other workflows unaffected. |
| `state.json` corrupted | Run dropped from `workflow_list` with warning; run dir preserved on disk for manual inspection. |
| Two turns try to advance the same run concurrently | Per-run `asyncio.Lock` serializes; second waiter sees post-transition state. |

Recovery is intentionally minimal in v1: the user can `workflow_switch` away, manually inspect / fix files, or abandon the run. Future iterations may add a `workflow_recover` tool or richer error states.

## Testing

### Unit tests (deterministic, no LLM)

- `types.py`: dataclass round-trips, equality
- `loader.py`: valid workflows load; invalid workflows fail at expected validation gate (missing edge target, undeclared phase, bad YAML, missing prompt file, subagent constraints violated)
- `runs.py`: create / load / list / switch persistence, atomic write, transition history append, cross-conversation `workflow_list` walk
- `engine.py`: every transition shape (no gate, with gate approve, with gate deny, subagent advance, subagent missing output, terminal) drives `state.json` to expected next state
- `workflow_tools.py`: dynamic `phase_advance` enum reflects current phase; `workflow_status` includes valid targets with `when:` text
- `context.py`: overlay-applied `context-profile` flags actually flip composer branches (memory retrieval off → no `vault_references` message; notes injection off → no `conversation_notes` message; phase-boundary clearing → prior phase's tool messages stubbed)

### Fixture workflows (under `tests/workflow/fixtures/`)

- `linear/` — 3-phase chain, no gates, no subagents (smoke test of basic flow)
- `branching/` — phase with multiple `next-phases` (proves enum + routing)
- `gated/` — phase with gate edge (proves EndTurnConfirm integration)
- `subagent/` — phase with `kind: subagent` + outputs (proves child dispatch + post-condition check)
- `cyclic/` — phase with backward `next-phases` edge (proves revision loops)

### Eval cases (real LLM)

- `tool_choice` cases: in a 2-edge phase with distinct `when:` clauses, the LLM picks the matching target reliably. Guards the dynamic-enum + tool-description routing surface per the project convention "new or sharpened tool description → add a tool_choice case."
- One full-workflow eval: agent completes the fixture `branching` workflow end-to-end with correct routing decisions, bounded by `max_tool_iterations`.

Use positive `expect_tool` assertions on `phase_advance` targets with tight `max_tool_calls`; avoid `expect_no_tool` (reflection retries can introduce noise).

## Open questions for planning

- **Demo workflow concrete shape.** Decided at plan time; spec does not constrain it beyond "exercises every engine feature."
- **`subagent-skill:` semantics.** Should the engine pass the run's `artifacts/{phase}/` as the child's working dir, or does the referenced skill manage its own outputs? V1 default: engine still owns the artifacts dir, but the child skill writes wherever it wants and the engine post-condition checks `outputs:` against the artifacts dir as usual.
- **Tool glob expansion timing.** Resolve globs at workflow load (cached) or per turn (dynamic registry)? Per turn is safer if the registry is dynamic; cached is faster. Decide based on `tool_registry.py` semantics at plan time.
- **`workflow_list` performance.** Walking `workspace/workflows/*/runs/*/state.json` is fine for tens of runs; if the list grows to hundreds, an index file may be needed. Defer.

## References

- Issue #255 — Design: first-class workflow abstraction (LangGraph-inspired)
- `src/decafclaw/skills/project/state.py` — current state-machine implementation pattern
- `src/decafclaw/skills/project/tools.py` — current dynamic `get_tools(ctx)` and `EndTurnConfirm` usage
- `docs/context-composer.md` — composer modes, retrieval modes, decision slice, tool-result clearing
- `docs/notes.md` — always-loaded conversation notes (mechanism for cross-phase context)
- `docs/skills.md` — skill loader, dynamic tools, scan order
- LangGraph — graph-based agent workflows with mechanical control flow
- Anthropic "Building Effective Agents" — workflow vs. agent distinction
