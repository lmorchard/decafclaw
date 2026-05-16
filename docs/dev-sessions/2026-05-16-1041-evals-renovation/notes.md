# Evals renovation — session notes

Running log for this dev session. One section per PR; end-of-session retro at the bottom.

## Pre-flight

- Re-audit captured in `audit.md`. Baseline: 24/30 pass (80.0%) on `vertex-gemini-flash`, bundle at `evals/results/2026-05-16-1041-vertex-gemini-flash/`.
- Spec + plan in `spec.md` / `plan.md`. Four PRs in dependency order.

### Re-audit highlights

- PR #429 smoke test (`saves memory when asked`) is bit-rotted: `notes_append` competes with `vault_journal_append`. Hardest finding from the re-audit; will be fixed in PR-B via tool-description tightening.
- Other failures are mostly the same shape as the 2026-04-24 audit: vague memory prompts don't trigger retrieval; `project_update_plan` registry confusion still alive (#355 still open).
- No new harness gaps surfaced — the renovation plan covers all known harness needs.

## PR-A — Harness polish

Branch: `evals-harness-polish`. Three commits beyond the session-scaffolding commit:

1. **`response_contains_all` + judge-prompt assertion coverage (#354)** — `_check_assertions` gains an AND-semantics list assertion mirroring `response_contains` (case-insensitive substring, `re:` prefix for regex). `reflect.py` now renders all set assertions as a bullet list and includes the harness's actual failure reason — non-`response_contains` failures previously got `Expected: ?`. Extracted `_summarize_expectations` for direct unit testing.
2. **`expect_workspace` post-turn state assertions (#352)** — new top-level block (parallel to `setup` / `expect`) with `workspace_files` (existence + content match), `workspace_file_exists`, `workspace_file_absent`. Runs once at end-of-test. Sandbox-checked symmetrically with `setup.workspace_files`. Regex form uses `re.IGNORECASE | re.DOTALL` so multi-section page assertions can use `.+` across newlines.
3. **`setup.conversation_history` seed (#353)** — pre-populates both the on-disk archive (`{workspace}/conversations/eval.jsonl` via the production `append_message`) AND the in-memory history passed to `run_agent_turn`. Per-turn delta accounting means seeded tool messages don't get charged against the first turn's `max_tool_calls`. Unblocks #342 (conversation evals).

### Tests added

- `test_eval_runner_assertions.py`: +7 cases for `response_contains_all`.
- `test_eval_reflect.py` (new): 8 cases for the judge-prompt summarizer.
- `test_eval_workspace_assertions.py` (new): 16 cases (substring, regex, DOTALL, exists/absent, three sandbox paths).
- `test_eval_conversation_history.py` (new): 8 cases (archive format, timestamp behavior, tool-call round-trip, role validation, round-trip through `read_archive`).

### Decisions during execution

- **`expect_workspace` at the test-case top level, not nested in `expect`.** The issue spec said "add to expect" but multi-turn tests have no test-level expect block — only per-turn. A separate top-level field also makes the timing (end-of-test) unambiguous.
- **`re.DOTALL` on regex content matches.** Discovered needed when writing the section-edit example: `re:## A.+## B` won't span newlines without it. Already-existing `response_contains` regex doesn't use DOTALL, but workspace files are typically multi-line so the tradeoff lands the other way.
- **Conversation history seed returns the stamped list rather than re-reading the file.** Cheaper, simpler, and the returned list ↔ on-disk archive equality is unit-tested.

### Smoke-run result

`make eval` against `vertex-gemini-flash`, post-merge baseline: **26/30 pass (86.7%)**, up from 24/30 (80.0%) pre-PR-A. Bundle: `evals/results/2026-05-16-1130-vertex-gemini-flash/`. All 4 remaining failures are pre-existing and tracked by downstream PRs:

- #11 `finds specific cat fact via semantic search` — `memory-semantic.yaml` (slated for deletion in PR-B).
- #18 `saves memory when asked` — notes/vault disambiguation (F1, PR-B).
- #20 `uses think tool for complex question` — vague memory prompt (PR-B #348 will reword + tighten).
- #29 `executes plan steps` — `project_update_plan` registry confusion (#355, out of renovation scope).

The 2-test improvement (24→26) is mostly LLM noise from rerun variance, not anything PR-A directly fixed; #28 passed this run where it failed last run, #17 passed cleanly where it failed last run. PR-A doesn't change agent behavior — only assertion + judge-prompt machinery. The improved baseline is genuinely improved judge prompts for the failures, not improved pass rate.

### PR

[#521](https://github.com/lmorchard/decafclaw/pull/521) — open against `main`.

### Things to watch in PR-B onwards

- The `expect_workspace` field is now available to vault evals (PR-B) for section-edit and write tests.
- The `setup.conversation_history` field is now available to conversation evals (PR-C) for compaction tests.
- Judge-prompt failures now include `failure_reason` verbatim — should produce sharper reflections when PR-B / PR-C tests exercise non-`response_contains` assertions.

## PR-B — Vault + memory cleanup + notes/vault disambiguation

_To fill in during execution._

## PR-C — Tool-selection coverage sweep

_To fill in during execution._

## PR-D — Pass-rate trend tracking

_To fill in during execution._

## End-of-session retro

_To fill in at end._
