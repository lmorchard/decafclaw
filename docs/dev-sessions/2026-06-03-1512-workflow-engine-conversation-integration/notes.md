# Workflow engine: conversation integration redesign (handoff)

**Status:** Brainstorm complete (2026-06-03). See [spec.md](./spec.md) for the finalized design. Cold-restart handoff preserved below.

**Issue:** [#255](https://github.com/lmorchard/decafclaw/issues/255) · **Prior PR (closed-as-superseded):** [#572](https://github.com/lmorchard/decafclaw/pull/572) · **Prior session:** [`2026-06-01-1055-workflow-step-primitive-design`](../2026-06-01-1055-workflow-step-primitive-design/)

---

## What happened to PR #572

The step-primitive engine landed mechanically — six step kinds, jinja templates, state model, ~150 tests, three bundled smoke workflows, eval cases that passed on Flash. The engine MECHANISM is sound. The PR closed without merging because **the integration with the agent loop is structurally wrong**.

### The failure shape

In live testing, `/interview` started the workflow but failed mid-cycle in multiple ways:

1. **First attempt:** widget shown, user responded, workflow stayed in `paused_user_input` because the agent's next LLM iteration started before `engine.resume_user_input` (scheduled via `loop.create_task`) had a chance to complete. Race condition.

2. **Patch 1:** made the on_response callback async + awaited resume inline. Workflow advanced to next pause — but then no widget surfaced for the new question. The agent's next LLM iteration saw a paused workflow and improvised, calling Television's `ask_user_multiple_choice` and trying to re-call `workflow_start` with bogus args.

3. **Patch 2:** made `workflow_start` idempotent for paused-user-input + same-name calls (returns the current pause's widget). The agent could now recover IF it called workflow_start. But in real testing, the agent saw the synthetic user message and responded conversationally instead of calling the tool.

4. **Patch 3 (the realization):** made the callback LOOP internally — after `engine.resume_user_input`, if paused at another user_input, build a fresh `ConfirmationRequest` and `await ctx.request_confirmation` directly. Keeps the cycle inside the callback so the LLM never sees the intermediate state.

The user pushed back on patch 3: "if we determined the LLM cannot drive the workflow, we shouldn't be relying on it." Correct. Each patch was the same failure mode (LLM-as-control-flow) in a new costume — we kept patching symptoms.

### The structural issue

The workflow engine runs **nested inside a tool call** in the agent loop:

```
agent turn
  └─ LLM iteration
       └─ workflow_start tool call
            └─ engine runs
                 └─ widget pause (return ToolResult with widget)
       └─ agent loop awaits widget response
       └─ on_response callback fires
       └─ synthetic user message injected
  └─ next LLM iteration  ← LLM creeps back in here, every time
```

As long as the workflow lives inside a tool call, the LLM has opportunities to interfere at every boundary: deciding to invoke `workflow_start` in the first place, "responding" to synthetic messages mid-cycle, interpreting workflow output. Each patch we tried plugged one boundary; the next failure mode appeared at another.

**This is the failure mode the design thesis exists to prevent**, and the prior brainstorm didn't push on it. The 9 entangled design questions (phases, vocabulary, state, graph, authoring, subagents, gates, #557 survival, smoke target) covered the engine's *interior* well but hand-waved the integration: "user_input builds on existing confirmation infra." That hand-wave is the entire bug.

## The pivot

Workflows need to be a **peer** to agent turns, not nested inside them. The agent loop is not the only top-level driver of a conversation — workflows are another. The conversation manager dispatches between them.

Truly code-driven means:

- `/interview` does NOT go through an agent LLM call. The command system dispatches directly to the workflow engine.
- The workflow IS the conversation while it's running. It emits the question messages and widgets directly to the user (not as tool results).
- User responses route to the workflow's pending pause, not to an agent turn.
- The agent loop is **not invoked** while a workflow is active.
- When the workflow completes, control returns to "agent mode" — the next user message triggers a normal agent turn.

This is the architectural shape the next brainstorm needs to nail down.

## What's salvageable from PR #572

- **Engine modules** (largely intact for next iteration):
  - `src/decafclaw/workflow/types.py` — StepDef, StepKind, RunStatus, EdgeRef, RouteChoice, WorkflowDef, WorkflowState
  - `src/decafclaw/workflow/loader.py` — workflow.yaml parser + load-time validation
  - `src/decafclaw/workflow/engine.py` — `start_workflow`, `_run_to_suspension`, `_apply_step_result`, resume helpers
  - `src/decafclaw/workflow/step_executors.py` — `llm_call`, `tool_call`, `route`, `python`, `subagent` (all six minus user_input)
  - `src/decafclaw/workflow/jinja_env.py` — sandboxed environment
  - `src/decafclaw/workflow/conv_state.py` — per-conversation state persistence + lock
  - `src/decafclaw/workflow/subagent.py` — child agent dispatch (carry-forward from PR #557, native StepDef)
  - `src/decafclaw/workflow/registry.py` — workflow lookup
- **Bundled workflows** (the YAML + tools.py + prompts are sound):
  - `workflow_hello`, `research_brief`, `interview`
- **Skills-loader integration:** `kind: workflow` recognition in `src/decafclaw/skills/__init__.py`
- **Eval framework extension:** `evals/workflows.yaml` + `_EvalConversationManager` stub in `eval/runner.py`
- **Tests:** ~150 tests in `tests/workflow/` — engine, loader, executors, jinja, cycles, conv_state

## What probably has to be rewritten

- **`src/decafclaw/tools/workflow_tools.py`** — the LLM-facing thin tool surface. The new design likely deletes most of this. `workflow_start` becomes a direct command-dispatch target, not an LLM tool. `workflow_status`, `workflow_abort`, `workflow_artifact_read/write` may stay as LLM tools (for the agent to inspect/control workflows), but workflow_start as an LLM tool is a code smell — the LLM shouldn't be the one starting workflows.
- **`user_input` step kind executor** — the in-callback loop pattern is the wrong architecture. New design needs a completely different mechanism for surfacing widgets and capturing responses — probably the workflow engine emits these directly to the conversation transport, bypassing tool-result plumbing.
- **The synthetic-message injection path** — workflows shouldn't inject synthetic user messages into the conversation. Their output IS the conversation.
- **Skill SKILL.md imperative prompts** — these were band-aids for the LLM-driven dispatch. If `/command` doesn't go through the LLM, the prompts become irrelevant.
- **Confirmation-handler wiring for workflow user_input** — needs a different model that's not "agent loop awaits confirmation."

## Open architectural questions for this brainstorm

These are entangled. Resolve in roughly this order:

1. **Where does `/interview` go?** Does the command system dispatch directly to the workflow engine (skipping the agent loop)? Or does it create a "workflow turn" with its own dispatch path that's a sibling to agent turns?

2. **`ConversationManager` state model.** Does the manager track "active workflow" alongside or instead of "active agent turn"? Mutually exclusive? Both at once? What's the lifecycle?

3. **How are workflow messages rendered?** Currently, the workflow's questions are tool-result text + widget. If workflows emit messages directly, what's the wire shape? Do they look like assistant messages in the archive? Do they get a different role like `workflow_message`?

4. **How are user responses routed?** When a workflow has a pending user_input pause and the user submits to the widget, the response should route to the engine directly, not through an agent turn. The web/Mattermost transports need to know "this conversation is in workflow mode."

5. **What does the agent loop do during workflow execution?** Suspended (not invoked)? Allowed in for off-workflow messages? The user types a message mid-workflow — what happens? Probably the workflow either ignores it, treats it as the answer, or some configurable behavior.

6. **Subagent steps still need an agent loop.** Subagent dispatch spawns a child agent loop. That nesting is intentional and aligned (the LLM is doing focused work inside a child turn). How does that interact with the parent workflow's "no agent loop" model?

7. **LLM steps inside the workflow still happen.** `llm_call`, `route`, the agent-loop inside `subagent` — all involve LLM calls. These are FINE because they're constrained workers, not control-flow drivers. But the brainstorm should make the boundary explicit: which LLM calls are "constrained worker" vs "control driver." Anywhere not constrained-worker is code-territory.

8. **The pre-mortem.** Enumerate the ways the LLM could sneak back into control flow under the new design, and design them out. Examples to consider:
   - Agent calling `workflow_start` from inside an agent turn — should this be possible? Or workflows are only command-initiated?
   - What if the user types `/interview` mid-workflow? Mid-other-workflow?
   - What about scheduled tasks / heartbeat — can they start workflows?
   - Subagents calling `workflow_start` on a child workflow?

9. **Live integration testing.** `src/decafclaw/client/` on main provides programmatic WebSocket-driven turn smoke testing (`action: "send"` / `action: "respond"` for confirmations). The new design needs end-to-end smoke tests using this — not just unit tests + manual UI poking. The bugs that killed #572 were all visible only via live testing; if we'd had scripted smoke tests, we'd have caught them in execute phase.

## Pointers

- **Prior session (the step-primitive design):** [`../2026-06-01-1055-workflow-step-primitive-design/`](../2026-06-01-1055-workflow-step-primitive-design/) — spec.md, plan.md, notes.md. The engine design that survived; only the integration plan was wrong.
- **Prior PR (closed-as-superseded):** [#572](https://github.com/lmorchard/decafclaw/pull/572)
- **Branch with all the engine code:** `feat/255-workflow-step-primitive` (this worktree). New PR will be on a fresh branch when execute time comes.
- **Design thesis memory:** `~/.claude/projects/-Users-lorchard-devel-decafclaw/memory/project_workflow_design_thesis.md` — load-bearing principle.
- **Smoke-test tooling:** `src/decafclaw/client/` on main. CLI: `python -m decafclaw.client`. Actions: `send` (drive an agent turn), `respond` (resolve a pending confirmation). Output: jsonl or summary. Exit codes map to turn outcome (complete / error / halted_confirmation / timeout).
- **Sophie reference:** `/Users/lorchard/devel/tabs-project/sophie/packages/core/src/orchestrator.ts` — Sophie has separate orchestration vs agent layers; useful pattern reference for "peer not nested."

## How to resume cold

1. Read this notes.md.
2. Skim the prior session's [spec.md](../2026-06-01-1055-workflow-step-primitive-design/spec.md) and [plan.md](../2026-06-01-1055-workflow-step-primitive-design/plan.md) to understand what's in the engine (the surviving parts).
3. Read the [project-workflow-design-thesis](../../../../.claude/projects/-Users-lorchard-devel-decafclaw/memory/project_workflow_design_thesis.md) memory.
4. Read [`src/decafclaw/client/run.py`](../../../src/decafclaw/client/run.py) to understand the smoke-test interface — the new design should be smoke-testable end-to-end via the client.
5. Run `/dev-session brainstorm`. Resolve the open questions above, starting with #1 (dispatch model) and #2 (manager state) since those gate the rest.
6. The spec for this session should specifically include:
   - A diagram or table of "where the LLM is allowed to be" — the constrained-worker boundary made explicit
   - The conversation lifecycle: agent turn vs workflow run, and how transitions work
   - Concrete dispatch flow for `/interview` from keystroke to first widget render
   - Pre-mortem section listing the ways the LLM could sneak back, and the design choices that prevent each

## Things this session is NOT trying to do

- Not redesigning the step primitives — those are decided ([prior spec](../2026-06-01-1055-workflow-step-primitive-design/spec.md)).
- Not abandoning the engine code — most of it carries forward as-is.
- Not necessarily a single PR — the new design may decompose into a sequence of smaller changes (e.g., conversation-manager refactor → command dispatch refactor → transport changes → engine integration). Plan phase will decide.

---

## Brainstorm session outcome (2026-06-03)

Worked through 9 entangled architectural questions, in order. Full design in [spec.md](./spec.md). Research findings (existing decafclaw integration surfaces) in [research.md](./research.md).

Headline decisions:

1. **Workflows are a peer turn kind: `TurnKind.WORKFLOW_RUN`.** Branching dispatch in `_start_turn`: if WORKFLOW_RUN, call `engine.start_workflow`; else `run_agent_turn`. One manager API, shared busy/cancel.
2. **New command mode `workflow` in `dispatch_command`** (peer to `inline`/`fork`/`help`). SKILL.md body is irrelevant — workflow dispatch is data-driven from `name` + `args` only.
3. **New role `workflow_message`** with widget attachment extended. `ROLE_REMAP → user` with content tagged `[workflow:<name> step:<id>]` so the LLM can distinguish workflow content from direct user prose.
4. **Engine uses `ctx.request_confirmation` directly** for user_input pauses. Two coordinated records per pause: workflow_message (display) + ConfirmationRequest (routing), sharing a `confirmation_id`. Deletes the entire `on_response` callback machinery and synthetic-message injection.
5. **Remove `workflow_start` from LLM tools.** Keep `workflow_status`/`workflow_abort`/`workflow_artifact_read/write` as inspection/kill-switch only.
6. **Subagent step kind unchanged.** MVP doesn't support workflow-as-subagent (recursive WORKFLOW_RUN spawn deferred to future work).
7. **Off-workflow inputs use existing busy-flag queueing.** UI affordances for "workflow is active" are future polish.
8. **Four residual concerns get explicit spec treatment:** workflow_message metadata tag in remap, scheduled-task workflow dispatch via WORKFLOW_RUN, heartbeat workflows forbidden, step error semantics enumerated.
9. **Unit tests (carry forward from PR #572) + live integration via `decafclaw-client` are required to merge.** Live integration coverage is the layer that was missing — every PR #572 bug was visible only via live testing.

**Load-bearing architectural principle:** the spec includes a "Where the LLM is allowed to be" table that names the only three surfaces where the LLM is admitted (inside `llm_call`, inside `route`, inside `subagent`'s CHILD_AGENT). Anywhere not on that list is code-territory. The table is the design's invariant.

Next phase: `/dev-session plan`. Plan probably stages this as: conversation-manager refactor → command dispatch refactor → message wire shape + transport rendering → engine integration (user_input executor rewrite, on_response deletion) → scheduled-task workflow dispatch → live integration tests.
