# Issue #165 — Add type hints across public APIs

Branch: `issue-165-type-hints` from `origin/main` @ `9173771da6e410635f37d7b5b6426f78ef4792ef`.
Worktree: `.claude/worktrees/issue-165-type-hints/` (HTTP_PORT=18891 in `.env` copy).
Identity: MokaGnome. Board: Ready → In progress (2026-10-15, readback confirmed, item `PVTI_lAHNVLfOAUg5p84KJkDr`).

## Scope (authoritative per issue body, triage:ready)

Three files: `src/decafclaw/context.py`, `src/decafclaw/config.py`, `src/decafclaw/reflection.py`.
Explicit per-symbol annotation list + explicit exclusions in the issue body govern.

## Already-satisfied parts (verified at base 9173771)

- All nine `Config` property accessors (`agent_path`, `workspace_path`,
  `http_callback_base`, `vault_root`, `vault_agent_dir`, `vault_agent_pages_dir`,
  `vault_agent_journal_dir`, `tool_context_budget`, `compaction_context_budget`)
  **already carry the exact return annotations** the issue asks for
  (config.py:259-304 at base). No change needed; recorded as pre-existing.

## Decisions taken

1. `Config.discovered_skills` → `list[SkillInfo]` (issue offered `list[Any]` or
   `list[SkillMetadata]`). Evidence: `discover_skills(...) -> list[SkillInfo]`
   (skills/__init__.py:548) and `load_system_prompt` returns
   `("\n\n".join(sections), skills)` from that (prompts/__init__.py:115);
   consumers already type these as `list[SkillInfo]` (commands.py:83,106;
   skills/__init__.py:848,856). No `SkillMetadata` class exists in the codebase;
   `SkillInfo` (skills/__init__.py:42) is the actual element type. Importing
   `SkillInfo` into config.py is cycle-safe: `skills/__init__.py` has no
   module-level config import (only TYPE_CHECKING `Context`), and config.py
   already imports `decafclaw.skills` transitively via
   `from .skills.vault._grants import normalize_folder`.
2. `reflection.py` `config: Config` via `if TYPE_CHECKING:` import (config.py is
   not in reflection.py's runtime import chain, but TYPE_CHECKING keeps the
   annotation zero-cost and matches the project's ctx-annotation convention).
3. `context.py` `dynamic_provider_definitions: dict[str, list[dict]]` (line 74)
   left untouched — not in the issue's explicit per-symbol list (which names
   only `extra_definitions`, `deferred_pool`, and the `skill_contributions`
   nested definitions list).

## Checks

- Baseline (before any edit): `make test` → 4566 passed, 2 skipped in 65.74s.
  (worktree venv via `uv sync`; `.env` copied from main clone + HTTP_PORT=18891.)
- pyright config: `typeCheckingMode: basic`, `reportAttributeAccessIssue: error`,
  python 3.13, include `src/decafclaw`, `scripts`, `contrib`.
- Caller compatibility (checked): agent.py `history: list` (= `list[Any]`)
  assignable to `list[dict[str, Any]]`; `call_llm`/`call_llm_streaming` take
  bare `messages: list, tools: list | None` so the narrower `BoundLlmClient`
  params stay compatible.

## Edits made

- `context.py`: `extra_definitions`/`deferred_pool` → `list[dict[str, Any]]`;
  `skill_contributions` nested definitions → `list[dict[str, Any]]`;
  `BoundLlmClient.__call__`/`.complete` params `list[dict[str, Any]]`,
  return `dict[str, Any]`.
- `config.py`: `T = TypeVar("T")`; `load_sub_config(dc_class: type[T],
  json_data: dict[str, Any], ...) -> T`; `discovered_skills: list[SkillInfo]`
  (import added); `dataclasses.fields` call widened via `cast(type[Any], …)`
  (pyright rejects bare `type[T]` for `type[DataclassInstance]`; see decision 1
  rationale — keeping T unbound per the issue's literal spec).
- `reflection.py`: `TYPE_CHECKING` import of `Config`;
  `_extract_tool_lines`/`build_tool_summary`/`build_prior_turn_summary`
  history params `list[dict[str, Any]]`; `_coerce_bool(value: Any)`;
  `_extract_verdict(data: dict[str, Any])`; `evaluate_response(config: Config)`.

## Gate results (local, final head `81e0a444a72cf1fd788fbabdcffc174cd081d606`)

- `make typecheck` (pyright): **0 errors, 0 warnings** — success condition 1.
- `make lint` (ruff): **0 errors** — success condition 2.
- `uv run pytest tests/test_agent_turn.py tests/test_ws_model.py`: **62 passed** — success condition 3.
- `make fmt`: 434 files, no changes needed (before each commit).
- `make check` (full gate incl. npm policy, message-types drift, fmt-check,
  lint, typecheck, tsc, browser-assets): all green.
- `make test` (full suite): 4566 passed, 2 skipped — identical to the
  pre-edit baseline (4566 passed, 2 skipped).
- Hosted CI: runs on the published head (draft PR #1079); not yet read back.

## Handoff

- Draft PR: https://github.com/lmorchard/decafclaw/pull/1079
  (base `main`, head `issue-165-type-hints`, `verify-commit --pr-head` clean
  at `81e0a444`). Draft only: not marked ready, no review requested,
  `Closes #165` deliberately left for the ready submission.
- Remaining: independent review of the published head (user-approved
  same-local-model exception), then ready submission.
