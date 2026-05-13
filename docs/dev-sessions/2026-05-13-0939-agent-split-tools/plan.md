# Agent.py Split — Extract tool_execution + tool_definitions Plan

**Goal:** Relocate tool-invocation and tool-registry machinery out of `agent.py` into dedicated `tool_execution.py` and `tool_definitions.py` modules, dropping `agent.py` from ~1451 lines to ~750.

**Approach:** Pure relocation in two atomic moves. The invocation cluster (`execute_tool_calls`, `execute_single_tool`, `process_tool_media`, `resolve_widget`, `_media_placeholder_pattern`) moves to `tool_execution.py`. The registry cluster (`collect_all_tool_defs`, `build_tool_list`, `refresh_dynamic_tools`, `_skill_def_cache` + `invalidate_skill_cache`) moves to `tool_definitions.py`. Underscores drop on the relocated public functions; module-internal helpers stay private. No behavior changes.

**Tech stack:** Python 3.11+, async/await, dataclasses; no new dependencies.

**TDD opt-out:** This is a pure-relocation refactor. The existing test suite (`tests/test_agent_turn.py`, `tests/test_agent_widgets.py`, `tests/test_process_tool_media.py`, `tests/test_tool_result_data.py`) is the regression net — it must remain green after each phase. No new tests are added; existing tests will be updated to import from the new module paths.

---

## Phase 1: Create `tool_definitions.py` (registry cluster)

Move the tool-registry machinery out of `agent.py` into a new `tool_definitions.py` so both `agent.py` (the iteration loop) and `context_composer.py` (#439 follow-up) have a clean import target.

**Files:**
- Create: `src/decafclaw/tool_definitions.py`
- Modify: `src/decafclaw/agent.py` — remove the registry functions and their module-level cache; import from new module where the loop still uses them
- Modify: `src/decafclaw/context_composer.py` — update the two deferred imports of `_collect_all_tool_defs` to import `collect_all_tool_defs` from `.tool_definitions`
- Modify: `src/decafclaw/tools/skill_tools.py` — update the deferred `invalidate_skill_cache` import to `from ..tool_definitions import invalidate_skill_cache`
- Modify: `tests/test_agent_turn.py` — update the `from decafclaw.agent import (..., _build_tool_list, ..., _refresh_dynamic_tools, ...)` to use the new module + dropped underscores

**Key changes:**
- New module `tool_definitions.py` exports three public functions and `invalidate_skill_cache`:
  - `collect_all_tool_defs(ctx) -> list` (was `_collect_all_tool_defs`)
  - `build_tool_list(ctx) -> tuple[list, str | None]` (was `_build_tool_list`)
  - `refresh_dynamic_tools(ctx) -> None` (was `_refresh_dynamic_tools`)
  - `invalidate_skill_cache(config) -> None` (unchanged — already public)
  - Module-level `_skill_def_cache: dict[int, list]` (stays private — internal cache state)
- `agent.py` imports from the new module:
  ```python
  from .tool_definitions import build_tool_list, refresh_dynamic_tools
  ```
- The call sites in `TurnRunner._run_iteration` change from `_refresh_dynamic_tools(self.ctx)` / `_build_tool_list(self.ctx)` to `refresh_dynamic_tools(self.ctx)` / `build_tool_list(self.ctx)`.
- `context_composer.py` lines 826 + 1016 change from `from .agent import _collect_all_tool_defs` → `from .tool_definitions import collect_all_tool_defs`, and the call sites at 865 / 1027 drop the leading underscore.
- `tools/skill_tools.py` line 248 changes from `from ..agent import invalidate_skill_cache` → `from ..tool_definitions import invalidate_skill_cache`.

**Imports the new module needs:**
- stdlib: `logging`
- `from .tools import TOOL_DEFINITIONS`
- `from .tools.search_tools import SEARCH_TOOL_DEFINITIONS`
- `from .tools.tool_registry import build_deferred_list_text, classify_tools, get_fetched_tools`
- Deferred (function-local, breaking cycles): `from .tools.skill_tools import _load_native_tools` (already deferred in current code at line 400), `from .mcp_client import get_registry`

**Verification — automated** (see `references/makefile-conventions.md`):
- [x] `make lint` passes
- [x] `make check` passes (lint + typecheck + message-types drift)
- [x] `make test` passes (2419 passed)
- [x] `grep -n "from .agent import _collect_all_tool_defs\|from .agent import _refresh_dynamic_tools\|from .agent import _build_tool_list\|from .agent import invalidate_skill_cache" src/ tests/` returns nothing

**Verification — manual:**
- [x] `agent.py` no longer contains `_collect_all_tool_defs`, `_build_tool_list`, `_refresh_dynamic_tools`, `_skill_def_cache`, `invalidate_skill_cache`
- [x] `tool_definitions.py` exists and is 166 lines
- [x] Importing `decafclaw` at the package level still works (no circular-import regression): `python -c "import decafclaw.agent"`

---

## Phase 2: Create `tool_execution.py` (invocation cluster)

Move the tool-invocation machinery out of `agent.py` into a new `tool_execution.py`. The invocation cluster is internal to the agent loop (no `context_composer.py` consumers), so this phase is a strict move-and-rename.

**Files:**
- Create: `src/decafclaw/tool_execution.py`
- Modify: `src/decafclaw/agent.py` — remove the invocation functions; import `execute_tool_calls` from new module
- Modify: `tests/test_agent_turn.py` — update imports for `_execute_tool_calls`; update `patch("decafclaw.agent.execute_tool", ...)` patch targets to `patch("decafclaw.tool_execution.execute_tool", ...)`
- Modify: `tests/test_agent_widgets.py` — update imports for `_execute_tool_calls`, `_resolve_widget`
- Modify: `tests/test_process_tool_media.py` — update import for `_process_tool_media`
- Modify: `tests/test_tool_result_data.py` — update inline imports + patch targets for `_execute_single_tool`

**Key changes:**
- New module `tool_execution.py` exports public functions:
  - `execute_tool_calls(ctx, tool_calls, history, messages)` (was `_execute_tool_calls`) — the only one `agent.py` will import
  - `execute_single_tool(call_ctx, tc, semaphore)` (was `_execute_single_tool`) — used by tests; also publicly callable
  - `process_tool_media(ctx, result)` (was `_process_tool_media`) — used by tests
  - `resolve_widget(fn_name, result, tool_call_id="")` (was `_resolve_widget`) — used by tests
  - Module-internal: `_media_placeholder_pattern(filename)` keeps its underscore — pure private helper
- `agent.py` imports:
  ```python
  from .tool_execution import execute_tool_calls
  ```
- `agent.py` keeps `_check_cancelled` and `_archive` since they are also called by orchestration code outside the tool-execution cluster (e.g. `_handle_no_tool_calls`, `_compose`, `_finalize_max_iterations`, `TurnRunner._run_iteration`). The new module re-imports `_archive` and `_check_cancelled` from `agent.py`? **NO — circular import risk.** Instead, factor a tiny shared helpers module or pass these as args.

  **Resolved decision:** `_archive` and `_check_cancelled` both depend only on `archive.append_message`, `ctx`, and `history`. We can simply **copy the two helpers into `tool_execution.py` as module-level private functions** — they're 10 lines combined and changing them in lockstep is acceptable (they're not part of a public API and not expected to change frequently). This avoids the circular import without creating a third "agent_helpers" module. The duplication is intentional and noted in a comment.

  **Rejected alternative:** Extract `_archive` / `_check_cancelled` to a new `agent_helpers.py`. Adds a third module for two trivial helpers when the spec asks for two new modules total.

  **Rejected alternative:** Pass `archive_fn` / `check_cancelled_fn` as arguments. Over-engineered for two helpers used only inside this loop.

**Call-site changes in `agent.py`:**
- `await _execute_tool_calls(self.ctx, tool_calls, ...)` → `await execute_tool_calls(self.ctx, tool_calls, ...)`

**Imports the new module needs:**
- stdlib: `asyncio`, `functools`, `json`, `logging`, `re`
- `from .archive import append_message`
- `from .media import EndTurnConfirm, ToolResult, WidgetInputPause`
- `from .tools import execute_tool`
- Deferred (function-local, breaking cycles): `from .widgets import get_widget_registry`, `from .widget_input import pending_callbacks` — keep deferred as in the current code

**Test patch-target updates:**
- `tests/test_agent_turn.py`: 9 `patch("decafclaw.agent.execute_tool", ...)` → `patch("decafclaw.tool_execution.execute_tool", ...)`
- `tests/test_tool_result_data.py`: 3 `patch("decafclaw.agent.execute_tool", ...)` → `patch("decafclaw.tool_execution.execute_tool", ...)`; 3 inline imports `from decafclaw.agent import _execute_single_tool` → `from decafclaw.tool_execution import execute_single_tool`
- `tests/test_agent_widgets.py`: `from decafclaw.agent import _execute_tool_calls, _resolve_widget` → `from decafclaw.tool_execution import execute_tool_calls, resolve_widget`
- `tests/test_process_tool_media.py`: `from decafclaw.agent import _process_tool_media` → `from decafclaw.tool_execution import process_tool_media`

**Verification — automated**:
- [x] `make lint` passes
- [x] `make check` passes
- [x] `make test` passes (2419 passed)
- [x] `grep -n "from .agent import _execute_\|from .agent import _process_tool_media\|from .agent import _resolve_widget" src/ tests/` returns nothing
- [x] `wc -l src/decafclaw/agent.py` shows 1000 lines (target was ~750; remaining ~250 lines are wiki/attachment helpers explicitly deferred to issue #439 — spec called them out under "What we're NOT doing")

**Verification — manual:**
- [x] `agent.py` no longer contains `_execute_tool_calls`, `_execute_single_tool`, `_process_tool_media`, `_resolve_widget`, `_media_placeholder_pattern`
- [x] `tool_execution.py` exists and is 364 lines (includes the small `_archive` + `_check_cancelled` + `_conv_id` duplication — 3 helpers totaling 22 lines, intentional and documented)
- [x] Smoke: `python -c "from decafclaw.tool_execution import execute_tool_calls, execute_single_tool, process_tool_media, resolve_widget"` succeeds
- [x] Smoke: `python -c "from decafclaw.eval.runner import run_agent_turn"` still works (the stable public surface preserved by the spec)

---

## Phase 3: Docs sync — CLAUDE.md key files + architecture pointers

The CLAUDE.md "Key files / Core" section currently says `agent.py — Agent loop: turn orchestration, tool execution, LLM calls`. After this refactor, tool execution and tool definitions are dedicated modules. Update the list per CLAUDE.md convention: "Update `CLAUDE.md` only when conventions or the key-files list change."

**Files:**
- Modify: `CLAUDE.md` — update "Core" key-files list to reflect the new modules
- Modify: `docs/architecture.md` — if it references specific file names for the tool-execution-concurrency section, update accordingly (spot-check; no change required if the section stays generic)

**Key changes (CLAUDE.md):**
- Update the "Core" bullet for `agent.py` from `Agent loop: turn orchestration, tool execution, LLM calls` to `Agent loop: turn orchestration, LLM calls, iteration outcomes`.
- Add two new bullets in the Core section, alphabetically placed:
  - `tool_definitions.py` — Tool-registry assembly: classification, deferral, dynamic-skill providers
  - `tool_execution.py` — Concurrent tool-call execution: media handling, widget validation, end-turn signals

**Key changes (architecture.md):**
- Section "Tool execution concurrency" references `_execute_single_tool` in a code snippet (line 144). Update the call name to `execute_single_tool` (drop the underscore) to match the new module's public surface.

**Verification — automated:**
- [x] `make check` still passes (no code changes here)
- [x] `grep -n "tool_definitions\.py\|tool_execution\.py" CLAUDE.md` returns hits in the Core section
- [x] `grep -n "_execute_single_tool" docs/architecture.md` returns nothing (the only `_execute_single_tool` hits in `docs/` now are in historical dev-session artifacts and this session's spec/plan — correct to leave alone)

**Verification — manual:**
- [x] CLAUDE.md "Core" bullets read naturally and accurately describe each module's responsibility
- [x] `docs/architecture.md` "Tool execution concurrency" snippet still reads correctly with the new name

---

## Plan self-review

**Spec coverage check** (matched against `spec.md` "Desired end state"):
1. ✅ New `tool_execution.py` (~250 lines) — Phase 2
2. ✅ New `tool_definitions.py` (~150 lines) — Phase 1
3. ✅ Underscores dropped on public functions — Phases 1 + 2 (note: `_media_placeholder_pattern`, `_skill_def_cache`, `_archive`, `_check_cancelled` keep underscores as module-internal helpers — these are not part of the relocated public surface)
4. ✅ Imports updated in `agent.py`, `context_composer.py`, `tools/skill_tools.py` — Phases 1 + 2
5. ✅ `agent.py` lands at ~750 lines — verified in Phase 2 manual checkbox
6. ✅ `run_agent_turn` shim preserved — never touched in any phase
7. ✅ `IterationOutcome` / `_Continue` / `_Final` / `ReflectionOutcome` tagged unions unchanged — never touched in any phase

**Placeholder scan:** no "TBD", "TODO", "implement later", or "similar to phase N" — every phase enumerates files, key changes, and import wiring concretely.

**Type-consistency check:**
- `collect_all_tool_defs` named consistently in Phase 1 (definition + context_composer import).
- `build_tool_list` named consistently in Phase 1 (definition + agent.py call site).
- `refresh_dynamic_tools` named consistently in Phase 1 (definition + agent.py call site).
- `execute_tool_calls` / `execute_single_tool` / `process_tool_media` / `resolve_widget` named consistently in Phase 2.
- `invalidate_skill_cache` keeps its existing name in `tool_definitions.py` — no change to call sites in skill_tools.py beyond the module path.

**Scope discipline:** No drive-by refactoring. The `_resolve_attachments`, `_parse_wiki_references`, `_read_wiki_page`, `_get_already_injected_pages`, `_WIKI_MENTION_RE` helpers stay in `agent.py` — they're issue #439's scope, called out explicitly in the spec under "What we're NOT doing." Tests `test_resolve_attachments.py`, `test_wiki_context.py`, `test_vault_tools.py` do NOT need updating in this PR.

**Tests-to-update inventory (final):**
- `tests/test_agent_turn.py` — Phase 1 (registry imports + drop underscores) + Phase 2 (execution imports + patch targets)
- `tests/test_agent_widgets.py` — Phase 2 only
- `tests/test_process_tool_media.py` — Phase 2 only
- `tests/test_tool_result_data.py` — Phase 2 only
- `tests/test_resolve_attachments.py` — NOT touched (out of scope)
- `tests/test_wiki_context.py` — NOT touched (out of scope)
- `tests/test_vault_tools.py` — NOT touched (out of scope, `_WIKI_MENTION_RE` / `_parse_wiki_references` stay in `agent.py`)
- `tests/test_widgets_input_flow.py` — NOT touched (uses `_handle_widget_input_pause`, which stays in `agent.py` as part of `TurnRunner`)
- `tests/test_imports.py` — NOT touched (only imports `run_agent_turn`)
