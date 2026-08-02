# Reflection: shared verifier_model config (resolved through model_configs)

**Source:** https://github.com/lmorchard/decafclaw/issues/591

Captured verbatim from the issue body (agent-session:spec marker stripped).

## Context

Multiple reflection judges want to run on a model *distinct from the author* so the author isn't grading its own homework (cf. Osmani, ["loop engineering"](https://addyosmani.com/blog/loop-engineering/)). Today `evaluate_response` (`reflection.py`) resolves the judge model as `reflection.model` → else `config.default_model` → else legacy, which in practice means **same model as the author**.

There are now three consumers that all want a distinct verifier model:
- #529 — source-grounding judge
- #530 — pre-write edit-safety judge
- #589 — adversarial reflection mode

That's the threshold where a shared primitive is warranted. This issue establishes it so the three judges can be worked in any order without each re-inventing model routing.

## Proposal

- Add `reflection.verifier_model` to `ReflectionConfig` — a `model_configs` ref (named-model indirection, matching how `reflection.model` is intended to resolve).
- Resolution: when `verifier_model` is set and present in `config.model_configs`, all reflection judges route their LLM call through it. When unset, fall back to today's behavior (`reflection.model` → `default_model` → legacy) so nothing changes by default.
- Document the convention: the specialized judges (#529/#530) and adversarial reflection (#589) inherit `verifier_model` unless they set a more specific override.

## Acceptance

- `reflection.verifier_model` on `ReflectionConfig`, resolved through `model_configs`.
- Unit test: with `verifier_model` set, `evaluate_response`'s LLM call provably routes to it; with it unset, existing fallback chain is unchanged.
- No behavioral eval needed here (pure config/routing, no new LLM-visible behavior) — the judges that consume it carry the evals.

## Related

- #589 — adversarial reflection mode (blocked-by this)
- #529, #530 — specialized judges (inherit this)
- #409 — reflection telemetry

---

<!-- Appended by agent-session:triage on 2026-07-29. Author's text above is unchanged. -->

## Verified-false claims

**None — every stated fact checked out.** Verified individually at triage:

- `evaluate_response` exists at `src/decafclaw/reflection.py:264`.
- "resolves the judge model as `reflection.model` → else `config.default_model` → else legacy" is
  **exact** — `reflection.py:292-302`, where the comment literally reads
  `# Route through named model config: explicit > default_model > legacy`.
- "in practice means same model as the author" — verified by probe: with `reflection.model` unset and
  `default_model="author"`, the patched `call_llm` received `{'model_name': 'author'}`.
- `reflection.verifier_model` is genuinely **absent** today. `ReflectionConfig` fields are
  `['enabled', 'url', 'model', 'api_key', 'max_retries', 'visibility', 'max_tool_result_len']`.
- The three consumers #529/#530/#589 all exist and are OPEN; **none of their code exists yet**
  (`rg source_grounding|edit_safety|adversarial src/` → no hits), so today there is exactly one
  reflection judge call site.
- `model_configs` / `default_model` on `Config` at `config.py:179-180`; `reflection.model` doubles as
  either a `model_configs` key or a raw legacy model name.

**Oracle preconditions, both verified:** model resolution is a **pure function** —
`resolve_model()` at `src/decafclaw/config.py:281-308` does dict lookups over `config.model_configs` /
`config.providers`, raises `KeyError`, no I/O. **No credentials or network are needed** to check any
criterion below: `resolve_model` never inspects `ProviderConfig.api_key`, and the criteria patch
`decafclaw.reflection.call_llm` so the harness-state-dependent provider registry
(`llm/registry.py`'s module-global `_providers`, populated by `init_providers(config)`) is never
consulted. Each test constructs `Config(...)` inline and never calls `load_config()`.

## Verifiable acceptance criteria

- CRITERION: WHERE `reflection.verifier_model` is set to a key present in `config.model_configs`, WHEN
  `evaluate_response` runs, the reflection judge SHALL issue its LLM call with
  `model_name=<verifier_model>` AND SHALL NOT use `default_model` or the legacy url/model/api_key override.
  CHECK: `uv run pytest tests/test_reflection.py::TestEvaluateResponse::test_verifier_model_routes_judge_call`
  — constructs `Config` inline with `model_configs={'author':…, 'judge':…}`, `default_model='author'`,
  `reflection.model=''`, `reflection.verifier_model='judge'`, patches `decafclaw.reflection.call_llm`,
  and asserts `call_args.kwargs == {'model_name': 'judge'}`.
  VERIFIED DISCRIMINATING: `no tests ran in 2.19s`, **exit 5**. Supporting evidence the field is absent:
  `uv run python -c "...fields(ReflectionConfig)..."` → `HAS_VERIFIER_MODEL False`.
  ORACLE_EXISTS: yes — the `TestEvaluateResponse` harness is at `tests/test_reflection.py:454-462`
  (fixture) with the `patch("decafclaw.reflection.call_llm")` pattern at `:465-537`. An equivalent
  probe was run in-process at triage and worked.

- CRITERION: WHEN `reflection.verifier_model` is unset (empty), the reflection judge SHALL resolve
  exactly as today — `reflection.model` if in `model_configs` → `config.default_model` → legacy
  `reflection.resolved(config)` url/model/api_key.
  CHECK: `uv run pytest tests/test_reflection.py::TestEvaluateResponse::test_verifier_model_unset_preserves_fallback_chain`
  — asserts **all three branches** on the patched `call_llm`: (a) `reflection.model='judge'` in
  `model_configs` → `{'model_name':'judge'}`; (b) `reflection.model=''`, `default_model='author'` →
  `{'model_name':'author'}`; (c) `model_configs={}`, `default_model=''`,
  `reflection.model='cheap-judge-model'` → kwargs contain `llm_model='cheap-judge-model'`.
  VERIFIED DISCRIMINATING: node absent today → exit 5. Branches (a) and (b) were confirmed to be
  current behaviour by probe; branch (c) is already covered by the existing `test_uses_reflection_model`
  (`tests/test_reflection.py:503-513`), which passes today.
  Not satisfiable without the work — a stub would not produce these three distinct kwarg dicts.

- CRITERION: IF `reflection.verifier_model` names a key that is **NOT** in `config.model_configs`, THEN
  the reflection judge SHALL fall back to today's chain AND SHALL NOT pass the unknown name as
  `model_name`.
  CHECK: `uv run pytest tests/test_reflection.py::TestEvaluateResponse::test_unknown_verifier_model_falls_back`
  VERIFIED DISCRIMINATING: node absent today → exit 5.
  This is the issue's own wording ("set **and present in** `config.model_configs`") turned into an
  assertion. **Without it, the cheapest implementation silently hands an unknown name to `_resolve`,
  which logs a warning and falls through to the default provider** (`llm/__init__.py:106-107`).

### Deliberately rejected as a criterion

"Document the convention that #529/#530/#589 inherit `verifier_model`" (Proposal bullet 3). The only
available check is `rg -q 'verifier_model' docs/config.md docs/reflection.md` — **a keyword grep,
satisfiable by typing the word**; whether the *convention* is explained is a human read. There is also
no docs↔dataclass sync test in this repo (`rg -ln config.md tests/` → no hits), and `docs/config.md:214-221`
already omits the existing `max_tool_result_len` field, so no existing gate would catch it either.
**Treat as a review note on the PR, not a criterion.**

## Regression guards

- GUARD: `uv run pytest tests/test_reflection.py -q` — protects the whole judge path including the
  existing fallback-chain test. Observed at triage: `44 passed in 2.76s`. Invariant: no test lost,
  newly skipped, or newly failing.
- GUARD: `uv run pytest tests/test_config.py -q` — protects the generic `load_sub_config` loader
  (`config.py:99-131`), which derives the `REFLECTION_VERIFIER_MODEL` env var and
  `config set reflection.verifier_model` **for free** from the dataclass field, so nothing should need
  registering. Observed at triage: `55 passed in 2.47s`.
- GUARD: `uv run pytest tests/test_agent_turn.py -q -k reflection` — protects the agent-loop
  integration of `evaluate_response` (`agent.py:1008-1045`). Observed at triage: `11 passed in 2.49s`.
- GUARD (invariant): full suite — no test lost, newly skipped, or newly failing.
  **UNRUN (needs a serial full-suite run).** Do not read as verified.

## Tier: `auto-ok`

Neither trigger fires.

**Trigger 1:** all three criteria pair with concrete-example unit tests whose harness exists today —
verified by running an equivalent probe in-process (inline `Config`, patched `call_llm`, no
`load_config()`, no provider registry, no network, no API key). Each fails today (exit 5, node absent),
and none is satisfiable by a stub because the criterion **states the exact kwargs to assert**.

**Trigger 2:** no risk-gated path. `verifier_model` is a `model_configs` key, **not a credential**;
provider credentials keep resolving through the unchanged `config.providers` mechanism
(`resolve_model`, `config.py:299-306`), and `ReflectionConfig.api_key`'s `metadata={"secret": True}` is
untouched. No migration/deletion, no CI/infra, no dependency change.

## One clarification worth a one-line answer

When **both** `reflection.verifier_model` and `reflection.model` are set, the Proposal says
`verifier_model` wins ("all reflection judges route their LLM call through it"), while the
document-the-convention bullet says judges inherit it "unless they set a more specific override" —
which could be read as `reflection.model` winning for the base judge. **The first criterion above
assumes `verifier_model` wins.** If that reading is wrong, its assertion flips.

## Latent bug found while verifying — do NOT fold into this issue

Pre-existing, not claimed by the issue, and **this issue's work will run straight through it**: at
`reflection.py:292-296`, if `reflection.model` is set but is **not** a `model_configs` key while
`default_model` **is** set, the branch order silently discards `reflection.model` and uses
`default_model`. **The documented "cheap judge model" escape hatch (`docs/reflection.md:53`,
`decafclaw config set reflection.model gemini-2.5-flash`) is therefore dead on any modern config.**
The existing `test_uses_reflection_model` only passes because its fixture leaves `default_model` empty.

Worth its own issue. **Do not fix it silently here** — "fixing" it would change the second criterion's
branch (b), which is exactly the amendment-vs-clarification trap.

## Scope notes

- With #529/#530/#589 all unimplemented, "shared primitive" has **exactly one consumer today**. A
  structural criterion ("resolution lives in one helper, no judge re-implements it") is **not freezable
  now** — the second consumer does not exist, so any grep count is 1 either way. Leave the extraction
  shape to the implementer and let #529 be the issue that proves reuse.
- The eval-loop judge (`src/decafclaw/eval/reflect.py:96-100`, driven by `--judge-model`, a CLI arg at
  `eval/__main__.py:96`) is a **separate** grader-model mechanism that reads no config field. If the
  intent is one verifier convention repo-wide, that call site is out of scope as written and should be
  named explicitly in a follow-up rather than assumed.
- Board size S looks right: one dataclass field + one branch + three test cases.
