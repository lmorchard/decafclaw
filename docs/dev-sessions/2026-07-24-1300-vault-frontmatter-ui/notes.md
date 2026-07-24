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
- Task 7 — `<wiki-metadata>` read-only + `wiki-page` on `body`: pending
- Task 8 — metadata editing: pending
- Task 9 — docs + wrap-up: pending

## Open question to answer during Task 7

Does `wiki-editor.js:109`'s `md !== prev` listener fire on initial load? If it
does, merely *opening* a page in edit mode corrupted its frontmatter, not just
editing one. Answer belongs in the PR description either way.

**Answer:** _pending_

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
