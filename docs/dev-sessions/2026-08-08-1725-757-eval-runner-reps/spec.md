
Filed by an `agent-session` triage pass, 2026-08-03. **This is a blocker discovered three times
independently**, once per issue, by scanners that could not see each other's work: #676, #695 and
#693 all need a K-of-N pass rate before their "done" can be graded honestly, and no such capability
exists. Each was tiered `needs-review` partly because of it.

## The problem

Every eval-shaped issue in this repo eventually reduces to "the case passes now". That is not a
check a loop can trust, because the sample is **n=1 per case**:

- `src/decafclaw/eval/tool_choice/runner.py` does exactly one `call_llm` per case and sets
  `passed = (picked == case.expected)`; `__main__.main` returns 0 only if all pass.
- `src/decafclaw/eval/__main__.py` accepts only `--model --judge-model --verbose --concurrency
  --history --history-limit`. There is **no repeat flag and no per-case filter**, so "run this one
  case N times" is not expressible.
- `src/decafclaw/eval/tool_choice/__main__.py` offers only `--model --models --include-mcp --matrix
  --verbose --concurrency`. `grep -n "reps\|repeat\|trials" src/decafclaw/eval/tool_choice/*.py`
  returns nothing.
- `evals/history.jsonl` records one aggregate row per run, not per-case rep counts.

The consequence is concrete and measured. On #693 the dropped case fired on roughly 10-15% of ~25
real runs — so **a single run passes 85-90% of the time with no fix at all**. A green result is not
evidence. On #702/#695 the same case failed 1-in-5 and 3/3 in isolation, which cannot distinguish a
20% flake from a 5% one.

## Acceptance criteria

*The oracle here is a mocked LLM, so none of this is model-dependent or costs anything to run.*

- **CRITERION 1:** GIVEN `--reps N`, WHEN a case is run, THEN `call_llm` SHALL be invoked exactly N
  times for that case and the result SHALL carry a per-case pass count out of N.
  **CHECK:** a new node in `tests/test_eval_tool_choice_runner.py` using the existing
  `patched_call_llm` monkeypatch fixture (line 96), asserting the invocation count and the
  aggregated rate.
  **OBSERVED (2026-08-03): fails today — discriminates.** No reps flag and no aggregation exist;
  the fixture that makes this testable without a model does exist.

- **CRITERION 2:** GIVEN a mocked LLM scripted to return the expected tool on exactly 3 of 5
  invocations, THEN the reported rate for that case SHALL be `3/5`.
  **CHECK:** same file — a scripted-sequence test asserting the exact fraction, not merely that
  some rate is reported.
  **OBSERVED: fails today** (no aggregation to assert against). This criterion exists because
  "a rate is reported" is an existence proxy satisfiable by hardcoding `1/1`.

- **CRITERION 3:** GIVEN a per-case name filter, WHEN it is supplied, THEN only matching cases SHALL
  be run, and a filter matching nothing SHALL exit non-zero with a message naming the filter —
  **not** silently report success over an empty set.
  **CHECK:** two nodes — one asserting only the matching case ran, one asserting the empty-match
  exit status and message.
  **OBSERVED: fails today** (no filter exists). The empty-match half is deliberate: "0 cases, all
  passed" is the exact shape of a check that grades nothing, and this repo has been bitten by a
  detector that examined an empty set before.

## Regression guards

- **GUARD:** the tool-choice harness tests stay green — no test lost, newly skipped, or newly
  failing.
  **CHECK:** `pytest tests/test_eval_tool_choice_runner.py`
  **OBSERVED:** 13 passed.

- **GUARD:** default behavior is unchanged when `--reps` is omitted — one call per case, same exit
  semantics. The flag must be **opt-in**, so existing baselines are not silently re-measured.
  **CHECK:** existing runner tests pass unmodified (they assert one call per case today).

- **GUARD:** no test lost, newly skipped, or newly failing (invariant, not a pinned count).
  **CHECK:** `make test` — **UNRUN at scan time.** The freeze phase must run it.

## Explicitly NOT in scope

**Choosing N or the pass threshold for any particular eval.** This issue builds the capability to
*measure* a rate; deciding what rate counts as "fixed" is a human call belonging to each dependent
issue. Folding a threshold in here would fire trigger 1.

**Wiring any eval into a pre-merge check.** That is CI config and fires trigger 2. Out of scope.

## Unblocks

- **#676** — `make eval-tools` has failing disambiguation cases; needs a rate, and currently nothing
  even persists which cases failed.
- **#695** — `evals/skill-authoring.yaml` first case; the only honest criterion is K-of-N.
- **#693** — `workspace_delete` guessing; at a 10-15% base rate, one run cannot discriminate.

## Tier: auto-ok

**Trigger 1 does not fire.** Ordinary CLI plus aggregation, graded by unit tests with a mocked LLM —
no model in the loop, nothing nondeterministic, and the mocking fixture already exists. Nothing is
withheld: the criteria say exactly what to assert.

**Trigger 2 does not fire.** Eval-harness code only. No auth, secrets, migration/deletion,
deploy/CI config, or dependency change, and none of decafclaw's risk cluster.

