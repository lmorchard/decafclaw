# Epic 10 — Stringly-typed → enums sweep

A mechanical type-safety sweep. `project/state.py` already demonstrates
`ProjectState(str, Enum)`; bring the rest up to it. Each item is independent.

---

### 10.1 — Add `TerminationReason` enum

**Size:** S
**Triage:** ready

**Problem.** `termination_reason` is compared/set as raw strings across
`agent.py` (~L2099) and `conversation_manager.py` (~L2095-2216), with an implicit
"cancelled > failed > completed" precedence that a typo silently breaks.

**Result.** A `TerminationReason` enum replaces the literals; the precedence is
expressed once.

**Verify.** `make test` — `tests/test_agent_turn.py`,
`tests/test_conversation_manager.py`.

**Tests.** Existing termination tests pass; add a precedence-ordering case.

**Deps.** none

---

### 10.2 — Replace raw `task_mode: str` with `TurnKind`/enum

**Size:** M
**Triage:** ready

**Problem.** `task_mode` is a bare string threaded from `TurnKind` through the
transport boundary, free-form at the edge.

**Result.** Task mode is an enum inside; free string only at the transport boundary.

**Verify.** `make test` — `tests/test_modes.py`, `tests/test_agent_turn.py`.

**Tests.** Existing turn-kind tests pass; update boundary-parse tests.

**Deps.** none

---

### 10.3 — Add `RetrievalMode` enum

**Size:** S
**Triage:** ready

**Problem.** `always`/`headlines`/`on_demand` are compared as raw strings in
`context_composer.py` (~L995/L1161).

**Result.** A `RetrievalMode` enum replaces the literals.

**Verify.** `make test` — `tests/test_context_composer.py`.

**Tests.** Existing retrieval-mode tests pass.

**Deps.** none

---

### 10.4 — Add `JobStatus` enum

**Size:** S
**Triage:** ready

**Problem.** Background `status` is a `str` (`running`/`completed`/`error`/`expired`/
`stopped`) enumerated only in a comment (`background/tools.py` ~L45).

**Result.** A `JobStatus` enum; the tool schema's `enum` array sources from it.

**Verify.** `make test` — `tests/test_background_tools.py`.

**Tests.** Existing background-job tests pass.

**Deps.** none

---

### 10.5 — Add `ProgressParserPreset` enum

**Size:** S
**Triage:** ready

**Problem.** `progress_parser: str = "auto"` is compared as raw strings.

**Result.** A `ProgressParserPreset` enum.

**Verify.** `make test` — `tests/test_background_tools.py`.

**Tests.** Existing parser-selection tests pass.

**Deps.** none

---

### 10.6 — Add `StepStatus` enum

**Size:** S
**Triage:** ready

**Problem.** Project step status is `("pending","in_progress","done","skipped")`
compared as raw strings (`project/tools.py` ~L558).

**Result.** A `StepStatus` enum.

**Verify.** `make test` — project skill tests.

**Tests.** Existing project-step tests pass.

**Deps.** none

---

### 10.7 — Add `VaultSourceType` enum

**Size:** S
**Triage:** ready

**Problem.** `source_type` uses raw `"journal"/"page"/"user"` strings
(`vault/tools.py` ~L373).

**Result.** A `VaultSourceType` enum.

**Verify.** `make test` — `tests/test_vault_tools.py`.

**Tests.** Existing source-type tests pass.

**Deps.** none

---

### 10.8 — Add MCP `ConnState` enum

**Size:** S
**Triage:** ready

**Problem.** `MCPServerState.status` is a `str` mutated across 20+ sites; the
`"ready"` value is a dead branch (never assigned).

**Result.** A `ConnState` enum; delete the dead `"ready"` branch.

**Verify.** `make test` — `tests/test_mcp_client.py`.

**Tests.** Existing connection-state tests pass.

**Deps.** none

---

### 10.9 — Centralize JS status/command constants

**Size:** S
**Triage:** ready

**Problem.** Conversation-status strings and side-effect commands
(`/terminal`, `/retitle`, `!retitle`) plus the `--child-` separator are hardcoded
across `conversation-store.js`, `app.js`, `chat-controls.js`.

**Result.** A `lib/constants.js` (or message-types extension) exports
`STATUS_LABELS`, `SIDE_EFFECT_COMMANDS`, and the child-id separator.

**Verify.** `make test-js` — conversation-store/message-store tests.

**Tests.** Existing tests pass; add a case asserting the constants are the single
source.

**Deps.** none

---

### 10.10 — Unify `execute_command`/`dispatch_command` return type

**Size:** S
**Triage:** ready

**Problem.** `execute_command` returns a raw `tuple[str, str]` while
`dispatch_command` returns a typed `CommandResult`, with overlapping mode vocabularies.

**Result.** Both use `CommandResult` (or a `Literal`/enum mode).

**Verify.** `make test` — `tests/test_commands.py`.

**Tests.** Existing command dispatch tests pass.

**Deps.** none