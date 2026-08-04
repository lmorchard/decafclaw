# Frozen acceptance checks

**Source:** https://github.com/lmorchard/decafclaw/issues/752
**Frozen at:** `d34c9df` (2026-08-03) — recorded in the follow-up commit, since a commit cannot
contain its own hash. Re-anchor if the branch is rebased; the freeze commit must stay an ancestor
of the pushed head so the tamper diff is re-runnable by a reviewer.
**Check files — read-only from Phase 1 onward:**
- `tests/test_reflection.py`

Guard ids were assigned after the pre-freeze review, which added one guard (G2) and re-scoped
two others. The set below is the frozen one.

## C1

CRITERION: GIVEN a config where `reflection.model` names a model absent from `model_configs`
AND `default_model` is set, WHEN `evaluate_response` runs, THEN the judge call SHALL route to
`reflection.model` via the legacy `resolved()` url/model/api_key rung, and SHALL NOT use
`default_model`.

CHECK: `pytest tests/test_reflection.py::TestEvaluateResponse::test_nonconfig_reflection_model_beats_default_model`
— asserting the `call_llm` kwargs carry the configured reflection model rather than the
`default_model` key.

AT FREEZE: **fails — 1 collected, 1 failed** (exit 1, not exit 5), 2026-08-03.
Observed: `AssertionError: assert {'model_name': 'author'} == {'llm_api_key': 'test-key',
'llm_model': 'cheap-judge-model', 'llm_url': 'http://test/v1/chat/completions'}` at
`tests/test_reflection.py:694`. Correct reason: the body ran all the way through
`evaluate_response` and `call_count == 1` passed immediately before the failing line, so this
is the routing defect — not an import error, missing fixture, or `Config` construction problem.

The check has two configs. The first is the issue's reported case. The second sets
`reflection.url` / `reflection.api_key` to values *distinct* from `config.llm`, closing the hole
the pre-freeze review found: with them left empty, `resolved()` backfills both from `config.llm`,
so an implementation that skips `resolved()` and reads `config.llm` directly emits byte-identical
kwargs and passes while silently discarding two documented settings — one of them a secret.
Both configs also pin `call_count == 1`, since `call_args` reads only the last call and judging
twice would still bill the author's model.

## Guards

(Pass today; must keep passing. Not criteria — they can't fail at freeze.)

- **G1:** the legacy-rung path and the `verifier_model` path both keep working.
  CHECK: `pytest tests/test_reflection.py::TestEvaluateResponse::test_uses_reflection_model tests/test_reflection.py::TestEvaluateResponse::test_verifier_model_routes_judge_call`
  AT FREEZE: **2 passed** (2 collected), 2026-08-03.
  Scope note from the pre-freeze review: `test_uses_reflection_model` uses the shared `config`
  fixture, which has **no** `model_configs` and **no** `default_model` — so it exercises the
  *legacy* rung, not the `model_configs` rung. The clause "a `reflection.model` that IS a
  `model_configs` key still routes to it" is pinned by **G3** case (a), not by this pair. Recorded
  so the guard is read as what it actually covers.

- **G2:** WHERE `verifier_model` names a key in `model_configs` AND `reflection.model` is set but
  absent from `model_configs`, `verifier_model` SHALL still win. Added by the pre-freeze review:
  no test in the suite set both fields non-empty, so the cheapest edit greening C1 could hoist the
  `reflection.model` branch above the `verifier_model` branch, inverting #591's documented
  precedence (`docs/reflection.md`: `verifier_model` "outranks every other judge-model setting")
  with the whole suite green.
  CHECK: `pytest tests/test_reflection.py::TestEvaluateResponse::test_verifier_model_outranks_nonconfig_reflection_model`
  AT FREEZE: **1 passed** (1 collected), 2026-08-03 — confirming this precedence is current
  behaviour, so it is a true regression guard and not a second criterion.

- **G3:** `test_verifier_model_unset_preserves_fallback_chain` keeps passing **unmodified.**
  If landing the fix requires editing this test, that is a signal the change reached further
  than intended.
  CHECK: `pytest tests/test_reflection.py::TestEvaluateResponse::test_verifier_model_unset_preserves_fallback_chain`
  plus: `git diff <freeze-sha> -- tests/test_reflection.py` shows **no hunk whose range falls
  between `def test_verifier_model_unset_preserves_fallback_chain` and the next `def`.** (Stated
  at function granularity rather than whole-file, because C1's own test already adds hunks to this
  file and a whole-file diff would be non-empty by construction.)
  AT FREEZE: **1 passed** (1 collected), 2026-08-03.

- **G4:** the stale documentation of the *old* precedence is corrected in the same change rather
  than left contradicting the code. The pre-freeze review widened this from one site to four —
  a reviewer following the original one-site wording would have fixed the comment and shipped
  three contradicting docs, including the exact passage users act on.
  1. `src/decafclaw/config_types.py:243-250` — the `verifier_model` comment, whose last sentence
     states the #752 misordering as intended.
  2. `docs/reflection.md:56` — the caveat under `decafclaw config set reflection.model ...`,
     including the now-stale *"see #752 for the fix"* pointer the spec calls out.
  3. `docs/reflection.md:88-92` — the resolution-order table; the `reflection.model` and
     `default_model` rows both change under branch (a).
  4. `docs/config.md:212` — a pointer into that table. Verify; it may need no edit, since its own
     claim is about `verifier_model` and stays true.
  CHECK: none — human read at review. Deliberately not graded: its only mechanical check would be
  a keyword grep, which is satisfied by deleting the sentence and writing nothing accurate in its
  place. Recorded so it is not dropped.
  AT FREEZE: n/a (not a runnable check).

- **G5:** no test lost, newly skipped, newly deselected, or newly failing.
  CHECK: `make test` reports **`passed >= 3732`**, **`skipped == 2`**, **`failed == 0`**.
  AT FREEZE: **3732 passed, 2 skipped**, 2026-08-03.
  A floor-plus-ceiling rather than "an invariant, not a pinned count" — the review showed that
  wording leaves the only mechanical "no test lost" signal unread. Two ways to lose a test under
  the looser rule: delete one (`3731 passed`, no failure, no skip rise), or mark it
  `@pytest.mark.integration`, which `addopts`' `-m "not integration"` silently deselects — and
  under `-n auto` the deselected count is not printed at all. The floor costs nothing, since new
  tests only push it up. The two skips are `contrib/skills/rss-ingest/test_fetch_feeds.py` on a
  missing `feedparser`; the ceiling is what catches a newly-broken optional import.

## Adjudication

Reviewed before the freeze commit by a read-only context (no Edit/Write), given `checks.md` and
the repo but not the implementation approach or the criteria's rationale. One disposition per
check and per guard, including the ones it cleared.

- **C1: strengthened** — two holes. (i) The criterion names `resolved()`, but with
  `reflection.url`/`api_key` left empty the fixture cannot distinguish `resolved()` from a direct
  `config.llm` read; a second config with distinct values was added. (ii) The assertion reads
  `call_args`, the last call, so a double-judge implementation would pass while still billing the
  author's model; `call_count == 1` was added. What the review *cleared*: the branch ordering
  itself is well pinned — collapsing the two `rc_model` branches breaks G3 case (a), and deleting
  the `default_model` rung breaks G3 case (b), so neither is a cheap way through.
- **G1: strengthened** — re-scoped, not re-checked. The guard claimed two protections; the review
  found `test_uses_reflection_model` exercises the legacy rung rather than the `model_configs`
  rung, so the second clause is really G3's. The wording now says what the pair covers, and the
  clause it does not cover names its real home.
- **G2: strengthened** — this guard did not exist. Added in response to the review's finding that
  no test set `verifier_model` and `model` both non-empty, leaving #591's precedence unpinned
  against exactly the reorder this change performs. Authored, run, and confirmed passing today.
- **G3: strengthened** — the diff half was tightened from whole-file to function-range granularity
  (see the CHECK above). The review's other point, that the freeze sha must be in `checks.md`
  before the freeze commit lands, is **noted rather than adopted**: a commit cannot contain its own
  hash, which is why the procedure records it in a follow-up commit. The substance — that the
  baseline must be written down for anyone else to re-run the diff — is satisfied there. The
  pytest half was cleared: all three cases use full-dict equality, a `skip` marker would flip the
  recorded `1 passed` and trip G5, and deleting or renaming makes the nodeid uncollectable.
- **G4: strengthened** — widened from one site to four (enumerated above). The review agreed it
  cannot be graded mechanically and that escalating has nowhere to go; the fix was to make the
  human read a checklist instead of a pointer.
- **G5: strengthened** — replaced "invariant, not a pinned count" with an explicit floor and
  ceiling, closing the silent-deletion and silent-deselection vectors (rationale above). The
  skipped-count invariant as originally written was cleared as well chosen.

## Tamper verdict

**clean**, recorded 2026-08-03 before push, and re-runnable by anyone: the freeze commit
`d34c9df` is an ancestor of the pushed head, and the branch was not squashed.

```
git diff d34c9df -- tests/test_reflection.py
```

Empty (0 bytes). The frozen check file was not touched after the freeze. `git diff d34c9df --stat`
shows four other files — `checks.md` (the sanctioned `Frozen at` sha append plus this section),
`plan.md` (new session artifact), and the two source files plus `docs/reflection.md` that Phase 1
named. No collateral edits. `origin/main` did not advance during the run, so no rebase occurred
and the freeze sha needed no re-anchoring.

Independently verified by a fresh context given only `checks.md` and the repo — not the plan, not
the implementation notes. Its per-check results: C1 pass (1 collected), G1 pass (2), G2 pass (1),
G3 pass (1) + empty diff, G5 3734 passed / 2 skipped / 0 failed (3736 collected). Every run
exited 0, none exited 5.

## Amendments

(Append-only.) **None.** No frozen check was edited, relaxed, or replaced at any point, so the
run's `auto-ok` tier stands undowngraded.
