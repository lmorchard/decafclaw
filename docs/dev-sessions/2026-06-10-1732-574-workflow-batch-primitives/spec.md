# Workflow batch primitives Spec

**Goal:** Extend the workflow replay engine with four new journaled primitives — `wf.tool_call`, `wf.subagent`, `wf.parallel`, `wf.pipeline` — so orchestrators can fan out work (research, audit, sweep, transform) without giving up durable replay. Ship a second hero workflow (`/research`) that exercises all four end-to-end and walks the same mid-run-restart smoke bar as `/interview`.

**Source:** [Issue #574](https://github.com/lmorchard/decafclaw/issues/574)

## Current state

PR #573 landed two journaled primitives on `WorkflowHandle`: `user_input` and `llm_call` (see `research.md` §1 — `src/decafclaw/workflow/handle.py:53-87`). Both follow the same shape: increment the integer `_cursor`, fingerprint args, check the journal, return cached on hit, run+record on miss. `WorkflowSuspended` raises out of `user_input` on a miss; `llm_call` invokes the LLM inline.

The journal is a flat list of `JournalEntry(seq, kind, args_fingerprint, result)` (`journal.py:26-31`), indexed by integer `seq`. Mismatches raise `WorkflowNonDeterministic` (`handle.py:41-51`). Persistence is a single `workflow.json` per conversation (`paths.py`).

Adjacent infrastructure already exists:
- **Child-agent dispatch**: `delegate._run_child_turn` (`delegate.py:112-258`) — forked config, restricted tool allowlist, `TurnKind.CHILD_AGENT`, configurable `return_schema`. Used today by `tool_delegate_task` / `tool_delegate_tasks`.
- **Tool execution by name**: `tools.execute_tool(ctx, fn_name, fn_args)` (`tools/__init__.py`) dispatches by name from a registry built at startup (`TOOL_DEFINITIONS` + skill tools + MCP). Used today from inside `tool_execution.execute_single_tool` (`tool_execution.py:232`).
- **Concurrent dispatch under semaphore**: `tool_execution.execute_tool_calls` (`tool_execution.py:282-364`) uses `asyncio.gather(..., return_exceptions=True)` with a `ctx.cancelled.wait()` watcher (`tool_execution.py:307-311`).

The hero workflow today is `workflow/workflows/interview.py` — single-orchestrator, sequential, one user input and one llm_call at a time. There is no fan-out test surface.

## Desired end state

Four new methods on `WorkflowHandle`, each a journaled boundary crossing:

1. **`wf.tool_call(name, **args) -> ToolResult-equivalent dict`** — invokes a tool by name via `tools.execute_tool` against the parent ctx's `allowed_tools`. Records `{"text", "data"}` (and any other JSON-serializable fields from `ToolResult`) to the journal. Fingerprint: `(name, args)`.

2. **`wf.subagent(prompt, *, schema=None, allowed_tools=None, allow_vault_retrieval=False, allow_vault_read=False, model=None) -> dict | str`** — dispatches a child agent loop via `delegate._run_child_turn` (direct call, not via the `delegate_task` tool wrapper). Returns the child's final text, OR the schema-validated structured result if `schema` is given (forced-tool output, same mechanic as `llm_call`). Fingerprint: `(prompt, schema, allowed_tools, allow_vault_retrieval, allow_vault_read)` — `model` excluded to match `llm_call`'s convention.

3. **`wf.parallel(thunks: list[Callable[[WorkflowHandle], Awaitable[T]]]) -> list[T]`** — runs N thunks concurrently. Each thunk receives a **sub-handle** (its own `_cursor`, journal key prefix `(parent_seq, thunk_idx)`). `asyncio.gather(*tasks)` with cancellation propagation via `ctx.cancelled`. Default error policy: propagate. Outer journal entry caches the aggregate result list on completion; child entries record each thunk's journaled calls for mid-parallel resume.

4. **`wf.pipeline(items: list, *stages: Callable[[Any, Any, int, WorkflowHandle], Awaitable[Any]]) -> list`** — for each item, runs `stage1 → stage2 → … → stageN` independently (no barrier). Wall-clock is the slowest single-item chain. Each item gets a sub-handle keyed `(parent_seq, item_idx)`; stages share that sub-handle's cursor sequentially. **Stage signature: `async def stage(prev, item, idx, sub)`** where `sub` is the per-item sub-handle stages must use for journaled calls. Fingerprint: `(items, stage_count)` — items must be JSON-serializable. Default error policy: propagate (any failed item raises out).

**Journal extension.** `JournalEntry.seq` becomes a path (tuple of ints). Storage shifts from list to dict keyed by tuple. On-disk JSON: path serialized as `"5.0.2"`-style dotted string for stable JSON keys. Migration: existing flat-int journals load by upgrading `seq: N → (N,)`.

**Sub-handle.** A `WorkflowHandle` variant carrying a key prefix tuple. All journaled-method seq computation prepends the prefix. Sub-handles are transient (lifetime of the thunk); they share `ctx`, `journal`, `_llm_caller`, and `_model` with the parent.

**Second hero workflow.** `/research <topic>`:
1. `wf.user_input` for clarifying questions (1-3 rounds).
2. `wf.llm_call` to expand the topic into N parallel search queries (forced-tool structured output).
3. `wf.parallel` fans out N `wf.tool_call("http_fetch", url=...)` or web-search tools.
4. `wf.pipeline` runs each fetched page through `extract` → `summarize` stages (each `wf.llm_call`).
5. Final `wf.llm_call` synthesizes the report; `wf.tool_call("vault_write", ...)` saves it.

**Smoke checklist.** Same bar as PR #573 (per `reference_workflow_smoke_pattern`):
- Live walk on `vertex-gemini-flash`, mid-run server restart, workflow resumes from journal.
- Headless `decafclaw-client` drives the conversation; web-only server via `MATTERMOST_ENABLED=false`.
- Verify journal entries on disk look correct under the new tuple-path scheme.

## Design decisions

- **Decision:** Hierarchical sub-handles with tuple-path journal keys.
  - **Why:** Issue body explicitly calls for "positional sub-keys"; nested fan-out (parallel-of-pipelines, etc.) needs to compose without re-numbering the global seq. Sub-handle pattern keeps each thunk's cursor local while preserving global determinism.
  - **Rejected:** Flat seqs + thunk index in fingerprint (order-fragile under refactoring); flat seqs with reserved range (caps nesting, picks an arbitrary block size).

- **Decision:** `wf.subagent` wraps `delegate._run_child_turn` directly.
  - **Why:** Mature dispatch path — restricted tools, blocked vault writes, child conv_id, event forwarding, timeout enforcement all exist. Direct call avoids tool-wrapper ergonomics (tool args go through JSON, schema handling is in the tool wrapper). Schema support comes free via `return_schema`.
  - **Rejected:** Aliasing as `wf.tool_call("delegate_task", ...)` (loses schema ergonomics); a fresh workflow-specific child-dispatch path (divergent semantics from delegate).

- **Decision:** Propagate errors out of `wf.parallel` and `wf.pipeline` by default.
  - **Why:** Decafclaw's general posture is fail-loud (`CLAUDE.md`: "Zero tolerance for warnings/traceback noise"). Drop-null silently swallows failures the workflow author may not have anticipated. Callers wrap individual thunks in try/except for tolerant collection.
  - **Rejected:** Drop-null (Claude Code's choice) — silent error swallowing; per-call `on_error` config — premature flexibility, biggest API surface, can be added later if needed.

- **Decision:** `wf.tool_call` is gated by the parent ctx's `allowed_tools`.
  - **Why:** Workflows shouldn't gain capabilities the parent agent didn't have. Predictable from an audit standpoint and reuses the existing gate.
  - **Rejected:** Workflow-declared allowlist (declaration friction now, can be added later); full registry no-gate (surprise capabilities).

- **Decision:** Cooperative cancellation via `ctx.cancelled` event.
  - **Why:** Matches `tool_execution.py:307-311`'s pattern. In-flight tasks check between journaled calls. Completed journal entries are kept; resume continues from where it stopped.
  - **Rejected:** Block-until-settle (slower shutdown); force-cancel + drop partial journal (idempotency-violating tools become unsafe).

- **Decision:** Pipeline stage signature is `async def stage(prev, item, idx, sub)`. Later stages see the original item and index, plus a per-item sub-handle for journaled calls.
  - **Why:** Stages need to make journaled calls (`sub.llm_call`, `sub.tool_call`) under the per-item sub-handle so replay keys stay positionally addressable. Claude Code's 3-arg signature works for them because their workflows aren't durable; ours are, so explicit sub-handle access is required. `item` and `idx` let later stages label work without threading context through stage 1's return value.
  - **Rejected:** `stage(prev, item, idx)` with implicit contextvar-bound `wf` (hidden inversion-of-control); journaling stages themselves as single units (coarser replay granularity — a partially-completed stage's tokens are lost on resume); `stage(prev)` only (loses item identity).

- **Decision:** `wf.subagent` supports `schema=` like `wf.llm_call`.
  - **Why:** `delegate.tool_delegate_task` already supports `return_schema`. The child's intermediate tool use is unconstrained; only the final output is gated. Symmetric with `llm_call` for the workflow author.
  - **Rejected:** Text-only subagent (loses an existing capability); separate `wf.subagent_structured(...)` method (doubles surface area).

- **Decision:** `model` excluded from `wf.subagent` and `wf.tool_call` fingerprints (consistent with `wf.llm_call`).
  - **Why:** Per-call model swap shouldn't invalidate the journal — the orchestrator's intent is the same. Operational latitude to upgrade models without breaking resume.
  - **Rejected:** Including `model` in fingerprint (resume-fragile when model defaults shift).

## Patterns to follow

- **Primitive structure:** mirror `WorkflowHandle.llm_call` (`handle.py:53-75`) — cursor increment, fingerprint, `_check_or_none`, journal append, save.
- **Sub-handle key prefix application:** every journaled method on a sub-handle constructs its seq as `prefix + (sub_cursor,)`. Top-level handle uses `prefix = ()`.
- **Subagent dispatch:** import `_run_child_turn` from `src/decafclaw/tools/delegate.py:112-258` and call directly (mark non-private once promoted to a primitive). Inherit its tool-restriction defaults; expose `allowed_tools` / `allow_vault_retrieval` / `allow_vault_read` as kwargs.
- **Tool call:** call `tools.execute_tool(ctx, name, args)` from `src/decafclaw/tools/__init__.py`. Pre-validate name against `ctx.allowed_tools`. Convert returned `ToolResult` to a JSON-serializable dict before journal storage.
- **Concurrent dispatch:** model on `tool_execution.execute_tool_calls` (`tool_execution.py:282-364`) — `asyncio.gather` + ctx-cancellation watcher. No semaphore: parallel sizing is the workflow author's responsibility (the agent's `max_concurrent_tools` is an agent-loop concern, not a workflow one).
- **Test pattern:** mirror `tests/test_workflow_engine.py` style — live path test + replay test per primitive. For sub-handle tests, exercise nested parallel-in-pipeline once to confirm key composition.
- **Hero workflow:** model `/research` on `workflow/workflows/interview.py` for command registration / orchestrator entry. Register as a user-invocable workflow with `$ARGUMENTS` for the topic.

## What we're NOT doing

- **Resolving WORKFLOW Context-kind semantics (#575).** The subagent decision uses `_run_child_turn` which already uses `TurnKind.CHILD_AGENT`; the parent workflow Context kind isn't forced by this work. Punt #575 as a separate ADR.
- **Sidecar directory migration (#576).** `workflow.json` continues to use the directory layout; flat sidecars stay flat for now.
- **LLM-generated workflows (#577).** Out of scope; downstream of this work.
- **A declarative step DSL.** Already rejected in PR #573 (`docs/workflows.md`). Control flow is the host language.
- **Per-call error policy config (`on_error="raise"|"null"|"tuple"`).** Defer until a real workflow demands it. Default propagate is enough.
- **Workflow-declared `allowed_tools` at `@workflow` decoration time.** Parent ctx's allowlist is the gate. Can be tightened later.
- **A "workflow-of-workflows" primitive.** Subagent already covers it (a subagent's prompt could trigger another workflow). No fresh primitive.
- **Performance tuning / per-primitive timeouts beyond inherited ones.** Tool calls inherit `TOOL_TIMEOUT_SEC`; subagents inherit `child_timeout_sec`. Parallel/pipeline have no timeout of their own.

## Open questions

- **Q: Should `wf.subagent`'s default `allowed_tools` be the parent ctx's allowlist (like `wf.tool_call`), or the existing `_run_child_turn` default (parent's allowed_tools minus delegations/vault-writes)?**
  - **Default:** match `_run_child_turn`'s existing default. Re-using established semantics; the workflow author can override via the kwarg.

- **Q: What does `wf.tool_call`'s journal entry store for tools that return media (images, files)?**
  - **Default:** strip media from the journal entry, keep `text` + `data`. Media bytes are large and rebuildable from upstream; the journal is for replay correctness, not full reconstruction of side-channel outputs.

- **Q: Should the outer journal entry for `wf.parallel` / `wf.pipeline` store the assembled result, or only a completion marker (delegating to child entries)?**
  - **Default:** outer entry stores the assembled list. Fast-path full-replay returns instantly; child entries are still present for crash-resume mid-fanout. Storage cost is the assembly itself — a list of already-journaled values.
