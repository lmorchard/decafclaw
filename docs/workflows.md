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

There are six journaled wrappers: `wf.llm_call(...)`, `wf.user_input(...)`,
`wf.tool_call(...)`, `wf.subagent(...)`, `wf.parallel(...)`, and `wf.pipeline(...)`. The first
two are the suspend/resume backbone; the last four are boundary-crossing wrappers for tools,
child agents, and fan-out. Each one journals at a positional key and replays from cache.

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

## The journaled primitives

### `wf.llm_call(*, prompt, schema, system="", tool_name="submit", model=None)`

Calls the LLM with a forced-tool structured-output pattern. The model is given exactly one
tool it **must** call; the args are parsed as the result. Retries once with a stricter nudge
on narrate-stall.

- `prompt` — user-role message.
- `schema` — JSON Schema dict for the tool's `parameters`. The result is the parsed tool args.
- `system` — optional system message.
- `tool_name` — cosmetic label for the forced tool (not part of the fingerprint).
- `model` — named model config; falls back to the workflow's registered model.

**Fingerprint:** `(prompt, schema, system)`. `tool_name` is cosmetic; `model` is an execution
detail. A per-call `model` override therefore MUST be deterministic across replays.

**Replay:** cache hit returns the parsed dict verbatim. The LLM is NOT re-invoked.

**Error:** schema-parse failures retry once with a stricter nudge; persistent failures
propagate and the run goes to `ERROR` status.

### `wf.user_input(prompt, *, choices=None)`

Asks the user a question. On the first (unanswered) encounter raises `WorkflowSuspended`
and ends the turn. On replay after the user has answered, returns the cached answer from the
journal.

- `prompt` — the question shown to the user.
- `choices` — optional list of button labels (free-text if omitted).

**Fingerprint:** `(prompt, choices)`.

**Replay:** cache hit returns the cached answer.

**Suspend / resume:** see [Suspend/resume mechanics](#suspendresume-mechanics-and-the-confirmation-path).

### `wf.tool_call(name, **args) -> dict`

Invokes a registered decafclaw tool by name and returns its result. The result shape is
`{"text": str, "data": Any | None}` — `ToolResult.media` is intentionally stripped from the
journal (media bytes can be large and are rebuildable from upstream sources; the journal
exists for replay correctness, not full reconstruction).

- `name` — tool name (e.g. `tabstack_research`).
- `**args` — keyword arguments forwarded to the tool.

**Gating:** the call is gated by `ctx.tools.allowed`, mirroring `execute_tool`'s semantics.
`allowed is None` means no restriction; a non-empty set narrows. A name missing from the
allowlist raises `WorkflowToolNotAllowed` *before* the journal is consulted, so orchestrator
bugs fail loud rather than burning a journal slot. The gate also fires during replay — if the
allowlist shrinks between turns, the caller hears about it.

**Fingerprint:** `(name, args)`.

**Replay:** cache hit returns the dict verbatim. The tool is NOT re-invoked.

**Error:** the tool's `ToolResult.text` is captured verbatim, including the `[error: ...]`
prefix when the underlying tool failed. `tool_call` itself only raises `WorkflowToolNotAllowed`.

**Cancellation:** in-flight tool execution is subject to the tool's own per-call timeout and
`ctx.cancelled` propagation.

**Example:**

```python
result = await wf.tool_call("tabstack_research", query="climate adaptation 2026")
text = result["text"]
```

### `wf.subagent(prompt, *, schema=None, allowed_tools=None, allow_vault_retrieval=False, allow_vault_read=False, model=None) -> dict | str`

Dispatches a child agent loop via `delegate.run_child_turn` (the mature child-agent
infrastructure: restricted tools, no further delegation, no skill activation, no vault writes
by default). Returns the child's final text when `schema` is None, or the parsed structured
dict when a schema is supplied (with a silent fallback to raw text on parse failure).

- `prompt` — task description handed to the child.
- `schema` — optional JSON Schema; when supplied, the child is prompted to emit a fenced JSON
  block matching the shape, and the parsed dict is returned.
- `allowed_tools` — narrows the tool allowlist further than the standard delegate excludes.
- `allow_vault_retrieval` — opt in to vault retrieval inside the child turn (default `False`).
- `allow_vault_read` — opt in to `vault_read` in the child's allowed tools (default `False`).
- `model` — per-call model override (NOT part of the fingerprint; must be deterministic).

**Fingerprint:** `(prompt, schema, sorted(allowed_tools), allow_vault_retrieval, allow_vault_read)`.
`model` is excluded, matching `llm_call`'s convention.

**Replay:** cache hit returns the cached value (dict or str). The child agent is NOT
re-invoked.

**Error:** the child's `[error: ...]`-prefixed text is returned as-is; `subagent` never raises
on child-turn failures.

**Cancellation:** the child turn participates in the parent's `ctx.cancelled` propagation
through `run_child_turn`.

**Example:**

```python
report = await wf.subagent(
    prompt="Synthesize these summaries into a titled markdown report:\n\n…",
    schema={"type": "object", "properties": {"title": {"type": "string"},
                                              "body": {"type": "string"}},
            "required": ["title", "body"]},
)
```

### `wf.parallel(thunks) -> list`

Runs N thunks concurrently under sub-handles. Returns results in thunk-index order.

```python
async def parallel(
    self,
    thunks: list[Callable[[WorkflowHandle], Awaitable[Any]]],
) -> list[Any]
```

Each thunk receives a sub-handle whose key prefix is `(outer_seq, thunk_idx)`, so its own
journaled calls land at hierarchical keys like `(outer, idx, 0)`, `(outer, idx, 1)`, ...
Sub-handles share `ctx`, `journal`, `llm_caller`, and `model` with the parent and start with
`_cursor = 0`.

**Fingerprint:** `(count,)`. Callables aren't JSON-serializable; per-thunk replay determinism
comes from the child entries inside each sub-handle's namespace.

**Replay (full hit):** the outer entry caches the assembled result list; on a cache hit the
list is returned verbatim and thunks are NOT re-dispatched.

**Replay (mid-fan-out resume):** if the outer entry was never written (e.g. the run crashed
or was cancelled mid-flight), replay re-dispatches every thunk. Each thunk's sub-handle hits
its existing journal entries (cached per inner call) and resumes execution from the first
non-cached call.

**Error:** the first thunk to raise a real (non-`CancelledError`) exception propagates out
in thunk-index order. Outstanding in-flight thunks are cancelled and their `CancelledError`s
are absorbed during cleanup so they cannot mask the real failure. The outer entry is NOT
written when an exception escapes. Wrap individual thunks in `try/except` for tolerant
collection.

**Cancellation:** `ctx.cancelled.set()` triggers an internal watcher that cancels all
in-flight thunks and raises `asyncio.CancelledError` out of `parallel`. The outer entry is
NOT written.

**Example:**

```python
def _make_search_thunk(query: str):
    async def _thunk(sub):
        return await sub.tool_call("tabstack_research", query=query)
    return _thunk

results = await wf.parallel([_make_search_thunk(q) for q in queries])
```

### `wf.pipeline(items, *stages) -> list`

Per-item run of `stage1 → stage2 → … → stageN`, with no barrier between stages: each item
flows through every stage independently. Item A can be in stage 3 while item B is still in
stage 1; wall-clock cost equals the slowest single-item chain.

```python
async def pipeline(
    self,
    items: list,
    *stages: Callable[[Any, Any, int, WorkflowHandle], Awaitable[Any]],
) -> list
```

Each stage receives `(prev, item, idx, sub)`:

- `prev` — the previous stage's return (or `item` itself for the first stage).
- `item` — `items[idx]`, the per-row input.
- `idx` — item index in the input list.
- `sub` — the per-item sub-handle. Stages MUST use this for any journaled call so keys land
  under `(outer_seq, idx, ...)`. Stages share their per-item sub-handle's cursor
  sequentially, so `sub.llm_call` in stage 1 and stage 2 of item 0 land at `(outer, 0, 0)`
  and `(outer, 0, 1)` respectively.

Items must be JSON-serializable (they go into the fingerprint).

**Fingerprint:** `(items, stage_count)`. Stages themselves are not in the fingerprint.

**Replay (full hit):** the outer entry caches the assembled per-item final-stage results;
on a cache hit the list is returned verbatim and stages are NOT re-run.

**Replay (mid-pipeline resume):** if the outer entry was never written, replay re-dispatches
every item; each item's sub-handle hits its already-journaled stage results and resumes from
the first non-cached call within that item's chain.

**Error:** the first item-stage to raise a real exception propagates out in item-index order;
other items' in-flight stages are cancelled. The outer entry is NOT written.

**Cancellation:** same as `parallel` — `ctx.cancelled.set()` cancels all in-flight items and
raises `asyncio.CancelledError`.

**Edge cases:** zero stages → items pass through unchanged. Zero items → returns `[]`
(written to the journal as a `pipeline` entry with an empty list).

**Example:**

```python
async def _extract_stage(prev, item, idx, sub):
    return prev.get("text", "") if isinstance(prev, dict) else str(prev)

async def _summarize_stage(prev, item, idx, sub):
    return await sub.llm_call(
        prompt=_summarize_prompt(prev),
        schema=_SUMMARY_SCHEMA, system=_SYS_SUMMARIZE)

summaries = await wf.pipeline(search_results, _extract_stage, _summarize_stage)
```

### Multi-primitive example: `/research`

See `src/decafclaw/workflow/workflows/research.py` for a hero orchestrator that exercises
all six primitives end-to-end. Outline:

1. `wf.user_input` to collect topic + scope from the user.
2. `wf.llm_call` to plan 3–5 search queries (forced-tool structured output).
3. `wf.parallel` to fan out the searches — each thunk uses `sub.tool_call("tabstack_research", query=q)`.
4. `wf.pipeline` for per-result extract → summarize — stage 1 is a plain dict→str transform
   (not journaled), stage 2 uses `sub.llm_call` to journal the structured summary.
5. `wf.subagent` for final synthesis with a `{title, body}` schema.

Trigger via `/research` in the web UI. The final artifact is rendered as a markdown
`# Title\n\nbody` block.

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

### Skill activation

Workflows can declare additional skills they need at decoration time:

```python
@workflow("research", requires_skills=("tabstack",))
async def research(wf):
    ...
    await wf.tool_call("tabstack_research", query=q)
```

At workflow-turn start, `run_workflow_turn` activates:

1. **Always-loaded skills** (e.g. `vault`, `background`, `mcp`) — the same set the agent
   loop auto-activates via `_setup_turn_state`. Tools from these are reachable from
   `wf.tool_call` without explicit declaration. Fail-soft per skill — a failed activation
   logs and continues so one broken always-loaded skill doesn't take down every workflow.
2. **`requires_skills` entries** — declared per-workflow. Activated against the same code
   path as the agent loop's `activate_skill` tool. Workspace-tier skills ARE permitted here
   (unlike always-loaded, where workspace skills can't self-mark). Fail-loud — see below.

**Failure mode.** A missing skill name in `requires_skills`, or a skill whose `init()`
raises, or a skill whose `tools.py` fails to import — any of these surfaces as
`WorkflowSkillActivationFailed` BEFORE the orchestrator runs. The turn returns
`ToolResult(text="[error: skill activation failed: …]")`; the journal status is marked
`"error"`. The workflow author hears about typos and broken dependencies up front rather
than discovering them when `wf.tool_call` returns an `[error: unknown tool]` response 30s
into the run.

**Idempotency.** Activation re-runs on every workflow turn (including post-`user_input`
resumes), but the `ctx.skills.activated` set short-circuits already-activated skills —
no observable difference.

**Subagents inherit.** `wf.subagent` dispatches a child agent turn via
`delegate.run_child_turn`, which inherits the parent's `ctx.tools.extra`. A workflow that
activated `tabstack` makes `tabstack_research` reachable from the child too — no separate
`requires_skills` on the subagent side.

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
  handle.py            WorkflowHandle — the wf object; llm_call + user_input +
                       tool_call + subagent + parallel + pipeline primitives
  llm.py               call_structured() — forced-tool structured-output LLM helper
  errors.py            WorkflowSuspended, WorkflowNonDeterministic,
                       WorkflowToolNotAllowed, WorkflowError
  paths.py             workflow_dir() + workflow_path() — per-conv file location helpers
  resume.py            run_workflow_turn() + WorkflowUserInputHandler (harness glue)
  workflows/
    interview.py       Suspend/resume hero orchestrator (@workflow("interview"))
    research.py        Multi-primitive hero orchestrator (@workflow("research"))
```

## What is not in v1

The following are explicitly deferred:

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
