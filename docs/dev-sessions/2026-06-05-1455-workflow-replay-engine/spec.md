# Workflow replay engine — spec

**Issue:** [#255](https://github.com/lmorchard/decafclaw/issues/255) (fresh approach;
supersedes PR #557, which is being closed).

## Why this exists

#255 asked for a first-class workflow abstraction. Four iterations on PR #557 built an
LLM-driven phase engine (cross-conv → conv-scoped → phase-turn → code-driven spike) that
**never walked a multi-phase workflow end-to-end against a live LLM** until the spike. The
diagnosis (Sophie analysis, in `docs/workflow-engine-255-redesign-proposal.md`): the engine
made the **LLM responsible for cranking the state machine forward** (emit `phase_advance`),
and the model reliably narrates instead of firing the tool.

The lesson — confirmed by the spike and mirrored by Claude Code's "dynamic workflows"
feature — is that **a workflow needs a programmatic control scaffold.** Code drives the
process; the LLM is invoked only as a constrained structured-output worker on focused
problems. This project builds that scaffold as a first-class concept.

The goal is **practically useful learning**: how to empower an agent harness to run
*semi-deterministic* workflows — durable, human-in-the-loop processes where code owns
control flow. The transferable systems concept is **durable execution via deterministic
replay** (the model behind Temporal, DBOS, and Claude Code workflows).

## The load-bearing rule (record this prominently)

The engine re-runs the orchestrator function **from the top** on every resume. So the
dividing line for every operation in an orchestrator is:

- **Pure / deterministic / side-effect-free → plain Python, not journaled.** Re-runs on
  every replay, harmlessly, because it always produces the same result. **Control flow lives
  here**: `if`/`while`/`for`, string formatting, accumulating answers in a list, "routing"
  (an `if` on a journaled result). Re-running it during replay is free and correct.
- **Crosses the boundary to the outside world → must go through a journaled wrapper.**
  Anything nondeterministic or with side effects: the LLM, the user, tool calls, subagent
  spawns, the clock, randomness, the network. Recorded on first execution; **replayed from
  the journal** on re-run — never re-executed. A raw `await some_tool(...)` would fire twice
  (double write/email) or return a different value and diverge control flow.

> **Every call that crosses to the outside world goes through the journal; everything else
> is ordinary code.** That single rule is the entire discipline. It is Temporal's
> "activities vs. workflow code" line, DBOS's, and Claude Code's.

For the MVP that means exactly **two** journaled wrappers: `llm_call` and `user_input`.
Subagent/tool/`parallel`/`pipeline` wrappers join them later under the same rule.

## Architecture

A **workflow** is a registered async Python function (the *orchestrator*) the harness runs
as its own `TurnKind.WORKFLOW`. The orchestrator owns control flow. The LLM is never the
orchestrator — it is invoked only *inside* journaled primitives. Skills shape what the
*LLM* does; workflows are what the *harness* runs. Two clean concepts, no overload.

New module `src/decafclaw/workflow/` (fresh — not the #557 tree):

| File | Purpose |
|---|---|
| `registry.py` | `@workflow("interview", allowed_tools=[...])` decorator + startup discovery |
| `engine.py` | Replay executor: runs an orchestrator, manages the journal, raises/catches suspension |
| `journal.py` | Durable, ordered record of journaled-call results for one run |
| `primitives.py` | `llm_call`, `user_input` — the two journaled wrappers (MVP) |
| `workflows/interview.py` | The hero orchestrator |

## The replay engine + journal

The **journal** is a durable, ordered list of completed journaled-call results for one
workflow run, persisted as a per-conversation file at
`workspace/conversations/{conv_id}/workflow.json`. Each entry:
`{seq, kind, args_fingerprint, result}`, **keyed positionally by execution order** (the
*N*th journaled call executed gets sequence *N*). Positional keying is what makes loops
replay correctly: same control flow → same execution order → same keys.

- **Run:** the engine calls the orchestrator. Each journaled primitive, on first execution,
  runs live, appends its result to the journal (flushed to disk), and returns it. Pure
  Python between primitives runs normally.
- **Suspend:** `user_input` appends nothing — it raises `WorkflowSuspended` carrying the
  input-widget spec. The engine catches it, persists the journal, emits the widget, ends the
  turn.
- **Resume:** the user's answer is recorded as the journal entry for that `user_input`, then
  a new `WORKFLOW` turn re-runs the orchestrator **from the top**. Every prior journaled call
  returns its cached result instantly (no LLM calls, no re-spawns); control flow
  fast-forwards deterministically to just past the answered `user_input`; execution continues
  live. **Durable across restart** because the journal is just a file — a restart mid-suspend
  loses nothing.
- **Completion:** orchestrator returns; the engine marks the run done, writes the returned
  artifact, ends the turn.

## Harness integration

- New `TurnKind.WORKFLOW` in `ConversationManager`;
  `enqueue_turn(kind=WORKFLOW, workflow=..., resume=bool)`.
- Suspend reuses the **existing confirmation / `WidgetInputPause` persistence** for
  durability and page-reload recovery — but the resume callback routes to the **engine, not
  the agent loop.** That single difference is what keeps the LLM off the crank (the failure
  mode of #557's model and of the rejected "workflow-as-tool" approach).
- Invocation: a user command (`/interview`) enqueues the first `WORKFLOW` turn.
- Per-primitive `tool_status` events stream progress to the conversation timeline (as the
  spike did).

## Authoring surface — the interview, concretely

The whole orchestrator reads as ordinary Python:

```python
@workflow("interview", allowed_tools=[...])
async def interview(wf):
    topic = await wf.user_input("What should this interview be about?")

    answers = []
    while True:
        # pure-Python branching on a journaled result — no `route` primitive
        decision = await wf.llm_call(
            prompt=ask_next_question_prompt(topic, answers),
            schema={"done": bool, "question": str},
        )
        if decision["done"] or len(answers) >= MAX_Q:
            break
        reply = await wf.user_input(decision["question"])   # suspends here
        answers.append((decision["question"], reply))

    artifact = await wf.llm_call(
        prompt=synthesize_prompt(topic, answers),
        schema={"title": str, "body": str},
    )
    return artifact   # engine writes it, ends the run
```

`wf` is the engine handle exposing the journaled primitives. The `while`, the `if`, and the
`answers` list are plain Python — re-run freely on replay, always identical because they are
driven by journaled results. The two `await wf.*` calls are the only journaled boundary
crossings. The entire mental model fits on one screen — that is the lesson.

`llm_call` uses the spike's forced-tool structured-output pattern (one tool, "you MUST call
this" framing, parse args, retry once with a stricter nudge on narrate-stall). Provider-
agnostic; no `vertex.py` changes. Reference: `_call_structured` in the spike
(`src/decafclaw/skills/spike_research_brief/tools.py`, on the #557 branch).

## Error handling & the determinism guard

- **Primitive failure** (LLM error, schema-parse fail after one retry): run goes to `ERROR`
  status, journal preserved, user surfaced via the turn output. No silent fall-through.
- **Determinism guard:** each journal entry stores an `args_fingerprint`. On replay, when the
  orchestrator reaches journaled call *N*, the engine checks the replay's args against the
  recorded fingerprint. Mismatch ⇒ control flow diverged (a nondeterminism bug) ⇒ **fail
  loudly** ("workflow non-deterministic at step N") rather than silently returning a stale
  result. This is what makes the discipline *enforced*, not hoped-for — and it is the part
  that teaches the lesson.
- **Cancellation:** the orchestrator checks `ctx.cancelled` between primitives (as the spike
  does); a cancel ends the run cleanly.

## Testing

- **Unit (deterministic, no LLM):** journal round-trip; replay returns cached results without
  re-executing (sabotage check: assert a primitive's live path is *not* hit on replay);
  suspend raises and persists; resume fast-forwards past the answered input; determinism
  guard fires on a deliberately non-deterministic orchestrator. LLM/user primitives are
  faked.
- **Restart durability:** persist the journal mid-suspend, construct a fresh engine from
  disk, resume — assert it continues with no lost state.
- **Live smoke (the bar):** `/interview` against `vertex-gemini-flash` in the web UI; answer
  2–3 questions; **restart the server mid-interview**; resume; reach the artifact. That is the
  bar no prior iteration cleared, now including a restart.
- An eval case is premature in v1 (no LLM routing decision to guard); revisit when an
  orchestrator gains a real LLM-driven branch.

## Scope

**This project ships:**

- `src/decafclaw/workflow/` module (registry, engine, journal, primitives)
- `TurnKind.WORKFLOW` + engine-routed resume in `ConversationManager`
- `llm_call` + `user_input` journaled primitives
- The journal with positional keying + determinism guard, persisted at
  `conversations/{conv_id}/workflow.json`
- The `interview` orchestrator + `/interview` command
- Unit tests, restart-durability test, and the live restart smoke

**PR #557 disposition:** closed, not reworked. The new engine is replay-shaped, not
graph/DSL-shaped — different enough that a fresh branch is cleaner than reworking ~15k lines.
#557's commits stay as the record of what didn't work; ideas are salvaged as reference
(conv-scoped persistence + lock, `RunStatus` lifecycle), not code.

**Explicitly NOT in v1:**

- `subagent` / `parallel` / `pipeline` / `tool_call` primitives (these arrive with the batch
  fan-out case; they are real journaled wrappers, just not needed for the interview)
- LLM-generated per-task workflows (the Claude Code model — the engine should not preclude
  it, but we do not build it now)
- Migrating the existing flat sidecars (`.notes.md`, `.decisions.json`, `.context.json`) and
  the archive into the `conversations/{conv_id}/` directory layout. Only the new
  `workflow.json` adopts the directory now; two conventions coexist temporarily. **Deferred
  follow-up.**
- A declarative step-primitive DSL / typed-step vocabulary / template language (the direction
  explored in the shelved `2026-06-01-1055-workflow-step-primitive-design` notes). We
  deliberately reject this: control flow *is* the host language, so `route`/`branch`/`loop`/
  `set` as data-primitives would re-import a DSL we have decided not to build.

## Open questions (resolve during planning, none blocking)

- Exact `WorkflowSuspended` → `WidgetInputPause` wiring: does the engine construct the pause
  directly, or go through the existing confirmation registry? (Lean: reuse the registry's
  persistence; add a workflow-resume `ConfirmationAction`.)
- Journal flush granularity: per-entry fsync vs. flush-on-suspend. (Lean: flush on every
  append for crash-safety; revisit if it is a perf problem.)
- Where the final artifact is written for the interview (workspace path + naming).
- `user_input` affordances in v1: free-text only, or also button choices? (Lean: free-text
  for the interview; button choices fold in trivially since both ride `WidgetInputPause`.)
