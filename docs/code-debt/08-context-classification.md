# Epic 8 — Context classification & model-resolution consolidation

The mode/skip classification controls "unattended" authorization and what context
is injected — the highest-risk duplication in the project.

---

### 8.1 — Centralize mode/skip classification

**Size:** M
**Triage:** ready

**Problem.** The `{HEARTBEAT, SCHEDULED, CHILD_AGENT}` skip frozenset is re-declared
8× across 4 modules with subtly different membership (`_UNWATCHED_TASK_MODES` vs
`UNATTENDED_TASK_MODES` vs 6 inline composer copies). This has caused real
misclassification bugs before.

**Result.** One canonical source (e.g. `ComposerMode.is_skip(mode)` or a
`SKIP_MODES` frozenset) plus a single `task_mode_for(kind)` mapping on `TurnKind`,
with a comment distinguishing the deliberate `child_agent`/`background_wake`
asymmetry.

**Scope.** In: the canonical source only. Out: do not yet migrate callers (8.2).

**Verify.** `make check` + `make test` — `tests/test_modes.py`,
`tests/test_context_composer.py`.

**Tests.** Add a case asserting the canonical set matches current behavior.

**Deps.** none

---

### 8.2 — Migrate all call sites to the canonical source

**Size:** S
**Triage:** blocked-by #8.1

**Problem.** `context_composer` (6×), `agent._TASK_MODE_TO_COMPOSER`,
`context_cleanup._PRUNE_SKIP_MODES`, and `context.UNATTENDED_TASK_MODES` still use
private copies.

**Result.** All derive from the canonical source from 8.1.

**Verify.** `make test` — `tests/test_context_composer.py`,
`tests/test_context_cleanup.py`, `tests/test_agent_turn.py`.

**Tests.** Existing mode-classification tests pass unchanged.

**Deps.** blocked-by #8.1

---

### 8.3 — Centralize model-resolution ladders

**Size:** M
**Triage:** ready

**Problem.** Model resolution is scattered: the reflection judge ladder
(`reflection.py` ~299), `agent._resolve_model_override` (~624), `context.aux_llm`
(~251), and two divergent context-window resolvers in `context_composer.py`.

**Result.** One resolver per pointer in `model_catalog.py`
(resolve judge/verifier/aux/context-window), consumed by all four.

**Scope.** In: the resolver definitions. Out: do not yet migrate callers (8.4).

**Verify.** `make test` — `tests/test_model_catalog.py`, `tests/test_reflection.py`.

**Tests.** Add cases asserting each ladder's fallback chain.

**Deps.** none

---

### 8.4 — Migrate reflection/agent/context to the centralized resolvers

**Size:** M
**Triage:** blocked-by #8.3

**Result.** `reflection`, `agent`, and `context` call the centralized resolvers,
deleting their local ladders.

**Verify.** `make test` — `tests/test_reflection.py`, `tests/test_agent_turn.py`,
`tests/test_context.py`.

**Tests.** Existing model-selection behavior tests pass unchanged.

**Deps.** blocked-by #8.3

---

### 8.5 — Add a single `_conv_id` helper

**Size:** S
**Triage:** ready

**Problem.** `ctx.conv_id or ctx.channel_id or "unknown"` is duplicated 6× with
differing fallback sentinels (`"unknown"` vs `"default"`).

**Result.** A `Context.key()` (or module function) returns the canonical
conversation key; all six call sites use it.

**Scope.** In: `context.py` + the six call sites (`agent.py`, `tool_execution.py`,
`compaction.py`, `context_composer.py`, `tools/notes_tools.py`).

**Verify.** `make test` — `tests/test_context.py` + affected tool tests.

**Tests.** Add a case asserting the canonical key resolves consistently; update
call-site assertions to the single sentinel.

**Deps.** none

---

### 8.6 — Fix mentions missing from `fixed_tokens`

**Size:** S
**Triage:** ready

**Problem.** Mentions are injected before the user message but not charged in
`fixed_tokens` (`context_composer._compose_impl` ~667-673), a latent budget bug.

**Result.** `_compute_fixed_tokens` is extracted as one phase and accounts for
mentions.

**Scope.** In: token-accounting in `_compose_impl`. Out: no composition-order change.

**Verify.** `make test` — `tests/test_context_composer.py` /
`tests/test_mentions_injection.py`.

**Tests.** Add a case asserting mention entries contribute to the fixed-token
total.

**Deps.** none