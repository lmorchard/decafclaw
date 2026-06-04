# Workflow ↔ Conversation Integration

**Goal:** Make workflows a *peer* to agent turns inside `ConversationManager`, eliminating every surface where the LLM could drive workflow control flow.

**Source:** Issue [#255](https://github.com/lmorchard/decafclaw/issues/255) · Closes superseded PR [#572](https://github.com/lmorchard/decafclaw/pull/572) · Brainstorm session 2026-06-03-1512

## Current state

The step-primitive engine modules from PR #572 (`src/decafclaw/workflow/{types,loader,engine,step_executors,jinja_env,conv_state,subagent,registry}.py`) are mechanically correct. The failure was integration: PR #572 ran workflows *nested inside a tool call* in the agent loop, which let the LLM creep into control flow at every boundary — choosing whether to call `workflow_start`, "responding" to synthetic user-messages mid-cycle, going conversational when it should have re-invoked the workflow tool. See `notes.md` for the four-patch failure history.

Research findings (`research.md`):
- All current `TurnKind`s (`USER`, `HEARTBEAT_SECTION`, `SCHEDULED_TASK`, `CHILD_AGENT`, `WAKE`) share one dispatch path: `_start_turn → run_agent_turn` (`conversation_manager.py:73-79, 402-506`)
- The `fork` command mode already bypasses the outer agent loop (`commands.py:419-429`, `websocket.py:318-323`) — non-LLM command dispatch is precedented
- `background_event` writes to archive + emits to WebSocket subscribers without an agent turn (`skills/background/tools.py:142-165`) — server-emitted messages outside the agent loop are precedented
- Widgets are currently bound *exclusively* to `role: "tool"` messages (`tool_execution.py:270-278`)
- `ctx.request_confirmation` is wired by `_start_turn` (`conversation_manager.py:1410-1412`); reusing it for workflow user_input is the cleanest pause/resume primitive

## Desired end state

### Architecture: workflow as peer turn kind

`TurnKind.WORKFLOW_RUN` is a new turn kind that the manager dispatches via a different code path. The agent loop is not invoked. The workflow engine drives the conversation directly.

```
User types /interview
   ↓
web/websocket.py:_handle_message
   ↓
commands.dispatch_command(ctx, "/interview topic=sleep")
   ↓ returns mode="workflow", workflow_name="interview", args={"topic": "sleep"}
   ↓
manager.enqueue_turn(
    conv_id=conv_id,
    kind=TurnKind.WORKFLOW_RUN,
    workflow_name="interview",
    initial_state={"topic": "sleep"},
)
   ↓
_start_turn(state, turn):
   if turn.kind == WORKFLOW_RUN:
       await engine.start_workflow(ctx, turn.workflow_name, turn.initial_state)
   else:
       await run_agent_turn(ctx, turn.prompt, ...)
   ↓
engine.start_workflow runs step graph, emits workflow_message records, awaits
user_input via ctx.request_confirmation
   ↓
When step is user_input: emit workflow_message (display) + ConfirmationRequest (routing); engine awaits
   ↓
User submits widget → respond_to_confirmation wakes engine → next step executes
   ↓
Workflow reaches terminal → emits final workflow_message → busy clears → next queued turn fires
```

### Where the LLM is allowed to be

The thesis applied uncompromisingly:

| Surface | LLM role | Constraint |
|---|---|---|
| `llm_call` step executor | constrained worker | One forced-tool structured-output call, declared schema |
| `route` step executor | constrained worker | Forced-tool structured-output call returning a declared enum value |
| `subagent` step's CHILD_AGENT turn | constrained worker | Runs on its own conv_id with a constrained skill prompt, terminates back to parent |
| `workflow_start` LLM tool | **forbidden** | Removed entirely; workflows initiate only via `/command`, scheduled task, or subagent step |
| `workflow_status`, `workflow_abort`, `workflow_artifact_read/write` LLM tools | inspection only | Read-only or kill-switch; no initiation surface |
| Synthetic user-message injection between steps | **forbidden** | Engine awaits `ctx.request_confirmation` directly; no `on_response` callback, no synthetic message |
| Agent loop running during a workflow | **forbidden** | `WORKFLOW_RUN` dispatch bypasses `run_agent_turn` entirely |
| SKILL.md body injection as LLM context for `/command` | **forbidden** | Workflow-mode dispatch ignores the SKILL.md body; only `name` + `args` go to the engine |
| Widget tied to `role: "tool"` (so workflows must emit a tool-result) | **forbidden** | Widgets attach to `workflow_message` role directly |

Anywhere not in the first three rows is **code-territory**. If the design admits an LLM surface there, the design is wrong.

### Command dispatch

`commands.py:dispatch_command` grows a new `mode="workflow"` branch (next to the existing `inline`, `fork`, `help`, `unknown`, `error`). When the matched skill has `kind: workflow` in its SKILL.md frontmatter:

```python
return {
    "mode": "workflow",
    "workflow_name": skill.name,
    "args": _parse_args(argument_string),  # e.g., "topic=sleep" → {"topic": "sleep"}
}
```

Transport handlers (`web/websocket.py`, `mattermost.py`) detect `mode="workflow"` and call `manager.enqueue_turn(kind=TurnKind.WORKFLOW_RUN, ...)`. SKILL.md body is *not* injected anywhere — workflow dispatch is fully data-driven from `name` + `args`.

### Message wire shape

New role `workflow_message` carries workflow-emitted content. Archive entries:

```json
{
  "role": "workflow_message",
  "workflow": "interview",
  "step": "ask_user",
  "content": "Tell me about your background.",
  "widget": {
    "widget_type": "text_input",
    "data": {"prompt": "...", "fields": [{"key": "value", ...}]}
  },
  "confirmation_id": "...",
  "timestamp": "..."
}
```

Terminal output (e.g., interview's final_summary):
```json
{
  "role": "workflow_message",
  "workflow": "interview",
  "step": "final_summary",
  "content": "...synthesized summary...",
  "timestamp": "..."
}
```

`ROLE_REMAP` (`context_composer.py:25-29`) maps `workflow_message → user` for LLM context, with the content prefixed by `[workflow:<name> step:<id>] ` so the LLM sees this is workflow content, not direct user prose.

Widget attachment is extended to allow `workflow_message` role. The web UI renders this role with workflow-specific affordance (icon/label/styling — UI detail for execute phase).

### Response routing

Each user_input pause emits **two coordinated records** sharing a `confirmation_id`:

1. `workflow_message` — visible question + widget for display in chat history
2. `ConfirmationRequest` with `action_type=WORKFLOW_USER_INPUT` — invisible routing record; existing `pending_confirmation` slot

The engine's user_input executor:
```python
async def _execute_user_input(ctx, step, state):
    cfg = step.config
    prompt = render_template(cfg["prompt"], state.state)
    confirmation_id = secrets.token_hex(...)
    # Emit display record:
    _emit_workflow_message(ctx, step, prompt, widget, confirmation_id)
    # Build routing record + await:
    req = ConfirmationRequest(
        action_type=WORKFLOW_USER_INPUT,
        action_data={"workflow": state.workflow, "step": step.id, "mode": cfg["input"]},
        confirmation_id=confirmation_id,
    )
    response = await ctx.request_confirmation(req)
    user_input = _transform_response(response.data, cfg["input"])
    state.state[step.id] = user_input
```

When the user submits the widget, web/Mattermost route through the existing `respond_to_confirmation` lifecycle — no new manager API. The engine's `await` wakes with `response.data` containing the widget values directly. No callback, no synthetic message, no race.

### Off-workflow input handling

When a workflow is active (`busy=true`), incoming inputs use existing busy-flag behavior:

- **Widget submit** with the active workflow's `confirmation_id` → routed to engine (above)
- **Typed message** → queues as next USER turn, fires when workflow completes
- **Other `/command` invocation** → queues as next turn of appropriate kind
- **`workflow_abort` LLM tool call from a queued agent turn** → no-op (workflow has already aborted or completed by the time the queued turn runs)
- **User-initiated cancel** (UI stop button) → triggers `cancel_event` → engine task cancellation

UI affordances ("workflow is active; your message will be processed after") are future work; backend behavior is settled.

### LLM tool surface

`src/decafclaw/tools/workflow_tools.py` exposes only these to the LLM:

- `workflow_status(ctx)` — inspection, returns status text
- `workflow_abort(ctx, reason)` — kill switch
- `workflow_artifact_read(ctx, path)` — file read from workflow artifacts dir
- `workflow_artifact_write(ctx, path, content)` — file write

**`workflow_start` is NOT an LLM tool.** Workflows initiate only via:
1. `/command` dispatch (Q2)
2. Scheduled task dispatching a `kind: workflow` skill as `TurnKind.WORKFLOW_RUN` (not `SCHEDULED_TASK`)
3. `subagent` step kind from within another workflow

### Scheduled-task / heartbeat dispatch

`schedules.py:run_schedule_task` checks the skill's `kind`:
- `kind: workflow` → `manager.enqueue_turn(kind=TurnKind.WORKFLOW_RUN, ...)`
- Otherwise → `manager.enqueue_turn(kind=TurnKind.SCHEDULED_TASK, ...)` (existing path)

Heartbeat skills **cannot be `kind: workflow`** — loader rejects with a clear error. Heartbeat is for periodic agent self-check; workflow semantics don't fit.

### Subagent step kind (MVP scope)

The carry-forward `subagent.py` spawns `TurnKind.CHILD_AGENT` on a new `conv_id`. Parent workflow's busy stays held; child has its own conv with its own lifecycle. Parent's engine task awaits the child's completion future.

MVP constraint: subagent step's `skill:` field references a **regular skill**, not a workflow. Recursive workflow-as-subagent is future work — would require nested `WORKFLOW_RUN` semantics across the parent/child conv boundary, complexity not justified for MVP.

### Step error semantics

- **`llm_call` schema validation fails after retries** → engine sets `RunStatus.ERROR`, persists state, raises
- **`route` returns enum value not in declared choices** → ERROR (existing behavior from PR #572 engine)
- **`tool_call` underlying tool errors** → ERROR
- **`python` function raises** → ERROR
- **`subagent` declared outputs missing** → ERROR via existing `_verify_subagent_outputs` (`subagent.py`)
- **`user_input` cancelled (confirmation denied or cancel_event set)** → workflow transitions to `RunStatus.ERROR` with cancellation reason in transitions
- **Workflow reaches `_MAX_STEPS` cap** → ERROR (existing guard from PR #572)

`RunStatus.ERROR` distinguishes user-cancel from execution failure via the transitions log (`transitions[-1].get("cancelled")` or similar). No new `RunStatus` enum value.

### Testing

- **Unit tests** carry forward the ~150 tests from PR #572's `tests/workflow/` — engine kernel, loader, step executors, jinja, cycles, conv_state. Adapt mocks to the new manager-driven dispatch.
- **Live integration tests** via `python -m decafclaw.client`. New Makefile target (e.g., `make smoke-workflows-live`). Required to merge. Smoke flows:
  - `/workflow_hello` → assert terminal workflow_message appears
  - `/interview` → respond with text → assert next workflow_message appears → respond → ... → assert final_summary appears
  - `/research_brief topic=X` → assert all steps fire (gather subagent + outline + draft + critique + publish workflow_messages in archive)
  - Scheduled workflow dispatch → assert WORKFLOW_RUN turn fires (not SCHEDULED_TASK)
  - Concurrent: queue a USER turn mid-workflow, verify it processes after workflow ends

## Design decisions

- **`TurnKind.WORKFLOW_RUN` with branching dispatch in `_start_turn`.**
  - Why: keeps one manager API; busy/lock/cancel shared with turns; workflow is structurally a peer.
  - Rejected: manager-unaware (command handler dispatches direct to engine) — manager loses awareness of active work, can't share busy/cancel. Separate `enqueue_workflow` API — duplicates lifecycle machinery for no benefit.

- **New command mode `workflow` in `dispatch_command`.**
  - Why: extends the existing mode-dispatch pattern (`inline`/`fork`/`help`/`unknown`/`error`); transport handlers add one branch.
  - Rejected: centralize all mode routing in manager — refactor scope creep. Transport-side `kind` detection — duplicates logic across web + Mattermost.

- **New role `workflow_message` with widget attachment extended; `ROLE_REMAP → user` with metadata-tagged content.**
  - Why: distinguishable from agent speech for UI and LLM both; small targeted change to widget binding code; metadata tag in remapped content prevents LLM confusion between workflow questions and user prompts.
  - Rejected: repurpose `assistant` role — loses distinguishability (the ambiguity that caused PR #572's failure mode). Two-emission split (message + widget event) — race conditions on client reconciliation.

- **Engine uses `ctx.request_confirmation` directly; two coordinated records per pause (display + routing).**
  - Why: reuses existing confirmation lifecycle (persistence, archive, wakeup); eliminates the `on_response` callback and synthetic-message-injection paths entirely.
  - Rejected: new workflow-specific pause/resume API — duplicates ConfirmationRequest infrastructure. Auto-route in `respond_to_confirmation` by TurnKind — adds branching to a method that already handles complex concurrency.

- **Remove `workflow_start` from LLM tools; keep `status`/`abort`/`artifact_read/write`.**
  - Why: the LLM-as-initiator surface is the largest control-flow risk; the others are inspection/kill-switch only.
  - Rejected: keep all five — reintroduces the failure mode from PR #572 debugging. Remove all five — loses inspection capability for post-workflow agent turns.

- **Off-workflow inputs use existing busy-flag queueing.**
  - Why: backend behavior is already correct; UX affordances are UI-layer concerns.
  - Rejected: strict reject — every dispatch entry point grows the check, more code paths. Workflow-pluggable handling — premature flexibility.

- **Scheduled-task dispatch checks skill kind; heartbeat workflows forbidden.**
  - Why: scheduled workflows should get the peer-architecture guarantees just like `/command`-initiated ones. Heartbeat skills don't fit workflow semantics; better to reject explicitly.
  - Rejected: scheduled workflows always go through `SCHEDULED_TASK` — loses peer-architecture for scheduled workflows. Heartbeat workflows allowed — admits a use case we don't have and can't validate.

- **`workflow_message → user` remap embeds workflow metadata.**
  - Why: LLM sees clearly when content is workflow-emitted vs direct user; prevents the agent from treating workflow questions as user prompts on follow-up turns.
  - Rejected: silent remap — risks LLM confusion on post-workflow turns.

- **Live integration tests via `decafclaw-client` are required to merge.**
  - Why: every failure mode in PR #572 was visible only via live testing. Unit tests alone are insufficient.
  - Rejected: unit tests only — reintroduces the gap that killed #572. Live tests only — loses fast feedback for engine-kernel changes.

## Patterns to follow

- **Command mode branching** — `commands.py:279-355` is the existing pattern; add `workflow` mode as a sibling
- **Transport mode handling** — `web/websocket.py:309-362` for `fork`/`inline`/`help`; mirror for `workflow`
- **TurnKind branching at dispatch** — currently no precedent in `_start_turn`, so the new design *introduces* this. Keep the branch minimal: a single `if turn.kind == WORKFLOW_RUN` check selects `engine.start_workflow` vs `run_agent_turn`
- **Server-emitted message + WebSocket emit** — `skills/background/tools.py:142-165` shows `append_message(config, conv_id, record)` + `manager.emit(conv_id, {"type": ..., "record": rec})`. Workflow message emission follows this exactly
- **`ConfirmationRequest` lifecycle** — `conversation_manager.py:1136-1160, 533-703, 1085-1101` for the create/emit/respond/clear cycle. Engine reuses unchanged
- **`ROLE_REMAP` pattern** — `context_composer.py:25-29` for adding the workflow_message → user mapping with metadata tag
- **Carry-forward engine modules** — `src/decafclaw/workflow/{types,loader,engine,step_executors,jinja_env,conv_state,subagent,registry}.py` from PR #572 mostly intact; only `step_executors._execute_user_input` substantially rewrites, and `step_executors._make_on_response` / the synthetic-message paths get deleted

## What we're NOT doing

- **Not redesigning the step primitives.** Six kinds, state model, edge conditions, Jinja templates — all decided in [`../2026-06-01-1055-workflow-step-primitive-design/spec.md`](../2026-06-01-1055-workflow-step-primitive-design/spec.md).
- **Not fixing the LLM-as-control-flow class-of-bug analogues** (checklist tool, project skill phase advancement, `delegate_task` semantics). They have the same shape but are out of scope for this PR.
- **Not supporting workflow-as-subagent** (recursive `WORKFLOW_RUN` spawn). Subagent step's `skill:` references regular skills only in MVP.
- **Not implementing UI affordances** for "workflow is active" indicators, "queued behind workflow" notices, or workflow-specific message styling beyond basic role differentiation. These are future polish.
- **Not implementing voice/multimodal input** for widget responses. Text + button choice only.
- **Not adding a new `RunStatus` enum value** for user-cancel. Distinguish via transitions log entry.
- **Not redesigning `conv_state` persistence.** Workflow state on disk format unchanged from PR #572.
- **Not changing the existing `widget_input` infrastructure** beyond extending widget attachment to `workflow_message` role and reusing `ctx.request_confirmation` from a new caller (the engine).
- **Not preserving any of PR #572's synthetic-message-injection or `on_response` callback code paths.** Those get fully deleted.

## Open questions

- **Web UI rendering of `workflow_message` role.** What does it look like — different bubble color, prefix icon, sidebar indicator? UI-layer detail for execute phase; default answer: minimal differentiator (e.g., label "workflow: interview") on the assistant-like bubble until UX work prioritizes more.

- **`workflow_message` metadata tag exact format.** `[workflow:interview step:ask_user]` is the working assumption; could be JSON-frontmatter-style, could be HTML-comment-style for clean prose. Default: bracketed inline tag, execute phase finalizes.

- **Mattermost rendering of workflow_message + widgets.** Web UI gets first-class support; Mattermost might fall back to plain text + post-action buttons. Default answer: minimum-viable Mattermost rendering (text-only fallback for widgets), prioritize web UI for the smoke tests.

- **Cancellation race semantics.** User clicks UI stop button while widget submission is in flight. Existing confirmation infra has lock-protected ordering — does the new path inherit those guarantees correctly? Default answer: yes (we reuse `respond_to_confirmation`'s existing lock model); execute phase verifies with a targeted live test.
