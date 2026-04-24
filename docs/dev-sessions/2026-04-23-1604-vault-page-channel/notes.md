# Session Notes

## 2026-04-23

- Session started. Part of [#292](https://github.com/lmorchard/decafclaw/issues/292) — vault page as 3rd channel (Phase 4 of the channel-adapter arc).
- Working in an isolated worktree at `.claude/worktrees/vault-page-channel/` on branch `vault-page-channel`.
- Landed-decision going in:
  - **Option (a)** — dedicated daily rollup page at `agent/pages/notifications/YYYY-MM-DD.md`. Parallels the per-event model of the other channels while keeping notifications separate from `vault_journal_append`-authored journal entries (different semantics: system events vs agent observations).
  - Dream/garden can later consolidate old daily files into rolling summaries if volume warrants.
- Lightweight session like MM DM / email: brainstorm + spec, skip `plan.md` unless the brainstorm surfaces unexpected complexity.

## Brainstorm — Q&A trail

- **Q1: Entry format.** Bullet-per-entry (A) vs subheading-per-entry (B) vs table (C). **Landed on B** — each entry becomes its own markdown section, appears in Obsidian's document outline as a daily index, is fragment-linkable (`[[YYYY-MM-DD#14:32 UTC]]`), and handles variable-length bodies without awkward wrapping.
- **Q2: Embeddings.** Reindex on append (A) vs skip (B) vs throttled (C). **Landed on B** — notifications are rolling audit log, not reference material. Daily page grows throughout the day so each append would re-embed an ever-growing document. Cost outweighs the marginal search value; adding embedding later is a small forward change if a real use case surfaces.
- **Q3: Concurrent appends.** Per-path `asyncio.Lock` (A) vs POSIX atomic append (B). **Landed on A** — same pattern as `notifications._locks`, and POSIX atomic append silently breaks for entries > PIPE_BUF (e.g., a background-job failure with 500 chars of stderr).
- **Q4: Folder configurable vs hardcoded.** **Landed on A (configurable with sensible default)** — single string field, validated at use time via the same sandboxing pattern the vault tools use. Default `agent/pages/notifications` covers the common case.
- **Q5: Default `min_priority`.** `low` (A) vs `normal` (B) vs `high` (C). **Landed on A** — the channel's job is completeness. DM/email default high because external delivery is noisy; local page has no such concern. Users can raise the threshold if a producer gets chatty.
- **Q6: Index / landing page.** Maintain one (A) vs rely on vault navigation (B). **Landed on B** — Obsidian folder view + `vault_list(folder=...)` cover discoverability. The garden skill can build a summary later if useful.

Pre-resolved in spec:
- **Cross-referencing** — include `record.link` as-is; no speculative wiki-link construction.
- **Idempotency on restart** — out of scope; inbox is source of truth.

All 6 questions landed in one brainstorm pass. Skipping `plan.md` — execution is straightforward given the existing channel-adapter template.
