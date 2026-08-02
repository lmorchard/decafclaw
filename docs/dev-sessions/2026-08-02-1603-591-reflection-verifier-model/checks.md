# Frozen acceptance checks

**Source:** https://github.com/lmorchard/decafclaw/issues/591
**Frozen at:** `e99c860` (2026-08-02)
**Check files — read-only from Phase 1 onward:**
- `tests/test_reflection.py`

Criteria and checks are copied verbatim from the issue body.

> **Read C1/C2/C3 as ONE gate.** Only C1 discriminates the routing behavior. C2 and C3 flip green
> the moment the dataclass field exists, with zero routing logic — their fail-first at freeze is
> **field-derived, not behavior-derived** (both fail on
> `TypeError: ReflectionConfig.__init__() got an unexpected keyword argument 'verifier_model'`).
> A green C2 or C3 is therefore NOT evidence the feature works. C3 still earns its place: it kills
> the membership-check-free shortcut. If these are ever evaluated separately, C2 and C3 certify
> nothing. Established by the check-reviewer's decisive experiment, recorded under `## Adjudication`.

## C1

CRITERION: WHERE `reflection.verifier_model` is set to a key present in `config.model_configs`, WHEN
`evaluate_response` runs, the reflection judge SHALL issue its LLM call with
`model_name=<verifier_model>` AND SHALL NOT use `default_model` or the legacy url/model/api_key override.
CHECK: `uv run pytest tests/test_reflection.py::TestEvaluateResponse::test_verifier_model_routes_judge_call`
— constructs `Config` inline with `model_configs={'author':…, 'judge':…}`, `default_model='author'`,
`reflection.model=''`, `reflection.verifier_model='judge'`, patches `decafclaw.reflection.call_llm`,
and asserts `call_args.kwargs == {'model_name': 'judge'}`.

AT FREEZE: fails, exit 1 — `TypeError: ReflectionConfig.__init__() got an unexpected keyword
argument 'verifier_model'` (`tests/test_reflection.py:561`). Confirmed behavior-derived as well as
field-derived: with the field simulated (constructor monkeypatched, `reflection.py` untouched) it
still fails `AssertionError: assert {'model_name': 'author'} == {'model_name': 'judge'}`.
`1 failed in 0.07s`.

## C2

CRITERION: WHEN `reflection.verifier_model` is unset (empty), the reflection judge SHALL resolve
exactly as today — `reflection.model` if in `model_configs` → `config.default_model` → legacy
`reflection.resolved(config)` url/model/api_key.
CHECK: `uv run pytest tests/test_reflection.py::TestEvaluateResponse::test_verifier_model_unset_preserves_fallback_chain`
— asserts **all three branches** on the patched `call_llm`: (a) `reflection.model='judge'` in
`model_configs` → `{'model_name':'judge'}`; (b) `reflection.model=''`, `default_model='author'` →
`{'model_name':'author'}`; (c) `model_configs={}`, `default_model=''`,
`reflection.model='cheap-judge-model'` → kwargs contain `llm_model='cheap-judge-model'`.

AT FREEZE: fails, exit 1 — same `TypeError` (`tests/test_reflection.py:594`).
**Fail-first is field-derived only.** With the field simulated and `reflection.py` untouched, C2
passes. It is a preservation criterion; treat its green as "the existing chain still works," never
as "the feature works."

## C3

CRITERION: IF `reflection.verifier_model` names a key that is **NOT** in `config.model_configs`, THEN
the reflection judge SHALL fall back to today's chain AND SHALL NOT pass the unknown name as
`model_name`.
CHECK: `uv run pytest tests/test_reflection.py::TestEvaluateResponse::test_unknown_verifier_model_falls_back`

AT FREEZE: fails, exit 1 — same `TypeError` (`tests/test_reflection.py:652`).
**Fail-first is field-derived only**, but C3 is a live coupling constraint on C1's implementation:
against the naive `if vm: call_llm(..., model_name=vm)` shortcut it fails with
`AssertionError: assert {'model_name': 'no-such-model'} == {'model_name': 'author'}`.
Must never be evaluated as a standalone criterion.

## Guards

(Pass today; must keep passing. Not criteria — they can't fail at freeze.)

- **G1:** the pre-existing judge suite, with the three criterion nodes deselected:

  ```
  uv run pytest tests/test_reflection.py -q \
    --deselect "tests/test_reflection.py::TestEvaluateResponse::test_verifier_model_routes_judge_call" \
    --deselect "tests/test_reflection.py::TestEvaluateResponse::test_verifier_model_unset_preserves_fallback_chain" \
    --deselect "tests/test_reflection.py::TestEvaluateResponse::test_unknown_verifier_model_falls_back"
  ```

  Invariant: **44 passed, none skipped, none lost.** (Scoped at freeze per the check-reviewer: the
  issue's unscoped `pytest tests/test_reflection.py -q` became a strict superset of C1–C3 the moment
  the checks were authored into that file, so it cannot be red-to-green-neutral and cannot
  distinguish "a pre-existing judge test regressed" from "a criterion isn't done yet.")

- **G2:** `uv run pytest tests/test_config.py tests/test_config_cli.py -q` — protects the generic
  `load_sub_config` loader (`config.py:99-131`) and the generic `config set` CLI
  (`config_cli.py`, generic over `fields()`). Invariant: **71 passed, none skipped.**
  (`tests/test_config_cli.py` added at freeze; the issue's G2 named only `tests/test_config.py`
  while claiming to cover `config set`, which lives in a module that file never imports.
  **Overclaim removed:** nothing under `tests/` asserts `REFLECTION_VERIFIER_MODEL` — a repo-wide
  grep for `REFLECTION_[A-Z_]+` in `tests/` returns zero hits — so a green G2 is *not* evidence the
  env var reaches the new field. See "Coverage gap" below.)

- **G3:** `uv run pytest tests/test_agent_turn.py -q -k reflection` — protects the agent-loop
  integration of `evaluate_response` (`agent.py`). Invariant: **11 passed, none skipped.**
  Disjoint from the criterion nodes, so it is a real invariant.

- **G4 (invariant):** full suite, `make test`. **Evaluable only after C1–C3 are green** — `make test`
  collects `tests/test_reflection.py`, so today it necessarily reports the three known-failing
  criterion nodes. Invariant at the end of the run: **3720 passed, 2 skipped** (3717 pre-existing +
  the 3 criterion nodes), no test lost and no test newly skipped.

## Evidence at freeze

Re-run in this worktree at `2fab896` (branch base), before any implementation.

**Precondition (the gap is still there):**

```
uv run python -c "...dataclasses.fields(ReflectionConfig)..."
FIELDS ['enabled', 'url', 'model', 'api_key', 'max_retries', 'visibility', 'max_tool_result_len']
HAS_VERIFIER_MODEL False
```

**Criterion nodes absent before authoring** (all three, one invocation):
`ERROR: not found: …::test_verifier_model_routes_judge_call` (× 3 nodes), `no tests ran in 0.03s`,
**exit 4**. The issue recorded exit 5; exit 4 is what pytest returns when *explicitly named* node ids
don't resolve (usage error) versus 5 for an empty collection. Same absence, different invocation
shape — recorded as observed rather than as filed.

**Guards at freeze (all pass, with counts), using the scoped commands above:**

| Guard | Observed |
|---|---|
| G1 | `44 passed in 1.54s` |
| G2 | `71 passed in 1.39s` |
| G3 | `11 passed in 2.05s` |
| G4 | `3717 passed, 2 skipped in 27.39s` (pre-authoring baseline) |

## Coverage gap (a PR review note, not a criterion)

No check or guard asserts that `REFLECTION_VERIFIER_MODEL` (env) or
`decafclaw config set reflection.verifier_model` reach the new field. Both are *derived* from the
dataclass field by generic machinery (`config.py:461-462` calls
`load_sub_config(ReflectionConfig, file_data.get("reflection", {}), "REFLECTION")`;
`config_cli.py` iterates `fields()`), so they should come for free — but "should" is the word
doing the work, and nothing in the frozen set proves it. The implementer should add a unit test
covering it; that test is **implementer scaffolding, freely editable**, not part of the frozen set.

## Adjudication

Written at freeze, before the freeze commit, by a read-only check-reviewer subagent that was given
`checks.md` and the repo but not the plan, the spec, or the criteria's rationale.

Its decisive experiment: monkeypatch `ReflectionConfig.__init__` to accept and store
`verifier_model`, leave `reflection.py` untouched, run the three nodes → `1 failed, 2 passed`.
Second experiment: the membership-check-free shortcut `if vm: call_llm(..., model_name=vm)` → C3
fails.

- **C1: accepted** — the only check with teeth. Tried and failed to green it cheaply: field-only
  no-op → still fails; unconditional `model_name="judge"` hardcode → defeated by C2(b)/C2(c)/C3 in
  the same gate; a raising stub → `call_args` is `None` because `evaluate_response` is fail-open, so
  it fails too. Assertion shape is right: positive destination, equality (not membership) so a stray
  legacy override alongside `model_name` fails. *Noted and judged non-load-bearing:* `call_llm`'s
  overrides are positional-capable, so `call_llm(cfg, msgs, None, url, model, key, model_name="judge")`
  would green C1 while violating the SHALL-NOT half; closing it fully needs
  `assert len(call_args.args) == 2`. Left as-is because every `call_llm` call site in the repo passes
  those three by keyword. Recorded so it is not rediscovered as a surprise.
  → *greenable without the work by:* nothing constructible short of the real membership-checked branch.

- **C2: strengthened** (annotation, not assertion) — the reviewer's finding is that C2 has **no
  teeth against any routing behavior** and no added assertion can give it any, because there is no
  input on which today's behavior and the intended behavior differ on the `verifier_model=''` path.
  It recommended reclassifying it as a guard. **Declined the reclassification, took the fallback
  remedy it named**: C2 is an independently-authored criterion copied verbatim from the issue, and
  the implementer demoting it would be the implementer editing its own oracle. Instead the manifest
  now states, at the top and under C2, that its fail-first is field-derived and its green is not
  evidence of the feature. The three branch assertions themselves were judged well-shaped —
  equality on (a) and (b), and (c) correctly pairs positive `llm_model` with negative
  `'model_name' not in kwargs`.
  → *greenable without the work by:* adding `verifier_model: str = ""` and changing nothing else
  (verified: 2 passed).

- **C3: strengthened** (annotation) — same field-derived fail-first, but the reviewer confirmed it
  discriminates the most likely wrong implementation (routing on `verifier_model` without the
  `in config.model_configs` membership check). Assertion shape is the strong form: names the
  concrete fallback destination (`author`) rather than only asserting the unknown name is absent.
  Manifest now records that it is a coupling constraint on C1 and must not be evaluated standalone.
  → *greenable without the work by:* the field-only no-op (verified) — hence the coupling note.

- **G1: strengthened** — as filed in the issue it **did not pass at freeze** (`3 failed, 44 passed`),
  because authoring the checks into `tests/test_reflection.py` made the guard a superset of its own
  criteria. Command scoped with three `--deselect`s and the invariant restated as `44 passed`.
  Verified after scoping: `44 passed in 1.54s`.
  → *greenable without the work by:* n/a — the opposite problem; unscoped it could not be green today.

- **G2: strengthened** — passed as filed (`55 passed`) but overclaimed. `tests/test_config.py` never
  imports `config_cli.py`, so it cannot protect `config set`; and nothing under `tests/` asserts any
  `REFLECTION_*` env var. Added `tests/test_config_cli.py` to the command (`71 passed`) and removed
  the env-var claim from the guard's text, logging it instead as a coverage gap above.
  → *greenable without the work by:* it is green today and stays green even if the field is never
  added — which is exactly why the overclaim had to come out.

- **G3: accepted** — `11 passed`. All eleven nodes are agent-loop integration around
  `evaluate_response`, the one function being edited. Claims only blast-radius protection and
  delivers exactly that; disjoint from the criterion nodes.
  → *no cheaper green found.*

- **G4: strengthened** — same structural defect as G1: `make test` collects the criterion nodes, so
  the invariant was being measured against a suite containing three known failures
  (`3 failed, 3717 passed, 2 skipped`). Restated as evaluable only post-implementation, with the
  explicit end-state count `3720 passed, 2 skipped`.
  → *greenable without the work by:* n/a — cannot be green today.

## Independent verifier report

Dispatched with a fresh context, given `checks.md` and the repo only — not the plan, not the notes,
not the spec. Run against the implemented tree.

| id | observed | exit | verdict |
|---|---|---|---|
| C1 | `1 item` collected, `1 passed in 1.20s` | 0 | **pass** |
| C2 | `1 item` collected, `1 passed in 1.22s` | 0 | **pass** |
| C3 | `1 item` collected, `1 passed in 1.19s` | 0 | **pass** |
| G1 | `44 passed in 1.37s`, none skipped | 0 | **pass** — invariant met exactly |
| G2 | `73 passed in 1.43s`, 0 skipped | 0 | **FAIL against the stated invariant** (`71 passed`) |
| G3 | `11 passed in 1.99s`, none skipped | 0 | **pass** — invariant met exactly |
| G4 | `3724 items`, `3722 passed, 2 skipped in 13.20s` | 0 | **FAIL against the stated invariant** (`3720 passed, 2 skipped`) |

**Tamper verdict: clean.** `git merge-base --is-ancestor e99c860 HEAD` → `0`, so the freeze commit is
a real ancestor and the diff is meaningful. `git diff e99c860 -- tests/test_reflection.py` → **empty**.
`git diff e99c860 --stat` shows 8 files, none of them a check file; the only change to `checks.md`
itself is the one-line `Frozen at` sha, a sanctioned write.

**Project gate:** `make check` exit 0 — message-types drift clean, `ruff: All checks passed!`,
`pyright: 0 errors, 0 warnings`, `tsc --noEmit` clean.

**Verifier's discriminating-power assessment of this diff** (asked because a green check is not by
itself evidence): C1 **could not** be green without the routing work — the diff contains the real
membership-checked branch, and a field-only no-op still fails C1. C2 and C3 **could** be green
without it; both are explained by the dataclass field alone. This matches the freeze-time
adjudication exactly. C1 carries the gate.

### The two guard failures — NOT amended, surfaced for a human

Both are the same defect, and it is in the **manifest's wording**, not in the implementation: G2 and
G4 were frozen with *exact pass counts* as their invariants (`71 passed`, `3720 passed`). The
implementation then added two legitimate tests to `tests/test_config.py`
(`test_reflection_verifier_model_env_override`, `test_reflection_verifier_model_defaults_empty`),
which close the env-var coverage gap the check-reviewer logged at freeze. Both counts moved +2. The
verifier confirms **no test was lost and none newly skipped** — the deviation is purely additive.

The substantive invariant the issue actually wrote was *"no test lost, newly skipped, or newly
failing."* Hardening that into an exact count was done at freeze, in this manifest, by the
implementer's side of the run — and it is now too tight to survive adding a test.

**This has NOT been fixed, deliberately.** Restating the invariant would change the guard's verdict
against the current tree (fail → pass) while leaving it unchanged at the freeze tree, which lands in
the *amendment* cell of `frozen-checks.md`'s four-cell table, not the clarification cell. The
amendment path requires a human, and an amendment downgrades the run's tier. So the guards stand as
frozen, both are reported as **failing**, and the gate reads `human-merge-required` on that basis.

The proposed amendment, for a human to accept or reject, is in the PR body. Deleting the two tests
to make the numbers match was considered and rejected: removing real coverage to satisfy a
bookkeeping artifact is the exact cheat guards exist to catch.

## Amendments

(Append-only. Empty unless an amendment was made.)

None. One amendment is **proposed and unapproved** — see the section above. Nothing in this manifest
has been altered since the freeze except the `Frozen at` sha and these appended verdicts.
