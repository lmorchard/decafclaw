# Issue #27 — Additional Tabstack skill tools

## Resume here

- **Goal:** Add optional `guardrails` to `tabstack_automate` and optional `geo_target` to `tabstack_automate` + `tabstack_extract_markdown` in `src/decafclaw/skills/tabstack/tools.py`; forward non-None/non-empty values to the SDK; document in TOOL_DEFINITIONS; unit tests per issue Verification Criteria.
- **Issue:** https://github.com/lmorchard/decafclaw/issues/27
- **Worktree:** `/Users/lmorchard/devel/mine/decafclaw/.claude/worktrees/issue-27-tabstack-tools` (branch `issue-27-tabstack-tools`, base `origin/main @ e3d31836424c4aab20b8b642c4105a0ffdf6035c`)
- **Flow:** implement → DRAFT PR via submit-pr (same session) → resume → final commits to same draft. No ready, no review request, no merge. Review (independent fresh-context) dispatched by coordinator after my return.
- **Identity:** MokaGnome (`gh api user` confirmed 2026-10-13). All git commit/push + gh writes via `python3 "$GHFLOW_CLI" exec --`.
- **Board:** move to In progress at start; stays In progress while PR is draft.
- **Confirmed decisions:** issue body is the settled spec; no open decisions. User-approved exception: same local model may implement and review; independent fresh-context review still required (coordinator dispatches it after my handoff — not my job).
- **Latest checks:** (baseline running)
- **Next action:** baseline `make test`; then implement test-first, `make fmt` before each commit, `make check` + `make test` before self-review/commit; publish first coherent checkpoint as draft.

## Findings

- SDK 2.6.1 (uv.lock pins tabstack==2.6.1; pyproject `tabstack>=2.6.1`). Verified via `inspect.signature` in worktree venv:
  - `AsyncAgentResource.automate(*, task, data=Omit, geo_target: GeotargetGeoTarget | Omit, guardrails: str | Omit, interactive, max_iterations, max_validation_attempts, url, ...)`
  - `AsyncExtractResource.markdown(*, url, effort, geo_target: GeotargetGeoTarget | Omit, metadata, nocache, ...)`
- `GeotargetGeoTarget` (tabstack.types) is a pydantic BaseModel with one field `country: Optional[str]` (ISO 3166-1 alpha-2).
- **SDK coercion evidence** (ran `async_maybe_transform` against `extract_markdown_params.ExtractMarkdownParams`): a plain string `geo_target='US'` passes through *unchanged* (no validation, would serialize `"geo_target": "US"` in the request body — wrong shape for the API); a `GeotargetGeoTarget(country='US')` instance or `{'country': 'US'}` dict serializes to `{'country': 'US'}`. → **Decision (settled from evidence, within spec):** tool accepts `geo_target: str | None` (country code, per issue signature) and forwards `GeotargetGeoTarget(country=geo_target)` so the SDK serializes the expected object. Also type-correct against the SDK stubs (pyright).
- `guardrails` is `str | Omit` — forward the string directly.
- Existing forward pattern in `tool_tabstack_automate`: `kwargs` dict, conditional `if url: kwargs["url"] = url`. Follow it (`if guardrails:`, `if geo_target:` — truthiness drops both None and empty string, matching "do not forward None/empty values").
- `tests/test_tabstack.py` does NOT exist — only `tests/test_tabstack_tools.py`. All new tests go there.
- Test fakes: `_FakeAgent.automate` records `last_automate_kwargs`; `_FakeExtract` only implements `json` (no `markdown`) — extend the fake or add a markdown recorder for the new extract_markdown tests.
- SKILL.md documents some params (e.g. research `mode`); add a brief mention of the new params to keep the skill doc accurate (feature-change docs rule).
- `.env` copied from main clone + `HTTP_PORT=18927` (distinct from other worktrees: 18853/18891/18892/18893/8054/8095/8097/8765/8791).
- `uv sync` done in worktree.
- Out of scope per issue: other Tabstack tools (extract_json, generate, research), SDK upgrade, interactive confirmation flows.

## Checks

- **Baseline** (before code changes, worktree @ e3d31836): `make test` → **4536 passed, 2 skipped, 21.89s**.
- **Red phase** (tests written, before implementation): `uv run pytest tests/test_tabstack_tools.py -n 0` → 5 failed (`TypeError: unexpected keyword argument 'guardrails'/'geo_target'`), 24 passed.
- **Green** (after implementation): `uv run pytest tests/test_tabstack_tools.py -n 0` → 29 passed.
- `make fmt` → 1 file reformatted (ruff), no other changes.
- `make check` → **passed** (install-js, check-message-types, fmt-check, ruff lint "All checks passed!", pyright 0 errors 0 warnings, check-js, module-graph test 2 passed). Pre-existing pyright "new version available" notice — informational, not a failure, present on main too.
- `make test` → **4544 passed, 2 skipped, 21.03s** (baseline 4536 + 8 new tests).
- Evals not run: tool *description* text unchanged (only new optional properties added), disambiguation surface untouched → no `tool_choice` case per AGENTS.md trigger rule; issue's settled Verification Criteria require only the unit tests.

## Commits

- (commit SHA to be recorded after `git commit`)

## Handoff

- DRAFT PR published via submit-pr in same session; final commits pushed to same draft. Review (independent, fresh context, same-model exception approved by Les 2026-10-13) is coordinator-dispatched after my return — NOT performed in this session. PR left as draft, no review requested, not marked ready.
