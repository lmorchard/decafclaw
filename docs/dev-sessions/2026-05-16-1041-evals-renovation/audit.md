# Eval system re-audit — 2026-05-16

Refresh of the 2026-04-24 eval-coverage audit (`docs/dev-sessions/2026-04-24-0941-eval-coverage/evals-audit.md`). Captures what's changed in three weeks, the current pass-rate baseline, and what's still load-bearing for the renovation.

## TL;DR

- **Baseline:** 30 tests, 24 passed, 6 failed (370s, 1.05M tokens) against `vertex-gemini-flash`. Pass rate **80.0%** — down from **86.2%** (25/29) at the original audit.
- **Drift since audit:** one new file (`grace-turn.yaml`, +1 test), one harness PR landed (#429 → `expect_tool` / `expect_no_tool` / `expect_tool_count_by_name`), three audit inline-fixes survived.
- **The PR #429 smoke test is now broken.** "saves memory when asked" in `memory.yaml` asserts `expect_tool: vault_journal_append`; the agent now reaches for `notes_append` instead. New tool, new disambiguation problem. This is the most actionable single finding from the re-audit.
- **All other audit findings still stand.** `memory-semantic.yaml` is still silently broken. `memory.yaml` test #7 is still named after the long-removed `think` tool. Memory-multi-turn still has no bounds. Project-skill's `project_update_plan` confusion is still there (now hitting 2 tests, not 1).
- **13 open issues remain in scope** — 12 from the original split, plus #430 filed as the follow-on to #429. #350 was demoted P2→P3 (matrix runner).

## What changed since 2026-04-24

### Inbound work that landed

| Commit | What | Impact on this audit |
|--------|------|----------------------|
| #429 (`feat(eval): expect_tool/...`) | Three tool-name assertions on `expect:` | Unblocks tool-deferral evals; smoke test added to `memory.yaml`. Smoke test now fails — see below. |
| Audit inline fixes (#338) | `make eval` target, `reflect.py` multi-turn input fix, `docs/eval-loop.md` schema rewrite | Confirmed in place; no regression. |
| #496 (`feat(agent): iteration budget with grace turn`) | New `evals/grace-turn.yaml` (+1 test) + `setup.max_tool_iterations` harness support | Currently passing. Good shape — uses `response_contains` regex + `response_not_contains` + `max_tool_calls` bound. |
| #428 (`text_input widget`) | Adds `ask_user_text` tool; no eval coverage | Out of scope for this renovation. Worth filing as a follow-up. |

### Issue-state drift

- #349 closed by PR #429 (the only original audit child to land).
- #345/#346/#347 closed as redundant with unit-test coverage (per audit triage; not new).
- #350 (matrix runner) **demoted P2 → P3.**
- #430 filed as the explicit "now write the deferral evals" follow-on to #429.

### Coverage shape

The 6 YAML eval files at the original audit are still here; `grace-turn.yaml` is the only addition. **30 single-turn-equivalent tests** total (was 29). `evals/tool_choice/` has 2 files with ~12 disambiguation cases — that surface is healthy and separate.

## Baseline (2026-05-16, `vertex-gemini-flash`)

Per-file pass rate from `evals/results/2026-05-16-1041-vertex-gemini-flash/`:

| File | Tests | Pass | Fail | Δ vs audit |
|------|-------|------|------|------------|
| `grace-turn.yaml` | 1 | 1 | 0 | new |
| `ingest.yaml` | 1 | 1 | 0 | — |
| `memory.yaml` | 8 | 5 | 3 | -1 |
| `memory-multi-turn.yaml` | 4 | 4 | 0 | — |
| `memory-semantic.yaml` | 7 | 6 | 1 | -1 (but see note) |
| `postmortem.yaml` | 1 | 1 | 0 | — |
| `project-skill.yaml` | 8 | 6 | 2 | — |
| **Total** | **30** | **24** | **6** | **-1** |

### Per-failure analysis

| # | File / test | Category | Notes |
|---|-------------|----------|-------|
| 11 | `memory-semantic.yaml::finds specific cat fact via semantic search` | **(c) bit-rot** | Only test in the file that historically drove tool use. `allowed_tools` is the stale list, so the agent's attempt to call `tool_search` returns `[error: tool 'tool_search' is not available in this context]`. Now visible as a runtime failure where the audit found it as a silent pass. **Resolved by PR-B's deletion of this file.** |
| 17 | `memory.yaml::handles missing memories gracefully` | **(d) real LLM-behavior** | Agent didn't include the substring "don't" in its response. Likely worded its denial differently this run ("I'm not aware of…" etc.). The assertion is too tight — should accept any reasonable denial phrase. Fix in PR-B's #348 tightening. |
| 18 | `memory.yaml::saves memory when asked` | **(d) real LLM-behavior — new** | **The PR #429 smoke test.** Agent called `notes_append`, not `vault_journal_append`. `notes_append` is a critical-priority, always-loaded tool added after #429 was designed; its description ("things you want to remember across turns within this conversation") pulls hard on the "Please remember" prompt. This is a tool-description disambiguation problem — the user-level fact "favorite programming language" is arguably vault material, but the agent reads "remember" and reaches for notes. **Belongs in PR-B**: either fix the tool descriptions, or change the smoke test to a prompt where vault clearly wins ("Save this to my profile permanently…"), and add a `tool_choice` case for the disambiguation. |
| 20 | `memory.yaml::uses think tool for complex question` | **(d) real LLM-behavior (recurring)** | Same as 2026-04-24 audit #19. Agent fabricates a generic dinner menu, 0 tool calls. Prompt "If you were planning a dinner party for me, what would you serve?" doesn't trigger memory retrieval. Test name still references the long-removed `think` tool. Fix in PR-B's #348. |
| 27 | `project-skill.yaml::writes plan after spec approval` | **(d) real LLM-behavior (worsened)** | Agent calls `project_update_plan` repeatedly even though the registry suggests `project_update_step`, `project_update_spec`, etc. — same self-contradicting "did you mean" surface flagged in the 2026-04-24 audit (#355). The plan tool may actually exist as `project_update_plan` but the registry's fuzzy-match is including it in the suggestion list (which is what `#355` is about — separate bug). Out of scope for this renovation. |
| 28 | `project-skill.yaml::does not ask verbal approval question` | **(d) real LLM-behavior — new this run** | Same `project_update_plan` confusion. Out of scope for this renovation. |

### Smoke test bit-rot (the most actionable single finding)

PR #429 landed `evals/memory.yaml::saves memory when asked` as the smoke test for the new `expect_tool` machinery:

```yaml
- name: "saves memory when asked"
  input: "Please remember that my favorite programming language is Python. Confirm what you saved."
  expect:
    response_contains: "Python"
    max_tool_calls: 5
    expect_tool: vault_journal_append
    expect_no_tool: [shell, shell_patterns]
```

In the 2026-05-16 run, the agent called `notes_append` (a critical-priority always-loaded tool whose description explicitly says "use for things you want to remember"). The assertion fails on `expect_tool: vault_journal_append`.

**This is exactly the failure mode `evals/tool_choice/` exists to surface, but the case isn't there yet.** Renovation should:

1. Add a `notes_append ↔ vault_journal_append` case to `evals/tool_choice/` so the disambiguation gets a focused signal.
2. Decide whether the smoke test should be updated to a prompt that unambiguously routes to vault, OR whether `notes_append` should be added to `expect_no_tool` (forcing vault path), OR whether the tool descriptions need to disambiguate "remember for this conversation" from "remember about me forever."

The third path is the highest-leverage. Tool descriptions are the control surface; this is the kind of disambiguation #17's work demonstrated is tunable.

## Open issues still in scope (13)

Unchanged from the 2026-04-24 audit's split, minus the one that landed:

| # | Title | Pri | Size | Status |
|---|-------|-----|------|--------|
| #339 | Eval coverage: vault skill (replaces broken memory-semantic.yaml) | P1 | M | Still in Backlog, still load-bearing |
| #348 | Tighten existing memory evals (bounds, assertion quality, stale names) | P2 | S | Same |
| #354 | Eval harness: misc quality (judge-prompt + response_contains_all) | P2 | XS | Same |
| #352 | Eval harness: post-turn workspace-state assertion | P2 | S | Same |
| #353 | Eval harness: setup.conversation_history to seed archives | P2 | S | Same; unblocks #342 |
| #430 | Eval: tool-deferral evals using new expect_tool assertions | P2 | M | New since audit (follow-on to #349) |
| #344 | Eval coverage: tool deferral (tool_search + context budget) | P2 | S | Same; pairs with #430 |
| #340 | Eval coverage: workspace tools | P2 | S | Same |
| #341 | Eval coverage: shell tools | P2 | S | Same |
| #343 | Eval coverage: delegate_task | P2 | S | Same |
| #342 | Eval coverage: conversation tools | P2 | S | Same; blocked on #353 |
| #351 | Eval harness: pass-rate trend tracking across runs | P2 | S | Same |
| #350 | Eval harness: multi-model matrix runner | **P3** | M | Demoted; out of renovation scope |

13 in renovation scope (everything except #350).

## Findings not in any open issue

These are real, surfaced by the re-audit, but don't fit an existing issue. Each is one short paragraph for downstream filing.

### F1 — `notes_append` vs `vault_journal_append` disambiguation

PR #429's smoke test breaks because `notes_append` (added after #429's design) competes for the same prompt. Real tool-description ambiguity. Right home: either fold into the #339 vault evals as a description-tightening exercise, or file as a standalone "tighten notes/vault disambiguation" issue plus a `tool_choice` case. Recommend the former.

### F2 — `ask_user_text` (from #428) has no eval coverage

A new tool shipped without a `tool_choice` case or end-to-end eval. Not load-bearing for renovation; worth a follow-up issue.

### F3 — `project_update_plan` registry suggestions (recurring)

The audit's standalone bug #355 (self-contradicting "did you mean" — `unknown tool 'project_update_plan'. Did you mean: project_update_plan, ...`) is still alive and now causes two failures instead of one in `project-skill.yaml`. Already filed — leaving it alone.

## Renovation scope (recommended)

1. **PR-A** (harness polish): #354 + #352 + #353. Tiny, all three unblock or strengthen downstream work.
2. **PR-B** (the P1 + memory cleanup): #339 + #348 + **finding F1**. Replaces `memory-semantic.yaml` with a real `vault.yaml`; tightens existing memory evals; either fixes the PR #429 smoke test or tightens tool descriptions so it passes; adds a `tool_choice` case for the disambiguation.
3. **PR-C** (coverage sweep): #430 + #344 + #340 + #341 + #343 + #342. Six small YAML files using the new harness pieces.
4. **PR-D** (trend tracking): #351 only.

Validation gate after each PR: `make eval` against `vertex-gemini-flash` ≥ 80% pass rate (the post-PR-A baseline). PR-D logs a per-run record so we can see the renovation arc.

## Out of renovation scope (still parked)

- **#350 matrix runner** (demoted P3).
- **All P3 audit deferrals**: dream/garden eval, claude_code subagent eval, effort-switching, cancel probe, `expect_tool_args`, scheduled/heartbeat sim. Unchanged.
- **Behavior fixes** for failures #17, #20, #27, #28: surface them as issues if not already, but don't sink the renovation into chasing them.
- **F2** (`ask_user_text` coverage): file as a follow-up after renovation.
