# Eval coverage: audit and expand

Tracking: [#240](https://github.com/lmorchard/decafclaw/issues/240)

## Problem

Eval coverage is thin and likely stale. The issue calls out `evals/memory*.yaml` as the only coverage, but a quick scan shows there are also `ingest.yaml`, `postmortem.yaml`, and `project-skill.yaml` — so the issue text itself is already out of date. Doc rot is visible in `docs/eval-loop.md`, which describes fields (`prompt`, `expect_contains`, `expect_tool` as flat keys) that don't match what the runner actually accepts (`input`, `expect: {response_contains, ...}`). No `expect_tool` assertion exists in the runner.

#240 is large (5 skills + 6 tool groups + 4 system behaviors). It should be an umbrella, not a single PR.

## Session scope

This session is an **audit + trivial inline fixes + split #240 into smaller per-system issues**. Not writing new eval files. Not building new harness features.

### Deliverables

1. **`evals-audit.md`** in the session dir — per-file verdict for every existing eval, per-subsystem coverage gap list, harness-gap list, priority ordering for the spun-out issues.
2. **Inline PR(s)** on the `eval-coverage` branch for any doc/comment rot caught during the audit (sentence-level).
3. **Per-system GitHub issues filed via `gh`** on the project board, linked from the audit doc, each scoped to roughly one eval file ≈ one PR.
4. **#240 closed** once children are filed and linked.

## Audit approach

1. **Static read** of each existing eval YAML + eval runner + eval docs. Per-file notes: tests covered, assertions used, any obvious mismatch against current tool signatures or skill SKILL.md text.
2. **Full run** of all existing evals against the default model (single-model data point). Record pass/fail per test. Failures feed into per-file verdicts.
3. **Harness capability survey** — what the runner actually supports vs what #240's scope implies we need. Note gaps.
4. **Coverage gap survey** — walk the list in #240 against the file inventory. For each uncovered area, note which eval file it should live in and what the minimal viable test set looks like.
5. **Inline fix pass** — apply sentence-level doc/comment fixes surfaced by steps 1–3.
6. **Prioritize and file issues** — one per eval file (≈ per subsystem) plus separate issues for harness gaps. Each issue: title, problem statement, scope, acceptance criteria, link back to #240, priority, size.

### Inline-fix bar

- ✅ Fix inline on this branch: doc field names that don't match the runner, comments that describe removed behavior, outdated tool names in eval assertions where the intent is obvious, missing `max_tool_errors` where the current tests silently pass despite errors.
- 🔀 Spin out as its own issue: `expect_tool` / `expect_tool_args` / `expect_tool_count_by_name` assertions, multi-model matrix runner, pass-rate trend tracking, context-budget / cancel / effort-switching harness support, anything requiring > ~20 lines or new test surface.

### Issue filing policy

- File directly via `gh` once the audit doc is committed. Add each to the project board with priority and size.
- Default priority: **P2**. Default size: **M**. Override only when obviously different (e.g. XS for a known-tiny fix, P1 if flagged critical during audit).
- Each issue links back to #240 with `(split from #240)` in the body and references the audit doc for detailed scope.
- Close #240 with a summary comment linking all spun-out children.

### Model

Default model only (whatever `default_model` resolves to in the runner venv). Multi-model matrix is flagged as a spun-out infrastructure issue.

## Acceptance criteria

- `evals-audit.md` exists, committed, with per-file verdicts + gap list + harness-gap list + prioritized issue list.
- All current evals have been actually run (or explicitly noted as un-runnable with reason).
- Any inline doc/comment rot caught is fixed on this branch.
- Each spun-out issue is filed on GitHub with project-board priority + size, and linked from the audit doc.
- #240 is closed with a comment linking the children.

## Out of scope

- Writing new eval YAML files for any subsystem. (That's what the spun-out issues are for.)
- Building harness features (`expect_tool` assertion, multi-model matrix, trend tracking). Spun out.
- Fixing broken tool descriptions / SKILL.md wording caught during the audit unless it's a trivial sentence-level fix. Non-trivial fixes get noted in the audit doc and tracked on their own.
- Running evals against non-default models.
- Touching the eval result bundles under `evals/results/` (those are ignored).

## Self-review: gaps worth flagging

These are questions I noticed *after* the initial brainstorm — surfacing now so we can resolve before planning:

- **Eval fixtures.** `evals/fixtures/` contains at least `cat-facts-embeddings.db`; the `make build-eval-fixtures` target regenerates them. If the audit reveals that fixture shape is stale (e.g. schema changed in `embeddings.py`), that's a real infrastructure issue, not a sentence-level fix. Treat as spin-out.
- **Harness runs tests concurrently at 4×.** A full `evals/` run will hammer the default LLM provider in parallel. If the provider is rate-limited, we may get spurious failures that look like bit-rot. **Decision:** fall back to `--concurrency 1` and re-run before concluding anything's broken. Note the fallback in the audit.
- **`auto_confirm: true` masks confirmation UX.** The existing project-skill evals already note this explicitly. Worth confirming during the audit that no current eval accidentally relies on the wrong auto-confirm default.
- **`make dev` may be running.** Running evals from the worktree venv should be fine since each test creates its own tempdir `data_home`, but there's shared state for the LLM provider. Non-issue functionally, but if Les is actively using the dev instance during the run, eval results could bleed into his prompt budget.
- **Project board mutation.** Filing issues via `gh` adds them to the GitHub project board only if invoked with the right flags or via the `gh project` subcommand. **Decision:** every filed issue MUST end up on the board with priority + size set. Figure out the right incantation during plan-phase (either `gh issue create --project "decafclaw"` or `gh issue create` + `gh project item-add`), verify with the first filed issue, then apply the same pattern to the rest.
