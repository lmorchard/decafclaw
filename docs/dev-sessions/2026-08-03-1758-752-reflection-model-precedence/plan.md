# Reflection judge-model precedence (#752) Implementation Plan

**Goal:** Honor `reflection.model` even when it is not a `model_configs` key, so the documented
escape hatch (`decafclaw config set reflection.model gemini-2.5-flash`) actually moves the judge.

**Source issue:** https://github.com/lmorchard/decafclaw/issues/752 — **Tier:** `auto-ok`
(every criterion is machine-checkable and was verified to fail today; no risk-gated path — judge
model routing is not auth, secrets, data migration, deploy/CI, or a dependency change).

**Approach:** Branch (a) from the issue, chosen by Les on 2026-08-03: reorder the chain so a
`reflection.model` that is *not* a `model_configs` key reaches the legacy `resolved()` rung
instead of losing to `default_model`. `verifier_model` keeps its position at the top —
unconditionally, per #591 — so the only rung that moves is `reflection.model`'s non-`model_configs`
case, which today is unreachable whenever `default_model` is set. Branches (b) and (c) from the
issue are explicitly not the deliverable.

**Criteria:** C1 — a `reflection.model` absent from `model_configs` beats `default_model` and
routes via the legacy `resolved()` url/model/api_key rung.

Full text + checks live in `checks.md`. Ids are assigned there and referenced here.

---

## Phase 0: Freeze the acceptance checks — COMPLETE

Frozen at `d34c9df`; sha recorded in `65bf103`. No implementation in this phase.

**Files:**
- Created: `docs/dev-sessions/2026-08-03-1758-752-reflection-model-precedence/checks.md`
- Created: `docs/dev-sessions/2026-08-03-1758-752-reflection-model-precedence/spec.md`
- Modified: `tests/test_reflection.py` — C1's test plus the new G2 guard (99 insertions, 0 deletions)

**Verification — automated:**
- [x] C1's check runs and fails for the expected reason — 1 collected, 1 failed,
      `assert {'model_name': 'author'} == {...legacy kwargs...}` at `tests/test_reflection.py:694`,
      with `call_count == 1` passing immediately before it (so: routing defect, not a setup error)
- [x] Every guard runs and passes — G1 2 passed, G2 1 passed, G3 1 passed, G5 3732 passed /
      2 skipped. G4 is a human read, not runnable.
- [x] Check-reviewer dispatched read-only (no Edit/Write), given `checks.md` and the repo but not
      this plan and not the criteria's rationale; `## Adjudication` carries one disposition per
      check and per guard
- [x] Freeze commit made; sha recorded in the follow-up commit

---

## Phase 1: Reorder the chain, and correct every place that documents the old order

One slice: the routing change, the inline comment that currently states the old behaviour as
intended, and the user-facing docs that promise the order this changes. They ship together because
the docs *are* the reported harm — the issue is that `docs/reflection.md` documents an escape hatch
that does nothing.

**Advances:** C1 (fully). Satisfies G4, which is a guard rather than a criterion because it can
only be graded by a human read.

**Files:**

- Modify: `src/decafclaw/reflection.py` (~line 291-310) — move the non-`model_configs`
  `reflection.model` case above the `default_model` rung.
- Modify: `src/decafclaw/config_types.py:243-250` — the `verifier_model` comment's last sentence
  currently states the #752 misordering as intended-for-now. (G4 site 1.)
- Modify: `docs/reflection.md:56` — the caveat under `decafclaw config set reflection.model ...`,
  including the stale *"see #752 for the fix"* pointer. (G4 site 2.)
- Modify: `docs/reflection.md:88-92` — the resolution-order table; the `reflection.model` and
  `default_model` rows both change. (G4 site 3.)
- Verify: `docs/config.md:212` — a pointer into that table. Its own claim is about `verifier_model`
  and stays true, so it likely needs no edit; confirm rather than assume. (G4 site 4.)
- Test: none added. The frozen acceptance tests already cover this slice and are **read-only from
  here on** — `tests/test_reflection.py` is the frozen check file.

**Key changes:**

The current chain in `evaluate_response`:

```python
        verifier = config.reflection.verifier_model
        rc_model = config.reflection.model
        if verifier and verifier in config.model_configs:
            response = await call_llm(config, messages, model_name=verifier)
        elif rc_model and rc_model in config.model_configs:
            response = await call_llm(config, messages, model_name=rc_model)
        elif config.default_model:
            response = await call_llm(config, messages, model_name=config.default_model)
        else:
            rc = config.reflection.resolved(config)
            response = await call_llm(
                config, messages,
                llm_url=rc.url, llm_model=rc.model, llm_api_key=rc.api_key,
            )
```

becomes — the only structural change is one added conjunct on the `default_model` rung, which
demotes it below an explicitly configured `reflection.model`:

```python
        verifier = config.reflection.verifier_model
        rc_model = config.reflection.model
        if verifier and verifier in config.model_configs:
            response = await call_llm(config, messages, model_name=verifier)
        elif rc_model and rc_model in config.model_configs:
            response = await call_llm(config, messages, model_name=rc_model)
        elif config.default_model and not rc_model:
            response = await call_llm(config, messages, model_name=config.default_model)
        else:
            # Reached two ways: reflection.model is set but is not a
            # model_configs key — a legacy raw model name, which outranks
            # default_model, because moving the judge off the author's model is
            # the whole point of setting it (#752) — or nothing above resolved
            # and resolved() supplies the llm-group fallback.
            rc = config.reflection.resolved(config)
            response = await call_llm(
                config, messages,
                llm_url=rc.url, llm_model=rc.model, llm_api_key=rc.api_key,
            )
```

**Self-review changed this shape.** The first draft added a fifth branch (`elif rc_model:` with
its own `resolved()` call) above `default_model`. Behaviourally identical, but it duplicates the
legacy call site — and that is the one rung that reads `reflection.api_key`, a field marked
`metadata={"secret": True}`. Two call sites for a credential read is a worse trade than one
slightly compound condition, so the added conjunct wins. The four cases both shapes must agree on:
`rc_model` in `model_configs` → rung 2; `rc_model` set but absent → legacy; `rc_model` empty with
`default_model` → rung 3; `rc_model` empty without `default_model` → legacy.

Two properties this shape preserves deliberately:

- **`verifier_model` stays on top and stays conditional on `model_configs` membership.** G2 pins
  this. An unresolvable `verifier_model` still falls through, which is what
  `test_unknown_verifier_model_falls_back` asserts.
- **The `resolved()` call is what routes**, not a direct read of `config.llm`. C1's second config
  pins this: `reflection.url` / `reflection.api_key`, when set, must reach the provider.

The duplicated `else` tail is acceptable at this size; collapsing it would mean restructuring the
chain further than the criterion asks, and scope discipline says no. It is two calls with
identical bodies, both reached only when `resolved()` is the answer.

**Verification — automated:**
- [ ] C1's check passes: `pytest tests/test_reflection.py::TestEvaluateResponse::test_nonconfig_reflection_model_beats_default_model`
- [ ] G1 passes: `pytest tests/test_reflection.py::TestEvaluateResponse::test_uses_reflection_model tests/test_reflection.py::TestEvaluateResponse::test_verifier_model_routes_judge_call`
- [ ] G2 passes: `pytest tests/test_reflection.py::TestEvaluateResponse::test_verifier_model_outranks_nonconfig_reflection_model`
- [ ] G3 passes: `pytest tests/test_reflection.py::TestEvaluateResponse::test_verifier_model_unset_preserves_fallback_chain`
- [ ] G3's diff half: `git diff d34c9df -- tests/test_reflection.py` shows no hunk between
      `def test_verifier_model_unset_preserves_fallback_chain` and the next `def`
- [ ] G5: `make test` reports `passed >= 3732`, `skipped == 2`, `failed == 0`
- [ ] `make lint` passes
- [ ] `make check` passes (lint + pyright + JS typecheck)

**Verification — manual:**
- [ ] G4 (human read, four sites): `config_types.py:243-250`, `docs/reflection.md:56`,
      `docs/reflection.md:88-92`, `docs/config.md:212`. Each must describe the new order, and no
      remaining text may point at #752 as an open fix.
- [ ] The "still open, but not tier-bearing" question from the spec is answered in the PR body
      rather than left to be discovered in the diff: under branch (a), does `reflection.model`
      also beat an *unresolvable* `verifier_model`? (It does, and that falls out of the branch
      order rather than needing its own code: an unresolvable `verifier_model` fails its
      membership test, and the `rc_model` rung is now next. `test_unknown_verifier_model_falls_back`
      keeps passing because it sets `model=""`.)

**Evals:** none. Per `CLAUDE.md`, evals cover LLM-driven decisions; this is deterministic routing
with no change to any tool description or system prompt. No `tool_choice` case applies.

---

## Phase 2: Independent verification

Dispatch a verifier subagent with a fresh context, given only `checks.md` and the repo — not this
plan, not the implementation notes. It runs each criterion and guard by its own command and reports
observed output plus the tamper diff against `d34c9df`.

**Advances:** C1 — grades it; does not implement it. (This phase advances no criterion by writing
code, deliberately: it is the gate, and per `frozen-checks.md` the grading context must not be the
one that wrote the code.)

**Verification — automated:**
- [ ] Verifier's report carries a per-criterion command + observed output + pass/fail
- [ ] Tamper diff `git diff d34c9df -- tests/test_reflection.py` is empty
