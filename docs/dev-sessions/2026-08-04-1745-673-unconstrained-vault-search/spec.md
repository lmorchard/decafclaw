# 673 — vault_search with no criteria returns the whole vault framed as search results

**Source:** https://github.com/lmorchard/decafclaw/issues/673

Captured verbatim from the issue body (marker line stripped).

---

Pre-existing behavior, surfaced (not caused) by #669. Deferred out of that PR — see below.

## Behavior

An unconstrained `vault_search` returns every page, reported as a successful search:

```
BARE_RETURN='Found 2 result(s):\n\n- agent/pages/a (modified: ...)\n- agent/pages/b (modified: ...)'
```

With an empty `query` and no `tags`/`folder`/`source_type`/`days`, the substring path matches everything.

## Why this is pre-existing, not new

`vault_search(query="")` has always been callable — only *omitting* the argument raised `TypeError`, which is what #669 fixed. `tests/test_vault_tools.py::TestVaultSearchTags::test_empty_tags_leaves_behavior_unchanged` deliberately asserts this dump-everything behavior today (its actual purpose is guarding the `pages_with_tags([])` footgun, but it pins the empty-query result as a side effect).

I briefly added a guard for this inside #669 and reverted it: it broke that deliberate test, and my premise — "defaulting `query` made this reachable for the first time" — was simply wrong. Changing it is a behavior change to existing functionality and belongs on its own.

## Why it's worth fixing anyway

Two problems, both mild:

1. **Unbounded output.** On a real vault this dumps the full page list into context. `Found N result(s)` scales with the vault, not with intent.
2. **Misleading framing.** "Found N result(s)" reads as a successful, relevant search when what happened is "no filter was applied." An agent has no signal that its query was empty, so it may treat the dump as responsive and reason from arbitrary pages.

`vault_list` already exists for plain enumeration, so the dump path is redundant as well as noisy.

## Options

- **Refuse and redirect:** require at least one of `query` / `tags` / `folder` / `source_type` / `days`; otherwise return an error naming `vault_list`. Clearest signal to the model. Requires updating `test_empty_tags_leaves_behavior_unchanged` to keep its real assertion (`pages_with_tags` not called) while dropping its incidental empty-query expectation.
- **Cap and label:** allow it but truncate and relabel (e.g. `Listing first N of M pages (no search criteria given)`). Less disruptive, keeps a working call.

I'd lean toward refuse-and-redirect — an unconstrained search is almost always a mistake, and `vault_list` covers the legitimate intent — but it's a judgment call about how strict to be with the agent.

Size: XS/S depending on option.

---

## Decision (Les, 2026-08-03): refuse and redirect

Recorded in the body rather than a comment, because downstream modes read the body only.

**An unconstrained `vault_search` refuses and points at `vault_list`.** The cap-and-label branch is
not the deliverable — it is closed, not merely unchosen, so a later reader does not reopen it by
analogy. This matches the author's stated lean; the two branches had mutually exclusive checks
(each fails under the other), which is why the issue could not be graded until now.

## Acceptance criteria

*Added by `agent-session` triage, 2026-08-03.*

- **CRITERION:** IF `vault_search` is called with an empty `query` AND no `tags`, `folder`,
  `source_type` or `days`, THEN it SHALL return an error-shaped `ToolResult` whose text names
  `vault_list`, SHALL NOT list any page path, and SHALL NOT call `pages_with_tags`.

  **CHECK:** `pytest tests/test_vault_search_tags_only.py::test_unconstrained_search_refuses_and_names_vault_list`
  — to be authored, asserting all three: (a) `"vault_list" in result.text`, (b) no vault page stem
  appears in `result.text`, (c) the `pages_with_tags` mock was not called.

  **All three assertions are load-bearing; do not reduce this to (a).** A check for the string
  `vault_list` alone is a keyword proxy — the cheapest way to green it is editing a format string
  while still enumerating the vault underneath. (b) and (c) are what make it grade behavior.

  **OBSERVED (2026-08-03): fails today — discriminates.** Established from the opposite direction,
  since the node does not exist yet: `pytest tests/test_vault_tools.py::TestVaultSearchTags::test_empty_tags_leaves_behavior_unchanged`
  passes today, and that test asserts `"Foo" in result_omitted.text` — i.e. the unconstrained call
  currently enumerates pages. `src/decafclaw/skills/vault/tools.py:866,942` emit
  `Found {len(lines)} result(s)` unconditionally.

## Regression guards

- **GUARD:** the real assertion of the pinning test survives — the empty/omitted-`tags` path still
  never enters `pages_with_tags(config, [])`.
  **CHECK:** `pytest tests/test_vault_tools.py::TestVaultSearchTags::test_empty_tags_leaves_behavior_unchanged`
  **OBSERVED:** 1 passed.

  **Explicit permission for the freeze phase:** this test's incidental `"Foo" in result` assertions
  *must* be dropped under this branch — they pin the behavior being removed. Its
  `mock_pages_with_tags.assert_not_called()` assertion **must remain**. Stated outright because
  "edit a pinning test" otherwise reads as tampering.

- **GUARD:** the #674 schema steer pointing at `vault_list` is not silently dropped, and omitting
  `query` stays equivalent to passing `""`.
  **CHECK:** `pytest tests/test_vault_search_tags_only.py::test_schema_steers_away_from_the_unconstrained_call tests/test_vault_search_tags_only.py::test_omitting_query_matches_passing_empty_string`
  **OBSERVED:** 2 passed.

- **GUARD:** tags-only, folder, `source_type` and days-filtered searches keep working — they are
  *constrained* and must not be caught by the new refusal.
  **CHECK:** `pytest tests/test_vault_search_tags_only.py`
  **OBSERVED: UNRUN at scan time** (targeted commands only). Individual nodes from the file passed.

- **GUARD:** no test lost, newly skipped, or newly failing (invariant, not a pinned count).
  **CHECK:** `make test` — **UNRUN at scan time.** Not verified; the freeze phase must run it.

## Still open, but not tier-bearing

Does the refusal live as a hard guard in `tool_vault_search`, or as a schema-level
`required`/`anyOf` constraint? The issue's "require at least one of … at the schema level" phrasing
implies a different check surface (schema assertion vs. behavioral assertion). The criterion above
is written at the behavioral level and holds either way, so this is implementation style — but
prefer the behavioral guard, since a schema constraint alone would not stop a direct call.

## Tier: auto-ok

**Trigger 1 no longer fires.** The withheld product decision is now made, and the surviving branch
has a strong deterministic in-process check whose current-behavior contradiction is verified by a
passing test that asserts the opposite.

**Trigger 2 does not fire.** One function in `src/decafclaw/skills/vault/tools.py` plus its schema
description; no auth, secrets, migration/deletion, deploy/CI config, or dependency change.
