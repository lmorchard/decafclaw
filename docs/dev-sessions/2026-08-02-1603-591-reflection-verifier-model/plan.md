# Reflection `verifier_model` Implementation Plan

**Goal:** Give the reflection judge a shared, opt-in `verifier_model` config ref so judges can run on
a model distinct from the author's, without changing any default behavior.

**Source issue:** https://github.com/lmorchard/decafclaw/issues/591 — **Tier:** `auto-ok`
(all three criteria pair with concrete unit tests whose harness exists and which fail today; no
risk-gated path — `verifier_model` is a `model_configs` key, not a credential).

**Approach:** Add one field to `ReflectionConfig` and one branch at the head of the existing routing
chain in `evaluate_response`. The branch fires only when the field is non-empty **and** the name is a
key in `config.model_configs`; otherwise control falls through to today's chain untouched. Env var
(`REFLECTION_VERIFIER_MODEL`) and `config set reflection.verifier_model` come for free from the
generic `load_sub_config` / `config_cli` machinery — but "for free" is a claim, so Phase 1 adds a unit
test that proves it.

**Criteria:** C1 set-and-known → routes to it · C2 unset → today's chain unchanged · C3 set-but-unknown
→ falls back, never passed as `model_name`.
Full text, checks, guards, and adjudication live in `checks.md`. Ids are assigned there.

**Read `checks.md`'s gate note before executing.** Only C1 discriminates the routing behavior; C2 and
C3 go green on the dataclass field alone. Do not read "2 of 3 passing" as progress.

---

## Phase 0: Freeze the acceptance checks — DONE

Written and committed before any implementation, per `references/frozen-checks.md`.

**Files:**
- Created: `docs/dev-sessions/2026-08-02-1603-591-reflection-verifier-model/checks.md`
- Created: `docs/dev-sessions/2026-08-02-1603-591-reflection-verifier-model/spec.md`
- Modified: `tests/test_reflection.py` — the three tests C1–C3 name (**read-only from Phase 1 onward**)

**Verification — automated:**
- [x] Every criterion's check runs and fails for the expected reason — all three fail exit 1 with
      `TypeError: ReflectionConfig.__init__() got an unexpected keyword argument 'verifier_model'`;
      C1 additionally confirmed behavior-derived (`{'model_name': 'author'} != {'model_name': 'judge'}`
      with the field simulated)
- [x] Every guard runs and passes — G1 `44 passed`, G2 `71 passed`, G3 `11 passed`, G4 `3717 passed,
      2 skipped`. G1 and G4 were **rescoped at freeze** (see the adjudication): as filed they were
      supersets of their own criteria and could not pass.
- [x] Check-reviewer dispatched read-only, without this plan or the criteria's rationale;
      `## Adjudication` carries one disposition per check and per guard
- [x] Freeze commit `e99c860`; sha recorded in `checks.md` by follow-up commit `034e739`

---

## Phase 1: `verifier_model` field + routing branch + docs

One vertical slice: config dataclass → routing logic → the derived config surfaces → user-facing docs.
Kept as a single phase because the change has one behavior and splitting it would leave a phase
advancing no criterion.

**Advances:** C1, C2, C3 — completely. Nothing remains for a later phase.

**Files:**
- Modify: `src/decafclaw/config_types.py` — add `verifier_model` to `ReflectionConfig` (~line 239)
- Modify: `src/decafclaw/reflection.py` — new leading branch in `evaluate_response`'s routing block
  (~line 291)
- Modify: `docs/config.md` — add the field to the `reflection` table (~line 214)
- Modify: `docs/reflection.md` — document the judge-model convention under "Judge model" (~line 71)
- Test: `tests/test_config.py` — **new** unit test that `REFLECTION_VERIFIER_MODEL` reaches the field.
  Implementer scaffolding, freely editable. NOT part of the frozen set. This closes the coverage gap
  the check-reviewer logged: nothing under `tests/` asserts any `REFLECTION_*` env var today.
- **Do NOT modify** `tests/test_reflection.py` — frozen at `e99c860`. A failing frozen check is a
  report-back, not a fix-up.

**Key changes:**

`ReflectionConfig` gains one field. Placed after `model` so the two model knobs read together:

```python
@dataclass
class ReflectionConfig:
    enabled: bool = True
    url: str = ""       # empty = resolve from llm
    model: str = ""     # empty = resolve from llm
    # A model_configs key. When set AND present in config.model_configs, every
    # reflection judge routes through it, so the author does not grade its own
    # homework (#591). Empty = today's chain (model -> default_model -> legacy).
    verifier_model: str = ""
    api_key: str = field(default="", metadata={"secret": True})
    ...
```

`evaluate_response`'s routing block gains one leading branch. The rest is **unchanged** — that is
what C2 asserts:

```python
        # Route through named model config:
        #   verifier_model > reflection.model > default_model > legacy
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

The `and verifier in config.model_configs` half is load-bearing, not defensive: C3 fails without it
(verified at freeze — the shortcut routes `no-such-model` through as `model_name`). It also matches
the issue's own wording, "set **and present in** `config.model_configs`".

`verifier_model` wins over `reflection.model` when both are set. The issue flagged this as its one
ambiguity and pinned it: C1's assertion assumes `verifier_model` wins.

The new config test:

Mirrors `test_vault_guide_env_override` (`tests/test_config.py:109-116`) exactly — same
`(self, tmp_path, monkeypatch)` signature, same `DATA_HOME` pin, same bare `load_config()`:

```python
    def test_reflection_verifier_model_env_override(self, tmp_path, monkeypatch):
        """Env prefix REFLECTION_* reaches the new verifier_model field."""
        monkeypatch.setenv("REFLECTION_VERIFIER_MODEL", "judge")
        monkeypatch.setenv("DATA_HOME", str(tmp_path))
        c = load_config()
        assert c.reflection.verifier_model == "judge"
```

Place it in the same env-override test class the `vault_guide` case lives in.

**Docs — say what the convention IS, not just that the field exists.** The check-reviewer noted the
only available check here is a keyword grep, which is satisfiable by typing the word; the issue
deliberately rejected it as a criterion and left it a PR review note. So:
- `docs/config.md`: one table row, `verifier_model | str | `""` | REFLECTION_VERIFIER_MODEL |`.
- `docs/reflection.md`, under "Judge model": state that `verifier_model` is a `model_configs` key that
  takes precedence over `reflection.model`; that it exists so the judge can differ from the author;
  and that future specialized judges (#529 source-grounding, #530 edit-safety, #589 adversarial)
  inherit it unless they set a more specific override.

**Out of scope — do not touch:**
- The latent branch-order bug at `reflection.py:292-296` (a set-but-unknown `reflection.model` is
  silently discarded when `default_model` is set, killing the documented "cheap judge model" escape
  hatch). Pre-existing; the issue says explicitly not to fold it in, and fixing it would change what
  C2 branch (b) asserts. **File a follow-up issue instead** — see Phase 2.
- `src/decafclaw/eval/reflect.py`'s `--judge-model` CLI arg. A separate grader-model mechanism that
  reads no config field; the issue puts it out of scope.
- Any extraction of routing into a shared helper. The issue's scope notes are explicit: with
  #529/#530/#589 unimplemented there is exactly one consumer, so a "shared primitive" abstraction is
  unfreezable today. Let #529 prove the reuse.

**Verification — automated:**
- [x] C1's check passes — `1 passed in 0.03s`:
      `uv run pytest tests/test_reflection.py::TestEvaluateResponse::test_verifier_model_routes_judge_call`
- [x] C2's check passes — `1 passed in 0.03s`:
      `uv run pytest tests/test_reflection.py::TestEvaluateResponse::test_verifier_model_unset_preserves_fallback_chain`
- [x] C3's check passes — `1 passed in 0.02s`:
      `uv run pytest tests/test_reflection.py::TestEvaluateResponse::test_unknown_verifier_model_falls_back`
- [x] G1 passes — **`44 passed in 1.21s`**, none skipped:
      `uv run pytest tests/test_reflection.py -q --deselect "tests/test_reflection.py::TestEvaluateResponse::test_verifier_model_routes_judge_call" --deselect "tests/test_reflection.py::TestEvaluateResponse::test_verifier_model_unset_preserves_fallback_chain" --deselect "tests/test_reflection.py::TestEvaluateResponse::test_unknown_verifier_model_falls_back"`
- [x] G2 passes — **`73 passed in 1.59s`**, none skipped:
      `uv run pytest tests/test_config.py tests/test_config_cli.py -q`
      (73, not the predicted 72: two config tests were added, not one — the env-override case plus a
      default-is-empty case. Recorded as observed.)
- [x] G3 passes — **`11 passed in 2.12s`**: `uv run pytest tests/test_agent_turn.py -q -k reflection`
- [x] G4: `make test` — **`3722 passed, 2 skipped in 13.44s`** (3717 baseline + 3 criterion nodes +
      2 new config tests), none lost, none newly skipped
- [x] `make check` passes — `ruff: All checks passed!`, `pyright: 0 errors, 0 warnings`,
      message-types drift check clean, `tsc --noEmit` clean
- [x] Tamper diff empty: `git diff e99c860 -- tests/test_reflection.py` → no output

**Verification — manual:**
- [ ] No human-judgment criterion in this set — nothing to grade at the gate on behavior.
- [ ] Read `docs/reflection.md`'s new paragraph as a user: does it explain the *convention*
      (`verifier_model` beats `reflection.model`; #529/#530/#589 inherit it), or does it just name
      the field? This is the issue's deliberately-rejected criterion, carried as a PR review note.

---

## Phase 2: File the follow-up issue for the latent branch-order bug

Not a code phase. The issue instructs that the latent bug be surfaced separately rather than fixed
here, and "surface it" is only done when there is a record.

**Advances:** no criterion — deliberately. This is the issue's own out-of-scope instruction being
honored, not scope creep; it ships zero code and touches zero file in the repo.

**Files:** none.

**Verification — automated:**
- [x] **Filed as https://github.com/lmorchard/decafclaw/issues/752.** A GitHub issue exists
      describing: at `reflection.py:292-296`, a `reflection.model` that is set
      but is not a `model_configs` key is silently discarded whenever `default_model` is set, so the
      documented escape hatch at `docs/reflection.md:53`
      (`decafclaw config set reflection.model gemini-2.5-flash`) is dead on any modern config. Note
      that `test_uses_reflection_model` only passes because its fixture leaves `default_model` empty.
- [ ] The new issue number is linked from PR #591's body.
