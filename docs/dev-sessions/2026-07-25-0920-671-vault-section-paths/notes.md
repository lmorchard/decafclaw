# Notes — #671 vault_section path resolution

## Phase 4 — does the agent actually benefit? (2026-07-25)

`vertex-gemini-flash`, worktree at `6513b5f`.

### `evals/vault.yaml` — 4/6

```
[1/6] saves user-level fact to vault journal on 'remember'   FAIL  (1 tools)
[2/6] reaches for vault_search on explicit search request    PASS  (1 tools)
[3/6] finds specific fact under heavy distractor load        PASS  (0 tools)
[4/6] reads a named vault page directly without searching    PASS  (1 tools)
[5/6] reaches for vault_backlinks to find inbound links      PASS  (1 tools)
[6/6] adds a section without rewriting other sections        FAIL  (3 tools)
```

Case 1 picked `vault_write` over `vault_journal_append` — the #670 case, which
scored 10/10 under repeated sampling two days ago. One sample; noise-floor
territory, and nothing in this branch touches those two descriptions.

### The target case: the fix works, a different defect took over

**Tool calls dropped from 4 to 2 on passing runs** (baseline: 4, in
`2026-07-24-2323`). That is the win the fix was aimed at.

Re-ran the case 3× per the plan rather than concluding from one run:

| run | result | calls |
|---|---|---|
| in `vault.yaml` | FAIL | 3 |
| `addsec-1` | **PASS** | **2** |
| `addsec-2` | FAIL | 4 |
| `addsec-3` | **PASS** | 4 |

2/4 overall. **In every failure `after: "Background"` — the bare title — was
accepted.** Path resolution is no longer the blocker; #671's defect is fixed.

### What blocks it now: `vault_section add` can't set a body

`Document.add_section` has taken a `content` parameter all along.
`tool_vault_section` never exposed it — pre-existing on `origin/main`, nothing
to do with this branch. So "add a section titled X with body Y" always needs a
second operation, and the second operation is where it goes wrong:

```
# run 1 — guessed the obvious API
vault_section {"title": "Status", "after": "Background", "content": "Working on it."}
  -> [error executing vault_section: tool_vault_section() got an unexpected
      keyword argument 'content'. Expected parameters: page, action, section,
      title, level, after, before, parent]
  -> fell back to read + manual rewrite

# addsec-2 — created the section, then rewrote the page to fill it
vault_section {"title": "Status", "after": "Background"}   # empty section
vault_read
vault_write  # full-page rewrite, mangled the layout
```

The capability exists one layer down:

```python
d.add_section('Status', level=2, content='Working on it.', after='Background')
```

produces exactly the output the eval asserts, in one operation.

**Decision:** Les scoped this in as Phase 5 rather than deferring it — same
tool, same class of defect (a natural operation the tool blocks), and the
underlying method already implements it.

### Minor formatting nit — noted, not fixed

`add_section` emits `## Status\nWorking on it.` with no blank line after the
heading. Valid markdown, not idiomatic. Left alone deliberately: it touches
shared insertion formatting that existing round-trip tests may pin, and it
isn't blocking anything.

---

## Phases 5–6 — the content gap, and the level default it exposed

### Phase 5: `content` on `vault_section add`

One parameter, a passthrough, a description, two tests. The eval repro then
went **2/4 → 0/3** — worse, not better.

That was a genuine (if indirect) regression from the change: by making the
one-call path viable, it made the agent hit a pre-existing bad default far more
often.

### Phase 6: `level` defaulted to 1, which reparents the page

The agent omitted `level`, the tool defaulted to `1`, and a `# Status` landed
mid-page after a `##`. That isn't cosmetic — it silently restructures the
document:

```
# Project Notes
  ## Background
# Status              <- inserted here
  ## TODO             <- now a child of Status
  ## References       <- now a child of Status

paths change: 'Project Notes/TODO' -> 'Status/TODO'
```

`level` now defaults to whatever the anchor implies — sibling of
`after`/`before`, one below `parent`, and only 1 when there is no anchor at
all. Explicit levels still win.

**A bug I introduced and then caught by running the real thing:** the tool
guarded `not isinstance(level, int)` before `level` became optional, so an
omitted level died with `[error: level must be between 1 and 6, got None]`.
The Phase 6 unit tests called `Document.add_section` directly and sailed past
it — the tool-layer guard was only visible in the eval. Two tool-level tests
added; this is the
[[feedback_signature_change_test_scope]] shape exactly.

### Phase 2's own message had a dead end

The last eval failure surfaced a flaw in the error text written earlier this
session. When two headings share a path, the candidate list renders
identically:

```
ambiguous section path 'Status' matches 2 sections:
  Project Notes/Status
  Project Notes/Status
Use a longer path to disambiguate.
```

No longer path exists. The agent retried, then fell back to a full rewrite and
blew its call budget. Duplicate paths now get their own message pointing at the
only real ways out (rename/remove one, or edit by line number).

### Measured outcome

| stage | repro result | notes |
|---|---|---|
| baseline `2026-07-24-2323` | pass | 4 tool calls |
| after Phases 1–3 | 2/4 | bare titles accepted; blocked on missing `content` |
| after Phase 5 | 0/3 | one-call path viable → hit the `level=1` default |
| after Phase 6 | **3/3** | |
| confirmation run | **2/3** | one run reached **1 tool call** |

**5/6 across the two post-fix runs, versus 2/4 before**, and the best runs do in
1–2 calls what took 4 at baseline. The single remaining failure was the
duplicate-heading loop, now addressed.

Still not a *reliable* case — this is a flaky corner of the suite and was before
any of this. Recorded rather than chased.

---

## Retrospective

Shipped as [#705](https://github.com/lmorchard/decafclaw/pull/705), merged
`053c1bb`, closing #671.

### Recap

The reported bug — section paths silently required a page-H1 root — was fixed
by ~20 lines in `find_section`. Everything else in the PR came from what that
unblocked: two further defects in the same tool, better miss diagnostics, and
an unrelated build trap found while preparing the PR.

### Scope drift

Three expansions, each surfaced and approved rather than absorbed:

1. **`content` on `vault_section add`** (Phase 5) — the next blocker on the
   eval case once paths resolved.
2. **`level` inference** (Phase 6) — Phase 5 exposed it by making a one-call
   path viable.
3. **`describe_section_miss` duplicate-heading branch** — fixing a dead end in
   my *own* Phase 2 message, found by an eval failure.

The pattern is worth naming: **fixing an ergonomic blocker doesn't reveal a
working tool, it reveals the next blocker.** Each fix moved the agent one step
further down the happy path and into the next pothole. I'd expect that shape
again on any "the tool won't let me do the obvious thing" issue, and would now
plan for two or three rounds rather than treating the first fix as the whole
job.

One thing I deliberately did *not* absorb: the `## Status\nWorking on it.`
missing-blank-line nit. Recorded, left alone.

### Surprises

- **My #670 retro hypothesis was wrong.** I'd speculated #671 might be a
  harness artifact because its eval case started passing after the eval
  system-prompt fix. One command disproved it: the bug reproduces
  deterministically in plain Python. The eval passed because the agent spent
  *4 tool calls instead of 2* working around it. **A test going green can mean
  the workaround got cheaper, not that the defect went away.**
- **Phase 5 made the measured outcome worse** — 2/4 → 0/3. Correct change,
  worse number, because it exposed a bad default underneath. Worth remembering
  that a regression in the metric isn't automatically a regression in the code.
- **An H1 inserted mid-page silently reparents everything below it.** I went
  looking for a heading-level cosmetic mismatch and found a document-structure
  bug: `Project Notes/TODO` becomes `Status/TODO` with no error.
- **`make check` rewrote a tracked lockfile.** Nothing to do with this work;
  caught only because a final diffstat read `558 deletions` on a file the
  branch never touched.

### Workflow friction

- **Running the real thing caught two defects the unit tests structurally
  could not.** The `level=None` validation gap lived at the tool layer while my
  tests called `Document.add_section` directly; the duplicate-heading dead end
  only appears when an agent actually loops on it. This is
  [[feedback_signature_change_test_scope]] again — and the fix is not "write
  more unit tests" but "exercise the layer the caller actually uses."
- **The `[!]` checkbox state earned its keep on its first outing.** Three
  checkboxes ended `[!]` rather than ticked or deleted. Writing "DOES NOT HOLD
  AS WRITTEN" next to the evidence kept the plan honest as a record and forced
  the Phase 5 → Phase 6 investigation instead of quietly moving on.
- **Re-running before concluding was load-bearing, repeatedly.** Every single
  eval number in this session moved between runs. The one-sample conclusions I
  would have drawn — "case 43 is deterministic", "Phase 5 broke it", "Phase 6
  fixed it reliably" — were all wrong or overstated.

### Misses

- **`git add -A src/` is too broad a habit.** It swept in a lockfile rewrite
  and would have shipped it silently in an unrelated PR. #709 notes the same
  thing already happened in #700. Stage named paths, or read the diffstat before
  every squash — I caught it only at the last review gate.
- **I checked `_section_path`'s output format late.** The candidate paths in my
  new error messages would have rendered lowercase (`project notes/background`)
  if I hadn't happened to look while writing the plan. Worth checking what a
  helper *renders* before building user-facing text on it.
- **Copilot found a genuine regression in my own refactor** — mixed anchor
  precedence when `after` and `before` are both passed. My tests covered each
  argument alone and never the combination. When a refactor collapses several
  branches into one resolution step, test the argument combinations the old
  branches kept separate.

### Memory candidates

1. "A green test can mean the workaround got cheaper" — the #671-was-not-a-
   harness-artifact lesson. Generalizes past this repo. **Worth saving.**
2. `git add -A` / `git add <dir>` sweeping unrelated tracked modifications into
   a commit; read the diffstat before squashing. **Worth saving** — it has now
   cost real work twice in this repo.
3. Ergonomic fixes reveal the next blocker; plan for rounds. **Worth saving.**
4. `make check` rewriting the lockfile → now guarded by #709, and the
   background is in that PR. **No memory needed.**

### Skill candidates

- `pr` phase could make "read the full `--stat` before squashing, and account
  for every file" an explicit step rather than something I did by habit. It is
  what caught the lockfile here.
