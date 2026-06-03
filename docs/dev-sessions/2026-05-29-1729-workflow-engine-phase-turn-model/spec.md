# Workflow Engine — Phase-Turn Model

> **⚠ Superseded as the implementation target.** Sophie's PR #557 analysis (<https://github.com/lmorchard/decafclaw/pull/557#issuecomment-4587813874>) reframed the diagnosis: `phase_advance` as an LLM-emitted tool is itself the failure mode, not something to wrap in a nudge loop. New direction = code-driven engine spike — see [`../2026-05-31-1223-code-driven-engine-spike/notes.md`](../2026-05-31-1223-code-driven-engine-spike/notes.md). This spec stays useful as the "engine drives turn scheduling" framing — that part survives; what doesn't is `phase_advance` as the tool the LLM must remember to emit.

**Issue:** [#255 — Design: first-class workflow abstraction (LangGraph-inspired)](https://github.com/lmorchard/decafclaw/issues/255)
**Prior specs (informing this one, not superseded conceptually):**
- [`../2026-05-19-2121-workflow-engine/spec.md`](../2026-05-19-2121-workflow-engine/spec.md) — original cross-conv design.
- [`../2026-05-21-1732-workflow-engine-rework/spec.md`](../2026-05-21-1732-workflow-engine-rework/spec.md) — conv-scoped rework.

**Branch:** `feat/255-workflow-engine` (PR #557). This iteration continues in place.
**Date:** 2026-05-29
**Status:** Spec — pending review

## Why a third iteration

The conv-scoped rework removed the cross-conversation machinery and ran clean unit + smoke tests at the engine + tool layer. A live web-UI smoke against the demo workflow revealed the deeper problem the prior iterations didn't address: **the LLM doesn't drive the workflow forward.**

In the live smoke:

- `workflow_start` ran. `required-skills: [tabstack]` activated. The gather subagent was dispatched synchronously.
- The subagent wrote `sources.md` (a "topic not provided" stub — see Bug 1 below) to the parent's conv-scoped artifacts dir, proving the conv_id override worked for artifact paths.
- The engine auto-advanced to the `draft` phase.
- `workflow_start` returned "Current phase: draft. Status: running. Use phase_advance to move forward."
- Iteration 2 of the parent agent's turn: the LLM narrated "Okay, I've started the workflow. The first phase is draft." and ended the turn.

Nothing went wrong mechanically. The state machine, the subagent dispatch, the conv-scoped paths, the dynamic `phase_advance` enum — all the wiring proved correct. What failed was the **assumption that the LLM would naturally keep iterating through phases.** The LLM treated `workflow_start` as a discrete task completion and stopped, exactly as it had in the pre-rework smoke. The architecture passed every unit test that didn't require an LLM, and every test that did require an LLM has hit the same stall.

Secondary issue: the conv_id override (added in the rework's Task 6 to make `workflow_artifact_*` resolve to the parent's directory) caused the subagent's exchange to be archived in the parent's JSONL file alongside the parent's own messages. The LLM in iteration 2 saw a polluted history with the subagent's "I have no topic" exchange interleaved with its own tool calls, which made the stall worse — but the stall happens even with a clean history.

The root issue is structural: in the current model, the LLM is the primary driver. The conversation loop calls the LLM, the LLM picks tools, the engine reacts to tool calls. For free-form chat, that's right. For a workflow that needs to walk N phases reliably, it's too loose — the LLM has no strong reason to keep working past the first `phase_advance`/`workflow_start` call.

This spec flips the relationship: **the engine drives the workflow, and the LLM is the worker for each phase.** Each phase is a discrete conversation turn the engine enqueues automatically. The LLM's job per turn is to complete the current phase's task and call `phase_advance` to signal "done, route me." The user can interrupt between phases by sending their own message; phase context still applies to that user-driven turn.

## Goals

A workflow author should be able to:

1. Declare a multi-phase task in markdown (frontmatter + body) — **unchanged**.
2. List `required-skills:` in SKILL.md frontmatter; engine auto-activates them on `workflow_start` — **unchanged**.
3. Constrain the agent per phase: tool catalog, system prompt, context-composer profile — **unchanged**.
4. Route between phases as a graph with annotated `when:` clauses, via dynamically-regenerated `phase_advance` — **unchanged**.
5. Gate edges with user-facing review confirmations — **unchanged**.
6. Have phases run as **discrete, observable turns** the user can see and interrupt — **NEW**.

A workflow runs to completion within a single conversation, walking phase-by-phase as separate turns. The user can interject between phases. Phases that need user input (e.g., interview-style) just don't call `phase_advance` until the user has provided the answer — the LLM works the phase across multiple turns naturally.

## Non-goals (mostly unchanged)

- Cross-conversation workflow resumption — explicitly dropped.
- Concurrent workflows within one conversation — at most one active workflow per conversation.
- Conditional edge logic evaluated by code (LLM-routed `when:` only).
- Nested workflows.
- Input-widget gates (only review gates in v1).
- Migration of data from any prior architecture (branch hasn't merged).

## Architecture overview

### The split: engine drives flow, LLM drives routing

The two responsibilities the prior iterations conflated:

- **Driving flow** — what keeps the workflow moving from one phase to the next, what schedules the next iteration when a phase isn't yet done, what handles the mechanics of subagent dispatch / gate buttons / state persistence. This is **engine-owned**. The LLM doesn't have to remember it's in a workflow or decide "I should keep iterating." If the LLM stops feeling productive, the engine still drives.

- **Deciding routing** — when a phase has multiple `next-phases`, which target to advance to (or which backward edge to loop to). This is **LLM-owned and inherent**. Only the LLM can read the situation and choose "review" vs. "gather" or "back to research." The engine has no business making this call.

The seam between them is `phase_advance(target_phase_id, reason)`. The LLM picks the target — that's its routing decision, schema-constrained to the phase's `next-phases`. The engine then carries out the mechanics: applies the transition, enqueues the next phase turn, dispatches subagents, fires gate UIs.

Everything else in this spec implements that split.

### The phase-turn model

The conversation is a sequence of agent turns. A workflow is a sequence of phase turns interleaved with optional user turns. Each phase corresponds to **one or more turns** of kind `WORKFLOW_PHASE`. The engine enqueues these turns; the user can interject by typing into the same conversation.

```
[user]   /research_brief start the history of movable type
[agent]  workflow_start tool call (ends turn, enqueues next)
[gather] CHILD_AGENT turn (subagent w/ phase prompt as system, tabstack tools,
         own conv_id for archive, parent_conv_id for workflow paths)
         → child writes sources.md, calls phase_advance(draft), turn ends
         → engine enqueues draft turn
[draft]  WORKFLOW_PHASE turn (main agent, draft phase prompt as system,
         draft tool whitelist, parent's conv_id)
         → reads sources.md, writes brief.md, calls phase_advance(review)
         → turn ends, engine enqueues review turn
[review] WORKFLOW_PHASE turn → presents brief, calls phase_advance(publish)
         which has a gate → EndTurnConfirm shows Approve/Deny, turn pauses
[user]   clicks Approve (via UI button)
         → gate callback fires phase_advance(publish), engine enqueues publish turn
[publish] WORKFLOW_PHASE turn → writes vault page, terminal phase, state DONE
```

Each phase is a focused, single-purpose turn. The user sees a clear sequence of agent activities, each labeled with its phase. They can pause anything by interrupting with a chat message — the next turn (USER kind) sees the workflow is active and applies the current phase's context to the agent.

### Each phase turn's compose() shape

The big change inside `ContextComposer.compose()` for `WORKFLOW_PHASE` mode: the phase prompt becomes the **primary** system context, not an additive overlay. Concretely:

- The general "you are decafclaw, a friendly AI assistant" preamble is suppressed or compressed.
- The system prompt is built around the `<workflow_phase>` block: the phase body, the available transitions with `when:` clauses, a small "you are working in this phase; complete its task and call `phase_advance`" framing.
- The phase's tool whitelist is enforced via `ctx.tools.allowed` (already implemented).
- The dynamic `phase_advance` schema is injected with `priority: critical` (already implemented).

The composer modes become: `INTERACTIVE` (general chat), `WORKFLOW_PHASE` (NEW — phase turn, main agent), `CHILD_AGENT` (subagent), `HEARTBEAT`, `SCHEDULED`. `WORKFLOW_PHASE` is similar to `INTERACTIVE` but with the phase-context-replaces-system-prompt rule.

For a USER turn (user types a message while a workflow is active), compose() can detect the active workflow and apply phase context similarly — so the agent in a user-driven turn still operates in the phase frame. The user's message is appended to the otherwise-phase-shaped context.

### Turn scheduling

Both `tool_workflow_start` and `tool_phase_advance` become:

```python
async def tool_workflow_start(ctx, name):
    # ... validate, activate required-skills, init state ...
    state = init_workflow_state(ctx, workflow=name, initial_phase=wf.initial_phase)
    initial_phase = wf.phase(state.current_phase)
    _enqueue_phase_turn(ctx, state, initial_phase)
    return ToolResult(
        text=f"Started workflow '{name}'. Engine will dispatch phase '{state.current_phase}' as the next turn.",
        end_turn=True,
    )

async def tool_phase_advance(ctx, target_phase_id, reason):
    state, wf = _get_workflow(ctx)
    # ... apply transition, possibly fire gate (separate path) ...
    if state.status == RunStatus.DONE:
        return ToolResult(
            text=f"Workflow '{state.workflow}' complete.",
            end_turn=True,
        )
    next_phase = wf.phase(state.current_phase)
    _enqueue_phase_turn(ctx, state, next_phase)
    return ToolResult(
        text=f"Advanced to phase '{state.current_phase}'. Engine will dispatch next turn.",
        end_turn=True,
    )
```

`_enqueue_phase_turn(ctx, state, phase)` is a small helper:

```python
def _enqueue_phase_turn(ctx, state, phase):
    if phase.kind == PhaseKind.SUBAGENT:
        # Child agent runs the phase. Own conv_id, parent's for workflow paths.
        child_conv_id = f"{ctx.conv_id}--wf-{state.workflow}-{phase.id}-{secrets.token_hex(4)}"
        ctx.manager.enqueue_turn(
            kind=TurnKind.CHILD_AGENT,
            conv_id=child_conv_id,
            parent_conv_id=ctx.conv_id,
            workflow_phase=phase.id,
            tools_allowed=_resolve_phase_tools(phase.tools),
            # ... etc
        )
    else:
        # Inline phase = main agent's next turn.
        ctx.manager.enqueue_turn(
            kind=TurnKind.WORKFLOW_PHASE,
            conv_id=ctx.conv_id,
            workflow_phase=phase.id,
        )
```

The manager processes turns one at a time per conversation (existing constraint). User messages interleave naturally — they queue behind any in-flight phase turn.

### Phase-internal loop: "are you done?" before exiting

The phase-turn model so far covers transitions BETWEEN phases. But there's a within-phase failure mode that the smoke test ran into: the LLM completes (or thinks it completes) the phase's work, ends the turn with a text response, but never calls `phase_advance`. In a strict "phase = one turn" model this stalls the phase indefinitely — the engine has no signal that the LLM is done, just that the LLM stopped tool-calling.

The fix is to make the agent loop inside a `WORKFLOW_PHASE` turn phase-aware. The existing `TurnRunner` agent loop exits when the LLM returns a response with no tool calls. For a phase turn, that exit gets one extra check before bailing: **did `phase_advance` fire this turn?**

- **Yes** — the LLM explicitly signaled "done, route me here." Turn ends. Engine enqueues the next phase turn per `phase_advance`'s target.

- **No, and the loop has iterations left** — inject a synthetic user-role message: "Looks like you've stopped working on this phase, but you haven't called `phase_advance` yet. If the phase is complete, call `phase_advance` with the right target (review / gather / etc.). If not, finish the remaining work." Continue the loop with this prompt.

- **No, and we've hit the phase-internal iteration cap** — mark the phase as `error` with a clear message ("Phase X ended without calling `phase_advance` after N nudges"). State persists; user can `workflow_abort` or manually intervene.

The cap is small (default 2 nudges, so 3 total attempts at the phase before bailing). Workflows can override per phase via a `max_continuations:` field in the phase frontmatter. The default lives on `config.workflow.max_phase_continuations`.

**For subagent phases, the "are you done?" check is mechanical, not LLM-based.** When the child agent's turn ends, the engine checks declared `outputs:` files. If all present, the engine auto-fires `phase_advance` against the single `next-phases` edge (subagent phases only have one). If missing, mark `error`. This is already the conv-scoped rework's behavior — no LLM nudge needed for subagents.

**Why in-turn rather than across-turn:** the alternative would be to end the turn cleanly and enqueue a fresh follow-on turn with a "continue" prompt. That works but is more expensive (fresh compose, fresh archive entry) and surfaces to the user as "the agent stalled, then re-engaged" — uglier than the in-turn version where the phase appears as one continuous activity that took a couple iterations to complete.

**Why "are you done?" rather than just continuing silently:** the synthetic prompt explicitly frames the LLM's choice as "signal done via `phase_advance` OR keep working." Without the prompt, just iterating with no new input invites the LLM to repeat what it already said. The framing forces the binary decision.

**Pseudocode for TurnRunner's extended exit:**

```python
# Inside TurnRunner.run, when an iteration returns no tool calls:
if not response_has_tool_calls:
    workflow_state = load_workflow_state(self.ctx)
    is_phase_turn = self.ctx.task_mode == TaskMode.WORKFLOW_PHASE
    advanced_this_turn = self.workflow_advanced_this_turn  # tracked by tool_phase_advance

    if is_phase_turn and not advanced_this_turn and workflow_state \
            and workflow_state.status == RunStatus.RUNNING \
            and self.phase_continuations < self.config.workflow.max_phase_continuations:
        # Inject the synthetic nudge, increment counter, continue loop.
        nudge = {
            "role": "user",
            "content": (
                "You've stopped working on this phase, but haven't called "
                "phase_advance yet. If the phase is complete, call "
                "phase_advance with the right target. If not, finish the "
                "remaining work."
            ),
        }
        self.messages.append(nudge)
        self.phase_continuations += 1
        continue  # back to top of loop

    if is_phase_turn and not advanced_this_turn and \
            self.phase_continuations >= self.config.workflow.max_phase_continuations:
        # Mark phase error and exit.
        workflow_state.status = RunStatus.ERROR
        workflow_state.error = (
            f"phase '{workflow_state.current_phase}' ended without "
            f"phase_advance after {self.phase_continuations} continuations"
        )
        save_workflow_state(self.ctx, workflow_state)
        # Fall through to normal turn end.

    # ... normal turn-end code ...
```

The `workflow_advanced_this_turn` flag is set by `tool_phase_advance` when it fires. TurnRunner reads it to decide whether to nudge or exit.

### `parent_conv_id` field

Added to `Context` as a separate field from `conv_id`:

- `ctx.conv_id` — drives archive writes. Each agent (main, child) has its own.
- `ctx.parent_conv_id` — set on child agents to the parent's `conv_id`. `conv_state` path helpers resolve to `parent_conv_id or conv_id`, so workflow tools called from a child resolve to the parent's directory.

Default `parent_conv_id = ""` for main agents (falls through to `conv_id`). Set explicitly on child agents during turn setup.

This fully resolves Bug 2 from the smoke test — subagent archives are separate again; workflow paths still resolve correctly.

### Gates fire as before (with one tweak)

A gated edge still fires `EndTurnConfirm`. The current turn ends with buttons surfaced to the user. The callback wires:

- `on_approve` → engine applies the transition to the gate's edge target, then enqueues that target's turn.
- `on_deny` → engine applies the transition to `gate.on_deny`, then enqueues that target's turn.

The user's button click triggers the enqueue. No new mechanism — just the existing callback pattern with the post-transition `_enqueue_phase_turn` call.

### User interruption is automatic

The user types a message while the workflow is mid-flow:

1. Their message becomes a USER turn enqueued behind whatever's in flight.
2. When the USER turn fires, `compose()` detects the active workflow and current phase, applies that phase's context (system prompt, tool whitelist).
3. The agent in that turn reads the user's message, can answer questions, can call `phase_advance` if appropriate.
4. Turn ends — engine doesn't automatically enqueue another phase turn because no `phase_advance` was called. The workflow waits.
5. The agent has to call `phase_advance` (or `workflow_abort`) to drive forward; otherwise, the workflow stays in the current phase indefinitely. The user's next message is the next trigger.

For phases that explicitly need user input (interview-style), the phase prompt says something like: "Ask the user about X. When you have the answer, call `phase_advance(next, reason='...')`." The first phase turn fires, agent asks the question via a regular text response, ends turn (no `phase_advance`). User answers. USER turn fires with phase context, agent reads the answer, calls `phase_advance`. Engine enqueues the next phase turn. Natural multi-turn phase.

### What's deleted from the conv-scoped rework

- `engine.dispatch_subagent_if_needed` and its sync chain logic — replaced by `_enqueue_phase_turn(kind=CHILD_AGENT)`.
- `subagent.py:_run_child` synchronous dispatcher — replaced by the manager's child-turn machinery + the new `CHILD_AGENT` compose path that takes the phase prompt as system context.
- `child_ctx.conv_id = parent_ctx.conv_id` override (from rework Task 6) — replaced by `parent_conv_id`.
- The "subagent dispatch is synchronous within a tool call" pattern entirely.

### What's added

- `TurnKind.WORKFLOW_PHASE` enum value.
- `Context.parent_conv_id: str = ""` field.
- `ContextComposer` mode `WORKFLOW_PHASE` (and updated `CHILD_AGENT` to also apply phase-as-system-prompt when invoked from workflow dispatch).
- `_enqueue_phase_turn` helper on the engine.
- Detection inside `compose()` for "workflow is active in this conversation; apply phase context even though the turn is USER kind."
- **Phase-internal loop** — `TurnRunner` extended for `WORKFLOW_PHASE` turns to inject a "you stopped without `phase_advance`" nudge and continue iterating, bounded by `config.workflow.max_phase_continuations` (default 2). After cap, the phase is marked `error`.
- `workflow_advanced_this_turn` flag on `TurnRunner` set by `tool_phase_advance` so the loop knows whether to nudge or exit.
- Optional `max_continuations:` field in phase frontmatter for per-phase overrides.
- `config.workflow.max_phase_continuations: int = 2` config field.

### What survives unchanged from the conv-scoped rework

- Conv-scoped state at `conversations/{conv_id}/workflow.json` + `artifacts/`
- `required-skills:` in SKILL.md frontmatter and auto-activation on `workflow_start`
- Per-phase tool catalog hard-gate via `ctx.tools.allowed`
- Dynamic `phase_advance` enum + `priority: critical`
- Edge gates via `EndTurnConfirm`
- Phase prompt format (`phases/*.md` with frontmatter)
- `workflow_start` / `workflow_status` / `workflow_abort` / `workflow_artifact_*` tools (with the small wiring changes above)
- The loader, validation, registry, types
- Conv-scoped `init_workflow_state` / `archive_workflow_state` / `conv_lock`

## State machine (unchanged from rework)

`NO_WORKFLOW ↔ ACTIVE` per conversation. Sequential workflows after `workflow_abort` or terminal state. The `WorkflowState` dataclass shape stays. Statuses stay: `running`, `paused-gate`, `paused-subagent` (now mostly transient — between phase turn end and next enqueue), `done`, `error`, `aborted`.

`paused-subagent` becomes a brief intermediate state: when a phase_advance lands on a subagent, the parent's state is set to `paused-subagent` while the child turn runs. When the child calls `phase_advance` from inside its turn, the engine applies the parent's transition and clears `paused-subagent`. If the child crashes / aborts, `paused-subagent` persists until something cleans it up (e.g., `workflow_abort`).

## What this gets us

### Wins

1. **The workflow actually runs.** Each phase fires as its own turn. The LLM in each turn has one focused job — complete this phase, call `phase_advance`. Stalls would now be diagnosable to a specific phase rather than "the LLM just stopped after one tool call."
2. **The "LLM forgot to call `phase_advance`" failure mode is bounded.** The phase-internal loop nudges the LLM up to `max_phase_continuations` times before bailing. A phase either completes (LLM calls `phase_advance`), explicitly errors (cap exhausted), or gets aborted by the user. No silent indefinite stalls.
3. **Clean separation between subagent and parent archives.** With `parent_conv_id`, child agents archive to their own JSONL; workflow tools still resolve to the parent's directory. Bug 2 from the smoke test goes away cleanly.
4. **Phase prompts are load-bearing.** Replacing (not appending) the system prompt means the phase prompt actually shapes the LLM's behavior, not just nudges it.
5. **User interruption is free.** Users can interject at any phase boundary or between phase-internal iterations (within the cap), and the agent in that interruption sees the right context.
6. **Phases can span multiple turns naturally.** Interview-style phases work without special infrastructure: the phase just doesn't call `phase_advance` until it has what it needs.
7. **Observability.** Each phase shows up as a separate turn in the conversation UI. Users can see "the agent is in the gather phase" rather than mysterious tool calls happening inside `workflow_start`. Phase-internal continuations show as the same turn with multiple agent responses.

### Tradeoffs / costs

1. **More turns visible to the user.** A 4-phase workflow is now 4+ agent turns instead of 1-2. The chat reads longer. The cost is also higher (per-turn compose, per-turn LLM call, per-turn archive). For workflows with many small phases, this is real overhead. Mitigation: phases should be coarse enough that one phase = one meaningful LLM-driven task. Workflow authors who slice too finely will feel it.

2. **Per-turn LLM calls add latency.** A workflow that previously ran 4 phases inside one tool call now spans 4 user-visible turns. For demos this is fine (latency is OK); for batch automation it's slower.

3. **The "general assistant" preamble is suppressed inside phases.** The agent inside a `WORKFLOW_PHASE` turn won't help with off-topic requests as naturally. If the user interjects "hey what's 2+2?" during a research_brief workflow, the agent in that user-turn sees the phase context dominantly. Probably fine — the agent can still answer simple things, just framed by the phase. But if it gets weird, we may want phase prompts to say "if the user asks something off-topic, answer briefly and return to the phase."

4. **Architectural reach.** Touches `Context`, `ContextComposer`, `ConversationManager` turn scheduling, `agent.py` compose logic, plus the workflow engine itself. More files than the conv-scoped rework, which mostly stayed inside `workflow/` and `tools/`.

5. **Existing turn scheduling assumptions.** `ConversationManager` currently doesn't auto-enqueue follow-on turns of its own accord — turns come from user messages, heartbeats, schedules, or wake events. Adding "engine enqueues a follow-on phase turn" is a new pattern. Needs careful design around: what if the manager is shutting down? What if the conversation is busy? Reentrance under tool calls (the tool returning `end_turn=True` AND enqueuing the next turn — order matters).

6. **`paused-subagent` becomes a transient state.** Currently it's a stable "the engine is dispatching a child" state. In the new model, it's mostly "the child turn is queued or in-flight." Cleanup semantics on child failure need to be thought through — if the child crashes mid-turn, parent state stays at `paused-subagent` and only gets unstuck via `workflow_abort` or a timeout. Should there be a timeout?

7. **Risk that this doesn't fix the underlying behavior.** The proposition is "with a stronger framing (phase prompt as system + one focused job + small clear task), the LLM will actually work the phase." If the LLM still stalls (e.g., gets confused by the inverted turn structure), we'd need another iteration. The smoke we ran was incomplete — we don't yet know if a focused-system-prompt + one-job-per-turn model fixes the stall. We'd be betting on that.

### What this doesn't address (still open)

- **Bug 1 from the smoke (topic-passing):** `workflow_start` accepts no arguments; the subagent has no way to receive the user's topic from `$ARGUMENTS`. Independent of the phase-turn model. Likely fix: add a `params: dict` parameter to `workflow_start` that gets stored on `WorkflowState` and exposed to phase prompts via `{{params.topic}}`-style interpolation. Or simpler: workflow_start accepts the user message verbatim, and phase prompts can reference it.
- **Engine doesn't write `workflow_phase_boundary` markers** (#562). Composer reads them; engine writes none yet. Phase-boundary tool clearing remains a no-op. Could be wired into `_enqueue_phase_turn` cheaply.
- **`decision-slice: off` override** (#563) — still cross-subsystem with compaction.
- **`subagent-skill:` integration test** (#564) — escape hatch supported in loader + subagent, no real-skill round-trip test.

## V1 scope under the phase-turn model

### Ships

- `TurnKind.WORKFLOW_PHASE` enum value + manager scheduling support.
- `Context.parent_conv_id` field + `conv_state` path helpers consult it.
- `ContextComposer` learns `WORKFLOW_PHASE` mode + phase-as-primary-system-prompt rule.
- `tool_workflow_start` / `tool_phase_advance` end_turn=True and enqueue follow-on phase turns.
- Subagent dispatch reframed as `_enqueue_phase_turn(kind=CHILD_AGENT)`; `_run_child` and `dispatch_*` synchronous helpers deleted.
- Demo workflow: works end-to-end against a real LLM, walks gather → draft → review (gate) → publish to terminal.
- Live smoke + at least one passing eval case that exercises the full walk.
- Test suite updates: replace synchronous subagent dispatch tests with turn-enqueue tests.

### Does not ship (deferred items, unchanged from prior specs)

- Phase-boundary markers written by engine (#562) — though they'd be cheap to add inside `_enqueue_phase_turn`. Might land here as a small bonus.
- `decision-slice: off` override (#563).
- Input-widget gates (only review gates).
- Workflow-scoped notes.
- `params:` argument on `workflow_start` for topic-passing (will need a follow-up issue).

## Open questions for review

1. **Should USER-kind turns mid-workflow apply phase context to their system prompt?** Probably yes — the agent should still be in "workflow mode" when responding to the user. But there's a question of how strong: replace system prompt entirely, or apply a lighter overlay so the agent can also help with off-topic questions? Lean: apply phase context for USER turns same as for WORKFLOW_PHASE turns, accept that the agent will be less general while a workflow is active.
2. **Should the engine timeout a `paused-subagent` state?** If a child agent crashes silently or takes too long, the parent stays stuck. Adding a timeout (e.g., 5 min) that flips to `error` would prevent stuck workflows. Or: rely on the child timeout already in `delegate.py`'s pattern. Likely the latter — child timeout already exists.
3. **Should `workflow_start` accept a `params` dict for phase-prompt interpolation?** Addresses Bug 1 directly. Small additive feature. Could land here or follow-up.
4. **Should the phase-prompt-as-system-prompt rule preserve any of the general preamble?** E.g., a one-liner about decafclaw's identity, or the user's name? Or full replace? Lean: full replace within `WORKFLOW_PHASE` mode; lighter "phase-context-aware" overlay for USER turns mid-workflow.
5. **Token-cost concerns for many-phase workflows?** Each phase is a fresh turn = fresh full compose call (system prompt, tools, etc.). Worth modeling for the research_brief case (4 phases ≈ 4× compose+LLM-call overhead). If it's prohibitive, we could consolidate adjacent inline phases — but probably premature optimization.

## References

- Original spec (cross-conv): [`../2026-05-19-2121-workflow-engine/spec.md`](../2026-05-19-2121-workflow-engine/spec.md)
- Conv-scoped rework spec: [`../2026-05-21-1732-workflow-engine-rework/spec.md`](../2026-05-21-1732-workflow-engine-rework/spec.md)
- Conv-scoped rework plan: [`../2026-05-21-1732-workflow-engine-rework/plan.md`](../2026-05-21-1732-workflow-engine-rework/plan.md)
- Issue #255 — Design: first-class workflow abstraction
- Issue #561 — Priority decision (resolved by the conv-scoped rework)
- Issues #562, #563, #564 — Carry-forward concerns
