# Epic 4 — Config resolution refactor

`load_config()` hand-enumerates every field instead of using the layered
resolution the project otherwise relies on.

---

### 4.1 — Build `Config(**kwargs)` from field introspection

**Size:** M
**Triage:** ready

**Problem.** `load_config` (`config.py` ~765) hand-builds `Config(...)` enumerating
all 45 fields, violating the repo's "never enumerate fields" rule; a new field must
be added in three places.

**Result.** The `Config` construction iterates `dataclasses.fields(Config)` and
builds `Config(**resolved)`.

**Scope.** In: the construction step of `load_config`. Out: do not yet collapse
the 40 `load_sub_config` calls (4.2).

**Verify.** `make check` + `make test` — `tests/test_config.py`.

**Tests.** Existing config-loading tests pass unchanged; add a case loading a
fresh config and asserting all fields materialize.

**Deps.** none

---

### 4.2 — Migrate top-level scalars to a generic env-alias path

**Size:** S
**Triage:** ready

**Problem.** `default_model`, `auxiliary_model`, `extra_skill_paths`,
`skills_always_loaded`, and the `max_tool_output_*` scalars each load via separate
hand-written code, so they lack systematic env aliases.

**Result.** One `_resolve_top_level_scalar(field, default, env_keys)` routine
loads them, each with an explicit env alias.

**Scope.** In: the named top-level scalar loading paths. Out: sub-config objects.

**Verify.** `make test` — `tests/test_config.py`.

**Tests.** Add cases asserting each scalar resolves through its env alias.

**Deps.** none

---

### 4.3 — Give `default_model` / `auxiliary_model` env overrides

**Size:** S
**Triage:** ready

**Problem.** These two model selectors currently have no env override at all,
unlike sibling scalars.

**Result.** `DECAFCLAW_DEFAULT_MODEL` and `DECAFCLAW_AUXILIARY_MODEL` (or matching
convention) override the config-file values.

**Scope.** In: the two model selector env aliases. Out: no resolution-order change.

**Verify.** `make test` — `tests/test_config.py` / `tests/test_model_catalog.py`.

**Tests.** Add a case asserting each env var overrides the file value.

**Deps.** none

---

### 4.4 — Break the `config ↔ llm ↔ model_catalog` import cycle

**Size:** M
**Triage:** ready

**Problem.** A cycle is papered over with function-level imports in `config.py`,
`llm/__init__.py`, `model_catalog.py`, and `config_watcher.py`; `config_types.py`
imports `llm.types` just for provider constants.

**Result.** Provider-type constants and model resolution move into a leaf module
both layers can import, removing the deferred imports.

**Scope.** In: module boundaries for provider constants + model resolution. Out: no
runtime behavior change.

**Verify.** `make check` (typecheck) + `make test` — `tests/test_imports.py` if
present.

**Tests.** Existing import/type tests pass; add none.

**Deps.** none

---

### 4.5 — Split `Config` runtime-mutation state

**Size:** M
**Triage:** ready

**Problem.** `Config` mixes ~45 data fields with side-effecting
`apply_env`/`apply_env_update` and catalog-materialized state; `config_watcher`
mutates it in place via `setattr`. Which fields are immutable config vs runtime
state is unclear.

**Result.** A separate `ConfigRuntime` dataclass holds the env-applied and
catalog-materialized values; `apply_env`/`apply_env_update` mutate `ConfigRuntime`,
not `Config`, and callers read runtime state from it.

**Scope.** In: introduce `ConfigRuntime`; move `apply_env*` and the
catalog-materialized tracking onto it; update `config_watcher` and consumers to
read the runtime holder. Out: do not change the resolution *order*
(defaults → file → env).

**Verify.** `make check` + `make test` — `tests/test_config_watcher.py` +
`tests/test_config.py` + `tests/test_model_catalog.py`.

**Tests.** Existing config/reload tests pass unchanged; add a case asserting a
reload mutates `ConfigRuntime` while the base `Config` data stays unchanged.

**Deps.** none