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
