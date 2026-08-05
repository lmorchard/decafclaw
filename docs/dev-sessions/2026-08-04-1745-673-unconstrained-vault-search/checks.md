# Frozen acceptance checks

**Source:** https://github.com/lmorchard/decafclaw/issues/673
**Frozen at:** `48aab5e` (2026-08-04)
**Check files — read-only from Phase 1 onward:**
- `tests/test_vault_search_tags_only.py`
- `tests/test_vault_tools.py`

`tests/test_vault_tools.py` is listed because G1 lives in it. The issue grants **explicit,
scoped permission** to drop that one test's incidental `"Foo" in result` assertions *at the
freeze phase* — they pin the behavior C1 removes. That edit therefore lands **inside the freeze
commit**, so the tamper baseline already contains it and the read-only rule governs unchanged
from Phase 1 onward. Its `mock_pages_with_tags.assert_not_called()` assertion must survive; it
is the guard's actual content.

## C1

CRITERION: IF `vault_search` is called with an empty `query` AND no `tags`, `folder`,
`source_type` or `days`, THEN it SHALL return an error-shaped `ToolResult` whose text names
`vault_list`, SHALL NOT list any page path, and SHALL NOT call `pages_with_tags`.

CHECK: `pytest tests/test_vault_search_tags_only.py::test_unconstrained_search_refuses_and_names_vault_list`
— to be authored, asserting all three: (a) `"vault_list" in result.text`, (b) no vault page stem
appears in `result.text`, (c) the `pages_with_tags` mock was not called.

All three assertions are load-bearing; do not reduce this to (a). A check for the string
`vault_list` alone is a keyword proxy — the cheapest way to green it is editing a format string
while still enumerating the vault underneath. (b) and (c) are what make it grade behavior.

**Assertion (b) was strengthened before the freeze to cover `result.data` as well as `result.text`
— see Adjudication.** The criterion says "SHALL NOT list any page path", and `data` is model-visible:
`tool_execution.py` appends `json.dumps(result.data)` to the tool message as a fenced JSON block.

AT FREEZE: **fails, correct reason** — 1 failed, 0 passed (collected 1, exit 1; not exit 5).
Trips at (a) because the unconstrained call still enumerates the vault:

```
>       assert "[error:" in result.text
E       AssertionError: assert '[error:' in 'Found 2 result(s):\n\n- agent/pages/compute-notes
    (modified: 2026-08-05 01:02)\n- agent/pages/storage-notes (modified: 2026-08-05 01:02)'
tests/test_vault_search_tags_only.py:159: AssertionError
```

The `tagged_pages` fixture really is on disk and both stems really do appear under current
behavior, so (b) is not vacuous. The `pages_with_tags` patch target was confirmed to intercept the
real call site (module-level import at `tools.py:36`, sole call site `_tag_filter_search` at
`tools.py:831`), so (c) cannot pass because the patch missed.

## Guards

(Pass today; must keep passing. Not criteria — they can't fail at freeze.)

- **G1:** the real assertion of the pinning test survives — the empty/omitted-`tags` path still
  never enters `pages_with_tags(config, [])`.
  CHECK: `pytest tests/test_vault_tools.py::TestVaultSearchTags::test_empty_tags_leaves_behavior_unchanged`
  AT FREEZE: **1 passed** in 1.28s (explicit passed line, not absence-of-failure).

- **G2:** the #674 schema steer pointing at `vault_list` is not silently dropped, and omitting
  `query` stays equivalent to passing `""`.
  CHECK: `pytest tests/test_vault_search_tags_only.py::test_schema_steers_away_from_the_unconstrained_call tests/test_vault_search_tags_only.py::test_omitting_query_matches_passing_empty_string`
  AT FREEZE: **2 passed** in 1.12s.

- **G3:** tags-only, folder, `source_type` and days-filtered searches keep working — they are
  *constrained* and must not be caught by the new refusal.
  CHECK: `pytest tests/test_vault_search_tags_only.py --deselect tests/test_vault_search_tags_only.py::test_unconstrained_search_refuses_and_names_vault_list`
  AT FREEZE: **12 passed** in 1.19s (was 9 before the freeze added three boundary cases).

  The `--deselect` is a **pre-freeze correction**, not an amendment. The issue's command ran the
  whole file, which now contains C1's not-yet-implemented node — so as written it could never be
  recorded green at freeze, which is what a guard's `AT FREEZE` must be. The deselected node is
  C1's own check and is run separately; nothing is excluded from grading. At verification the
  undeselected whole-file command is the stronger one and is what the verifier runs.

- **G4:** no test lost, newly skipped, or newly failing (invariant, not a pinned count).
  CHECK: `make test`, plus a node-id set diff against `collected-at-freeze.txt` (see below).
  AT FREEZE: **1 failed, 3737 passed, 2 skipped** in 13.29s. The single failure is C1's own node,
  which is the expected state at freeze; every other test passes. Skip count at freeze: **2**.
  `collected-at-freeze.txt` holds **3740** sorted node-ids — the non-integration set that `make
  test` actually runs (`addopts` is `-n auto --dist=worksteal -m 'not integration'`, deselecting 15
  integration nodes). Pre-freeze baseline on unmodified `origin/main` was 3734 passed / 2 skipped;
  the +3 passing is the three G3 boundary cases, +1 failing is C1.

  `make test` alone catches only *newly failing*. A deleted test makes the suite smaller and
  greener; a newly skipped one never fails. Since the criterion forbids pinning a count, the
  freeze records the **set** of collected node-ids to
  `collected-at-freeze.txt` (sibling of this file). The invariant at verification: that set is a
  **subset** of the final collected set, and the skip count did not rise.

## Reading a check's output

Every node-id check must be confirmed by an explicit `N passed` line, never by absence of failure.
With this repo's `-n auto` default, a vanished or typo'd node-id prints a bare `no tests ran` with
no `ERROR: not found` and no red line — exit 5, so a scripted gate catches it, but it reads as
clean to a human skimming the tail. Record the collected/passed counts, not just the verdict.

## Adjudication

Written at freeze step 4, before the freeze commit. The check-reviewer was a fresh read-only
context given this manifest and the repo, but neither the plan (none existed yet) nor the criteria's
rationale. It returned `strengthen` on all five items; every closer below is the reviewer's own,
applied as specified. Strengthening here is not an amendment and costs no tier — nothing was frozen
at the time.

- **C1: strengthened** — the three assertions all read `result.text`, but `tool_execution.py`
  appends `json.dumps(result.data)` to the model-visible tool message as a fenced JSON block, and
  today's dump carries the page list in `data["results"]` as well as in `text`. An implementation
  that swapped the text for `[error: … vault_list …]` while leaving `_substring_search`'s `data`
  intact would have greened all three assertions while still shipping every page path to the model
  — the exact keyword-proxy failure the criterion warns about, one layer down. The check now also
  asserts no stem appears in `json.dumps(result.data or {})`.

- **G1: strengthened** — the guard would have gone toothless the moment C1 landed. Both of its
  calls are fully unconstrained, i.e. exactly C1's refusal domain, and C1(c) forces the refusal
  above `tools.py:719` — so post-C1 `assert_not_called()` would be satisfied by the refusal rather
  than by the `and req_tags` conjunct it exists to protect. Mutating line 719 to `if not query:`,
  reintroducing the `pages_with_tags(config, [])` empty-AND footgun the docstring names, would have
  left it green. The check now makes a third call inside the same patch block that is constrained
  but tagless (`tags=[], days=1`), so at least one input still reaches line 719 with
  `req_tags == set()`.

- **G2: strengthened**, both halves. (i) The guard asserted only that the `query` description
  contains `vault_list` and one of `every page` / `lists every` — but the actual steer is the
  imperative `"Do NOT omit it with no other filter"`, and that sentence has to be reworded anyway
  once C1 lands. A rewording could satisfy both substrings with the prohibition deleted outright:
  steer gutted, guard green. It now also asserts a prohibitive token (`do not` / `don't` / `never`)
  survives in that description. (ii) `test_omitting_query_matches_passing_empty_string` compared
  only stem-presence, so post-C1 both sides are stem-free and every comparison degrades to
  `False == False` — vacuous exactly when the behavior it guards changes. It now also asserts
  `("[error:" in a) == ("[error:" in b)`, which stays indifferent to which behavior applies.

- **G3: strengthened**, and this was the most consequential finding. The guard's criterion names
  folder / `source_type` / days searches, but the suite contained **no** empty-query case on any of
  those three axes — every empty-query call passed `tags` or nothing. So the cheapest over-broad
  implementation, `if not query and not req_tags: refuse`, would have greened C1, G3 **and** G4
  while breaking precisely the constrained searches G3 exists to protect: C1's boundary had no
  fence on its non-tag side. Three cases were added asserting an empty-query call constrained on
  each axis is not refused. The `source_type` case deliberately asserts only non-refusal — see
  "Out of scope, found at freeze" below. The command also gained a `--deselect` so it can be
  recorded green at freeze at all.

- **G4: strengthened** — `make test` catches only the *newly failing* third of what the criterion
  names. A deleted test makes the suite smaller and greener; a newly skipped one never fails. Since
  the criterion forbids pinning a count, the freeze records the collected node-id **set** to
  `collected-at-freeze.txt`, and verification asserts that set is a subset of the final one and
  that the skip count did not rise.

## Out of scope, found at freeze

`source_type` is silently ignored on the **entire** substring path, not just for empty queries:
`_substring_search` (`tools.py:877`) takes no `source_type` parameter and its only call site
(`tools.py:795`) never passes one. The semantic branch honors it (`tools.py:729-731`, forwarded to
`search_similar`), as does `_tag_filter_search`. Because the test config uses
`search_strategy = "substring"`, `vault_search(ctx, "", source_type="page")` returns the whole vault
today. This is pre-existing and unrelated to #673; it is **not** fixed here, and G3's `source_type`
case is deliberately narrowed to non-refusal so that widening it later doesn't smuggle a second fix
into a boundary guard.

## Amendments

(Append-only. Empty unless an amendment was made.)
