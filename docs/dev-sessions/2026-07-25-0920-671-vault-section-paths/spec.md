# vault_section path resolution Spec

**Goal:** Make section paths resolve the way a caller would reasonably write
them — bare titles and partial paths — instead of requiring every path to be
rooted at the page's H1, and make the failure self-correcting when a path
genuinely can't resolve.

**Source:** [#671](https://github.com/lmorchard/decafclaw/issues/671)

## Current state

`Document.find_section(path)` splits on `/` and hands the parts to
`_walk_path`, which walks strictly from the document's top-level sections
(`_sections.py:155-158`, `:548-558`). On any page with an H1 title — i.e.
essentially every vault page — the H1 *is* the only top-level section, so a
`##` heading is addressable only as `<H1 Title>/<Section>`.

Reproduced on current `main` (`b1909a8`), post-#689:

```
find_section('Background')                 -> None
find_section('Project Notes/Background')   -> Background
find_section('TODO')                       -> None
find_section('## Background')              -> None
find_section('background')                 -> None

add_section('Status', level=2, after='Background')               -> False
add_section('Status', level=2, after='Project Notes/Background') -> True
```

`normalize_title` (`_sections.py:31-35`) already lowercases and strips
wiki-links, so case is *not* the problem — `'background'` fails for the same
rooting reason as `'Background'`. Leading `#` characters are not stripped.

**This is not a harness artifact.** The eval case "adds a section without
rewriting other sections" passes post-#689, but only because the agent now
spends 4 tool calls instead of 2 — with a real system prompt it discovers the
qualified path first rather than failing outright. The defect is unchanged;
fixing it should *reduce* tool calls.

13 call sites route through `find_section` (`_sections.py` ×11, `tools.py` ×2),
so fixing it there fixes `add` / `remove` / `rename` / `move` / checklist ops
uniformly.

## Desired end state

1. A path resolves if it matches **any trailing portion** of a full section
   path: `Background`, `Archive/Background`, and
   `Project Notes/Archive/Background` all reach the same section.
2. Exact full-path matches keep winning outright — no behavior change for
   callers already passing rooted paths.
3. When a path matches **more than one** section, the operation fails rather
   than guessing, and the error names every candidate path.
4. When a path matches **nothing**, the error lists the page's known paths.
5. `normalize_title` strips leading `#` characters, so `'## Background'`
   resolves.
6. Tool parameter descriptions state that a bare or partial title is accepted,
   with a realistic example.

## Design decisions

- **Decision:** Resolve by longest-suffix match against full paths — exact
  match first, then unique suffix.
  - **Why:** an agent that qualifies a path *more* should never get a worse
    result than one that qualifies it less. Leaf-only matching (what the issue
    proposes) leaves `Archive/Background` failing while both `Background` and
    the fully-rooted form work — a confusing seam.
  - **Rejected:** bare-leaf-only fallback (smaller, but leaves that seam).

- **Decision:** Ambiguity is an error, not a heuristic pick.
  - **Why:** these are mutating operations. Silently editing the wrong
    `## Notes` is worse than one extra round-trip, and the agent can
    self-correct from a candidate list.
  - **Rejected:** shallowest-match-wins — fewer round-trips, but the failure
    mode is a silent wrong-section edit with no signal.

- **Decision:** Keep `find_section(path) -> Section | None` unchanged; add a
  separate `Document.section_candidates(path) -> list[str]` used only when
  building an error message.
  - **Why:** `find_section` returning `None` on ambiguity keeps all 13 call
    sites correct-by-default (a mutation never proceeds on an ambiguous path)
    without threading a result object through every one. The error sites — the
    only places that need to *distinguish* ambiguous from missing — ask for
    candidates explicitly: non-empty means ambiguous, empty means missing.
  - **Rejected:** raising `AmbiguousSectionError` (turns a lookup into control
    flow across 13 sites); returning a `SectionLookup` dataclass everywhere
    (churns every call site for a diagnostic two of them need).

- **Decision:** Strip leading `#` in `normalize_title`.
  - **Why:** one line, applies uniformly to every caller, and `'## Background'`
    is a spelling the agent demonstrably reaches for — it's in the issue's
    repro.

## Patterns to follow

- `_sections.py:548-558` `_walk_path` — the existing exact-path walk. The
  suffix matcher goes alongside it, not inside it.
- `_sections.py:568-584` `_section_path` — already renders a section's full
  path; reuse for both the candidate list and the error text.
- `_sections.py:160-165` `list_sections` / `_flatten_sections` — the existing
  full-document walk to enumerate sections.
- `_sections.py:31-35` `normalize_title` — where `#` stripping belongs.
- Error style: `ToolResult(text="[error: ...]")` per CLAUDE.md — see
  `tools.py:1421`, `:1434`, `:1446`, and the bare-string variant at
  `_sections.py:612`.
- Tool descriptions at `tools.py:2083-2124` (`section`, `after`, `before`,
  `parent`) plus `:1991` and `:2038`.

## What we're NOT doing

- **Not changing `find_section`'s signature or return type.** The 13 call sites
  stay untouched.
- **Not making ambiguity resolvable by heuristic** (depth, document order,
  heading level).
- **Not touching the eval case** `adds a section without rewriting other
  sections` or its `max_tool_errors: 1` bound. The issue is explicit that the
  bound is right and the tool should be fixed instead.
- **Not restructuring document parsing or how the H1 is treated.** The H1
  remains a real section and a valid path segment.
- **Not fuzzy matching** — no edit distance, substring, or prefix matching.
  Segment titles still compare by exact normalized equality; only the *rooting*
  relaxes.
- **Not #683 / #676 / #650.**

## Open questions

- **How many paths should the "not found" error list on a large page?**
  **Default:** list up to 20, then truncate with `… and N more`. Per-page
  section counts are small in practice, and even a truncated list teaches the
  path shape, which is the point.
