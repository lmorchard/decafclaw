# issue-1049-preempt-matchers-raw-prompt

## Resume here

- Issue: https://github.com/lmorchard/decafclaw/issues/1049
- Worktree: /Users/lmorchard/devel/mine/decafclaw/.claude/worktrees/issue-1049-preempt-matchers-raw-prompt
- Branch: issue-1049-preempt-matchers-raw-prompt
- Base: origin/main at b47262d20fa2002efa07c24f631566e377c0df11
- Status: implemented, all local gates green (make check, make test). Committed
  at 243da2c1; draft PR #1083 published (https://github.com/lmorchard/decafclaw/pull/1083).
  Eval of `evals/tool-deferral.yaml` complete: 3/4 pass on gpt-4o-mini; the one
  failure reproduces identically on the base commit with the same model (pre-existing,
  not a regression). Remaining: coordinator dispatches the fresh-context local review;
  hosted CI green.

## Problem

`ContextComposer._compose_preempt_matches` and `_compose_preempt_skill_matches`
built their match input as `user_message + extract_last_assistant_text(history)`.
The prior assistant response is shaped by the previous turn's injected memory
context, so background-retrieval keywords (e.g. `meta-ingest`, `opencode` from an
injected vault page) leaked into the match input and drove irrelevant tool
promotions / `<preempt_skill_hint>` lines even when the user's current prompt was
unrelated — a feedback loop of "irrelevant background retrieval induces
irrelevant skill activation hints".

The issue spec: the matchers must "strictly evaluate the clean, raw user input
string (the user_message prior to any prepended memory, wiki references, or
journal injections)".

## Decision

Scope the match input to the **raw user message only** — drop the
`extract_last_assistant_text(history)` union from both matchers. This removes the
documented "user + previous assistant" topical-continuity heuristic (short
follow-ups like "yes, another one" no longer inherit the prior topic). That
trade-off is accepted per the issue: a keyword-less user message simply produces
no pre-emptive promotion, and deferred tools remain reachable via `tool_search`.

Consequences (all in scope):
- Both matcher methods lose their `history` parameter (now unused); call sites in
  `_compose_impl` updated.
- `extract_last_assistant_text` becomes dead code → removed from
  `preempt_search.py` and its 8 tests from `test_preempt_search.py`
  (no-deprecated-code convention).
- Docs updated: `docs/preemptive-tool-search.md` (input assembly, "Why the raw
  user message only?", ephemeral section, when-it-applies) and
  `docs/context-composer.md` (compose() step 7). Stale comments on
  `ToolState.preempt_matches` / `SkillState.preempt_matches` in `context.py`
  updated.

## Evidence

- Baseline `make test` (before changes): 4584 passed, 2 skipped.
- Reproduction: 4 new tests failed against unmodified src — the matcher's
  `input_tokens` contained the injected memory keywords (ingest, meta, opencode,
  pipeline, sessions, vault) alongside the user's words (water, plants), and the
  `meta-ingest` skill scored 5 / was hinted.
- Final regression test is end-to-end through `compose()`
  (`TestCompose::test_preempt_matchers_ignores_memory_context_history`): history
  carries a `vault_retrieval` memory message + an assistant echo; user message is
  unrelated; asserts no `preempt_matches`/`preempt_skill_matches` source, no
  `<preempt_skill_hint>` system message, and empty `ctx.tools/skills.preempt_matches`.
  Verified it FAILS with the old src (git stash of the two src files) and PASSES
  with the fix.
- Final `make test`: 4575 passed, 2 skipped
  (4584 − 8 removed `extract_last_assistant_text` tests − 2 removed
  `test_prior_assistant_carries_topic` tests + 1 new compose-level test).
- `make check` (install-js, check-npm-policy, check-message-types, fmt-check,
  lint, typecheck, check-js, check-browser-assets): exit 0.
- `make fmt`: clean.
- `evals/tool-deferral.yaml` (real LLM, `--model gpt-4o-mini`): 3/4 pass —
  "no tool_search when answer is reachable from critical tools" PASS,
  "pre-empted tool callable without tool_search" PASS (the raw-user-message
  promotion path), "preempt skill hint activates the clearly-relevant skill"
  PASS. "agent reaches a deferred tool end-to-end" FAIL: gpt-4o-mini routed
  the "Use the `wait` tool to pause for 1 second" prompt to `shell` (sleep)
  plus an unrelated tool instead of `wait`. Run on the base commit
  (b47262d2, unmodified src) with the same model fails the same assertion
  (`notes_append, shell` instead of `wait`) → pre-existing model behavior,
  not a regression from this change. Also consistent with the change being
  inert on that case: it is single-turn, so the old match input
  (`user_message + ""` — no prior assistant) is identical to the new one.

## Tests

- `tests/test_context_composer.py`: 26 matcher call sites updated to the new
  (history-less) signature; two `test_prior_assistant_carries_topic` tests
  removed (they codified the old union behavior); one new end-to-end
  `TestCompose::test_preempt_matchers_ignores_memory_context_history`.
- `tests/test_preempt_search.py`: removed `extract_last_assistant_text` import +
  `TestExtractLastAssistantText` class (8 tests).

## Log

- 2026-10-17 ~02:51 PDT — worktree created from origin/main @ b47262d2; board
  Ready → In progress.
- Read issue + triage decisions; traced the matcher input path; confirmed the
  leak is via `extract_last_assistant_text(history)` (prior assistant echo of the
  injected memory), not via the `user_message` string itself.
- Wrote failing tests, implemented the raw-user-message-only input, removed the
  now-dead helper, updated docs + comments, re-ran full gates green.
- Running `evals/tool-deferral.yaml` (real LLM) as a behavioral check of the
  pre-emptive promotion path.
- First two eval runs failed 4/4 at 0s/0 tokens with "All connection attempts
  failed". Root cause (found by probing `load_config()` + `_resolve()`): the
  worktree `.env` had no `DATA_HOME`, so `load_config()` used the worktree's
  own empty `data/` — no providers, no model_configs — and the auto-generated
  "default" provider fell back to the built-in legacy LLM default
  (LAN LiteLLM `http://192.168.0.199:4000`, which is down). Fix: added
  `DATA_HOME=/Users/lmorchard/devel/mine/decafclaw/data` to the worktree `.env`
  (eval test state still sandboxes to tmp via `_build_test_config`).
  Secondary infra limits (why the default model can't run here at all): the
  Vertex endpoint presents a TLS cert that doesn't match its hostname from this
  machine, and the shared config's vertex `service_account_file` points at
  `/Users/lorchard/...` (wrong home — pre-existing config typo, left alone).
- Committed 243da2c1, pushed, draft PR #1083 published and read back
  (draft, head verified via `verify-commit --pr-head`).
- Third eval run (after the DATA_HOME fix) with `--model gpt-4o-mini`: 3/4
  pass; the failing case ("agent reaches a deferred tool end-to-end") fails the
  same way on the base commit with the same model → pre-existing gpt-4o-mini
  routing behavior (picks `shell` sleep over the named `wait` tool), not a
  regression.
- Remaining for the coordinator: dispatch the fresh-context local review of the
  published head (same local model, waiver documented) and confirm hosted CI.
