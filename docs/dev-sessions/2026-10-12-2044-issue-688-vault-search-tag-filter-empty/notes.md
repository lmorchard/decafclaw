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
Independent review is the parent's job — do NOT mark the PR ready or request review.
Worktree and branch must be preserved for review.

- Worktree: `.claude/worktrees/issue-688-vault-search-tag-filter-empty`
- Branch: `issue-688-vault-search-tag-filter-empty` (from `origin/main` @ 4eaef9ee7fa841ab3c06d4ae985662917f7175da)
- Implementation model: llama.cpp/llama (local OpenCode subagent)

## Change

`src/decafclaw/skills/vault/tools.py` — `_substring_search` empty branch (~line 1005):

Before (both tag and no-tag cases):
    `No results matching '<query>'.`   (data: {query, mode: "substring", results: []})

After, when `req_tags` non-empty:
    `0 documents matched the tag filter ['incident', 'p1'] for query 'on-call'. The empty
    result is from the tag filter — not evidence that the vault has no relevant content.
    Matching pages may exist without those tags; retry without the `tags` filter to
    search untagged.`
    (data: {query, mode: "substring", tags: [...], any_tag, results: []})

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

NOT run: `make test-js`, `make check-tui`, `make test-tui` (CI-only surfaces for a
Python-only change; `make check` covers the in-gate JS checks), `make eval-tools`
(tool description unchanged — no new/sharpened description), `make run/dev/debug`
(never run, per project rules).

## PR

- Draft PR: https://github.com/lmorchard/decafclaw/pull/1066 (DRAFT, OPEN, base `main`)
- Published head: 516817bedcb4d541b3e1e9f195c009330a089940
  (`fix(vault): report the tag filter when a query+tags search is empty (#688)`);
  `verify-commit` confirmed it as the branch tip and the PR head (is_pr_head: true).
- Body file: /var/folders/8p/p5kz_p794bbbwgg2_hgn34nw0000gn/T/opencode/pr-688-body.md
- Board: #688 moved Ready → In progress via `ghflow board set-status` (readback confirmed,
  readback_matches: true). Stays In progress — draft does not qualify for In review.
- NOT done (out of scope for this worker): mark ready, request review, merge, CI follow-up.
