# issue-1049-preempt-matchers-raw-prompt

## Resume here

- Issue: https://github.com/lmorchard/decafclaw/issues/1049
- Worktree: /Users/lmorchard/devel/mine/decafclaw/.claude/worktrees/issue-1049-preempt-matchers-raw-prompt
- Branch: issue-1049-preempt-matchers-raw-prompt
- Base: origin/main at b47262d20fa2002efa07c24f631566e377c0df11
- Status: implemented, all local gates green (make check, make test). Committed;
  draft PR published. Eval of `evals/tool-deferral.yaml` re-running with
  `--model gpt-4o-mini` (Vertex endpoint TLS-broken from this machine).

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
- First eval run (default model `vertex-gemini-flash`) failed 4/4 with
  "All connection attempts failed" at 0s/0 tokens: the Vertex endpoint
  (`aiplatform.us-central1.googleapis.com`) presents a TLS certificate that does
  not match its hostname from this machine (curl exit 60), and the LAN LiteLLM
  endpoint (192.168.0.199:4000) times out. Infrastructure, not code. Re-running
  with `--model gpt-4o-mini` (OpenAI endpoint verified reachable, HTTP 401 =
  alive, key configured).
