# Notes: vault frontmatter rendering + editing

Session working log. Final summary goes here before the PR.

## Baseline

Worktree `.claude/worktrees/vault-frontmatter-ui`, branch `feat/vault-frontmatter-ui`,
off `origin/main` at `60079d5`. `HTTP_PORT=18897`.
`make test` before any changes: **3234 passed, 2 skipped** in 86.58s. The two
warnings are pre-existing `forkpty` deprecations in the terminal tests.

## Task log

- Task 1 — body-only writes preserve frontmatter verbatim: pending
- Task 2 — relocate `merge_frontmatter` to `frontmatter.py`: pending
- Task 3 — `vault_read` splits frontmatter/body: pending
- Task 4 — PUT `frontmatter` patch: pending
- Task 5 — PUT `frontmatter_raw` replace: pending
- Task 6 — sidebar summaries: pending
- Task 7 — `<wiki-metadata>` read-only + `wiki-page` on `body`: done
- Task 8 — metadata editing: pending
- Task 9 — docs + wrap-up: pending

## Open question to answer during Task 7

Does `wiki-editor.js:109`'s `md !== prev` listener fire on initial load? If it
does, merely *opening* a page in edit mode corrupted its frontmatter, not just
editing one. Answer belongs in the PR description either way.

**Answer:** No. Opening a page in edit mode and touching nothing did not
corrupt the file. Verified empirically against the isolated `/tmp/fm-smoke`
fixture (`agent/pages/FmSmoke`): opened the page in edit mode, waited 3.5s,
and the status indicator stayed idle (no "Saving..." ever appeared) — the
file was byte-identical to the pristine copy afterward. By contrast, typing a
single character into the body did trigger autosave ~1s later ("Saved"
status), and a byte-compare of just the `---`-delimited frontmatter block
(MD5) confirmed it was untouched — the body diff showed only the intended
edit plus an incidental Milkdown list-marker normalization (`*   ` → `* `,
pre-existing WYSIWYG round-trip behavior unrelated to this branch). So the
`markdownUpdated` listener's initial `defaultValueCtx` load does not count as
a change relative to itself (`md === prev` on first render) — the bug this
branch fixes was real editing, not merely opening a page.

## Task 7 details

Implemented `<wiki-metadata>` (compact strip, expandable to full detail incl.
unknown keys) and rewired `wiki-page.js` onto `body`/`frontmatter*` per the
brief, plus the `_loaded`-flag guard so an empty body with frontmatter still
renders.

**Deviation from the brief:** Step 6 only names `wiki-editor.js:246-247`, but
`#reload()` has three more raw `data.content` reads just below those two
lines (`replaceAll(data.content)`, `#lastSavedContent`, `#currentMarkdown`).
`#reload`'s fetch is hardcoded to `/api/vault/...` regardless of
`saveEndpoint`, so on the vault page those would all evaluate to `undefined`
once the endpoint stopped returning `content`, corrupting the reload/conflict
path. Fixed by computing `data.body ?? data.content ?? ''` once into a local
`newContent` and using it for all four assignments, keeping the `?? data.content`
fallback (and its rationale) for wiki-editor's other hosts.

**Browser verification** (isolated `DATA_HOME=/tmp/fm-smoke`, fixture
`agent/pages/FmSmoke`, restored afterward):
- View mode: no `<hr>`, no YAML list, summary + tag chips in the strip, body
  starts at the `# 0din` heading. Expanded strip showed all four known
  fields (summary, importance, tags, keywords) — no unknown keys in this
  fixture.
- Edit mode: Milkdown showed only the body, metadata strip rendered above it.
- Corruption check: typed a character into the body, waited ~2.5s for
  autosave ("Saved" status appeared). Byte-compared (MD5) the `---`-delimited
  frontmatter block against the pristine copy — identical. Body diff showed
  only the intended edit plus an incidental Milkdown list-marker
  normalization, unrelated to this branch. Fixture restored via `cp` from
  the pristine copy.
- See the "Open question" section above for the initial-load check.

`make check` clean (ruff, pyright, `tsc --checkJs`, message-types drift
check). `make test`: 3276 passed, 2 skipped (up from the 3234 baseline —
Tasks 1–6 added coverage; no regressions).

## Decisions (from brainstorm)

- Frontmatter is chrome, not content — split server-side, mirroring the
  existing `/api/schedules/*` pattern. Milkdown never sees the YAML.
- Server-side split, not client-side: no `js-yaml` dependency, and the server
  stays the single YAML authority.
- Body writes splice the raw block back **verbatim**, never through
  `yaml.dump`. `parse_frontmatter` reports `{}` for malformed YAML, so
  reserializing would delete it; `yaml.dump` also sorts keys and drops
  comments, churning formatting on every body edit.
- Raw editor holds the **whole** frontmatter with replace semantics — one rule
  instead of partitioning known vs unknown keys and erroring on collisions.
- Typed controls patch; raw replaces; mutually exclusive on the wire.
- Read-only compact strip in view mode, expandable.
- Tag chips inert this session — #318 owns tag query semantics.

## #318 coordination

#318 (first-class vault tags) is in flight in the `318-vault-tags` worktree and
reserves `/api/vault/tags` (Phase 5) and the web UI Tags tab (Phase 6).
`tags.py` already has `normalize_tag` and `pages_with_tags` with better
semantics than a frontmatter-only match — inline `#tags` too, AND-by-default
with an `any_tag` escape hatch.

Both branches edit `frontmatter.py`; the additions occupy different regions, so
whichever lands second resolves there. `split_frontmatter` must stay purely
lexical with no `tags` import so it doesn't deepen the existing
`frontmatter.py` ↔ `tags.py` cycle.
