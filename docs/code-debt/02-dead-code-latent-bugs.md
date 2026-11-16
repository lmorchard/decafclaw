# Epic 2 — Dead code & latent-bug cleanup

Small, independently shippable fixes for defects and dead code.

---

### 2.1 — Repair the dead `friction` skill

**Size:** S
**Triage:** ready (decision: repair)

**Problem.** `skills/friction/tools.py` exports no `TOOLS`/`TOOL_DEFINITIONS`/
`get_tools`. `SKILL.md` declares `allowed-tools: friction_analyze`, but the
function is named `tool_friction_analyze`. `!friction` activates with an empty
allowed-tool set, so the agent is told to call a tool that does not exist. The
underlying logic (`decafclaw.friction.analyze_friction`) is complete and
`tests/test_friction.py` passes.

**Result.** `!friction` activates a skill whose `friction_analyze` tool actually
runs and returns the friction summary.

**Scope.** In: register the tool (`TOOLS = {"friction_analyze": tool_friction_analyze}`
plus a matching `TOOL_DEFINITIONS` entry), keeping the key `friction_analyze` so
it matches `allowed-tools`. Out: no change to `analyze_friction`. Keep the skill.

**Verify.** `make test` — `tests/test_friction.py` + `tests/test_skills.py` /
`tests/test_discovered_skills_consumers.py`. Also lift `tests/test_friction.py`
out of `ruff.extend-exclude` in `pyproject.toml` and fix any lint it picks up.

**Tests.** Add an assertion that activating the friction skill exposes a working
`friction_analyze` tool, and that `!friction` dispatch yields a callable tool
(not just a non-error mode).

**Deps.** none

---

### 2.2 — Add a test asserting `allowed-tools` entries are registered

**Size:** S
**Triage:** ready

**Problem.** A skill can declare `allowed-tools: X` in SKILL.md while X is never
registered (as happened in 2.1), and activation succeeds silently.

**Result.** A test that walks every discovered skill and fails if any
`allowed-tools` entry does not resolve to a registered tool name.

**Scope.** In: add the test under `tests/`. Out: no production change.

**Verify.** `make test`.

**Tests.** This is the test. Seed it with the friction case to confirm it fails
before 2.1 and passes after.

**Deps.** none (land after 2.1)

---

### 2.3 — Remove `from __future__ import annotations` from `skills/garden/tools.py`

**Size:** XS
**Triage:** ready

**Problem.** The skill loader executes modules outside `sys.modules`; postponed
annotations can crash loading and silently drop the skill. `garden/tools.py` is
the only skill carrying this import.

**Result.** The import is gone; annotations are quoted inline or left concrete.

**Scope.** In: `garden/tools.py` only. Out: no other module.

**Verify.** `make check` (typecheck) + `make test`.

**Tests.** Add a case to the existing skills-catalog test asserting garden still
loads; none otherwise.

**Deps.** none

---

### 2.4 — Add a CI check scanning skill `tools.py` for future-annotations

**Size:** S
**Triage:** ready

**Problem.** Nothing prevents a future skill from reintroducing the 2.3
failure mode.

**Result.** A gate target that scans `src/decafclaw/skills/**/tools.py` for
`from __future__ import annotations` and fails, wired into `make check`.

**Scope.** In: add a script + Make target; wire into `check`. Out: TUI/browser
assets.

**Verify.** `make check` (prove it fails on a deliberate offender in a throwaway
file, per project convention).

**Tests.** The gate itself; confirm CI invokes the target.

**Deps.** none

---

### 2.5 — Unify retry-exhaustion error type in `openai_responses`

**Size:** S
**Triage:** ready

**Problem.** `openai_responses.py` (~213) raises `RuntimeError` on retry
exhaustion, where the other providers raise `LLMRetryExhaustedError`. That drops
the agent loop's exhaustion contract (status/body/retry_after).

**Result.** The Responses provider raises `LLMRetryExhaustedError`, forwarding
`exc.status`/`exc.body`/`exc.retry_after`.

**Scope.** In: the exhaustion raise site in `openai_responses.py`. Out: no retry
logic change.

**Verify.** `make test` — `tests/test_llm_streaming.py` /
`tests/test_provider_registry.py`.

**Tests.** Add a case asserting the Responses provider raises
`LLMRetryExhaustedError` after `_MAX_RETRIES` attempts.

**Deps.** none

---

### 2.6 — Clear `_failed_model_info` negative cache on reload

**Size:** S
**Triage:** ready

**Problem.** `_failed_model_info` (`llm/__init__.py` ~228) is never cleared, so a
model that becomes resolvable after a config reload stays "unconfigured context
window" until process restart.

**Result.** `init_providers`/config reload clears the cache so a newly-resolvable
model is picked up.

**Scope.** In: clear the cache at the reload point. Out: no cache-key redesign.

**Verify.** `make test` — `tests/test_config_watcher.py` / `tests/test_model_catalog.py`.

**Tests.** Add a case: fail a lookup, reload config with a working provider, assert
the context window resolves.

**Deps.** none

---

### 2.7 — Fix `_parse_dict` stringification

**Size:** S
**Triage:** ready

**Problem.** `_parse_dict` (`config.py` ~92) coerces every env dict value to
`str`, losing numeric/bool types and forcing a downstream `ast.literal_eval` hack.

**Result.** Env dict values preserve JSON scalar types (numbers, booleans, null).

**Scope.** In: `_parse_dict` coercion. Out: no schema change.

**Verify.** `make test` — `tests/test_config.py`.

**Tests.** Add a case asserting nested env dict numbers/bools stay typed.

**Deps.** none

---

### 2.8 — Remove the `ast.literal_eval` recovery in `ImageBudgetConfig`

**Size:** XS
**Triage:** blocked-by #2.7

**Problem.** `ImageBudgetConfig.resolved` (`config_types.py` ~545) uses
`ast.literal_eval` to re-parse stringified values; unnecessary once 2.7 preserves
types, and it silently ignores unparseable values.

**Result.** The `ast.literal_eval` path is removed.

**Scope.** In: `ImageBudgetConfig.resolved`. Out: nothing else.

**Verify.** `make test` — `tests/test_image_budget.py`.

**Tests.** Existing image-budget tests still pass.

**Deps.** blocked-by #2.7

---

### 2.9 — Delete dead code (batch)

**Size:** S
**Triage:** ready

**Problem.** Several dead or misleading symbols remain: `_coerce_bool` (skills,
never called), the `DEFAULT_AUX_APPROVAL_PRESETS` self-assignment
(`shell_tools.py:362`), dead `_config` globals (`dream`/`newsletter`, set but never
read), the dead `"ready"` MCP-state branch, and the empty error listener
(`app.js:528`).

**Result.** Each dead symbol is removed; `make check` and `make test` still pass.

**Scope.** In: the named symbols only. Out: no behavior change.

**Verify.** `make check` + `make test` + `make test-js` (for app.js).

**Tests.** Existing tests still pass; add none (pure deletion).

**Deps.** none

---

### 2.10 — Declare `_isBusy` reactive prop in `chat-input.js`

**Size:** XS
**Triage:** ready

**Problem.** `chat-input.js` assigns `this._isBusy` but does not declare it in
`static properties`, so the disabled state only re-renders incidentally.

**Result.** `_isBusy` is declared `{ type: Boolean, state: true }` and initialized,
matching `conversation-sidebar.js`/`chat-controls.js`.

**Scope.** In: `chat-input.js`. Out: nothing else.

**Verify.** `make test-js` — `components/chat-input.test.js`.

**Tests.** Add/extend a case asserting the menu disabled state updates when busy
changes alone.

**Deps.** none

---

### 2.11 — Remove deprecated `#flushSave` in `wiki-editor.js`

**Size:** S
**Triage:** ready

**Problem.** `wiki-editor.js` has both a public `flushSave()` and a deprecated
`#flushSave()` that three call sites still use (unawaited), risking a teardown
race against an in-flight PUT.

**Result.** The deprecated `#flushSave` is deleted and its call sites point at the
awaited `flushSave()`.

**Scope.** In: `wiki-editor.js`. Out: no save-path behavior change.

**Verify.** `make test-js` — `components/wiki-editor*.test.js`.

**Tests.** Existing wiki-editor tests still pass; add a save-await case if none
exists.

**Deps.** none