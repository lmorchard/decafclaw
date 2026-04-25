# Plan: Tool-choice disambiguation eval harness

Refer to `spec.md` for architecture, schema, and acceptance criteria.

Sized to keep each commit independently reviewable. Order is bottom-up:
build the deterministic plumbing first, then the LLM-touching runner,
then CLI/cases/docs.

---

## Phase 1 — Tool-list assembly helper

**What:** A pure function that returns the **fully loaded** tool
definitions list — every core tool plus every discovered skill's
`tools.py` exports, with MCP tools filtered out unless explicitly
opted in. This is the eval's analog of the production
`_build_tool_list()` but without priority deferral, activation
gating, or fetched-tool tracking.

**Files:**
- `src/decafclaw/eval/tool_choice/__init__.py` — empty package marker.
- `src/decafclaw/eval/tool_choice/loadout.py` — `build_full_tool_loadout(config, *, include_mcp: bool) -> list[dict]`. Imports `TOOL_DEFINITIONS` from `decafclaw.tools`, calls `discover_skills(config)`, and for every skill with `has_native_tools` imports its `tools.py` and concatenates its `TOOL_DEFINITIONS`. Filters out entries whose function name starts with `mcp__` when `include_mcp` is false.
- `tests/test_eval_tool_choice_loadout.py` — unit tests:
  - Default loadout contains a known core tool (e.g. `web_fetch`).
  - Default loadout contains skill tools (e.g. `vault_read`).
  - Default loadout has zero `mcp__*` entries.
  - `include_mcp=True` does not error when no MCP servers configured (returns same set as default).
  - All entries are dicts with the expected `function.name` shape.

**Verify:** `make lint && uv run pytest tests/test_eval_tool_choice_loadout.py`

### Prompt

> Create `src/decafclaw/eval/tool_choice/__init__.py` (empty) and
> `src/decafclaw/eval/tool_choice/loadout.py` exporting
> `build_full_tool_loadout(config, *, include_mcp: bool = False) ->
> list[dict]`. The function returns a flat list of tool definitions
> built by concatenating `decafclaw.tools.TOOL_DEFINITIONS` with the
> `TOOL_DEFINITIONS` exported from each discovered skill's
> `tools.py` (use `discover_skills(config)` and import dynamically
> via `importlib`, mirroring the live skill loader). Filter out
> entries whose function name starts with `mcp__` unless
> `include_mcp` is true. Add `tests/test_eval_tool_choice_loadout.py`
> with the cases listed above. `make lint && uv run pytest
> tests/test_eval_tool_choice_loadout.py` must pass.

---

## Phase 2 — Intercept runner: per-case LLM call

**What:** Given a case + a model name + a fully-loaded tool list,
make a single `provider.complete()` call and return the pick.

**Files:**
- `src/decafclaw/eval/tool_choice/case.py` — `@dataclass Case(name, scenario, expected, near_miss, notes)` plus `load_cases(path: Path) -> list[Case]` (handles file or directory of YAMLs, list-of-mappings shape).
- `src/decafclaw/eval/tool_choice/runner.py` — `async def run_case(case: Case, *, model: str, config: Config, tool_loadout: list[dict]) -> CaseResult` returning a `CaseResult` dataclass `(case, model, picked: str | None, all_picks: list[str], passed: bool)`. Builds messages = `[{"role": "system", "content": load_system_prompt(config)[0]}, {"role": "user", "content": case.scenario}]`, picks the right provider via the registry, awaits `provider.complete(model, messages, tools=tool_loadout, streaming=False)`, extracts `tool_calls` from the result. `picked` is the first call's function name, `all_picks` is every call's name. `<no_tool>` sentinel string for the empty/text-only case. `passed = (picked == case.expected)`.
- Concurrency wrapper: `async def run_cases(cases, *, model, config, tool_loadout, concurrency: int) -> list[CaseResult]` using `asyncio.Semaphore`.

**Files:**
- `tests/test_eval_tool_choice_runner.py`:
  - `load_cases` parses a fixture file with two cases, returns two `Case` objects with all fields populated.
  - `load_cases` rejects a case missing required fields (`name`, `scenario`, `expected`, or `near_miss`).
  - `run_case` with a faked provider that returns a tool_calls list correctly populates `picked` + `all_picks` + `passed`.
  - `run_case` with a faked provider returning empty `tool_calls` (text-only response) yields `picked == "<no_tool>"`, `passed == False`.
  - `run_case` with a multi-call response captures all picks but `picked` is the first.

**Verify:** `make lint && uv run pytest tests/test_eval_tool_choice_runner.py`

### Prompt

> Add `src/decafclaw/eval/tool_choice/case.py` with a `Case`
> dataclass and `load_cases(path: Path) -> list[Case]` reading
> YAML(s) of list-of-mappings shape (`name`, `scenario`, `expected`,
> `near_miss`, optional `notes`). Reject cases missing required
> fields with a clear `ValueError`.
>
> Add `src/decafclaw/eval/tool_choice/runner.py` exporting
> `CaseResult` dataclass and `async def run_case(case, *, model,
> config, tool_loadout)` plus `async def run_cases(cases, *, model,
> config, tool_loadout, concurrency)`. `run_case` builds a two-
> message conversation (system prompt from `load_system_prompt`,
> user prompt from `case.scenario`), resolves the provider via the
> existing registry (mirror what agent.py does for the same model),
> awaits `provider.complete(model, messages, tools=tool_loadout,
> streaming=False)`, and reads `tool_calls` from the response. The
> first call's function name is `picked`; all names go into
> `all_picks`; an empty `tool_calls` array sets `picked` to
> `"<no_tool>"`. `passed = (picked == case.expected)`.
>
> Add `tests/test_eval_tool_choice_runner.py` with the cases listed
> above. The runner test cases use a faked provider injected via the
> registry so no network calls fire. `make lint && uv run pytest
> tests/test_eval_tool_choice_runner.py` must pass.

---

## Phase 3 — Scoring + report

**What:** Aggregate `CaseResult`s into the report formats described in
the spec — per-case PASS/FAIL line, summary, sorted pair-overlap
table, optional confusion matrix.

**Files:**
- `src/decafclaw/eval/tool_choice/report.py` — pure functions:
  - `format_case_lines(results: list[CaseResult]) -> list[str]` — `"PASS  {name}"` or `"FAIL  {name}    picked {picked}; expected {expected}"`.
  - `format_summary(results) -> str` — `"Summary: 12/15 passed (80%)"`.
  - `compute_pair_overlap(results) -> list[PairOverlap]` — for each case, contributes one row per `(case.expected, near_miss_tool)` pair. Aggregate by pair: `{pair, swapped: int, total: int, pct: float}`. Sorted by pct descending.
  - `format_pair_overlap(rows: list[PairOverlap]) -> list[str]` — table form, `← tighten` annotation when pct ≥ 50%.
  - `compute_confusion_matrix(results) -> dict[str, dict[str, int]]` — `expected → picked → count`.
  - `format_confusion_matrix(matrix) -> list[str]` — readable two-level list (saves the user from rendering a wide matrix in a terminal). Show only rows with at least one off-diagonal pick.
- `tests/test_eval_tool_choice_report.py` — unit tests for each formatter / compute function with crafted `CaseResult` lists.

**Verify:** `make lint && uv run pytest tests/test_eval_tool_choice_report.py`

### Prompt

> Add `src/decafclaw/eval/tool_choice/report.py` with the pure
> functions `format_case_lines`, `format_summary`,
> `compute_pair_overlap`, `format_pair_overlap`,
> `compute_confusion_matrix`, `format_confusion_matrix`. Pair
> overlap rows = one per `(expected, near_miss[i])` tuple aggregated
> across cases; sort by overlap pct descending; annotate with `←
> tighten` when pct ≥ 50%. Confusion matrix only shows rows where
> the model picked something other than the expected tool at least
> once. Add `tests/test_eval_tool_choice_report.py` covering each
> function with crafted `CaseResult` inputs. `make lint && uv run
> pytest tests/test_eval_tool_choice_report.py` must pass.

---

## Phase 4 — CLI entry point + Makefile target

**What:** `python -m decafclaw.eval.tool_choice` ties the previous
phases together. CLI args per spec; sweep mode runs `run_cases` per
model and groups output.

**Files:**
- `src/decafclaw/eval/tool_choice/__main__.py` —
  - argparse with `path`, `--model`, `--models`, `--include-mcp`, `--matrix`, `--verbose`, `--concurrency`.
  - Mutual exclusion: `--model` and `--models` can't both be set.
  - `load_config()` then `init_providers(config)` (mirrors the existing eval `__main__.py`); without this, `provider.complete()` fails at runtime.
  - Resolve model list (default = `[config.default_model]`).
  - Build `tool_loadout` once via `build_full_tool_loadout`.
  - For each model: `await run_cases(...)`. Print per-model header when sweeping; print case lines, summary, pair overlap table; print confusion matrix iff `--matrix`. Print all-tool-calls list per case iff `--verbose`.
  - Exit 0 when all cases pass across all models, 1 otherwise.
- `Makefile` — append target `eval-tools: ; uv run python -m decafclaw.eval.tool_choice evals/tool_choice/`.
- `tests/test_eval_tool_choice_cli.py` — integration test using a faked provider:
  - One-case YAML fixture, `--model fake`, asserts exit 0 + correct stdout shape.
  - One-case YAML fixture where the fake returns the wrong tool, assert exit 1.
  - `--models a,b` runs twice and produces two grouped sections.

**Verify:** `make lint && uv run pytest tests/test_eval_tool_choice_cli.py && make eval-tools` (the make target will need real LLM access; for plan-time verification only run it manually after merge).

### Prompt

> Add `src/decafclaw/eval/tool_choice/__main__.py` implementing the
> CLI per `spec.md`. Use argparse; mutually exclude `--model` /
> `--models`. Default model = `config.default_model`. Build
> `tool_loadout` once before the model loop. For each model, run
> `run_cases` and print: per-model header (when sweeping), per-case
> PASS/FAIL lines, summary, pair-overlap table, optional confusion
> matrix (`--matrix`), optional verbose `tool_calls` list
> (`--verbose`). Exit 0 only when every case passed for every
> model.
>
> Append a `eval-tools` target to the `Makefile` invoking the new
> CLI on `evals/tool_choice/`.
>
> Add `tests/test_eval_tool_choice_cli.py` with a faked provider
> covering: a passing case (exit 0), a failing case (exit 1), and a
> two-model sweep producing two grouped report sections. `make lint
> && uv run pytest tests/test_eval_tool_choice_cli.py` must pass.

---

## Phase 5 — Seed cases

**What:** 10–15 canonical ambiguity cases shipped under
`evals/tool_choice/`. Each case has clear `expected` + `near_miss` and
useful `notes`.

**Coverage targets** (one or two cases per pair):

- `vault_search` ↔ `conversation_search` (curated knowledge vs raw chat)
- `vault_read` ↔ `workspace_read` (knowledge file vs working file)
- `web_fetch` ↔ HTTP tool (read-and-extract vs raw HTTP)
- `delegate_task` ↔ `activate_skill` (sub-agent for a job vs unlock tools)
- `vault_search` ↔ `vault_read` (find vs fetch known)
- `conversation_search` ↔ `conversation_compact` (find vs summarize)
- one negative-case where the right answer is `<no_tool>` (purely conversational reply, no tool needed) — sanity-check that we don't measure tool-happy bias as a correct outcome

**Files:**
- `evals/tool_choice/core_overlaps.yaml` — vault/workspace/conversation pairs.
- `evals/tool_choice/skills_and_meta.yaml` — `delegate_task`/`activate_skill`, conversational-only case.
- (Single combined file is fine if author prefers; split is just for readability.)

**Verify:** `make eval-tools` runs cleanly. Failures here are description-tightening opportunities, not blockers — but the seed should pass on `default_model` at merge time. If a particular description is just bad, document the failure and either tighten the description in this PR or file a follow-up issue.

### Prompt

> Author 10–15 seed cases at `evals/tool_choice/`, covering the
> overlap pairs listed in the plan. Each case has `name`,
> `scenario`, `expected`, `near_miss`, and `notes`. Run `make
> eval-tools` against `config.default_model`. Address any failures
> by tightening the offending tool description in this PR (the
> whole point of the eval is to surface these), or file a follow-up
> issue if a fix needs broader reasoning than this commit warrants.
> All cases must pass on default_model at merge time.

---

## Phase 6 — Docs

**What:** Extend `docs/eval-loop.md` (which already exists for the
agent-loop eval) with a "Tool-choice disambiguation eval" section
covering:
- The CLI, flags, when to run.
- Case YAML format with example.
- Authoring guidance: when to add a case, how to write a good
  `notes` field.
- Cross-link from the "Tool descriptions are a control surface"
  bullet in `CLAUDE.md`.

**Files:**
- `docs/eval-loop.md` — new section appended; preserve existing
  content unchanged.
- `CLAUDE.md` — extend the relevant bullet with "validate
  description edits with `make eval-tools`."

**Verify:** Eye-check links and example.

### Prompt

> Append a "Tool-choice disambiguation eval" section to
> `docs/eval-loop.md` (don't create a parallel file). Cover the CLI,
> the YAML case format with a worked example, and authoring
> guidance. Update the `CLAUDE.md` "Tool descriptions are a control
> surface" bullet to reference `make eval-tools` as the validation
> surface for description edits.

---

## Phase 7 — PR + Copilot review

**What:** Squash phase commits into one PR, request Copilot review.

**Actions:**
1. Squash all phase commits with a comprehensive message.
2. `git push -u origin tool-choice-eval`.
3. `gh pr create` with summary + test plan; `Closes #303`.
4. `gh pr edit N --add-reviewer copilot-pull-request-reviewer`.
5. Verify CI; address any review feedback.
6. Move #303 to **In review** on the project board.

---

## Risk register

- **Skill `tools.py` import side effects.** If any skill's tools.py
  has init-at-import side effects, the loadout assembly will trigger
  them. Mitigate by mirroring the existing skill loader's import
  pattern (`importlib.util.spec_from_file_location` etc.) — that's
  already proven safe in production.
- **Provider mismatch in faked tests.** The fake provider needs to
  match the real `Provider` protocol exactly so `run_case` doesn't
  hit method-not-found errors. Solution: minimal `complete()` mock
  returning `{"content": ..., "tool_calls": [...], "role":
  "assistant", "usage": {}}`.
- **Seed cases failing at merge time.** A failing seed case during
  authoring is *the eval working as intended*. Address case-by-case:
  tighten the tool description (small edit in the same PR) or file a
  follow-up if the fix is bigger than this PR's scope.
- **Sweep cost on real models.** Default is single model so this only
  applies when someone explicitly opts into `--models`. No quota
  enforcement; users can read the case count and do the math.
- **`<no_tool>` as a sentinel string.** It's a string, not an enum —
  someone could create a tool literally named `<no_tool>`. The angle
  brackets make collision practically impossible, but worth a brief
  comment in the runner explaining the sentinel.
