# Epic 14 — Test suite hygiene

---

### 14.1 — Split `tests/test_skills.py`

**Size:** M
**Triage:** ready

**Problem.** `tests/test_skills.py` (~3,099 lines) is a monolith, making `-n auto`
scheduling coarse and blame locality poor.

**Result.** Split by subsystem seam (activation, discovery/trust, config, shell
tools) into named files.

**Verify.** `make test`.

**Tests.** Pure reorganization; all existing tests pass unchanged.

**Deps.** none

---

### 14.2 — Split `tests/test_context_composer.py`

**Size:** M
**Triage:** ready

**Problem.** `tests/test_context_composer.py` (~3,182 lines) is a monolith.

**Result.** Split by concern (modes, retrieval, token budget, assembly).

**Verify.** `make test`.

**Tests.** Reorganization; pass unchanged.

**Deps.** none

---

### 14.3 — Split `tests/test_conversation_manager.py`

**Size:** M
**Triage:** ready

**Problem.** `tests/test_conversation_manager.py` (~3,594 lines) is a monolith.

**Result.** Split by concern (turn lifecycle, confirmation, enqueue, side-question).

**Verify.** `make test`.

**Tests.** Reorganization; pass unchanged.

**Deps.** none

---

### 14.4 — Prefer behavior-level JS assertions + shared `FakeWS`

**Size:** M
**Triage:** ready

**Problem.** JS tests (e.g. `conversation-store.test.js`, 1,121 lines) assert exact
`sent[]` wire-frame dumps and re-derive wire types, coupling to message ordering;
`FakeWS`/`makeStore` are re-declared per file.

**Result.** A shared `FakeWS`/`makeStore` helper; assertions move to observable
store state where practical.

**Verify.** `make test-js`.

**Tests.** Introduce the helper; migrate one representative store test file to
behavior-level assertions.

**Deps.** none

---

### 14.5 — Replace name-string fixture gating

**Size:** S
**Triage:** ready

**Problem.** `conftest.py` gates `mock_friction_llm` on `"friction" in request.node.name`,
so renaming a test silently disables its mock.

**Result.** An explicit marker/fixture replaces the name-string gate.

**Verify.** `make test` — friction tests.

**Tests.** Affected friction tests still pass; add a case proving the mock applies
without relying on the name.

**Deps.** none

---

### 14.6 — Remove OTel private-internals reset

**Size:** S
**Triage:** ready

**Problem.** `reset_otlp_singletons` reaches into
`trace._TRACER_PROVIDER_SET_ONCE._done`, which breaks on library upgrades.

**Result.** Use the public reset API (or a documented suppression) instead.

**Verify.** `make test` — telemetry/metrics tests.

**Tests.** Existing telemetry tests pass.

**Deps.** none