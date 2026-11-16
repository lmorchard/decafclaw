# Epic 6 — Tools registry & dispatch refactor

---

### 6.1 — Move dispatch helpers out of `tools/__init__.py`

**Size:** S
**Triage:** ready

**Problem.** `tools/__init__.py` (499 lines) mixes the manual registry with
dispatch logic (`_run_with_cancel`, `_resolve_tool_timeout`, `_typeerror_result`,
suggestion helpers).

**Result.** Those helpers move into a new `tools/dispatch.py`; `__init__.py`
re-imports them.

**Scope.** In: the named helpers. Out: do not yet change registry derivation (6.2)
or the import cycle (6.3).

**Verify.** `make check` + `make test`.

**Tests.** Existing dispatch tests pass (e.g. `tests/test_dispatch_validation_boundary.py`).

**Deps.** none

---

### 6.2 — Auto-derive `TOOLS`/`TOOL_DEFINITIONS` from one source

**Size:** M
**Triage:** ready

**Problem.** `TOOLS` (dict) and `TOOL_DEFINITIONS` (list) are two parallel
hand-maintained collections that can silently diverge in order/coverage.

**Result.** A module list (or registration decorator) drives both collections from
one source.

**Scope.** In: registry assembly in `tools/__init__.py`. Out: individual tool
modules' exports stay.

**Verify.** `make check` + `make test` — `tests/test_tool_definitions` if present,
else `tests/test_llm_guard.py` / tool catalog tests.

**Tests.** Add an assertion that every `TOOLS` key has a matching definition and
vice versa.

**Deps.** none

---

### 6.3 — Break the `tools/__init__ ↔ skill_tools ↔ tool_registry ↔ search_tools` cycle

**Size:** M
**Triage:** blocked-by #6.1

**Problem.** This cycle forces function-level imports throughout the tools layer
(documented "deferred: circular dep" comments).

**Result.** The cycle is broken (dispatch already moved in 6.1); function-level
imports are removed.

**Scope.** In: module boundaries for skill-tool loading and search. Out: no runtime
behavior change.

**Verify.** `make check` + `make test` — `tests/test_imports.py`.

**Tests.** Existing import/type tests pass.

**Deps.** blocked-by #6.1

---

### 6.4a — Create a shared file-tool helpers module; adopt in `workspace_tools`

**Size:** S
**Triage:** ready

**Problem.** `_is_cancelled`, `_file_error`, `_mini_diff`, and shared constants are
duplicated across `workspace_tools.py`, `admin_tools.py`, and `agent.py`.

**Result.** A shared module (e.g. `tools/_file_common.py`) owns these helpers;
`workspace_tools` imports them instead of keeping private copies.

**Scope.** In: define the shared module; migrate `workspace_tools`. Out:
`admin_tools` and `agent` (6.4b).

**Verify.** `make test` — `tests/test_workspace_tools.py`.

**Tests.** Existing workspace file-tool tests pass unchanged.

**Deps.** none

---

### 6.4b — Adopt the shared file-tool helpers in `admin_tools` + `agent`

**Size:** S
**Triage:** blocked-by #6.4a

**Result.** `admin_tools` and `agent` import the 6.4a helpers, deleting their
copies.

**Verify.** `make test` — `tests/test_admin_tools.py` + agent turn tests.

**Tests.** Existing admin/agent tests pass unchanged.

**Deps.** blocked-by #6.4a

---

### 6.5 — Extract a shared admin-mutation executor

**Size:** M
**Triage:** ready

**Problem.** The ~40-line write ritual (path resolve → secret refuse → snapshot →
confirm → lock → revalidate → write) is inlined four times in
`tool_admin_write`/`replace_lines`/`edit`/`delete`.

**Result.** One executor parameterized by the mutation; the four tools call it.

**Scope.** In: the four interactive mutation tools. Out: do not yet touch the
`_apply_mutation` recovery path (6.6).

**Verify.** `make test` — `tests/test_admin_tools.py`.

**Tests.** Existing admin-mutation tests pass unchanged (guards, confirmation,
oversized-diff).

**Deps.** none

---

### 6.6 — Route `_apply_mutation` recovery through the same executor

**Size:** M
**Triage:** blocked-by #6.5

**Problem.** `_apply_mutation` (`admin_tools.py` ~954) re-implements each write
body a second time for the post-restart recovery path, so a guard added to the
interactive path can be forgotten here.

**Result.** The recovery path calls the shared executor from 6.5, leaving one
write implementation.

**Scope.** In: `_apply_mutation`. Out: no recovery-protocol change.

**Verify.** `make test` — `tests/test_admin_tools.py` (recovery cases).

**Tests.** Existing recovery tests pass; add a case asserting the recovery path
honors the same secret-refusal guard as the interactive path.

**Deps.** blocked-by #6.5

---

### 6.7 — Split `check_shell_approval` into per-tier functions

**Size:** M
**Triage:** ready

**Problem.** `check_shell_approval` (`shell_tools.py` ~532-720, ~188 lines) mixes
five approval tiers plus the aux-LLM sub-flow, with two near-identical
archive/publish blocks.

**Result.** Per-tier functions (`_approve_by_security_monitor`, `_approve_by_patterns`,
`_tier2_llm`, `_aux_llm_attempt`) plus a shared `record_shell_approval` helper.

**Scope.** In: `shell_tools.py` approval logic. Out: no approval-policy change.

**Verify.** `make test` — shell-approval tests (e.g. `tests/test_shell_approval`
if present, else `tests/test_http_tools.py`/`test_admin_tools.py`).

**Tests.** Add tier-boundary cases asserting each tier's precedence is unchanged.

**Deps.** none

---

### 6.8 — Add `parse_mcp_tool_name` helper

**Size:** XS
**Triage:** ready

**Problem.** `mcp__<server>__<tool>` parsing (`name.split("__", 2)`) is duplicated
across `tool_telemetry.py`, `tool_registry.py`, and deferred-tool sorting.

**Result.** One `parse_mcp_tool_name(name) -> (server, tool) | None` helper used
everywhere.

**Scope.** In: the named parse sites. Out: no behavior change.

**Verify.** `make check` + `make test` — `tests/test_mcp_client.py`.

**Tests.** Add a case for the helper (well-formed, non-MCP name, missing tool).

**Deps.** none