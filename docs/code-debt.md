# Code Debt & Refactoring Backlog

A consolidated code-quality review of the DecafClaw codebase, grouped into epics
for a focused debt/cleaning sprint. Each item is concrete and independently
shippable; the plan is to file these as GitHub issues (or epic-with-checklist
issues) and work through them.

> Status: DRAFT — pending triage, filing, and prioritization on the project board.
> Nothing here is yet an issue. File from this list as agreed.

## Scope & method

Reviewed ~69k lines of Python (189 modules) and ~36k lines of frontend JS/TS,
plus the Python (`tests/`, `contrib/`) and JS test suites, by subsystem:

- Web/HTTP layer (`http_server.py`, `web/`)
- Core agent orchestration (`conversation_manager.py`, `context_composer.py`,
  `agent.py`, `compaction.py`, `reflection.py`)
- Tools layer (`tools/`)
- Skills system (`skills/`)
- LLM providers, configuration, persistence (`llm/`, `config*`, `persistence.py`,
  `mcp_client.py`, `model_catalog.py`, `embeddings.py`)
- Browser frontend (`web/static/`) and the test suites

### Baseline health (what's already good)

- 0 bare `except:` / `except: pass`; only 2 `TODO`/`FIXME`; 5 `type: ignore`; 31 `noqa`.
- Strong documentation (`docs/` is genuinely the source of truth).
- Heavy test coverage, including deterministic behavior tests and LLM evals.
- Consistent event-driven architecture with forked runtime context.

### Dominant themes (why we're here)

1. **God modules & functions** — `http_server.py` (5,293), `vault/tools.py`
   (2,353), `conversation_manager.py` (2,726), `create_app()` (~950),
   `config.load_config()` (~310), `conversation-sidebar.js` (985), `app.js` (844).
2. **Copy-pasted infrastructure** — retry/SSE/embed machinery across 3 providers,
   file-tool helpers, path containment, mtime-conflict checks, batch fan-out,
   model-resolution ladders, JS `responseError()` (4×).
3. **Stringly-typed values** where an enum would catch typos — `finish_reason`,
   `termination_reason`, `task_mode`, MCP connection state, job status,
   retrieval mode, command mode.
4. **Latent bugs hiding in dead or divergent code** — a `startswith` path bug,
   a dead `friction` skill, a provider error-type divergence, stringified env
   dicts, a never-cleared negative cache.
5. **Hand-maintained duplication that silently drifts** — ~600 lines of literal
   `TOOL_DEFINITIONS`, parallel `TOOLS`/`TOOL_DEFINITIONS` registries, test
   monoliths.

### Known overlap with in-flight work

Some findings may already be addressed by open worktrees/branches. Confirm status
before filing:

- `issue-1240-workspace-mutation-execution-shared` vs. **Epic 6** (shared mutation executor).
- `issue-1258-llm-query` vs. **Epic 5** (batch fan-out dedup).
- `dedupe-session-helpers` vs. **Epic 8** (shared `_conv_id`/session helpers).

---

## Epic 1 — Security & path-containment hardening

The one real security bug found, plus consolidation of the (currently three)
incompatible path-containment techniques and related write-path invariants.

- [ ] **Fix `startswith` path-containment bug** — `http_server.py:~1780` uses
  `str(resolved).startswith(str(workspace))`, which accepts a sibling path
  (`/data/ws-evil` under `/data/ws`). Replace with `resolve_safe`/`is_relative_to`
  like every sibling handler. _(HIGH — security)_
- [ ] **One shared path-containment/sandbox resolver** — three techniques coexist
  (`startswith`, `resolve_safe`, `is_relative_to`) plus three hand-rolled
  sandbox resolvers in `workspace_tools.py`, `admin_tools.py`, `skill_tools.py`
  with three different error wordings. Consolidate into one helper.
- [ ] **One mtime conflict-check helper** — three write paths use two tolerances
  (`1e-3` vs `1.0`) and divergent payload keys (`modified` vs `server_modified`).
- [ ] **Uniform JSON-body error handling** — unguarded `await request.json()` in
  `vault_*` and shell-promote handlers raises unhandled `JSONDecodeError` (500),
  while sibling handlers return a clean 400. Add a shared body-reader.
- [ ] **One vault page-name validator** — five copies of `if ".." in name or
  name.startswith("/")` with five different error strings, and substring `".."`
  matching wrongly rejects any name containing `..`. Use a proper traversal check.

## Epic 2 — Dead code & latent-bug cleanup

Small, independently shippable fixes for defects and dead code found across the
codebase.

- [ ] **Repair or remove the dead `friction` skill** — `skills/friction/tools.py`
  exports no `TOOLS`/`TOOL_DEFINITIONS`/`get_tools`; `SKILL.md` declares
  `allowed-tools: friction_analyze`, but the function is named
  `tool_friction_analyze`. `!friction` activates silently with zero tools. Add a
  test asserting every `allowed-tools` entry is actually registered. _(HIGH)_
- [ ] **Remove `from __future__ import annotations` from `skills/garden/tools.py`**
  — documented silent-skill-drop trap; add a CI check scanning skill `tools.py`.
  _(HIGH)_
- [ ] **Unify retry-exhaustion error type** — `openai_responses.py:~213` raises
  `RuntimeError` where the other providers raise `LLMRetryExhaustedError`,
  dropping the agent loop's exhaustion contract (status/body/retry_after lost).
- [ ] **Clear `_failed_model_info` negative cache** on config reload/`init_providers`
  (currently a model that becomes resolvable stays "unconfigured" until restart).
- [ ] **Fix `_parse_dict` stringification** — env dict values are coerced to
  `str`, forcing an `ast.literal_eval` recovery in `ImageBudgetConfig`.
- [ ] **Delete dead code**: `_coerce_bool` (skills), the
  `DEFAULT_AUX_APPROVAL_PRESETS` self-assignment (`shell_tools.py:362`), dead
  `_config` globals (`dream`/`newsletter`), dead `"ready"` MCP-state branch, empty
  JS error listener (`app.js:528`), deprecated `#flushSave` (`wiki-editor.js`).
- [ ] **Declare `_isBusy` reactive prop** in `chat-input.js` (currently undeclared;
  relies on incidental re-render).

## Epic 3 — LLM provider consolidation

The LLM layer has the most dangerous duplication in the project: retry/SSE/embed
machinery copy-pasted across three providers, owned by private names in one of
them.

- [ ] **Extract shared retry/SSE/embed infrastructure** out of `openai_compat.py`
  (`_RetryableError`, `_cancellable_sleep`, `_MAX_RETRIES` are imported by
  `vertex.py` and `openai_responses.py` via private names). Move to
  `llm/retry.py` or `llm/types.py`.
- [ ] **Deduplicate streaming retry/SSE/state machinery** across
  `openai_compat`, `vertex`, `openai_responses` (near-identical attempts,
  `_StreamState` classes, `finalize()` diagnostics).
- [ ] **Deduplicate `embed()`** — triplicated across `vertex._complete`, `openai`,
  and a legacy path in `embeddings.py`; consolidate into one transport.
- [ ] **Use finish-reason constants** — `FINISH_REASON_*` are declared but only the
  scripted test provider uses them; providers write raw strings.
- [ ] **Deduplicate `vertex.py` finish-reason/tool-call construction** — duplicated
  across streaming vs non-streaming paths (4× `thought_signature` chains).
- [ ] **Normalize `get_model_info`** — `openai_compat` returns `None` unconditionally
  (dead), while generic `ensure_model_context_window` reads provider-specific raw
  wire keys.
- [ ] **Unify `_parse_response` vs streaming empty-response classification** ("error"
  vs `None` for no candidates).

## Epic 4 — Config resolution refactor

`load_config()` mechanically enumerates every field instead of using the layered
resolution the project otherwise relies on.

- [ ] **Drive `load_config()` from dataclass field introspection** — replace ~40
  sequential `load_sub_config` calls + the hand-built `Config(...)` literal with a
  generic `dc_fields(Config)` loop and `Config(**kwargs)`.
- [ ] **Give top-level scalars consistent env aliases** — `default_model` /
  `auxiliary_model` currently have no env override; load via the same generic path.
- [ ] **Break the `config ↔ llm ↔ model_catalog` import cycle** — papered over with
  function-level imports; move provider-type constants/model resolution into a leaf
  module both can import.
- [ ] **Split `Config` god dataclass** — ~45 fields mixed with side-effecting
  `apply_env`/`apply_env_update` and 10 derived path properties; formalize the
  mutable-vs-immutable split (runtime env application, catalog-materialized state).

## Epic 5 — Tool failure contract & batch execution

Failure is currently encoded only as a `"[error:"` string prefix, re-parsed in
several places; two tools duplicate a batch-concurrency engine.

- [ ] **Add a structured `ToolResult` failure field** (`ok`/`status`) and populate
  it in `execute_tool`; derive `infer_outcome` and `ERROR_TEXT_PREFIX` checks from it.
  _(HIGH)_
- [ ] **Stop returning bare error strings** — `mcp_client._make_tool_caller`,
  `tool_output_tools._page_text`, and `core.py` return bare `"[error: ...]"` strings
  instead of `ToolResult`.
- [ ] **One batch-concurrency helper** — `delegate.py` and `llm_tools.py` duplicate
  the Semaphore/gather/summary engine; extract and share.

## Epic 6 — Tools registry & dispatch refactor

The tools layer is well-factored per-module but the registry/dispatch core and a
couple of modules have outgrown themselves.

- [ ] **Refactor `tools/__init__.py`** — move dispatch into `dispatch.py`; derive
  `TOOLS`/`TOOL_DEFINITIONS` from one source instead of two parallel hand-maintained
  lists; break the `__init__ ↔ skill_tools ↔ tool_registry ↔ search_tools` cycle.
- [ ] **Consolidate file-tool helpers** — `_is_cancelled`, `_file_error`, `_mini_diff`
  and shared constants duplicated across `workspace_tools`/`admin_tools`/`agent`
  (admin_tools is already the de-facto shared home for some).
- [ ] **One shared admin-mutation executor** — the ~40-line write ritual is inlined
  four times plus a second copy in `_apply_mutation` (recovery path); collapse to one
  executor used by both interactive tools and recovery.
- [ ] **Split `check_shell_approval` (188 lines)** — per-tier functions; dedupe the
  two near-identical archive/publish blocks.
- [ ] **Consolidate sandbox resolvers** (also listed in Epic 1; keeps the admin/workspace/skill
  wording consistent).
- [ ] **One `parse_mcp_tool_name` helper** — `mcp__<server>__<tool>` parsing duplicated
  across `tool_telemetry`, `tool_registry`, and deferred-tool sorting.

## Epic 7 — ConversationManager decomposition

The most complex code in the project; several methods are near their complexity
ceiling (which the 50-line invariant-comment blocks attest to).

- [ ] **Extract `_start_turn`'s nested `run()` closure** into a
  module-level turn-lifecycle object with explicit fields; split
  `_start_turn` into context-construction vs turn-run phases. _(HIGH)_
- [ ] **Unify the 4× confirmation lifecycle** (`respond_to_confirmation`,
  `cancel_pending_confirmation`, `request_confirmation`, `post_confirmation`) via a
  shared slot-resolve helper owning the under-lock archive/claim ordering.
- [ ] **Replace stringly-typed `asyncio.Future` attributes** (`rate_limited`,
  `queued`) with a typed result object.
- [ ] **Remove the append-then-pop inbox round-trip** in `enqueue_turn` (O(n) disk
  read+write per non-busy turn, later undone).
- [ ] **Dedupe side-question message flattening** — reuses existing
  `ROLE_REMAP`/`flatten_messages` instead of a private copy.

## Epic 8 — Context classification & model-resolution consolidation

The mode/skip classification is the highest-risk duplication in the project — it
directly controls whether a turn is "unattended" and what context is injected.

- [ ] **Centralize mode/skip classification** — the `{HEARTBEAT, SCHEDULED,
  CHILD_AGENT}` frozenset is re-declared 8× across 4 modules with subtly different
  membership (`_UNWATCHED_TASK_MODES` vs `UNATTENDED_TASK_MODES` vs the composer's
  6 inline copies). Derive all from one canonical source. _(HIGH)_
- [ ] **Centralize model-resolution ladders** — reflection judge ladder ↔
  `agent._resolve_model_override` ↔ `context.aux_llm` ↔ two divergent context-window
  resolvers, into `model_catalog`.
- [ ] **One `_conv_id` helper** — 6 copies with differing fallback sentinels
  (`"unknown"` vs `"default"`).
- [ ] **Fix token-accounting drift in `_compose_impl`** — mentions are injected but
  not added to `fixed_tokens` (latent budget bug); extract `_compute_fixed_tokens`
  as a single phase.

## Epic 9 — Skills system cleanup

Skills are the most duplicated structural layer: every skill re-implements config
glue, lookups, placeholder substitution, and widget payloads.

- [ ] **Split `vault/tools.py` (2,353)** into `_paths`, `_gate`, `_search`,
  `_journal`, `_sections_ops`, leaving thin wrappers + registry.
- [ ] **De-duplicate the ~600-line hand-written `TOOL_DEFINITIONS`** — build schemas
  from typed signatures or a shared schema builder, removing the name-spelled-twice
  drift risk.
- [ ] **Centralize skill boilerplate** — `SkillConfig`/`init`/global-state template,
  `index_skills` lookup, `$SKILL_DIR` placeholder substitution (3 copies), and the
  `progress_tracker` payload builder (7 hand-built copies).
- [ ] **Promote private cross-module API** — `vault/tools.py` reaches into
  `_sections`' `_delete_lines`/`_insert_into_doc`; make public methods.
- [ ] **Have `activate_skill_internal` return an explicit result** instead of
  `activate_skills_for_workflow` introspecting `ctx.skills.activated` (with
  line-number-cited comments that rot).
- [ ] **Move the newsletter HTML/CSS template out of a Python f-string literal** into
  a sibling template file.

## Epic 10 — Stringly-typed → enums sweep

A mechanical type-safety sweep. `project/state.py` already demonstrates the
pattern (`ProjectState(str, Enum)`); bring the rest up to it.

- [ ] `TerminationReason` / `finish_reason` (drives the cancelled > failed >
  completed precedence).
- [ ] `TurnKind`/`task_mode` — replace raw `task_mode: str` where it originates from
  a known kind.
- [ ] `RetrievalMode` (`always` / `headlines` / `on_demand`).
- [ ] Background `JobStatus`, `ProgressParserPreset`, `StepStatus`, `VaultSourceType`,
  MCP `ConnState` (delete the dead `"ready"` branch).
- [ ] JS conversation-status + command-string constants (`STATUS_LABELS`,
  `SIDE_EFFECT_COMMANDS`, `--child-` separator) in a shared module.
- [ ] `commands.py` — `execute_command` returns a raw `tuple[str, str]` while
  `dispatch_command` returns a typed `CommandResult`; unify.

## Epic 11 — HTTP/web layer split

`http_server.py` is the single biggest onboarding obstacle in the codebase.

- [ ] **Split `http_server.py` (5,293)** — extract `create_app()`'s ~90-route table
  into per-resource modules (workspace, vault, config, models, schedules, widgets,
  canvas, shell-approval, conversations) using `@router` and dropping the
  `openapi_extra` boilerplate where decorators generate it. _(HIGH — epic, N PRs)_
- [ ] **Split the WebSocket event dispatcher** — `on_conv_event` (~350-line
  `if/elif` chain) into a per-event-type projector registry; hoist the
  `streaming_buffer` flush into one helper; dedupe `_confirmation_to_dict`.
- [ ] **Deduplicate auth preamble** — two mechanisms coexist (a `@_authenticated`
  decorator vs inline `username-or-401` checks in ~10 handlers); pick one. Rename
  `_get_username_or_401` (it never returns 401).
- [ ] **Make `response_model=` actually validate** — routes return raw
  `JSONResponse(dict)`, so declared models never run and OpenAPI drifts; return the
  Pydantic model instead.
- [ ] **Deduplicate the `models_catalog_set_*` and shell-approval handlers** — 4–6
  near-identical clones each.
- [ ] **Hoist magic numbers** (autocomplete caps, title-preview length, mtime
  tolerance) to named constants.

## Epic 12 — Frontend consolidation

Frontend duplication is lighter-weight but already drifting in at least one spot.

- [ ] **Split `conversation-sidebar.js` (985)** — extract `conversation-list` and
  `conversation-controls`; stop mirroring every store field into local reactive state.
- [ ] **Split `app.js` (844)** — `lib/panel-url.js` for the duplicated URL-state
  dance; reuse `setupResizeHandle` for wiki/canvas; drop the dead error listener.
- [ ] **Collapse `wiki-editor.js` endpoint triplication** — a `SAVE_ENDPOINTS` table
  driving one `#save`/`#forceSave`/`#reload`; delete the deprecated `#flushSave`.
- [ ] **Dedupe `responseError()`** (4 copies, already drifted in `file-page.js`) and
  the model/mode `<select>` rendering (3×) into `lib/`.

## Epic 13 — Frontend robustness & generated-code policy

- [ ] **Standardize fetch/WS error handling** — silent `ApiError` swallowing and
  dropped WS sends when disconnected; add an outbound queue/toast.
- [ ] **Replace native `prompt()/confirm()/alert()`** with the project's
  confirm/toast primitives.
- [ ] **Annotate/gate the generated `api-client/index.js` boundary** — confirm the
  generated-vs-authored split and gate drift in `check-js` (like message-types).

## Epic 14 — Test suite hygiene

- [ ] **Split Python test monoliths** — `test_skills.py` (3,099), `test_context_composer.py`
  (3,182), `test_conversation_manager.py` (3,594) by subsystem seam.
- [ ] **Prefer behavior-level JS assertions** over exact `sent[]` wire-frame dumps;
  factor a shared `FakeWS`/`makeStore` helper.
- [ ] **Replace name-string fixture gating** (`"friction" in request.node.name`) and
  OTel private-internals reset in `conftest.py` with explicit markers.

---

## Suggested sequencing

1. **Epics 1–2** (bugs & dead code) — small, independently reviewable, real defects.
   Epic 1's path bug is a security patch.
2. **Epics 3–6, 8** (consolidation) — highest drift-removal per effort; also unblocks
   the larger splits.
3. **Epic 10** (enums) — low-risk parallel fill work.
4. **Epics 7, 9, 11, 12** (structural splits) — behind the consolidation, benefits
   from shared helpers landing first.
5. **Epics 13–14** — wherever bandwidth allows.

## Tips for filing

- Epic 1, 2, and 10 are natural "one issue per checklist item."
- Epics 7, 9, 11 are natural epic issues with sub-tasks (each god-module split is
  multiple PRs).
- Cross-link items that share a helper (e.g. Epic 1 "sandbox resolver" and Epic 6's
  item) so they land together rather than racing.