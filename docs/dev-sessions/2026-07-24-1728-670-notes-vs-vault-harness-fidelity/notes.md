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
