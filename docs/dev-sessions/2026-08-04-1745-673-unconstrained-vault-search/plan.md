# Plan — #673 unconstrained `vault_search` refuses and redirects

**Spec:** `spec.md` · **Frozen checks:** `checks.md` (frozen at `48aab5e`)
**Tier:** `auto-ok`

## Phase 0 — Freeze (DONE)

Committed as `48aab5e`, sha recorded in `8f4a144`. C1 fails for the right reason; G1–G4 pass.
Adjudication (one disposition per check and per guard) is inside the freeze commit.

**Check files, read-only from here on:**
- `tests/test_vault_search_tags_only.py`
- `tests/test_vault_tools.py`

A failing frozen check from this point is a report-back, not a fix-up.

## Phase 1 — Refuse the unconstrained call and point at `vault_list`

**Advances:** C1. Holds: G1, G2, G3, G4.

One vertical slice — the whole change crosses behavior + schema + docs in a single function's
worth of surface, and splitting it would produce a half-slice that fails its own guard (changing
the schema wording without the behavior breaks nothing, but changing the behavior without the
wording leaves the description asserting something false).

### 1a. The behavioral guard

In `tool_vault_search` (`src/decafclaw/skills/vault/tools.py:682`), after the existing vault-exists
and `folder` validation and **before** the `if not query and req_tags:` tags-only branch
(`tools.py:719`), return an error-shaped `ToolResult` when nothing narrows the call:

```python
if not query and not req_tags and not folder and not source_type and days <= 0:
    return ToolResult(text="[error: ...names vault_list...]",
                      display_short_text="no search criteria")
```

Three placement/shape constraints, each traceable to a frozen assertion:

- **Ahead of line 719.** C1(c) asserts `pages_with_tags` is never reached. Placing it after would
  also leave G1's third call as the only thing still grading the `and req_tags` conjunct.
- **`[error: …]` prefix**, per the repo's `ToolResult` error convention — C1(a) asserts it.
- **No `data=`.** C1(b) asserts over `json.dumps(result.data or {})` as well as `text`, because
  `tool_execution.py` appends the serialized `data` to the model-visible tool message. Returning a
  refusal with the old `data={"results": [...]}` attached would ship the page list anyway.

The condition enumerates all five narrowing axes. `days <= 0` rather than `not days` for
readability; both are equivalent given the `int` default, and negative days is already meaningless.

Per the spec's "Still open, but not tier-bearing": this is the **behavioral** guard, not a schema
`anyOf`. A schema constraint alone would not stop a direct call, and the criterion is written at
the behavioral level.

### 1b. The schema description

Update the `query` parameter description (`tools.py:1819-1828`). It currently says an unconstrained
call "lists every page in the vault rather than searching" — false once 1a lands. G2(i) pins three
things that must survive the rewrite: the literal `vault_list`, one of `every page` / `lists every`,
and a prohibitive token (`do not` / `don't` / `never`). Those are satisfiable honestly — the new
text says the call is *refused* and that `vault_list` is how you enumerate every page — so this is
a rewrite, not a squeeze against the guard.

### 1c. Docs

`docs/vault.md:153` — the `vault_search` row currently describes only the tags-only mode and #669.
Add the refusal and the `vault_list` redirect. No other doc names this behavior
(`semantic-search.md` and `context-composer.md` reference `vault_search` generically).

`CLAUDE.md` needs no change: no convention or key-file moves.

### Verification (each command run by name, output read)

- [ ] `uv run pytest tests/test_vault_search_tags_only.py::test_unconstrained_search_refuses_and_names_vault_list -q` — C1, must now PASS with an explicit `1 passed`
- [ ] `uv run pytest tests/test_vault_tools.py::TestVaultSearchTags::test_empty_tags_leaves_behavior_unchanged -q` — G1
- [ ] `uv run pytest tests/test_vault_search_tags_only.py::test_schema_steers_away_from_the_unconstrained_call tests/test_vault_search_tags_only.py::test_omitting_query_matches_passing_empty_string -q` — G2
- [ ] `uv run pytest tests/test_vault_search_tags_only.py -q` — G3, whole file (no `--deselect`; the freeze-time deselect existed only because C1 could not pass yet)
- [ ] `make test` — G4, plus node-id subset diff against `collected-at-freeze.txt` and skip count still 2
- [ ] `make check` — project gate
- [ ] `git diff 48aab5e -- tests/test_vault_search_tags_only.py tests/test_vault_tools.py` — tamper diff, must be empty

## Phase 2 — Eval coverage for the sharpened description

**Advances:** no criterion. Required by a repo convention, not by the spec.

`CLAUDE.md`: *"New or sharpened tool description → add a `tool_choice` case."* 1b sharpens exactly
the `vault_search` ↔ `vault_list` boundary, and `evals/tool_choice/core_overlaps.yaml` has cases for
`vault_recent` vs both, but **none for "enumerate the vault" → `vault_list`** — the disambiguation
this PR leans on. That is the rot vector the convention names.

Add one declarative case to `evals/tool_choice/core_overlaps.yaml` in the existing shape
(`name` / `scenario` / `expected: vault_list` / `near_miss: [vault_search]` / `notes`).

**Open question with a default answer:** `make eval-tools` makes real LLM calls and the worktree
shares `DATA_HOME` with the main clone. Default: attempt the run; if credentials are absent or it
would write into the shared `data/`, **say so plainly and ship the case unrun**, flagged in the PR
body — do not report an eval as passing that did not run. A declarative case that is never executed
still guards against silent deletion of the steer in a future edit, which is most of its value.

This phase is deliberately last so that a problem here cannot block C1.

## What this does NOT do

- **Does not fix `source_type` being ignored on the substring path.** Found at freeze, documented
  under "Out of scope" in `checks.md`. `_substring_search` takes no `source_type` parameter at all,
  so this is broader than #673 and predates it. G3's `source_type` case is deliberately narrowed to
  non-refusal so a later fix isn't smuggled into a boundary guard.
- **Does not implement cap-and-label.** Closed by Les's recorded decision, not merely unchosen.
- **No drive-by cleanup** of `tool_vault_search`'s surrounding structure (e.g. the stray double
  blank line at `tools.py:723-724`, the function-level `from datetime import timedelta` imports).
  Noted here, not fixed.

## Self-review

- **Criteria coverage, both directions.** C1 → Phase 1. Every guard is verified in Phase 1's
  checklist. Phase 2 advances no criterion and says so, with its justification (a named repo
  convention) rather than being scope creep — it is separable and last.
- **Checks cited by command.** Every checkbox is the exact command from `checks.md`, except G3,
  which is deliberately *stronger* at verification (whole file, no deselect) than at freeze. Stated,
  not silent.
- **Placeholder scan.** No TBD. The one open question carries a default answer.
- **Type consistency.** One function, one condition, no new signatures. The five axes in 1a's
  condition are exactly the five in C1's text and in 1b's description; a sixth appearing in one and
  not the others would be the bug to watch, and there isn't one.
