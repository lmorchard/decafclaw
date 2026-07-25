# Notes — #670 eval runner system-prompt fidelity

## Phase 3 — targeted eval re-run (2026-07-24)

Model `vertex-gemini-flash`, run in this worktree at commit `0a4073f`.

### `evals/memory.yaml` — 7/8

```
[1/8] finds preference by direct term                        PASS  (2.0s, 13962 tokens, 0 tools)
[2/8] finds preference by related term                       PASS  (2.3s, 13961 tokens, 0 tools)
[3/8] recalls recent memories (project + style)              FAIL  (16.5s, 28005 tokens, 0 tools)
[4/8] handles missing memories gracefully                    PASS  (2.5s, 13830 tokens, 0 tools)
[5/8] saves memory when asked                                PASS  (4.3s, 28590 tokens, 1 tools)
[6/8] connects related memories (event bus + async)          PASS  (2.7s, 14149 tokens, 0 tools)
[7/8] synthesizes from multiple memories for a complex q.    PASS  (4.9s, 14115 tokens, 0 tools)
[8/8] finds info with indirect phrasing                      PASS  (2.3s, 13972 tokens, 0 tools)
8 tests, 7 passed, 1 failed (37.5s, 140584 tokens)
```

**Case 20 "saves memory when asked" → PASS.** This is #670's primary case.

`recalls recent memories (project + style)` also fails, but it was **already
failing in the 2026-07-24 baseline** (`evals/results/2026-07-24-1621-vertex-gemini-flash/`)
— not a flip caused by this change. Left alone per spec.

### `evals/vault.yaml` — 6/6

```
[1/6] saves user-level fact to vault journal on 'remember'   PASS  (4.3s, 28654 tokens, 1 tools)
[2/6] reaches for vault_search on explicit search request    PASS  (7.8s, 30578 tokens, 1 tools)
[3/6] finds specific fact under heavy distractor load        PASS  (2.3s, 13952 tokens, 0 tools)
[4/6] reads a named vault page directly without searching    PASS  (4.1s, 30216 tokens, 1 tools)
[5/6] reaches for vault_backlinks to find inbound links      PASS  (3.8s, 29557 tokens, 1 tools)
[6/6] adds a section without rewriting other sections        PASS  (12.9s, 45059 tokens, 2 tools)
6 tests, 6 passed, 0 failed (35.2s, 178016 tokens)
```

**Case 43 → PASS.** Bonus: `adds a section without rewriting other sections`
was a baseline failure and is now green too — that is the #671-adjacent case,
so #671 may be partly a symptom of the same missing prompt. **Not investigated
here; worth a look when #671 is picked up.**

### `make eval-tools` — the three control cases hold

All three `vault_journal_append` <-> `notes_append` cases PASS in both runs,
and the pair overlap stayed clean:

```
notes_append <-> vault_journal_append     0/1 swapped (0%)
vault_journal_append <-> notes_append     0/2 swapped (0%)
```

### Plan checkbox that did NOT hold as written

> "the four #676 failures are the *only* other failures — no new ones"

**False as written, but not a regression from this change.** `tool_choice` has
its own runner (`eval/tool_choice/runner.py`) that never imports
`eval/runner.py`, so nothing in this PR can affect it. What the runs show is
that **`eval-tools`' failure set is not stable run-to-run**:

| run | failures | count |
|---|---|---|
| Les's, in #670 (2026-07-24) | workspace-write-vs-canvas, tabstack-automate, tabstack-research, vault-recent-vs-list | 4 |
| mine, run 1 | workspace-write-vs-canvas, tabstack-automate, tabstack-research, ask-choice-vs-text, frontmatter-vs-write, no-tool-greeting | 6 |
| mine, run 2 | canvas-vs-workspace-write, tabstack-automate, tabstack-research, ask-choice-vs-text | 4 |

Summaries: 26/32 (81%) then 28/32 (88%).

Only **two** cases fail in all three runs: `tabstack-automate-vs-research-form-fill`
and `tabstack-research-vs-automate-multi-source` (both pick `web_fetch`). The
rest move around, and several `<no_tool>` picks look like the model declining
to act rather than misrouting.

**Implication for #676:** its "4 failures" is a snapshot, not a set. #676 should
be re-scoped to the two deterministic tabstack cases, with the rest folded into
#650's noise-floor work. Not doing that here — out of scope — but it's the
finding.

---

## Phase 4 — full-suite re-baseline (2026-07-24)

| | baseline `2026-07-24-1621` | new `2026-07-24-1822` |
|---|---|---|
| result | **45 / 52** (86.5%) | **51 / 52** (98.1%) |
| duration | 647.2s | 473.0s |
| tokens | 1,308,185 | 2,272,538 (**1.74x**) |

Token growth came in at 1.74x, below the 2.4x/case I extrapolated from the
two-case probe — many cases are multi-turn or tool-heavy, so the fixed ~16.6k
system prompt is a smaller share of their total than it is for a one-shot case.

### Every baseline failure flipped green

All seven. None of them were touched by this PR beyond the system prompt now
being present.

| case | baseline | new |
|---|---|---|
| recalls recent memories (project + style) | fail | **pass** |
| saves memory when asked *(#670 case 20)* | fail | **pass** |
| postmortem produces all five sections, blamelessly framed | fail | **pass** |
| writes spec when asked | fail | **pass** |
| dream consolidation fills page frontmatter after writing | fail | **pass** |
| saves user-level fact to vault journal on 'remember' *(#670 case 43)* | fail | **pass** |
| adds a section without rewriting other sections | fail | **pass** |

That is the measure of how much the empty system prompt was distorting: **six
of the seven "failures" in the 45/52 baseline were harness artifacts, not agent
defects.** Anyone who had picked up one of those issues would have been
debugging an agent that doesn't ship.

Two of them have open issues that should now be re-checked before any work
starts on them:
- **#671** (`vault_section` H1-rooted paths) — "adds a section without
  rewriting other sections" is its eval case and it now passes. #671 may be
  wholly or partly a harness artifact. **Not investigated here.**
- The `dream` frontmatter case likewise.

### One new failure — does not reproduce

| case | baseline | new |
|---|---|---|
| continues interviewing after first answer | pass | **fail** |

`evals/project-skill.yaml:40`. Turn 1 asserts the response contains a `?`.
Observed response:

```
Project "homepage-redesign" has been created.

Please call `project_next_task()` to get your first instruction.
```

Two things wrong with that: it skipped the interview, and it instructed *the
user* to call a tool — the phantom-instruction shape `AGENT.md`'s guardrail
exists to prevent.

**But it does not reproduce.** Re-running turn 1 in isolation, 3 reps:
**3/3 PASS**. So this is a noise-floor case, not a regression from this change.
Recorded, not filed — filing a 1-in-4 flake as a bug would be noise. It belongs
to #650. Flagged to Les for the call.

---

## Phase 5 — Copilot caught a hole in the hermeticity claim

Copilot flagged, on both the code comment and the docs, that
`load_system_prompt` calls `discover_skills(config)` — which reads
`config.extra_skill_paths`. Those paths live **outside `data_home`**, so the
tmp sandbox never reached them. The "bundled tier only" claim was false.

Verified and quantified rather than taken on faith:

```
extra_skill_paths = ['~/.agents/skills']        # on this machine
tiers: Counter({'extra': 113, 'bundled': 12})

WITH extra_skill_paths: 34,704 chars, 125 skills
WITHOUT:                23,262 chars,  12 skills
extra tier = 11,442 chars — 33% of the eval system prompt
```

So a third of every eval prompt was one developer's `~/.agents/skills`
catalog (`gws-*`, `recipe-*`, `persona-*`, `television-*`, …). None are
always-loaded, so no skill *bodies* leaked, but all 113 catalog entries did.
Eval results were machine-dependent — precisely the property the design
decision claimed to deliver.

**Fix:** `_build_test_config` now clears `extra_skill_paths` alongside the
`data_home` / `agent.id` sandbox, applied last so `config_overrides` can't
opt back in. Three new tests cover it, including an end-to-end assertion that
every discovered skill is `trust_tier == "bundled"`.

### Re-baseline under the hermetic config

| | baseline `1621` | with extra skills `1822` | hermetic `2323` |
|---|---|---|---|
| result | 45 / 52 (86.5%) | 51 / 52 (98.1%) | **53 / 54 (98.1%)** |
| tokens | 1,308,185 | 2,272,538 | 2,235,913 |

54 cases, not 52 — the rebase onto current `main` brought in two new ones
(`nudge_does_not_read_as_user_correction` from #681, `fixes a wrong-shaped
TOOLS export` from #677). Both pass.

Token total barely moved despite the 33% smaller prompt: fewer catalog
entries, but several cases spent more tool calls.

**`continues interviewing after first answer` passes here**, confirming the
earlier read that it was noise rather than a regression.

**One failure: `uses workspace_move for a rename`.** Also flaky — re-running
it 4 times gives **3/4 PASS**. The failure mode is the agent reaching for a
vault tool on a path-shaped rename and giving up on the error:

```
[error: vault page 'drafts/old-name.md' not found]
I wasn't able to rename `drafts/old-name.md` because it doesn't seem to exist.
```

That is a `vault_*` vs `workspace_*` disambiguation wobble, which is #683 /
#650 territory. Recorded, not fixed.

### Note on `evals/history.jsonl`

The trend table now carries seven short probe runs from this session
(`1734`–`1819`, 2–20 cases each) interleaved with the real 52-case runs. The
file is gitignored so this stays local, but `make eval-history` output is noisy
until those age out.

---

## Retrospective

Shipped as [#689](https://github.com/lmorchard/decafclaw/pull/689), merged
`5285cb3`, closing #670.

### Recap

The reported bug was "two eval harnesses disagree on the same prompt." The
actual defect was that the full-agent eval runner had **never** assembled a
system prompt — `config.system_prompt` is set only in `decafclaw/__init__.py`,
and the eval CLI doesn't go through it. Fix is ~10 lines in `run_test` plus a
sandbox change; the value is in what it revealed.

### Scope drift

Two deliberate expansions, both approved before acting:

1. **Full-suite re-baseline** (planned from the start). Justified: 45/52 was
   measured against an agent that doesn't ship.
2. **Clearing `extra_skill_paths`** (unplanned, from Copilot). This one changed
   the shape of the fix — the "hermetic" design decision Les picked at
   brainstorm wasn't actually delivered by the first implementation.

One adjacent fix: a stale `docs/eval-loop.md` claim that `evals/history.jsonl`
is committed to git. Flagged rather than done silently, and only because the
new text sat beside it.

What *didn't* drift: no tool-description changes, despite that being the issue
body's proposed fix. Measurement said 20/20 correct with the prompt present.
Holding that line was the single most valuable scope decision — the issue's
own correction comment predicted it as the main hazard, and it was right.

### Surprises

- **The direction of the fidelity gap was backwards from the hypothesis.**
  `tool_choice` was the *higher*-fidelity harness. The issue ranked "tool_choice
  under-reports" as hypothesis 1; the truth was the full-agent runner had no
  prompt at all. Cheap to discover — one grep of `src/decafclaw/eval/`.
- **Six of seven baseline failures were harness artifacts.** Expected the fix to
  close two cases; it closed seven. Anyone who had picked up #671 or the
  postmortem/dream cases would have been debugging a phantom.
- **A third of the eval prompt was one developer's personal skill catalog** —
  113 skills from `~/.agents/skills` vs 12 bundled. Nothing in the issue or the
  brainstorm pointed at this.
- **Case 43 was flaky, not deterministic** (13/15), which quietly undercut the
  issue's "two cases failed identically → not a one-off" reasoning.

### Workflow friction

- **Brainstorm's hypothesis list was the highest-value artifact.** Les ranked
  five hypotheses and said "rule out 4 and 5 first, they're cheap." Both were
  ruled out in ~2 minutes of LLM time, which cleared the field fast. Worth
  keeping as a habit for diagnosis-shaped issues: rank by suspicion, but
  execute by cost.
- **The plan's per-phase verification checkboxes earned their keep** in an
  unexpected way: one Phase 3 checkbox turned out to be *false as written*
  (`the four #676 failures are the only other failures`). Having it written
  down forced marking it `[!]` with an explanation rather than quietly moving
  on. A vaguer plan would have hidden that.
- **Measuring before believing was the whole session.** Every hypothesis
  verdict, the flakiness of three separate cases, and the 33% figure all came
  from running things N times rather than reasoning once. The 2x2 crossing
  (5 reps/cell) cost ~240k tokens and definitively killed the trailing-clause
  hypothesis that reading alone would have left open.

### Misses

- **Should have grepped `extra_skill_paths` during brainstorm.** The design
  decision hinged on "what does `load_system_prompt` actually read?" and that
  question was answered only for the `data_home` half. Copilot caught it. The
  general lesson: when a design decision claims *hermeticity*, enumerate every
  input to the thing being sandboxed, not just the obvious one.
- **Asserted "the four #676 failures" from a single run** in the plan, taking
  the issue's snapshot as a stable set. Three runs later it was 4/6/4. Should
  treat any "the N failures are X" claim as needing repetition before it goes
  into a plan as a checkable assertion.
- **The PR title went stale** after the second re-baseline (said 51/52, shipped
  53/54) and needed a manual fix after opening. Worth re-reading the title, not
  just the body, after any late number change.

### Memory candidates

1. Eval runner hermeticity: `_build_test_config` clears both `agent.data_home`
   and `extra_skill_paths`; `extra_skill_paths` is the non-obvious one because
   it lives outside `data_home`. → better as a `docs/eval-loop.md` fact, which
   the PR already added. **No memory needed.**
2. `make eval-tools` failure sets are unstable run-to-run (4/6/4 observed);
   only the two tabstack cases are deterministic. → **worth saving**, it changes
   how to read #676 and any future eval-tools triage.
3. Diagnosis-shaped issues: rank hypotheses by suspicion, execute by cost;
   ruling out the cheap ones first clears the field. → **worth saving** as
   feedback.

### Skill candidates

- The dev-session `plan` phase could say explicitly: **a verification checkbox
  that turns out false should be marked `[!]` with evidence, not silently
  dropped or force-ticked.** That behavior emerged ad hoc here and was useful.
- `retro` currently assumes it runs before the PR. When the PR is already
  merged (as here), the retro needs its own branch — worth a line in the phase
  file about landing retro-only doc commits.
