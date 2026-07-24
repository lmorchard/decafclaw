# Spec: first-class vault tags (#318)

## Problem

The vault has partial tag support: `frontmatter.py` parses a `tags:` list and
`build_composite_text` folds it into embeddings, so frontmatter tags quietly
boost semantic search. Beyond that, tags are invisible:

- No way to **filter** search by tag.
- No way to **enumerate** tags in use (so the agent invents variants — `rust`
  vs `rust-lang` vs `Rust`).
- Journal entries store tags as body text (`- **tags:** foo`), not
  first-class metadata.
- Obsidian-style inline `#tags` in prose are ignored.

This issue makes tags a coherent, first-class subsystem.

## Prerequisite (satisfied)

Depends on #197 (merged): `vault_update_frontmatter` exists, and the
file-collision concern that gated this issue is gone. #197 also added
`read_recent_journal_entries` (#306), which parses the daily-file
`## YYYY-MM-DD HH:MM` per-entry format — a compatibility constraint this spec
respects.

## Design decisions (approved at brainstorm)

- **Unify at the index/query layer, not the file bytes.** Journal files keep
  the daily-file + `## timestamp` format untouched. "First-class tags" means
  the tag extractor + search/list/filter treat journal tags like page tags —
  the unification happens where it matters (queries), not in storage.
- **Inline `#tags` (Part 4) is the native per-entry mechanism.** Per-entry
  YAML frontmatter blocks were rejected: Obsidian only recognizes a single
  file-top `---` block; a mid-file `---` renders as a horizontal rule, so
  per-entry frontmatter would render *broken* in Obsidian and defeat the
  vault's Obsidian-compatibility. Inline `#tags` is Obsidian's native
  per-point tagging model.
- **On-demand scan, no persistent tag index.** `vault_tags()` and pure
  tag-filter scan pages+journals live per call. At personal-vault scale this
  is cheap, and it avoids the index-maintenance bug class (rename/delete
  desync, resolved-path handling) that cost #197 two Critical bugs.
- **AND by default** across multiple tags, with an `any_tag=true` escape hatch
  for OR.
- **Case-insensitive normalization** (lowercase canonical key, preserve
  first-seen display casing). **No** auto-merge of `-`/`_` variants — `rust`
  and `rust-lang` stay distinct (that's what `vault_tags()` visibility is
  for; auto-merging would be lossy).
- **Scope:** full feature, web UI Tags tab as the last/separable slice.

## Architecture

### `tags.py` — shared tag module (the core everything consumes)

- `normalize_tag(t: str) -> str` — strip leading `#`, lowercase, trim.
- `parse_inline_tags(body: str) -> set[str]` — inline `#tag` parser.
  Rules: after `#`, `[A-Za-z_/][A-Za-z0-9_\-/]*`; must not start with a digit
  (so `#42` is skipped); must be preceded by whitespace or start-of-line;
  **ignored inside fenced code blocks and inline code**.
- `extract_tags(content: str, source_type: str) -> set[str]` — the union of
  tags for one file: frontmatter `tags:` (`frontmatter.parse_frontmatter` +
  `get_frontmatter_field`) + journal `- **tags:**` bullet (journal source
  only) + inline `#tags`. Normalized + deduped.
- `collect_all_tags(config) -> dict[str, TagInfo]` — on-demand scan of all
  page + journal files → per-tag count + display form (+ page list for the
  UI click-through). Backs `vault_tags()` and `/api/vault/tags`.
- `pages_with_tags(config, tags: list[str], any_tag=False) -> list[str]` —
  pure tag filter over the scan; AND unless `any_tag`.

Fail-open on unreadable files (skip with `log.debug`), consistent with the
vault tools.

### Part 4 — inline `#tags` at index time

- `build_composite_text` (or the indexer) unions inline `#tags` from the body
  into the embedded metadata text, alongside frontmatter tags. **Requires a
  `make reindex`** for existing pages to pick this up — called out in docs.

### Part 3-lite — journal emission

- `vault_journal_append(tags, content)` — **unchanged signature**. Keeps the
  human-readable `- **tags:** foo` bullet (back-compat; #306 reader
  unaffected — it treats the entry as an opaque text block) **and**
  additionally emits the tags as inline `#tags` in the entry body. The
  extractor dedupes the two, so no double-count.

### Part 1 — tag-filtered `vault_search`

- Add `tags: list[str] = []` and `any_tag: bool = False`.
  - `query` + `tags` → semantic search, then filter candidates by
    `extract_tags` ⊇ tags (AND) / ∩ tags (any_tag).
  - empty `query` + `tags` → pure `pages_with_tags` scan.
  - Works across `page` / `journal` / `user` source types.
- Tighten the tool description (control surface). Add a `tool_choice` case +
  a behavior eval.

### Part 2 — `vault_tags()` + Tags tab (UI last)

- Tool `vault_tags()` → `[{tag, count}]` sorted by count; backed by
  `collect_all_tags`.
- REST `GET /api/vault/tags` → tags + counts (+ page list per tag for
  click-through). Follows existing `/api/vault/*` + `/api/workspace/*`
  conventions.
- Web UI: **Tags** tab in `web/static/components/conversation-sidebar.js`
  (next to Browse/Recent). Lists tags by count; clicking a tag shows its
  pages. Reuses the existing sidebar row conventions.

## Phases (commit per phase, subagent-driven)

1. **`tags.py` foundation** — `normalize_tag`, `parse_inline_tags`,
   `extract_tags`, `collect_all_tags`, `pages_with_tags` + full unit tests.
2. **Inline `#tags` at index time** — fold into `build_composite_text`; note
   reindex requirement.
3. **Journal inline emission** — `vault_journal_append` also emits inline
   `#tags`; back-compat tests (bullet preserved, #306 reader unaffected).
4. **Tag-filtered `vault_search`** — `tags`/`any_tag` params + description +
   `tool_choice` + behavior eval.
5. **`vault_tags()` tool + `/api/vault/tags`** — tool + REST endpoint + tests.
6. **Tags tab (web UI)** — Lit component + sidebar tab + smoke.

## Testing

- Unit (`tags.py`): extraction from each of the three sources + their union;
  code-block / inline-code exclusion; `#42` rejected; normalization + display
  preservation; AND vs `any_tag` filter; count aggregation; fail-open on bad
  files.
- Tool: `vault_search` with `tags` (query+tags, empty-query+tags, across
  source types); `vault_tags` shape/sort.
- Journal: `vault_journal_append` still writes the bullet AND emits inline
  `#tags`; `read_recent_journal_entries` still parses entries unchanged.
- Evals: tag-filtered search behavior; `vault_tags`-vs-`vault_search`
  `tool_choice` disambiguation.
- Web: Playwright/JS smoke that the Tags tab renders tags + click-through.
- `make check` + `make test` green before each phase commit.

## Docs (same PR)

- `docs/vault.md` — tags section (extraction sources, inline `#tags` rules,
  filter/list, journal behavior, reindex note).
- Web-ui docs — the Tags tab.
- `CLAUDE.md` — key-files entry for `tags.py`.
- `docs/context-composer.md` — only if the composite-text change warrants it.

## Non-goals (follow-ups)

- Tag renaming/merging tools (`rust-lang` → `rust`).
- Tag autocomplete in the page editor.
- Tag hierarchies beyond what inline `#foo/bar` naturally gives.
- Tag match as a third memory-retrieval scoring signal.
- Journal micro-evolution (the deferred #197 follow-up; now unblocked once
  journal tags are first-class).

## Risks

- **#306 journal-format compatibility** — the highest-risk integration point.
  Every journal change must keep `read_recent_journal_entries` +
  `vault_journal_append`'s existing format working; guarded by tests.
- **Reindex expectation** — inline-tag embedding boost needs `make reindex`;
  documented, not automatic.
- **Inline-tag false positives** — `#` in prose (e.g. `#1`, markdown ATX
  headings). Digit-start exclusion + whitespace-precedent + code-block
  exclusion mitigate; ATX headings start at SOL with `# ` (space) so `# Foo`
  is not a tag (`#Foo` would be) — verify in tests.
