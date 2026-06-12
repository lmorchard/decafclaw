# Research — Existing workflow engine & adjacent dispatch paths

Documentarian sweep, 2026-06-10. Source: `Explore` subagent run against the worktree.

## 1. Journal mechanics

**Storage shape.** `JournalEntry(seq, kind, args_fingerprint, result)` — `src/decafclaw/workflow/journal.py:26-31`. Entries stored in `Journal.entries` list, keyed by seq via array indexing — `journal.py:40-51`.

**Fingerprint.** `fingerprint(kind, args)` at `journal.py:15-23` — SHA-256 of `{"kind", "args"}` with sorted keys, returns 16-char hex. Non-JSON-serializable args raise TypeError by design (loud catch).

**Mismatch detection.** `WorkflowHandle._check_or_none(seq, kind, fp)` at `handle.py:41-51` — if stored entry's `kind` or `args_fingerprint` differ from current call, raises `WorkflowNonDeterministic`.

**Live vs replay branch (per primitive).**
- Live (no entry at seq): call runs → `journal.append(seq, kind, fp, result)` → `save_journal(ctx.config, ctx.conv_id, journal)` — `handle.py:73-74`.
- Replay (entry exists at seq): `_check_or_none` returns `(cached_result, True)` — `handle.py:51`. Primitive returns cached without re-executing.

**`WorkflowHandle.user_input(prompt)`** — `handle.py:77-87`:
1. `seq = self._cursor; self._cursor += 1`
2. `fp = fingerprint("user_input", {"prompt", "choices"})`
3. `_check_or_none(seq, "user_input", fp)` — hit → return cached
4. Miss → `raise WorkflowSuspended(seq, fp, prompt, choices)`. Turn ends; result lands in journal *after* the user answers and the workflow resumes.

**`WorkflowHandle.llm_call(prompt, schema, system, tool_name, model)`** — `handle.py:53-75`:
1. `seq = self._cursor; self._cursor += 1`
2. `fp = fingerprint("llm_call", {"prompt", "schema", "system"})` — **`tool_name` and per-call `model` are NOT in the fingerprint.**
3. Hit → return cached.
4. Miss → `self._llm_caller(ctx, system, user_msg, schema, tool_name, model)`; `journal.append(seq, "llm_call", fp, result)`; `save_journal(...)`.

**Cursor reconstruction on replay.** Engine re-runs the orchestrator from the top on every resume (`engine.py:41`). `_cursor` starts at 0 each run (`handle.py:37`). Journaled calls consume cursor positions in identical order; cached results return instantly; execution fast-forwards past completed steps.

## 2. WorkflowHandle lifecycle

**State** — `handle.py:32-39`:
- `ctx: Context`
- `journal: Journal`
- `_cursor: int = 0`
- `_llm_caller: Callable` (defaults to `_default_llm_call`)
- `_model: str = "vertex-gemini-flash"`

**Construction per turn.** `engine.py:34` in `run_workflow()`: `WorkflowHandle(ctx, journal, llm_caller=llm_caller, model=model)`. Called from `resume.py:44` in `run_workflow_turn()`, which `ConversationManager._start_turn` dispatches for `TurnKind.WORKFLOW`.

**Resume.** Same `run_workflow()` rebuilds the handle from the persisted journal + same ctx.

**Context access.** Handle carries `config` (Config with workspace_path), `conv_id` (journal file location), `ctx.publish()` for events, `ctx.cancelled` (asyncio.Event).

**Suspension flow.**
1. `handle.py:86-87`: `raise WorkflowSuspended(seq, fp, prompt, choices)`.
2. `engine.py:42`: `except WorkflowSuspended as s` returns outcome with `.suspend`.
3. `resume.py:58-74`: receives outcome, builds `ConfirmationRequest(action_type=WORKFLOW_USER_INPUT, action_data={...})`, calls `manager.post_confirmation(conv_id, request)` (no await), returns `ToolResult`. Turn ends.

## 3. Subagent / child-agent dispatch

**Existing tool.** `tool_delegate_task(ctx, task, model="", allow_vault_retrieval=False, allow_vault_read=False, return_schema=None)` — `src/decafclaw/tools/delegate.py:261-318`. Batch variant: `tool_delegate_tasks(ctx, tasks, ...)` — `delegate.py:406-515`, parallel via semaphore.

**`_run_child_turn` internals** — `delegate.py:112-258`:
- Child config via `dataclasses.replace(config, agent=..., system_prompt=child_system_prompt, discovered_skills=[])`.
- Unique child id: `f"{parent_conv}--child-{secrets.token_hex(4)}"`.
- Context setup: swap child config, `skip_vault_retrieval = not allow_vault_retrieval`, block vault WRITE tools, restrict `allowed_tools` to `parent − {delegate_task, activate_skill, tool_search, ...VAULT_WRITE}`.
- Event routing: child events forwarded to parent's subscriber via `event_context_id` — `delegate.py:194-197`.
- Dispatch: `manager.enqueue_turn(child_conv_id, kind=TurnKind.CHILD_AGENT, prompt=task, context_setup=setup, user_id=parent_ctx.user_id)`.
- Wait: `asyncio.wait_for(future, timeout=config.agent.child_timeout_sec)` — `delegate.py:253`.

**Context inheritance/reset.**
- Inherits: parent tools (minus delegations), parent's activated skills (bundled in system prompt).
- Reset: `discovered_skills=[]` (no new discovery), `skills.activated` cleared, `skip_reflection=True`, vault retrieval opt-in only.

**Return.** `_run_child_turn` returns text. `tool_delegate_task` wraps in `ToolResult(text=...)`. `tool_delegate_tasks` returns `ToolResult(data={"summary": {...}, "results": [{"index", "ok", "text", "data"}, ...]})`.

**TurnKind.** Uses `TurnKind.CHILD_AGENT` (`conversation_manager.py:74-82`).

## 4. Tool execution path

**Call signature.** Tool functions: `(ctx, **kwargs)` where `ctx` is a forked Context (`fork_for_tool_call()`) — `src/decafclaw/tool_execution.py:232`.

**`execute_single_tool(call_ctx, tc, semaphore)`** — `tool_execution.py:210-279`:
- Acquires semaphore, publishes `tool_start`.
- `tools.execute_tool(call_ctx, fn_name, fn_args)` — `tool_execution.py:232`.
- Catches `asyncio.CancelledError` and `Exception` → error `ToolResult`.
- Processes media via `process_tool_media` — `tool_execution.py:236`.
- Validates widget via `resolve_widget` — `tool_execution.py:248`.
- Publishes `tool_end`.

**`execute_tool_calls`** — `tool_execution.py:282-364`:
- `asyncio.Semaphore(ctx.config.agent.max_concurrent_tools)` — `tool_execution.py:292`.
- Per-call fork via `ctx.fork_for_tool_call(tc["id"])` — `tool_execution.py:297`.
- `asyncio.create_task(execute_single_tool(...))` — `tool_execution.py:298-301`.
- Cancellation watcher cancels in-flight on `ctx.cancelled.wait()` — `tool_execution.py:307-311`.
- Collects via `asyncio.gather(*tasks, return_exceptions=True)` — `tool_execution.py:316`.
- Returns `(None, end_turn_signal)`.

**ToolResult serialization.** `result.text` + optional `result.data` rendered as fenced `\`\`\`json\n...\n\`\`\``. `display_short_text` and widget payload copied to tool message if present.

**Tool lookup by name.** `tools.execute_tool()` in `src/decafclaw/tools/__init__.py` dispatches by name from a registry built at startup (`TOOL_DEFINITIONS` + skill tools + MCP). **No exported "call by name from outside the agent loop" is publicly documented** — invocation is via `execute_tool` directly.

## 5. Prior-session record (what's already been said about these primitives)

### From `docs/dev-sessions/2026-06-05-1455-workflow-replay-engine/spec.md`

> Line 44: "For the MVP that means exactly **two** journaled wrappers: `llm_call` and `user_input`. Subagent/tool/`parallel`/`pipeline` wrappers join them later under the same rule."

> Line 183: "**Explicitly NOT in v1:** ... - `subagent` / `parallel` / `pipeline` / `tool_call` primitives (these arrive with the batch fan-out case; they are real journaled wrappers, just not needed for the interview) ..."

### From `docs/dev-sessions/2026-06-05-1455-workflow-replay-engine/notes.md`

> Lines 79-80: "2. **Batch/fan-out primitives.** `subagent`, `parallel`, `pipeline`, `tool_call` wrappers are the natural next journaled primitives. They arrive when there is a concrete use case (batch fan-out); the engine does not preclude them."

### From `docs/workflows.md` "What is not in v1" (lines 266-286)

> "The following are explicitly deferred:
>
> - **`subagent` / `parallel` / `pipeline` / `tool_call` primitives.** These arrive with the batch fan-out case; they are real journaled wrappers, just not needed for the interview.
>
> - **LLM-generated per-task workflows.** The engine does not preclude it, but this is not built yet.
>
> - **Migrating existing flat sidecars** (`.notes.md`, `.decisions.json`, `.context.json`, `.archive.jsonl`) into the `conversations/{conv_id}/` directory layout. Only `workflow.json` uses the directory convention now; two conventions coexist temporarily.
>
> - **A declarative step DSL** (`route`/`branch`/`loop`/`set` as data-primitives). Deliberately rejected: control flow *is* the host language (`if`/`while`). A DSL re-imports the exact complexity the replay model was designed to eliminate.
>
> - **WORKFLOW turn Context-kind semantics.** WORKFLOW turns currently reuse the USER-style Context path (full interactive Context with per-conversation state). Whether they should use `Context.for_task` semantics instead is an open item to revisit after the live smoke."
