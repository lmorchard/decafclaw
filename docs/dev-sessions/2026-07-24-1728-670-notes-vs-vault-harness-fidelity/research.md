# Research — #670 harness disagreement

All numbers below were measured in this worktree on 2026-07-24 against
`vertex-gemini-flash`. Nothing here is inferred from reading alone unless
explicitly marked "source-level".

## Answer: the full-agent eval runner runs with an EMPTY system prompt

`config.system_prompt` is assembled exactly once in the codebase —
`src/decafclaw/__init__.py:49`, the app entry point:

```python
config.system_prompt, config.discovered_skills = load_system_prompt(config)
```

The eval CLI (`src/decafclaw/eval/__main__.py`) calls `load_config()` and
`run_eval()` directly. It never goes through `__init__.py:main()`.
`config.py:555` only reads `system_prompt` from a `SYSTEM_PROMPT` env var
(default `""`).

Source-level proof that the eval package never assembles it:

```
$ grep -rn "load_system_prompt\|system_prompt" src/decafclaw/eval/
src/decafclaw/eval/tool_choice/runner.py:17:from ...prompts import load_system_prompt
src/decafclaw/eval/tool_choice/runner.py:70:    system_prompt, _ = load_system_prompt(config)
src/decafclaw/eval/tool_choice/runner.py:72:        {"role": "system", "content": system_prompt},
```

Only `tool_choice` builds one. The full-agent runner does not.

`ContextComposer._compose_system_prompt` (`context_composer.py:582-589`) reads
`config.system_prompt` verbatim, so an unset value emits a **zero-length
system message**. Confirmed by direct probe of `compose()` under an
eval-shaped config (`tmp/probe_loadout.py`):

```
--- role='system'  len=0
--- role='system'  len=3173     <- <deferred_tools> block
--- role='user'    len=88
--- role='system'  len=51       <- [Context: ~9,192 / 100,000 tokens (9%)]
```

So every full-agent eval turn has been running without SOUL.md, AGENT.md,
USER.md, the skill catalog, **or the always-loaded skill bodies — including
the vault SKILL.md** that documents when to journal.

`load_system_prompt` docstring (`prompts/__init__.py:37-46`) lists exactly
what's missing.

## This inverts hypothesis 1

The issue's leading hypothesis was that `tool_choice` under-reports because
it can't reproduce the always-loaded / auto-injected `notes_append` context.
Measured, it's the other way round: **`tool_choice` is the higher-fidelity
harness on this axis** — it uses the production system prompt
(`tool_choice/runner.py:70`), and the full-agent runner is the one missing it.

`docs/eval-loop.md:246` already documents tool_choice as using "the production
system prompt." Nothing documents that the full-agent runner doesn't.

## Measurements

### Baseline — full agent, as shipped (empty system prompt)

`tmp/crossing.yaml`, 2x2 crossing, 5 reps/cell, `expect_tool: vault_journal_append`:

| prompt | no trailing clause | + "Confirm what you saved." |
|---|---|---|
| "…my favorite programming language is Python." | **1/5** | **0/5** |
| "…my dog's name is Luna and she's a border collie mix." | **5/5** | **4/5** |

Plus an earlier 5-rep run of the verbatim case-20 input: **0/5**.
Case-20 prompt overall: **1/15**. Case-43 prompt overall: **13/15**.

### Same 20 cases, system prompt assembled (probe patch)

**20/20 passed.** Prompt tokens per case: 11.9k → 28.6k (the ~16.6k system
prompt appearing). Every cell went green, including the 1/5 cell.

### tool_choice, same two sentences, 5 reps each

**10/10 passed.** `vault_journal_append <-> notes_append 0/10 swapped (0%)`.
The tool_choice PASS is robust, not a lucky sample.

## Hypotheses, resolved

| # | Hypothesis | Verdict | Evidence |
|---|---|---|---|
| 1 | notes auto-injection / always-loaded availability | **Ruled out as the cause; inverted** | Probe shows no `conversation_notes` message at all (fresh tmp workspace → empty notes.md). Both tools are *active* in both harnesses. tool_choice is the more faithful harness here, not the less. |
| 2 | Loadout differences | **Real, but not the cause** | agent-active = 40 tools + a 3,173-char `<deferred_tools>` prose listing + `tool_search`; tool_choice = 97 tools, all callable, no deferral, no `tool_search`. 58 tools are callable in tool_choice but only prose-listed for the agent. Both `notes_append` and `vault_journal_append` are **active in both**, so nothing flipped here. |
| 3 | System prompt / AGENT.md nudging toward the scratchpad | **Inverted — the prompt is absent, not misleading** | `grep -rn "notes_append\|scratchpad" src/decafclaw/prompts/` → no hits. The problem is the prompt is missing entirely. |
| 4 | The trailing "Confirm what you saved." | **Ruled out** | Crossing above: 1/5 → 0/5 and 5/5 → 4/5. Both within noise. Content drives the split, not the clause. |
| 5 | Reflection retries | **Ruled out** | `reflection.enabled: false`, 3 reps: **0/3**, still `notes_append`, 1 tool call each. |

## Why case 43 looked deterministic in the baseline bundle

It isn't. Measured 13/15 (~87%) under the broken condition — it happened to
fail in the 2026-07-24 bundle. The issue's "two independent cases failed
identically, so this is not a one-off" holds for case 20 (0/15) but not
case 43. Case 43 is a noise-floor case — relevant to #650.

Under the fixed condition both are 10/10.

## Blast radius of the candidate fix

Enabling the system prompt changes **every** full-agent eval case, not just
these two:

- Prompt tokens/case: 11.9k → 28.6k (**~2.4x**). Extrapolating the 52-case
  suite: ~1.3M → ~3M tokens/run.
- The 45/52 baseline was measured against an agent with no system prompt —
  i.e. not the agent that ships. Cases currently passing were implicitly
  calibrated against that agent and some may flip in either direction.
- Unmeasured. Establishing the new baseline needs one full `make eval` run.

## Related facts

- No unit test asserts `config.system_prompt` is populated in `run_test`.
  Existing eval tests (`tests/test_eval_*.py`) cover assertions, overrides,
  history, and the tool_choice loadout — none cover prompt assembly.
- `run_test` populates `config.discovered_skills` (runner.py:667-670) but
  not `config.system_prompt` — the adjacent gap.
- `load_system_prompt(config)` under an eval config reads from the sandboxed
  tmp `data_home`, so it resolves to **bundled** SOUL.md / AGENT.md with no
  per-agent overrides. That's the hermetic behavior a test harness wants.
- `load_system_prompt` also returns `discovered_skills`, so wiring it in has
  to keep `config.skill_tool_owners` built (the current `if not
  config.discovered_skills:` block would otherwise be skipped).

## Files

- `src/decafclaw/eval/runner.py:666-670` — where the gap is
- `src/decafclaw/__init__.py:47-50` — the production assembly it should mirror
- `src/decafclaw/eval/tool_choice/runner.py:70` — how tool_choice does it
- `src/decafclaw/context_composer.py:582-596` — `_compose_system_prompt`
- `src/decafclaw/prompts/__init__.py:36-53` — what the prompt contains
- `docs/eval-loop.md:246` — documents tool_choice's prompt; silent on the runner
