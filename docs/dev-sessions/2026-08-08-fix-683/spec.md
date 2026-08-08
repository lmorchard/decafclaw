

Found while diagnosing #670. **This was not the cause of #670** — both tools in that investigation were active in both harnesses — but it is a real fidelity gap in how we measure tool disambiguation, and it should be a deliberate choice rather than an accident.

## The gap

`tool_choice` presents every tool as callable. The real agent's first turn does not.

Measured in a worktree off `00334d7` (`tmp/probe_loadout.py`, no LLM calls — just `build_tool_list(ctx)` vs `build_full_tool_loadout(config)` under an eval-shaped config):

| | `tool_choice` | agent, turn 1 |
|---|---|---|
| callable tool schemas | **97** | **40** |
| `<deferred_tools>` prose listing | none | **3,173 chars, 58 tools** |
| `tool_search` offered | no | yes |

So 58 tools are directly callable in one harness and only prose-summarized-behind-`tool_search` in the other. Notably `tool_search` itself is the one tool present for the agent but absent from the `tool_choice` loadout.

The docstring in `src/decafclaw/eval/tool_choice/loadout.py:1-12` is explicit that this is intentional:

> Builds a fully-loaded tool definitions list … without the production deferral or activation gating logic. The eval intentionally measures description overlap under fair conditions, so every tool the model could pick is in scope simultaneously.

That is a defensible design for measuring *description overlap*. It's a poor proxy for *what the agent will actually do*, because in production a `normal`-priority tool competing with a `critical` one isn't in the schema at all — it's a one-line prose summary the model must first `tool_search` for.

## Why it matters

Any `tool_choice` case whose `expected` or `near_miss` tool is deferred in production is measuring a decision the agent never gets to make in that form. The pass tells you the descriptions disambiguate; it doesn't tell you the routing works.

Concretely, of the 58 deferred-at-turn-1 tools, several appear in `evals/tool_choice/core_overlaps.yaml` cases — e.g. `conversation_search`, `conversation_compact`, `workspace_write`/`workspace_append`, `canvas_*`, `send_email`, `http_request`.

## Possible directions

Not proposing one — that's the work.

1. **Leave as-is, document the limitation.** Cheapest. `tool_choice` stays a pure description-overlap instrument and we stop reading it as a routing predictor.
2. **Add a second mode** (`--production-loadout`) that runs `classify_tools()` and includes the `<deferred_tools>` block + `tool_search`, so both numbers are available per case.
3. **Switch to the production split** and re-baseline every case.

Option 2 is probably the interesting one — the delta between the two modes is itself the signal ("this pair disambiguates fine cold, but one of them is deferred so it never comes up").

## Constraint

`evals/tool_choice/core_overlaps.yaml:97-126` (the three `vault_journal_append` ↔ `notes_append` cases) are the control for #670 and currently pass 5/5 on repeated sampling. Any change here re-measures them.

## Relationship to other issues

- #670 — where this was found. Its fix (assembling the system prompt in the full-agent runner) is independent of this.
- #650 — noise-floor / sweep work. This decides *what* that work should be measuring; worth settling first or in parallel.

## Files

- `src/decafclaw/eval/tool_choice/loadout.py:25-64` — `build_full_tool_loadout`
- `src/decafclaw/eval/tool_choice/runner.py:56-90` — `run_case`, single completion, no deferred block
- `src/decafclaw/tool_definitions.py:126-151` — `build_tool_list`, the production path
- `src/decafclaw/tools/tool_registry.py:69-120` — `classify_tools`
- `docs/eval-loop.md:246` — describes the tool_choice call shape


---

## Decision (Les, 2026-08-03): Option 2 — add a production-loadout mode

Recorded in the body rather than a comment, because downstream modes read the body only.

**Add a second, opt-in production-loadout mode to the tool-choice runner.** Option 1 (document the
divergence) and Option 3 (re-baseline the eval) are closed, not merely unchosen. Option 1's only
check is a grep proxy; Option 3 needs paid model-dependent runs and re-measures the #670 control
cases.

## The numbers in the title are not the criterion

**Do not pin 97 / 40 / 58.** Those came from one eval-shaped config; running the same code against
a different local config at scan time produced **30 active / 67 deferred out of 97**. The split is
config-dependent, so a criterion asserting the literals would go red when someone adds a tool —
a false positive, not a regression. State the invariant instead, as the criteria below do.

## Acceptance criteria

*Added by `agent-session` triage, 2026-08-03.*

- **CRITERION 1:** WHEN the tool-choice loadout is built in production mode, the callable schema set
  SHALL equal the active set that `classify_tools()` returns for the same input, and the remainder
  SHALL be returned as a deferred set — `active | deferred == full loadout`.

  **CHECK:** a new node in `tests/test_eval_tool_choice_loadout.py` asserting
  `set(names(active)) == set(names(classify_tools(build_full_tool_loadout(config), config)[0]))`
  and that active ∪ deferred reconstitutes the full loadout. No LLM.

  **OBSERVED (2026-08-03): fails today — discriminates.** The oracle was confirmed constructible
  in-process and cheap: building the loadout and classifying it returned
  `loadout 97 / active 30 / deferred 67` in ~2s with no model call. No production mode exists to
  compare against, so the assertion cannot pass today.

- **CRITERION 2:** WHEN the tool-choice runner runs in production mode, THE SYSTEM SHALL offer
  `tool_search` as a callable tool AND SHALL include a `<deferred_tools>` block built by
  `build_deferred_list_text()` in the messages sent to the model.

  **CHECK:** a unit test on `run_case` with a stubbed `call_llm` capturing `tools=` and `messages=`;
  assert `"tool_search" in names(tools)` and that `<deferred_tools>` appears in the content.

  **OBSERVED (2026-08-03): fails today — discriminates.** `'tool_search' in loadout` is `False`, and
  `rg -n "deferred_tools>|build_deferred" src/decafclaw/eval/` returns zero hits —
  `build_deferred_list_text` lives at `src/decafclaw/tools/tool_registry.py:226` and is consumed
  only by `tool_definitions.py:216` and `context_composer.py:1294`, never by the eval package.

## Explicitly NOT a criterion

**The per-case delta between the two modes.** A report column can only be graded by grepping for a
header string, which is satisfiable by typing the word, and what the delta should *mean* per case
is an unmade product call. Build the delta if it is useful; it is not graded here, and folding it
in as a criterion would push this issue back to `needs-review`.

## Regression guards

- **GUARD:** the existing tool-choice loadout contract holds — core tools present, skill native
  tools present, zero `mcp__*` entries by default, every entry has `function.name`. No test lost,
  newly skipped, or newly failing in that file.
  **CHECK:** `pytest tests/test_eval_tool_choice_loadout.py`
  **OBSERVED:** 6 passed.

- **GUARD:** the default (non-production) mode remains a full, ungated loadout, so existing
  baselines are not silently re-measured. Production mode must be **opt-in**.
  **CHECK:** assert the default-mode name set still equals `build_full_tool_loadout(config)`.
  **OBSERVED:** passes today by identity. This is the guard that keeps Option 2 from turning into
  Option 3 by accident.

- **GUARD:** capability-tier gating on skill native tools stays enforced in any new loadout path
  (the `grants_capability` filter from #744).
  **CHECK:** `pytest tests/test_skill_native_tools_tier.py`
  **OBSERVED: UNRUN at scan time** (targeted commands only). The file exists.

- **GUARD:** no test lost, newly skipped, or newly failing (invariant, not a pinned count).
  **CHECK:** `make test` — **UNRUN at scan time.** Not verified; the freeze phase must run it.

## Still open, but not tier-bearing

What config shape defines "production" for the eval. The issue's 40/58 came from an eval-shaped
config and a local run gave 30/67, so the mode needs a stated config source before any *number*
means anything. This does not change which criteria apply — both are written as invariants against
whatever config is supplied — but decide it before reading the output as signal.

## Tier: auto-ok

**Trigger 1 no longer fires.** The issue said outright *"Not proposing one — that's the work"*, and
that is now resolved. Both surviving criteria were verified deterministic, in-process, LLM-free, and
failing today.

**Trigger 2 does not fire.** No auth, secrets, migration/deletion, deploy/CI config, or dependency
surface. The loadout only *reads* discovered skills through the existing `grants_capability` gate.
Note the boundary: wiring any of this into a pre-merge check would be CI config and would fire
trigger 2 — it is out of scope here, and should stay out.

