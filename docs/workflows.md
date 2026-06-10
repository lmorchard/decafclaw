# Workflow Replay Engine

DecafClaw supports first-class **workflows** — durable, human-in-the-loop processes where
code owns control flow and the LLM is invoked only as a structured-output worker on focused
sub-problems.

Issue: [#255](https://github.com/lmorchard/decafclaw/issues/255).

## What a workflow is (and what it is not)

A **workflow** is a registered async Python function (the *orchestrator*) that the harness
runs as its own `TurnKind.WORKFLOW` — completely separate from the LLM agent loop.

The key distinction:

| Concept | What it is | LLM role |
|---|---|---|
| **Agent turn** | LLM drives the loop, picks tools, decides what to do next | The orchestrator |
| **Skill** | Capability package the agent activates; shapes what the LLM does | The orchestrator |
| **Workflow** | Python function the harness runs; code owns control flow | A constrained worker called on demand |

**Skills shape what the LLM does; workflows are what the harness runs.** Two clean concepts,
no overload.

The prior approach (PR #557) put the LLM in charge of advancing the state machine by emitting
a `phase_advance` tool call. The model reliably narrated instead of calling the tool. The
lesson: the LLM must not be the orchestrator. Code must be.

This design mirrors how [Temporal](https://temporal.io), [DBOS](https://www.dbos.dev), and
Claude Code's dynamic workflows work — durable execution via deterministic replay.

## The load-bearing rule

The engine re-runs the orchestrator **from the top** on every resume. So for every operation
in an orchestrator, the rule is:

**Pure / deterministic / side-effect-free → plain Python, not journaled.** It re-runs on
every replay harmlessly, because it always produces the same result. Control flow lives here:
`if`, `while`, `for`, string formatting, accumulating results in a list, branching on a
journaled result. Re-running this during replay is free and correct.

**Crosses the boundary to the outside world → must go through a journaled wrapper.**
Anything nondeterministic or with side effects: the LLM, user input, tool calls, the clock,
randomness, the network. Recorded on first execution; **replayed from the journal** on
re-run — never re-executed. A raw `await some_tool(...)` would fire twice or return a
different value and diverge control flow.

> **Every call that crosses to the outside world goes through the journal; everything else
> is ordinary code.** That single rule is the entire discipline. It is Temporal's "activities
> vs. workflow code" line, DBOS's, and Claude Code's.

For the MVP there are exactly **two** journaled wrappers: `wf.llm_call(...)` and
`wf.user_input(...)`. Subagent, tool, parallel, and pipeline wrappers are deferred to future
work.

## The journal and deterministic replay

The **journal** is a durable, ordered list of completed journaled-call results for one
workflow run, persisted as a per-conversation file at
`workspace/conversations/{conv_id}/workflow.json`.

Each entry:

```json
{
  "seq": 0,
  "kind": "llm_call",
  "args_fingerprint": "a3f1c2d8b4e7f9a1",
  "result": { "done": false, "question": "What's your background?" }
}
```

Entries are **keyed positionally by execution order** — the Nth journaled call gets
sequence N. This is what makes loops replay correctly: the same control flow produces the
same execution order and therefore the same keys.

### Execution lifecycle

1. **Run:** the engine calls the orchestrator. Each journaled primitive, on first execution,
   runs live, appends its result to the journal (flushed to disk), and returns.

2. **Suspend:** `wf.user_input(...)` raises `WorkflowSuspended` when it encounters a step
   with no journal entry. The engine catches it, posts the pending question as a
   `ConfirmationRequest`, and ends the turn. The journal is already persisted from prior
   steps.

3. **Resume:** the user's answer is journaled at the suspended sequence position, then a
   fresh `TurnKind.WORKFLOW` turn re-runs the orchestrator from the top. Every prior journaled
   call returns its cached result instantly (no LLM calls, no re-prompting). Control flow
   fast-forwards deterministically to just past the answered `user_input`; execution continues
   live.

4. **Complete:** the orchestrator returns a value. The engine marks the run `done`, renders
   the artifact, and ends the turn.

### Durability across restart

Because the journal is just a file on disk, a server restart mid-suspend loses nothing.
The user reloads the UI; the pending confirmation (the unanswered question) is recovered by
the existing confirmation-persistence machinery. The next answer journals at the right
position and a resume turn re-runs from the top.

### Determinism guard

Each journal entry stores an `args_fingerprint` (a 16-char SHA-256 of the call's kind and
args). On replay, when the orchestrator reaches journaled call N, the engine compares the
replay's fingerprint to the recorded one. A mismatch means control flow diverged — a
determinism bug in the orchestrator — and the engine raises `WorkflowNonDeterministic` rather
than silently returning a stale result.

**Orchestrators MUST NOT catch `WorkflowSuspended`.** Swallowing it (e.g. with a broad
`except Exception`) lets the cursor advance past a step the journal never recorded,
desynchronizing every later positional key.

## The two journaled primitives

### `wf.llm_call(*, prompt, schema, system="", tool_name="submit", model=None)`

Calls the LLM with a forced-tool structured-output pattern. The model is given exactly one
tool it **must** call; the args are parsed as the result. Retries once with a stricter nudge
on narrate-stall.

- `prompt` — user-role message.
- `schema` — JSON Schema dict for the tool's `parameters`. The result is the parsed tool args.
- `system` — optional system message.
- `tool_name` — cosmetic label for the forced tool (not part of the fingerprint).
- `model` — named model config; falls back to the workflow's registered model.

### `wf.user_input(prompt, *, choices=None)`

Asks the user a question. On the first (unanswered) encounter raises `WorkflowSuspended`
and ends the turn. On replay after the user has answered, returns the cached answer from the
journal.

- `prompt` — the question shown to the user.
- `choices` — optional list of button labels (free-text if omitted).

## Authoring a workflow

### Registration

```python
from decafclaw.workflow.registry import workflow

@workflow("my-workflow", model="vertex-gemini-flash")
async def my_workflow(wf):
    ...
```

The `@workflow` decorator registers the function in `REGISTRY` at import time. The `model`
parameter sets the default for `wf.llm_call` calls (defaults to `vertex-gemini-flash`).

Place new orchestrators in `src/decafclaw/workflow/workflows/` and ensure they are imported
by the package (via `workflows/__init__.py`). Bundled orchestrators are discovered
automatically on startup.

### The interview workflow — a walkthrough

`src/decafclaw/workflow/workflows/interview.py` is the hero example. The full orchestrator:

```python
@workflow("interview")
async def interview(wf):
    topic = await wf.user_input("What should this interview be about?")

    answers: list[dict] = []
    while len(answers) < MAX_Q:
        decision = await wf.llm_call(
            prompt=_ask_prompt(topic, answers),
            schema=_DECISION_SCHEMA, system=_SYS_ASK)
        if decision.get("done"):
            break
        reply = await wf.user_input(decision["question"])
        answers.append({"q": decision["question"], "a": reply})

    return await wf.llm_call(
        prompt=_synth_prompt(topic, answers),
        schema=_ARTIFACT_SCHEMA, system=_SYS_SYNTH)
```

Key observations:

- `topic = await wf.user_input(...)` — the first suspension. The journal is empty; the
  question is posted to the user; the turn ends. On resume, `topic` is the cached answer.

- `while len(answers) < MAX_Q` — plain Python. Re-runs on every replay; always identical
  because `answers` is driven by journaled results.

- `decision = await wf.llm_call(...)` — live on first pass; cached on replay.

- `if decision.get("done"): break` — plain Python branching on a journaled result.

- `reply = await wf.user_input(decision["question"])` — a second suspension point, inside the
  loop. Every iteration is a distinct journal position (the cursor is positional).

- `return await wf.llm_call(...)` — the final synthesis call. Returns `{"title": ...,
  "body": ...}`; the engine renders it as markdown and ends the turn.

The entire mental model fits on one screen. The `while`, `if`, and `answers` list are
ordinary Python. The only journaled boundary crossings are the two `wf.*` primitives.

## Suspend/resume mechanics and the confirmation path

The suspend/resume cycle reuses DecafClaw's existing confirmation persistence infrastructure,
with one important difference: the resume callback routes to the **workflow engine**, not
the agent loop.

1. `wf.user_input(...)` raises `WorkflowSuspended`.
2. `run_workflow_turn` (in `workflow/resume.py`) catches it and calls
   `manager.post_confirmation(conv_id, request)` with a
   `ConfirmationAction.WORKFLOW_USER_INPUT` request — **without awaiting a waiter**. The turn
   ends immediately after.
3. The confirmation is persisted as a conversation message. It survives page reload and server
   restart.
4. The user answers. The confirmation response routes to `WorkflowUserInputHandler.on_approve`,
   which:
   - Journals the answer at the suspended sequence position.
   - Calls `manager.enqueue_turn(conv_id, kind=TurnKind.WORKFLOW, ..., resume=True)`.
5. The new WORKFLOW turn calls `run_workflow_turn` again. `run_workflow` replays the
   orchestrator from the top; all prior calls return cached; execution continues past the
   answered input.

This is what keeps the LLM off the crank. The confirmation round-trip is purely between the
user and the harness; the LLM only runs when an orchestrator explicitly calls `wf.llm_call`.

## Invoking a workflow

In the web UI, type `/interview` (or the name of any registered workflow). The WebSocket
handler in `web/websocket.py` checks `workflow_commands()`, recognizes it as a workflow
trigger, and calls `manager.enqueue_turn(conv_id, kind=TurnKind.WORKFLOW, ...)`.

Progress events stream to the conversation timeline via `tool_status` events during execution.

The final artifact (for the interview: `{"title": ..., "body": ...}`) is rendered as a
markdown `# Title\n\nbody` block in the conversation.

**Conversation archive writes.** The WORKFLOW turn path bypasses the agent loop, so the
two ends of the run are archived explicitly:

- The `/<workflow-name>` invocation is written as a `role: "user"` message at the
  intercept point in `_handle_send` (before `enqueue_turn`).
- The rendered artifact is written as a `role: "assistant"` message inside
  `run_workflow_turn` when the outcome is `done`.

Without these writes, the conversation history would be empty on reload (the only other
archive rows from a workflow run are `confirmation_request` / `confirmation_response`,
which `load_history` filters via `_HIDDEN_ROLES`).

## Module structure

```
src/decafclaw/workflow/
  __init__.py          re-exports public surface; imports workflows/ to register orchestrators
  registry.py          @workflow decorator + REGISTRY + workflow_commands()
  engine.py            run_workflow(): calls orchestrator, classifies outcome
  journal.py           Journal dataclass + save_journal/load_journal + fingerprint()
  handle.py            WorkflowHandle — the wf object; llm_call + user_input primitives
  llm.py               call_structured() — forced-tool structured-output LLM helper
  errors.py            WorkflowSuspended, WorkflowNonDeterministic, WorkflowError
  paths.py             workflow_dir() + workflow_path() — per-conv file location helpers
  resume.py            run_workflow_turn() + WorkflowUserInputHandler (harness glue)
  workflows/
    interview.py       The hero orchestrator (@workflow("interview"))
```

## What is not in v1

The following are explicitly deferred:

- **`subagent` / `parallel` / `pipeline` / `tool_call` primitives.** These arrive with the
  batch fan-out case; they are real journaled wrappers, just not needed for the interview.

- **LLM-generated per-task workflows.** The engine does not preclude it, but this is not
  built yet.

- **Migrating existing flat sidecars** (`.notes.md`, `.decisions.json`, `.context.json`,
  `.archive.jsonl`) into the `conversations/{conv_id}/` directory layout. Only `workflow.json`
  uses the directory convention now; two conventions coexist temporarily.

- **A declarative step DSL** (`route`/`branch`/`loop`/`set` as data-primitives). Deliberately
  rejected: control flow *is* the host language (`if`/`while`). A DSL re-imports the exact
  complexity the replay model was designed to eliminate.

- **WORKFLOW turn Context-kind semantics.** WORKFLOW turns currently reuse the USER-style
  Context path (full interactive Context with per-conversation state). Whether they should use
  `Context.for_task` semantics instead is an open item to revisit after the live smoke.

## Error handling

- **Primitive failure** (LLM error, schema-parse failure after retry): the run goes to
  `ERROR` status, the journal is preserved for inspection, and the error is surfaced as the
  turn output.

- **`WorkflowNonDeterministic`:** raised when a replay fingerprint mismatch is detected.
  Fail-loud — the run goes to `ERROR` rather than silently returning a stale result.

- **Cancellation (deny):** `WorkflowUserInputHandler.on_deny` marks the journal `error` and
  appends a cancellation message to the archive.

## Testing

Unit tests cover the core contracts without LLM calls:

- `tests/test_workflow_journal.py` — round-trip and fingerprint correctness.
- `tests/test_workflow_handle.py` — replay returns cached results (sabotage check confirms
  the live path is not re-executed on replay); determinism guard fires on deliberate mismatch.
- `tests/test_workflow_engine.py` — suspension raises and persists; completion returns result.
- `tests/test_workflow_resume.py` — harness glue: suspend posts confirmation, answer journals
  and enqueues a resume turn.
- `tests/test_workflow_turn_integration.py` — TurnKind.WORKFLOW dispatch end-to-end.
- `tests/test_workflow_interview.py` — interview orchestrator logic.
- `tests/test_workflow_paths.py` / `test_workflow_registry.py` / `test_workflow_llm.py` — path
  helpers, registry decorator, structured-output helper.

The restart-durability test persists a journal mid-suspend, constructs a fresh engine from
disk, and asserts continuation with no lost state.

Evals are deferred until an orchestrator gains a real LLM-driven routing branch worth
guarding (there is no tool-disambiguation question for the current `interview` flow).
