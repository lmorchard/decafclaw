# Eval runner system-prompt fidelity — Implementation Plan

**Goal:** Make the full-agent eval harness assemble the production system prompt
(it has been running every case with an empty one), guard the invariant with a
unit test, document it, and re-baseline the suite.

**Approach:** Assemble inside `run_test` *after* `_build_test_config` applies the
`data_home=tmp` / `id="eval"` sandbox, so the prompt resolves to bundled
SOUL.md / AGENT.md / skill catalog / always-loaded skill bodies with no
per-agent overrides — reproducible across machines. Mirror
`src/decafclaw/__init__.py:47-50`. No tool-description changes: routing measured
20/20 correct once the prompt is present.

**Tech stack:** Python 3.13, pytest + pytest-asyncio, `uv run`.

**Working directory for every command below:**
`/Users/lorchard/devel/decafclaw/.claude/worktrees/670-notes-vs-vault-harness-fidelity`
Use an absolute `cd` in each verification command — a stray `cd` to the main
clone makes `make test` report a wrong-tree pass count and `pytest <newfile>`
say "no tests ran", both of which look like success.

**Baseline for comparison:** `make test` in this worktree = **3417 passed,
2 skipped** (recorded at session start).

---

## Phase 1: Assemble the system prompt in `run_test`, guarded by a test

Delivers the fix itself: every eval turn sees the same system prompt production
builds, and a unit test fails if the assembly is ever dropped again. No LLM
calls in the test.

**Files:**
- Create: `tests/test_eval_system_prompt.py`
- Modify: `src/decafclaw/eval/runner.py` — assemble the prompt at the top of
  `run_test`; split the `skill_tool_owners` build out from under the
  `discovered_skills` guard.

**Key changes:**

`src/decafclaw/eval/runner.py` — add the module-level import alongside the
existing ones (line 17 area; `tool_choice/runner.py:17` imports `prompts` at
module level, so there's no cycle):

```python
from ..prompts import load_system_prompt
```

Then replace the existing block at the top of `run_test` (currently
`runner.py:666-670`):

```python
    # Populate discovered_skills so dispatch_command can resolve `/foo` triggers.
    if not config.discovered_skills:
        from ..skills import build_skill_tool_owners
        config.discovered_skills = _discover_skills_fn(config)
        config.skill_tool_owners = build_skill_tool_owners(config.discovered_skills)
```

with:

```python
    # Assemble the system prompt exactly as __init__.py:49 does at startup.
    # Without this, config.system_prompt stays "" (config.py:555 only reads a
    # SYSTEM_PROMPT env var) and ContextComposer._compose_system_prompt emits a
    # zero-length system message — no SOUL.md, no AGENT.md, no skill catalog,
    # no always-loaded skill bodies. Every eval case ran that way until #670.
    #
    # Deliberately after _build_test_config's sandbox: load_system_prompt reads
    # per-agent overrides from config.agent_path, which now points at the tmp
    # data_home, so this resolves to the bundled tier only. Eval results stay
    # reproducible instead of tracking whatever is in data/{agent_id}/.
    if not config.system_prompt:
        config.system_prompt, config.discovered_skills = load_system_prompt(config)

    # Populate discovered_skills so dispatch_command can resolve `/foo` triggers.
    if not config.discovered_skills:
        config.discovered_skills = _discover_skills_fn(config)
    # Separate guard: load_system_prompt populates discovered_skills as a side
    # effect, so folding this into the branch above would skip it and silently
    # break command dispatch.
    if not config.skill_tool_owners:
        from ..skills import build_skill_tool_owners
        config.skill_tool_owners = build_skill_tool_owners(config.discovered_skills)
```

`tests/test_eval_system_prompt.py` — new. Patches `run_agent_turn` at the seam
so no model is called; captures the config the turn actually ran with:

```python
"""The full-agent eval runner must assemble a real system prompt (#670).

Before this guard, ``config.system_prompt`` was only ever set by
``decafclaw/__init__.py:49`` (the app entry point). The eval CLI calls
``load_config()`` + ``run_eval()`` directly, so every eval turn ran with a
zero-length system message: no SOUL.md, no AGENT.md, no <skill_catalog>, and
no always-loaded skill bodies. Measured effect on tool routing was 1/15 vs
10/10 — see docs/dev-sessions/2026-07-24-1728-670-*/research.md.

No LLM calls: ``run_agent_turn`` is patched at the seam.
"""

from unittest.mock import patch

import pytest

from decafclaw.config import Config
from decafclaw.eval.runner import _build_test_config, run_test
from decafclaw.media import ToolResult


@pytest.fixture
def captured_turn():
    """Patch run_agent_turn; record the ctx it was handed."""
    seen = {}

    async def _fake_turn(ctx, user_message, history, *args, **kwargs):
        seen["ctx"] = ctx
        seen["config"] = ctx.config
        history.append({"role": "assistant", "content": "ok"})
        return ToolResult(text="ok")

    with patch("decafclaw.eval.runner.run_agent_turn", _fake_turn):
        yield seen


@pytest.mark.asyncio
async def test_system_prompt_is_assembled_before_the_turn(tmp_path, captured_turn):
    config = _build_test_config(Config(), {"setup": {}}, str(tmp_path))
    assert config.system_prompt == "", "precondition: starts empty"

    await run_test(config, {"name": "t", "input": "hello"})

    prompt = captured_turn["config"].system_prompt
    assert prompt, "eval turn ran with an empty system prompt (#670)"


@pytest.mark.asyncio
async def test_prompt_carries_the_sections_production_gets(tmp_path, captured_turn):
    """Non-empty isn't enough — the always-loaded skill bodies are the part
    #670 turned on, so assert the sections individually."""
    config = _build_test_config(Config(), {"setup": {}}, str(tmp_path))
    await run_test(config, {"name": "t", "input": "hello"})
    prompt = captured_turn["config"].system_prompt

    assert "<skill_catalog>" in prompt
    assert "<loaded_skills>" in prompt
    # The vault skill is always-loaded and its body is what documents
    # vault_journal_append — the specific gap #670 surfaced.
    assert '<skill name="vault">' in prompt


@pytest.mark.asyncio
async def test_skill_tool_owners_still_populated(tmp_path, captured_turn):
    """load_system_prompt also returns discovered_skills, which would skip the
    `if not config.discovered_skills` branch and leave skill_tool_owners empty —
    silently breaking /foo command dispatch in evals."""
    config = _build_test_config(Config(), {"setup": {}}, str(tmp_path))
    await run_test(config, {"name": "t", "input": "hello"})

    ran_with = captured_turn["config"]
    assert ran_with.discovered_skills
    assert ran_with.skill_tool_owners
```

**Order of work (TDD):**
1. Write `tests/test_eval_system_prompt.py`. Run it. It MUST fail on the first
   assertion with an empty prompt — that is the reproduction.
2. Apply the `runner.py` change.
3. Re-run; all three pass.

**Verification — automated:**
- [x] `cd <worktree> && uv run pytest tests/test_eval_system_prompt.py -v` fails
      *before* the runner.py edit, with `eval turn ran with an empty system prompt`
      — **2 failed, 1 passed**; failure was `AssertionError: ... assert ''`
- [x] `cd <worktree> && uv run pytest tests/test_eval_system_prompt.py -v` passes
      after it (3 passed) — **3 passed in 1.24s**
- [x] `cd <worktree> && make test` passes, count >= 3420 (baseline 3417 + 3 new)
      — **3420 passed, 2 skipped in 13.00s**
- [x] `cd <worktree> && make check` passes — **exit 0**; ruff "All checks passed",
      pyright "0 errors, 0 warnings", tsc clean, message-types drift check clean
- [x] `cd <worktree> && uv run pytest tests/test_eval_system_prompt.py --durations=5`
      — no test above ~2s (discovery + file reads only; anything slower means a
      real scheduler or model call leaked in) — **slowest call 0.04s**

**Verification — manual:**
- [ ] `git diff src/decafclaw/eval/runner.py` touches only the `run_test` prologue
      and the new import — no other behavior changed
- [ ] The import is at module level, not inside the function (CLAUDE.md: stdlib
      and non-cycle imports at module level)

---

## Phase 2: Document which system prompt each harness uses

Delivers the doc half of the same change, per CLAUDE.md's "update the `docs/`
page as part of the same PR."

**TDD opt-out:** doc-only, no behavior. No test.

**Files:**
- Modify: `docs/eval-loop.md` — state the full-agent runner's system prompt in
  the main eval-loop section, and keep the tool_choice "How it works" step 2
  (`docs/eval-loop.md:246`) consistent with it.

**Key changes:**

`docs/eval-loop.md:246` currently reads, under tool_choice's "How it works":

> 2. Sends one chat completion with the production system prompt + the case's
>    user message + the full tool schema.

Add a short subsection to the main (full-agent) eval-loop section stating:

- The full-agent runner assembles the system prompt in `run_test` via
  `load_system_prompt(config)`, after the per-case `data_home` sandbox is
  applied.
- That means the **bundled** tier: bundled SOUL.md + AGENT.md, the skill
  catalog, and always-loaded skill bodies. Per-agent overrides under
  `data/{agent_id}/` (including USER.md) are deliberately NOT picked up, so
  results are reproducible across machines.
- Note the history discontinuity: runs before 2026-07-24 were measured with an
  empty system prompt (#670) and are not comparable to later ones.

**Verification — automated:**
- [x] `cd <worktree> && make check` passes — **exit 0**
- [x] `cd <worktree> && grep -n "load_system_prompt" docs/eval-loop.md` returns a hit
      — **2 hits** (new `## System prompt` section at :22, tool_choice step 2 at :269)

**Adaptation (in scope, flagged):** `docs/eval-loop.md` claimed
`evals/history.jsonl` was "committed to git, unlike the gitignored detail
bundles". It has been gitignored since #606 (`git ls-files evals/history.jsonl`
→ 0). Corrected, because the new history-discontinuity note sits in the same
section and would otherwise contradict it.

**Verification — manual:**
- [ ] Les reads the new subsection — it says which tier is used and why, not
      just that a prompt exists
- [ ] The tool_choice step-2 line and the new subsection don't contradict each other

---

## Phase 3: Confirm #670 is closed — targeted eval re-run

Delivers evidence that the two reported cases actually pass, and that the three
control cases in `core_overlaps.yaml` are untouched.

**TDD opt-out:** verification phase against real LLM calls; no code changes.

**Files:**
- Modify: `docs/dev-sessions/2026-07-24-1728-670-notes-vs-vault-harness-fidelity/notes.md`
  — record the results.

**Commands:**

```bash
cd /Users/lorchard/devel/decafclaw/.claude/worktrees/670-notes-vs-vault-harness-fidelity
uv run python -m decafclaw.eval evals/memory.yaml    # case 20 lives here
uv run python -m decafclaw.eval evals/vault.yaml     # case 43 lives here
make eval-tools                                      # the 3 control cases
```

Expected: `saves memory when asked` and `saves user-level fact to vault journal
on 'remember'` both PASS. `make eval-tools` still shows
`vault_journal_append <-> notes_append  0/N swapped (0%)` and the four known
unrelated failures (#676) — those are out of scope and must NOT be fixed here.

Note `evals/results/` and `evals/history.jsonl` are both gitignored, so the run
bundles stay local; `notes.md` is the committed record.

**Verification — automated:**
- [x] `evals/memory.yaml`: "saves memory when asked" = **PASS** (file 7/8; the
      one failure, `recalls recent memories`, was already failing in the
      2026-07-24 baseline)
- [x] `evals/vault.yaml`: "saves user-level fact to vault journal on 'remember'"
      = **PASS** (file **6/6** — `adds a section without rewriting other
      sections` was a baseline failure and is now green too)
- [x] `make eval-tools`: `vault-vs-notes-remember-preference`,
      `vault-vs-notes-save-profile-fact`, `notes-vs-vault-follow-up-this-thread`
      all **PASS**, in both runs; pair overlap 0/1 and 0/2 swapped (0%)
- [!] `make eval-tools`: the four #676 failures are the *only* other failures —
      no new ones — **DOES NOT HOLD AS WRITTEN.** Not a regression: `tool_choice`
      has its own runner that never imports `eval/runner.py`, so this PR cannot
      affect it. The real finding is that eval-tools' failure *set* is unstable
      run-to-run (4 / 6 / 4 failures across three runs, only the two tabstack
      cases constant). See `notes.md` for the table. Implication for #676 is
      recorded there; not acted on here.

**Verification — manual:**
- [ ] `notes.md` records pass/fail per case with the actual output pasted, not
      a summary

---

## Phase 4: Full-suite re-baseline and flip table

Delivers the new baseline. The 45/52 entry in `evals/history.jsonl` was measured
against an agent with no system prompt, so without this the trend line is
silently discontinuous and #650 starts from a bad anchor.

**TDD opt-out:** measurement phase; no code changes.

**Files:**
- Modify: `docs/dev-sessions/2026-07-24-1728-670-notes-vs-vault-harness-fidelity/notes.md`
  — the flip table.

**Commands:**

```bash
cd /Users/lorchard/devel/decafclaw/.claude/worktrees/670-notes-vs-vault-harness-fidelity
make eval          # ~11 min; expect ~3M tokens, up from ~1.3M (2.4x/case)
make eval-history  # trend table including the new run
```

Then diff against the pre-fix bundle at
`evals/results/2026-07-24-1621-vertex-gemini-flash/` and write a table into
`notes.md`:

| case | before (45/52 run) | after | direction |
|---|---|---|---|

**Scope discipline — this is the phase most likely to sprawl.** Per spec, any
newly-failing case gets *recorded and filed*, not fixed. Cases that were passing
against a promptless agent and now fail are measuring the real agent for the
first time; a total lower than 45/52 is an acceptable outcome. Do not touch tool
descriptions, prompts, or eval YAML to chase green.

**Verification — automated:**
- [x] `make eval` completes; new bundle exists under `evals/results/` —
      **`evals/results/2026-07-24-1822-vertex-gemini-flash/`, 51/52**
- [x] `make eval-history` shows the new run appended — **`2026-07-24-1822 …
      51 / 52  98.1%  473s  2.27M`**
- [x] Token count for the run is materially above the 1.3M pre-fix figure
      (sanity check that the prompt is actually being sent) —
      **1,308,185 → 2,272,538 (1.74x)**

**Verification — manual:**
- [ ] `notes.md` flip table lists every case that changed direction, both ways
- [ ] Each newly-failing case is either filed as an issue or explicitly noted as
      "expected under the real prompt" with a reason
- [ ] Les reviews the flip table before the PR opens — this is the judgment call
      the re-baseline exists to surface

---

## Plan self-review

**Spec coverage:**

| spec "Desired end state" item | phase |
|---|---|
| 1. `run_test` assembles the prompt (bundled tier) | 1 |
| 2. Unit test guards the assembly, no LLM calls | 1 |
| 3. `docs/eval-loop.md` states each harness's prompt | 2 |
| 4. memory.yaml case 20 + vault.yaml case 43 pass | 3 |
| 5. Fresh baseline + flip table in `notes.md` | 4 |

Spec design decisions all land: hermetic assembly point (Phase 1 comment +
placement), `skill_tool_owners` guard (Phase 1, its own test), no description
changes (absent from every phase, and called out in Phase 3/4 scope notes),
full re-baseline (Phase 4), guard test patches `run_agent_turn` (Phase 1).

**Placeholder scan:** no TBD/TODO. Every test is written out; every command is
literal and absolutely-pathed.

**Type consistency:** `load_system_prompt(config) -> (prompt_text, skills)`
matches `prompts/__init__.py:36,124`. `run_agent_turn(ctx, user_message,
history, archive_text="", attachments=None) -> ToolResult` matches
`agent.py:1148-1150`; the fake's `*args, **kwargs` tail absorbs the optional
params. `ToolResult` is defined at `media.py:69` and imported into `agent.py:33`
from there — the test imports `from decafclaw.media import ToolResult`, not from
`tool_execution`. `_build_test_config(config, test_case, tmp)` matches `runner.py:617`.
`run_test(config, test_case)` matches `runner.py:656`. Assertion strings
`<skill_catalog>` / `<loaded_skills>` / `<skill name="vault">` match
`prompts/__init__.py:88,115,109` and the vault skill's frontmatter `name: vault`.

**Known risk, accepted:** the Phase 1 tests run real skill discovery and read
the bundled prompt files. That is file I/O, not a scheduler or a model — the
`--durations=5` check is there to catch it if something heavier leaks in.
