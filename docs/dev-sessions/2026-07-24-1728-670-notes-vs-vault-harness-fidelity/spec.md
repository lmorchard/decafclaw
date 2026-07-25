# Eval runner system-prompt fidelity Spec

**Goal:** Make the full-agent eval harness run the agent that actually ships —
it has been running every case with an empty system prompt — and re-establish
the suite baseline against it.

**Source:** [#670](https://github.com/lmorchard/decafclaw/issues/670)
(use the 2026-07-24 correction comment; the body's "sharpen the descriptions"
diagnosis is retracted and is measurably wrong — see Design decisions).

## Current state

`config.system_prompt` is assembled in exactly one place —
`src/decafclaw/__init__.py:49`, the app entry point. The eval CLI
(`src/decafclaw/eval/__main__.py`) calls `load_config()` and `run_eval()`
directly and never goes through it, and `config.py:555` only reads the field
from a `SYSTEM_PROMPT` env var (default `""`).

`ContextComposer._compose_system_prompt` (`context_composer.py:582-589`) reads
`config.system_prompt` verbatim, so the eval agent gets a **zero-length system
message**: no SOUL.md, no AGENT.md, no `<skill_catalog>`, and no always-loaded
skill bodies — including the vault SKILL.md that documents when to journal.

`tool_choice` does not share the gap: it calls `load_system_prompt(config)`
directly (`eval/tool_choice/runner.py:70`). That is the entire disagreement in
#670 — and it **inverts** the issue's leading hypothesis. `tool_choice` is the
*higher*-fidelity harness here; the full-agent runner is the broken one.

Measured (see `research.md` for the full tables, all `vertex-gemini-flash`):

| condition | case-20 prompt | case-43 prompt |
|---|---|---|
| as shipped (empty prompt) | **1/15** | **13/15** |
| system prompt assembled | **10/10** | **10/10** |
| `tool_choice` | **5/5** | **5/5** |

Hypotheses 4 (trailing clause) and 5 (reflection retries) were ruled out by
measurement, not reasoning: a 2x2 crossing moved 1/5 → 0/5 and 5/5 → 4/5
(noise), and `reflection.enabled: false` still gave 0/3.

## Desired end state

1. `run_test` assembles the system prompt before running a turn, so eval turns
   see the same system prompt production does (bundled tier).
2. A unit test fails if that assembly is ever dropped again — no LLM calls.
3. `docs/eval-loop.md` states what system prompt each harness uses.
4. `evals/memory.yaml` case 20 and `evals/vault.yaml` case 43 pass.
5. A fresh full-suite baseline bundle exists under `evals/results/`, and
   `notes.md` records which cases flipped in each direction relative to the
   2026-07-24 45/52 run.

## Design decisions

- **Decision:** Assemble inside `run_test`, after `_build_test_config` has
  applied the `data_home=tmp` / `id="eval"` sandbox.
  - **Why:** `load_system_prompt` reads per-agent overrides from
    `config.agent_path`. Sandboxed, it resolves to bundled SOUL.md / AGENT.md
    with no `data/{agent_id}` overrides and no USER.md — so eval results are
    reproducible across machines and don't shift when the deployed agent's
    AGENT.md is edited.
  - **Rejected:** assembling in `eval/__main__.py` against the un-sandboxed
    config. Higher fidelity to Les's *particular* deployed agent, but makes
    every eval number machine-dependent.

- **Decision:** Do not touch `notes_append` or `vault_journal_append`
  descriptions.
  - **Why:** With the prompt in place, routing is 20/20 correct. There is no
    description defect to fix. Rewording would be tuning against a symptom and
    risks the three `tool_choice` cases that must stay passing.
  - **Rejected:** the fix proposed in the issue body (steps 1 and 2 of it).
    Step 2 ("add the missing `tool_choice` case") is already done —
    `evals/tool_choice/core_overlaps.yaml:97-126` has three.

- **Decision:** Keep `load_system_prompt`'s `discovered_skills` return wired
  in, and build `skill_tool_owners` under its own guard.
  - **Why:** `load_system_prompt` populates `discovered_skills` as a side
    effect. Left as-is, the existing `if not config.discovered_skills:` block
    is skipped and `config.skill_tool_owners` never gets built, silently
    breaking `/foo` command dispatch in evals. Hit during the probe.
  - **Rejected:** discarding the returned skills and re-discovering — wasteful
    and lets the two views drift.

- **Decision:** Run one full `make eval` to re-baseline within this PR.
  - **Why:** the fix moves ~2.4x tokens/case (11.9k → 28.6k) and changes the
    inputs to every case. The 45/52 entry in `evals/history.jsonl` was measured
    against an agent that doesn't ship; without a new bundle the trend line is
    silently discontinuous and #650's noise-floor work starts from a bad
    anchor.
  - **Rejected:** deferring to #650 (leaves the discontinuity) and re-running
    only memory+vault (confirms the issue closed but hides what flipped).

- **Decision:** Guard test patches `run_agent_turn` rather than calling an LLM.
  - **Why:** the invariant is "`config.system_prompt` is non-empty by the time
    a turn runs" — observable at the seam, no model needed. Keeps it in the
    default `make test` run.

## Patterns to follow

- **The assembly itself:** mirror `src/decafclaw/__init__.py:47-50` —
  `config.system_prompt, config.discovered_skills = load_system_prompt(config)`.
- **Where it goes:** `src/decafclaw/eval/runner.py:666-670`, the existing
  `discovered_skills` population block at the top of `run_test`, which is the
  adjacent gap.
- **How tool_choice does it:** `src/decafclaw/eval/tool_choice/runner.py:17,70`
  — module-level `from ...prompts import load_system_prompt`, called per run.
- **Test style:** the existing eval-runner tests
  (`tests/test_eval_runner_assertions.py`, `tests/test_eval_setup_overrides.py`)
  for fixture and patching conventions.
- **Docs:** `docs/eval-loop.md:246` already documents tool_choice's system
  prompt; extend that section rather than starting a new one.

## What we're NOT doing

- **Not rewording `notes_append` / `vault_journal_append` descriptions.**
  Measured unnecessary. This is the single biggest scope hazard in the issue.
- **Not modifying `evals/tool_choice/core_overlaps.yaml:97-126.`** Those three
  cases must stay passing, unchanged, as the control.
- **Not changing `evals/memory.yaml` case 20 or `evals/vault.yaml` case 43.**
  Both pass once the harness is fixed; the eval cases were right.
- **Not fixing the tool_choice loadout gap** (97 callable tools vs the agent's
  40 active + 58 prose-listed behind `tool_search`). Real, measured, and did
  NOT cause this. Files as a follow-up issue with the numbers.
- **Not #676** (the 4 other `eval-tools` failures), **#671**, **#673**, or
  **#650**.
- **Not chasing whatever the full re-baseline turns up.** New failures get
  recorded in `notes.md` and filed as issues; they are not fixed here.
- **Not adding a config knob** to toggle the eval system prompt. The empty
  prompt has no defensible use case.

## Open questions

- **Will the re-baseline surface regressions in currently-passing cases?**
  Unmeasured until the run. **Default:** record the deltas in `notes.md`, file
  anything that looks like a real behavior bug as a separate issue, and do not
  fix them in this PR. A lower total than 45/52 is an acceptable outcome if the
  cases are now measuring the real agent — that is the point of the change.
