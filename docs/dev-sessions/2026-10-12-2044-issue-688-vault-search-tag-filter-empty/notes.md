# Session notes — issue #688 vault_search tag-filter false-empty message

## Resume here

Issue #688: `vault_search` with `query`+`tags` returning 0 after the tag post-filter
reported `No results matching '<query>'.`, which the model read as total absence and
hallucinated ("I don't have any information…"). Owner's settled decision: **message-only
fix** — no automatic fallback search, no AND-semantics change, no tag pruning. In
`_substring_search`'s empty branch, when `req_tags` is non-empty, the message states that
0 documents matched the tag filter (naming the normalized sorted tags), makes clear the
empty result is the filter's (not evidence the vault lacks content), and hints that
retrying without `tags` is possible. The empty result's `data` gains `tags` + `any_tag`
(consistent with the pure-tag-filter mode). The no-tag path and the pure-tag-filter path
are byte-for-byte unchanged.

Implementation COMPLETE, local gates green, published as DRAFT PR (see below).
Independent review found NO defects but ONE ACCEPTED routine note, now corrected on the
same branch (see "Review correction" below): the message over-attributed the empty
result to the tag filter in the sub-case where the query itself matches nothing.
Gates re-run green on the correction. Review of the corrected code happens next by the
parent — do NOT mark the PR ready or request review.
Worktree and branch must be preserved for review.

- Worktree: `.claude/worktrees/issue-688-vault-search-tag-filter-empty`
- Branch: `issue-688-vault-search-tag-filter-empty` (from `origin/main` @ 4eaef9ee7fa841ab3c06d4ae985662917f7175da)
- Implementation model: llama.cpp/llama (local OpenCode subagent)

## Change

`src/decafclaw/skills/vault/tools.py` — `_substring_search` empty branch (~line 1005):

Before (both tag and no-tag cases):
    `No results matching '<query>'.`   (data: {query, mode: "substring", results: []})

After, when `req_tags` non-empty (corrected wording — see "Review correction" below):
    `0 documents matched query 'on-call' with the tag filter ['incident', 'p1'] — no page
    both matched the query and carried these tags. This is not evidence the vault lacks
    relevant content. If you expected matches, retry with a broader query or without the
    `tags` filter.`
    (data: {query, mode: "substring", tags: [...], any_tag, results: []} — unchanged)

After, when `req_tags` empty: unchanged `No results matching '<query>'.` / `No files found.`

Pure-tag-filter empty path (`_tag_filter_search`, `No pages found matching tags [...].`)
left untouched — it already names the filter and does not mislead.

`docs/vault.md` — one sentence added to the `vault_search` tool-table row documenting the
#688 empty-result wording (docs-must-stay-current rule; the row already records #669/#673).

Tests added (tests/test_vault_tools.py):
- `TestVaultSearch::test_search_no_results_message_unchanged_without_tags` (regression guard)
- `TestVaultSearchTags::test_query_plus_tags_false_empty_reports_the_tag_filter` (core #688 case)
- `TestVaultSearchTags::test_pure_tag_filter_empty_message_unchanged` (regression guard)
- `TestVaultSearchTagsSemantic::test_tag_filter_empties_semantic_falls_through_to_tag_message`
  (semantic post-filter empties candidates → substring fall-through carries the new message)
- `TestVaultSearchTags::test_query_matches_nothing_with_tags_reports_combined_predicate`
  (added with the review correction: query matches nothing + tags present → the message
  must still be the combined query+tag predicate, not an exclusive tag-filter attribution)

## Review correction (accepted routine note)

Independent review (fresh-context worker) found no defects but one accepted note: the
first wording ("0 documents matched the tag filter … The empty result is from the tag
filter … retry without the `tags` filter") is accurate when the tag filter dropped a page
the query matched, but in the reachable sub-case where the QUERY ITSELF matches nothing
(e.g. query="zqxwv", tags=["foo"]) it over-attributes the empty result to the tag filter,
and "retry without the `tags` filter" is a no-op there.

Fix (wording only, same commit as this note): the message now states the COMBINED
query+tag predicate — "0 documents matched query '<query>' with the tag filter [tags] —
no page both matched the query and carried these tags. This is not evidence the vault
lacks relevant content. If you expected matches, retry with a broader query or without
the `tags` filter." Accurate in both sub-cases; still no absence claim, no auto-fallback
description. `data` unchanged. No-tag branch, pure-tag-filter path, AND logic, schema,
and tool description untouched. No eval added (declined — out of the decision's scope).
`docs/vault.md` sentence updated to the combined-predicate wording. Core + semantic
tests re-asserted on the new wording; the two regression guards are unchanged.
Specificity re-confirmed: with `tools.py` temporarily reverted to the base version
(old generic message), all three new-behavior tests FAIL; restored → pass.
Correction commit: child of 44579c18a2aad4ad09eff75aed3c1d7cac3eef17 on this branch
(`git log` for the full SHA; it carries this notes update, so there is no notes-only commit).

## Baseline (pre-change)

- `uv sync`: OK (fresh worktree venv).
- `.env` copied from main clone + `HTTP_PORT=18892` added.
- `make test`: **4522 passed, 2 skipped in 21.12s** — clean baseline, no pre-existing failures.
- TDD reproduction: with tests added and code unmodified,
  `test_query_plus_tags_false_empty_reports_the_tag_filter` and
  `test_tag_filter_empties_semantic_falls_through_to_tag_message` FAILED with the old text
  `No results matching 'on-call'.` / `No results matching 'widget'.`; both regression guards PASSED.

## Checks (post-change)

- `uv run pytest tests/test_vault_tools.py tests/test_vault_search_tags_only.py tests/test_widgets_vault_search.py -n 0`: **174 passed**
- `make fmt`: 432 files left unchanged
- `make check` (install-js + check-message-types + fmt-check + lint + typecheck + check-js + check-browser-assets): **all green** (pyright 0 errors, 0 warnings; tsc clean; module-graph tests 2 passed)
- `make test`: **4526 passed, 2 skipped in 17.00s** (baseline 4522 + 4 new)
- `docs/vault.md` edit landed after the gates; markdown is outside every gate (ruff/tsc/pytest don't read it) — no gate re-run needed for that one line.

## Checks (post-correction, same order)

- `make fmt`: 432 files left unchanged
- `uv run pytest tests/test_vault_tools.py tests/test_vault_search_tags_only.py -n 0`: **167 passed**
- `make check`: **all green** (pyright clean; tsc clean; module-graph tests 2 passed)
- `make test`: **4527 passed, 2 skipped in 14.00s** (4526 + 1 new sub-case test)
- Specificity probe: base `tools.py` (old generic message) → 3 new-behavior tests FAILED
  (`No results matching …` in the failure text); restored → 3 passed.

NOT run: `make test-js`, `make check-tui`, `make test-tui` (CI-only surfaces for a
Python-only change; `make check` covers the in-gate JS checks), `make eval-tools`
(tool description unchanged — no new/sharpened description), `make run/dev/debug`
(never run, per project rules).

## PR

- Draft PR: https://github.com/lmorchard/decafclaw/pull/1066 (DRAFT, OPEN, base `main`)
- Commits: 516817bedcb4d541b3e1e9f195c009330a089940 (fix + tests + docs) →
  44579c18a2aad4ad09eff75aed3c1d7cac3eef17 (session notes) → correction commit on top
  (wording fix + updated tests + this notes update; full SHA in the worker's report and
  `git log`). PR head moves to the correction commit on push; `verify-commit --pr-head`
  re-run on it.
- Body file: /var/folders/8p/p5kz_p794bbbwgg2_hgn34nw0000gn/T/opencode/pr-688-body.md
- Board: #688 moved Ready → In progress via `ghflow board set-status` (readback confirmed,
  readback_matches: true). Stays In progress — draft does not qualify for In review.
- NOT done (out of scope for this worker): mark ready, request review, merge, CI follow-up.
