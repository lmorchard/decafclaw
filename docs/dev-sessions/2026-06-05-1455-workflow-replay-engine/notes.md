# Session notes — workflow replay engine (#255)

## Summary

Implemented a first-class workflow replay engine in `src/decafclaw/workflow/`. The core
insight is that code must own control flow, and the LLM must only be invoked as a
constrained structured-output worker. The engine re-runs the orchestrator from the top on
every resume, fast-forwarding through journaled results — the same model as Temporal, DBOS,
and Claude Code dynamic workflows.

Key deliverables: `@workflow` registry, `WorkflowHandle` with `llm_call` + `user_input`
primitives, positional-keyed journal with determinism guard, suspend/resume through the
existing confirmation infrastructure, `TurnKind.WORKFLOW` dispatch in
`ConversationManager`, `/interview` command bridge in `web/websocket.py`, full unit test
suite including restart-durability.

Docs: `docs/workflows.md`, linked from `docs/index.md` and `CLAUDE.md`.

---

## Live smoke checklist

Run this manually against `vertex-gemini-flash` in the web UI after merging.
This is the bar no prior iteration cleared — including a mid-interview restart.

### Setup

- [ ] Confirm no other bot instance is connected to Mattermost (a second silently misses
  WebSocket events — check with `ps aux | grep decafclaw`).
- [ ] Start the dev server: `make dev` (auto-restart on file changes, 10s graceful shutdown).
- [ ] Open the web UI and create a **new conversation**.

### Happy path

- [ ] Type `/interview` and submit.
- [ ] The bot should post: *"What should this interview be about?"*
- [ ] Paste a topic answer (e.g., "my experience learning Rust").
- [ ] The bot asks question 1. Answer it.
- [ ] The bot asks question 2 (or declares done). Answer if asked.

### Restart durability (the critical bar)

- [ ] While the bot is waiting for a question answer (pending confirmation visible in the
  UI), hit **Ctrl-C** to stop the dev process.
- [ ] Restart: `make dev` (or `make run`).
- [ ] **Reload the web UI** in the browser.
- [ ] The pending question should still be displayed (confirmation persisted across restart).
- [ ] Answer the pending question.
- [ ] The workflow resumes and continues — LLM calls replay from the journal; only the
  post-restart steps run live.

### Completion

- [ ] Continue answering until the model says it has enough (or MAX_Q=6 is hit).
- [ ] The bot synthesizes and posts the final artifact (title + markdown body).
- [ ] Artifact renders correctly in the conversation as a markdown block.

### Observed output (paste here)

```
# tool_status lines observed during the run:


# Final artifact:

```

---

## Deferred follow-up items

1. **WORKFLOW turn Context-kind semantics.** WORKFLOW turns currently reuse the USER-style
   Context path (full interactive Context with per-conv state). Comment in
   `conversation_manager.py` near `TurnKind.WORKFLOW` dispatch notes this may want
   `Context.for_task` semantics. Revisit after the live smoke confirms the current path works.

2. **Batch/fan-out primitives.** `subagent`, `parallel`, `pipeline`, `tool_call` wrappers
   are the natural next journaled primitives. They arrive when there is a concrete use case
   (batch fan-out); the engine does not preclude them.

3. **Sidecar directory migration.** Only `workflow.json` uses the `conversations/{conv_id}/`
   directory layout. The flat `{conv_id}.*` sidecars (`.notes.md`, `.decisions.json`,
   `.context.json`, `.archive.jsonl`) migrate later. Two conventions coexist until then.

4. **Eval case.** No eval case needed yet — the interview flow has no LLM-driven routing
   branch worth guarding. Add one if an orchestrator gains such a branch.

---

## Implementation complete (2026-06-08)

All 16 tasks landed on `feat/255-workflow-replay` (24 → 40 commits from spec/plan through the
final review fixes). Built via subagent-driven development: implement → spec-review →
code-quality-review per task, with a final whole-implementation review by the most-capable model.

**Final gate:** `make check` passes (ruff, pyright, tsc --checkJs, message-types drift);
full suite **2814 passed**. Workflow module: 34 tests; the durable-resume test reconstructs
the engine from disk only and proves replay continues to completion with the journaled answer.

**Critical gap caught by the final review (now fixed — Task 15):** the `/interview`
*invocation* bridge existed, but there was no UI path to *deliver* the user's answer — a
`workflow_user_input` confirmation rendered as a plain approve/deny card, so the answer was
always empty. Fixed by rendering a text input (+ choice buttons when `action_data.choices`
is set) in `confirm-view.js` and routing the typed value as `data={"value": …}` through
`confirm_response` → `respond_to_confirmation` → the handler → the journal. So in the smoke
below, the topic/question prompts now show a **text field**, not Approve/Deny.

**Run the smoke BEFORE opening/merging a PR** — it's the gate, not a post-merge check (the
checklist above predates this note; "after merging" should read "before merging").

**Deferred follow-ups (tracked, not blocking):**
- WORKFLOW turns reuse the USER `Context` path; revisit whether `Context.for_task` semantics
  fit better once the smoke confirms behavior.
- Batch primitives (`subagent`/`parallel`/`pipeline`/`tool_call`) — the fan-out case.
- LLM-generated per-task workflows (the Claude Code dynamic-workflow model).
- Migrate the existing flat conversation sidecars into `conversations/{conv_id}/` (only
  `workflow.json` uses the directory layout now).
- Per-call `model=` override on `llm_call` must be deterministic (documented in handle.py;
  not enforced by the fingerprint).

## Live smoke (2026-06-10): PASS

The bar three prior iterations never cleared. Drove `/interview` end-to-end on
`vertex-gemini-flash` via `decafclaw-client`, restarted the server mid-interview with a
pending question, and confirmed the workflow resumed cleanly and synthesized the final
artifact. Server logged `Recovered pending confirmation for conv web-lmor:
workflow_user_input` on restart; journal grew to 13 entries across the run; no
`WorkflowNonDeterministic` ever fired.

**Two integration findings surfaced and fixed in the same pass:**

1. **`decafclaw-client respond` had no `--value` / `--data` flag**, so the documented
   client couldn't actually deliver a `workflow_user_input` answer. The wire schema
   (`CliConfirmResponse.data: NotRequired[dict]`) and the server handler at
   `websocket.py:480-491` both supported it; only the CLI didn't expose it. Smoke needed
   a 35-line ad-hoc driver to walk through. Fixed by adding `--value VALUE` to the
   `respond` subparser and forwarding `data={"value": value}` from `run_respond`.

2. **The WORKFLOW turn dispatch path bypassed the conversation archive on both ends.**
   The `/interview` invocation was never archived (workflow-command intercept in
   `_handle_send` called `enqueue_turn` directly), and the final artifact emitted by
   `run_workflow_turn` was streamed live as a `message_complete` event but never
   persisted. Net effect: after the workflow ran, reloading the conversation showed
   zero messages (load_history filters confirmation rows). Fixed by adding
   `append_message` calls at both ends: `role: "user"` at the intercept site,
   `role: "assistant"` inside `run_workflow_turn` on `outcome.status == "done"`.

Both fixes landed with red-then-green tests (`test_respond_value_*`,
`test_run_respond_forwards_value_as_data`, `test_workflow_command_archives_user_invocation`,
`test_run_workflow_turn_done_archives_artifact`). Full suite still passes — **2819**
(was 2814; +5 new tests, no regressions). `make check` clean.
