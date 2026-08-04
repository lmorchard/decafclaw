# Spec — Reflection: a non-model_configs `reflection.model` is silently discarded when `default_model` is set

**Source:** https://github.com/lmorchard/decafclaw/issues/752

_Captured verbatim from the issue body (marker line stripped) at session setup, 2026-08-03._

---

Found while verifying #591 at triage, and deliberately **not** folded into that PR — fixing it
would change what #591's frozen check C2 branch (b) asserts.

## The bug

`evaluate_response` (`src/decafclaw/reflection.py`) resolves the judge model as:

```
verifier_model (new, #591) > reflection.model if in model_configs > default_model > legacy resolved()
```

If `reflection.model` is set to something that is **not** a `model_configs` key, and `default_model`
**is** set, the branch order silently discards `reflection.model` and uses `default_model`. The
legacy `reflection.resolved(config)` rung is only reachable when `default_model` is empty.

## Why it matters

`docs/reflection.md` documents exactly this as the recommended escape hatch:

```bash
decafclaw config set reflection.model gemini-2.5-flash
```

On any config that has a `default_model` — which is every modern config — that command does
nothing observable. The user believes they moved the judge to a cheap model; the judge keeps
running on the author's model, at the author's price.

## Why no existing test catches it

`tests/test_reflection.py::TestEvaluateResponse::test_uses_reflection_model` passes only because its
fixture leaves `default_model` empty, which routes it down the legacy rung. Add a `default_model` to
that fixture and it fails.

## Deciding the fix

Needs a judgment call rather than a patch, which is why it's its own issue:

- **(a)** Treat a non-`model_configs` `reflection.model` as a legacy raw model name and honor it over
  `default_model` — matches the documented behavior, but reorders the chain.
- **(b)** Keep the order and fix the docs to say `reflection.model` must be a `model_configs` key.
- **(c)** Warn at config load when `reflection.model` is set but unresolvable.

(b) is the smallest change; (a) is what the docs currently promise. Worth picking deliberately.

Related: #591 (introduced `verifier_model` above this rung), #529, #530, #589.

---

## Decision (Les, 2026-08-03): branch (a) — reorder the chain

Recorded in the body rather than a comment, because downstream modes read the body only.

**Branch (a) is the work: honor `reflection.model` even when it is not a `model_configs` key.**
Branches (b) and (c) are not the deliverable. Two notes that informed the call:

- **(b) is already substantially done.** `docs/reflection.md:90` states the `model_configs`
  constraint and line 56 already carries a caveat naming this issue, both landed during #591's
  triage. Choosing (b) would have meant closing with no code change, which is not what this issue
  is for. The stale *"see #752 for the fix"* pointer should be removed as part of (a).
- **(c) is coherent but separate.** Warning when `reflection.model` resolves nowhere at all is a
  reasonable second criterion in `config.py`; it is deliberately **not** in scope here. File it
  separately if wanted.

## Acceptance criteria

*Added by `agent-session` triage, 2026-08-03. Checks were run against `main` at scan time and the
observed result is recorded.*

- **CRITERION:** GIVEN a config where `reflection.model` names a model absent from `model_configs`
  AND `default_model` is set, WHEN `evaluate_response` runs, THEN the judge call SHALL route to
  `reflection.model` via the legacy `resolved()` url/model/api_key rung, and SHALL NOT use
  `default_model`.

  **CHECK:** `pytest tests/test_reflection.py::TestEvaluateResponse::test_nonconfig_reflection_model_beats_default_model`
  — to be authored at freeze, asserting the `call_llm` kwargs carry the configured reflection model
  rather than the `default_model` key.

  **OBSERVED (2026-08-03): fails today — discriminates.** Reproduced directly: with
  `reflection.model="gemini-2.5-flash"` (absent from `model_configs`) and
  `default_model="big-author"`, the patched `call_llm` received `{'model_name': 'big-author'}`.
  The configured reflection model was discarded, exactly as reported.

## Regression guards

- **GUARD:** the two already-covered judge-routing paths are preserved — a `verifier_model` present
  in `model_configs` still wins, and a `reflection.model` that *is* a `model_configs` key still
  routes to it.
  **CHECK:** `pytest tests/test_reflection.py::TestEvaluateResponse::test_uses_reflection_model tests/test_reflection.py::TestEvaluateResponse::test_verifier_model_routes_judge_call`
  **OBSERVED:** 2 passed.

- **GUARD:** `test_verifier_model_unset_preserves_fallback_chain` keeps passing **unmodified.**
  Checked deliberately, because #591's write-up says the fallback chain must be "unchanged" and
  that sounded like it would block this fix. It does not: that test covers (a) `reflection.model`
  *in* `model_configs`, (b) `reflection.model` *empty*, and (c) *no* `model_configs` — it never
  exercises the buggy case, which is `reflection.model` set but **absent** from `model_configs`
  with `default_model` set. This fix fills a gap in the chain rather than contradicting a pinned
  one. **If landing the fix requires editing this test, that is a signal the change reached
  further than intended.**

- **GUARD:** `ReflectionConfig.verifier_model`'s docstring (`src/decafclaw/config_types.py:243-250`)
  currently *documents this misordering as intended-for-now*. It must be updated in the same change
  rather than left contradicting the code.
  **CHECK:** none — this is a human read at review. Recorded so it is not dropped; deliberately not
  graded, because its only mechanical check would be a keyword grep.

- **GUARD:** no test lost, newly skipped, or newly failing (invariant, not a pinned count).
  **CHECK:** `make test` — **UNRUN at scan time** (triage runs targeted commands only). Not
  verified; the freeze phase must run it.

## Still open, but not tier-bearing

Under (a), does `reflection.model` also beat `verifier_model` when *the latter* is unresolvable?
Current code drops both to `default_model`. The issue is silent. This does not change which
criteria apply above, so it does not affect the tier — but answer it at review rather than
discovering it in a diff.

## Tier: auto-ok

**Trigger 1 no longer fires.** It did when this issue was scanned — the three-way fork was a goal
the loop would have had to pick rather than implement. Branch (a) is now chosen, and its criterion
was verified to fail today against a real reproduction, with the oracle (`tests/test_reflection.py`
and its `call_llm` patching idiom) already present.

**Trigger 2 does not fire.** Judge-model routing is not auth, secrets, data migration/deletion,
deploy/CI config, or a dependency change, and decafclaw's `CLAUDE.md` marks nothing here off-limits.
One thing for a reviewer's eye rather than a gate: `reflection.api_key` is marked
`metadata={"secret": True}`, and branch (a) newly routes through the legacy `resolved()` rung that
reads it. That is an existing code path being reached under a new condition, not a credentials
change.
