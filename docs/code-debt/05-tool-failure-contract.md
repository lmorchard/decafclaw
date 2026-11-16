# Epic 5 — Tool failure contract & batch execution

Failure is encoded only as a `"[error:"` string prefix; two tools duplicate a
batch-concurrency engine.

---

### 5.1 — Add a structured `ToolResult.ok`/`status` field

**Size:** M
**Triage:** ready

**Problem.** Tool failure is encoded only as a `"[error:"` text prefix; telemetry,
classification, and recovery all re-parse it. `tool_telemetry.py` documents the
gap.

**Result.** `ToolResult` carries a first-class `ok` (or `status`) field, populated
by `execute_tool` so callers need not parse text.

**Scope.** In: `ToolResult` + `execute_tool`/`_to_tool_result`. Out: do not yet
rewire consumers (5.2, 5.3).

**Verify.** `make check` + `make test` — `tests/test_tool_call_result` if present,
else `tests/test_core_tools.py` / `tests/test_tool_execution` if present.

**Tests.** Add cases asserting success/error results carry the right status.

**Deps.** none

---

### 5.2 — Derive `infer_outcome` / `ERROR_TEXT_PREFIX` from the field

**Size:** S
**Triage:** blocked-by #5.1

**Problem.** `tool_telemetry.infer_outcome` and the `ERROR_TEXT_PREFIX` reuse in
`delegate.py`/`llm_tools.py` guess success by `startswith("[error:")`.

**Result.** Both read the structured status field instead of string-signal logic.

**Scope.** In: `tool_telemetry.py`, `delegate.py`, `llm_tools.py`. Out: `delegate`
batch behavior otherwise.

**Verify.** `make test` — `tests/test_delegate.py` / `tests/test_llm_tools.py`.

**Tests.** Existing outcome-classification tests pass; add an error-classification
case if none exists.

**Deps.** blocked-by #5.1

---

### 5.3 — Stop returning bare error strings

**Size:** S
**Triage:** ready

**Problem.** `mcp_client._make_tool_caller`, `tool_output_tools._page_text`, and
`core.py` return bare `"[error: ...]"` strings instead of `ToolResult`, producing
heterogeneous return types.

**Result.** All error branches return `ToolResult(text="[error: ...]")`.

**Scope.** In: the named return sites. Out: no success-path change.

**Verify.** `make test` — `tests/test_mcp_client.py`, `tests/test_core_tools.py`.

**Tests.** Add cases asserting error returns are `ToolResult` instances.

**Deps.** none

---

### 5.4 — Extract a shared batch-concurrency helper

**Size:** M
**Triage:** ready

**Problem.** `delegate.py` and `llm_tools.py` duplicate the Semaphore / progress /
`asyncio.gather(return_exceptions=True)` / ordered-results / summary engine.

**Result.** One `run_concurrent(worker, items, concurrency, publish_status)`
helper returning ordered, normalized results + summary; both tools call it.

**Scope.** In: the fan-out/aggregation logic in both tools. Out: per-tool worker
semantics.

**Verify.** `make test` — `tests/test_delegate.py` + `tests/test_llm_tools.py`.

**Tests.** Existing concurrency/progress/exception tests pass unchanged.

**Deps.** none