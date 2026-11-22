---
name: garden
description: Vault gardening sweep — merge, link, split, and tidy agent pages
effort: strong
required-skills:
  - vault
user-invocable: true
context: inline
---

# Vault Gardening Sweep

Perform a holistic maintenance pass over your agent pages in the vault. This is about structural quality, not adding new information. Only read and write within `agent/`.

## Step 1: Survey

1. Use `vault_list` with `folder=agent/pages` to get all your pages.
2. Read through pages, noting structural issues.

## Step 2: Merge Overlapping Pages

- Call `vault_find_duplicate_candidates` to get high-similarity page pairs from the existing embedding index; it never re-embeds or merges.
- Review each candidate pair. Similar vectors are a hint, not a verdict — a shared topic is not always a duplicate.
- If two pages are about the same thing, consolidate into one well-organized page.
- Redirect the merged page's content and update any `[[wiki-links]]` that pointed to it.

## Step 2.5: Reorganize Clusters into Folders

- Use `vault_reorganize_folders` to detect clusters of 3+ related agent pages and move them into dedicated subdirectories.
- The tool automatically updates `[[wiki-links]]` pointing to the moved pages.
- Review the proposed or executed moves. (If dry_run is true, the tool will just report proposed moves without executing them.)

## Step 3: Fix Broken Links

- Call `vault_broken_links` to get unresolved `[[wiki-links]]` with line numbers and nearest-match suggestions.
- Review each entry; suggestions can be a moved page or just a similar title.
- For each broken link, decide:
  - Create a stub page if the topic deserves one
  - Remove the link if it's not useful
  - Fix a typo in the link if the target exists under a different name

## Step 4: Add Missing Connections

- Call `vault_graph_report` (sections `["totals", "sinks", "toeholds", "pages"]`) to see where the graph is thin in one pass.
- Sinks (high outbound, negligible inbound) need links *to* them; toeholds (high inbound, negligible outbound) need outward links. Isolates and weak fragments need both.
- Read the candidate pages and look for topics mentioned in the text that have their own pages but aren't linked.
- Add `[[wiki-links]]` where they're missing.

## Step 5: Update tl;dr Summaries

- Use `vault_frontmatter_audit`'s `missing_tldr` category to find pages longer than ~20 lines without a `> tl;dr:` summary.
- Add one if missing, update if the page content has changed significantly.

## Step 6: Split Oversized Pages

- Use `vault_frontmatter_audit`'s `oversized` category to find pages over ~100 lines.
- If a page has grown very long, consider splitting into sub-pages.
- Create a summary parent page that links to the sub-pages.
- Move detailed sections into their own pages.

## Step 7: Review Orphan Pages

- Call `vault_graph_report` with sections `["isolates", "components"]` to get every page with no incoming links, grouped by component size.
- For each orphan, find related pages and add links to it.
- If a page is truly disconnected and has little value, build an explicit deletion plan with `vault_delete_plan` in its default dry-run mode, review the proposed set, then execute with `dry_run=false`.
- `vault_delete_plan` refuses any page that still has inbound links unless you pass `allow_broken_links=true`; when you do, it reports the pages left with dangling links so you can repair them in the same pass.

## Step 8: Recompute Importance Scores

- Call `vault_recompute_importance` to refresh every agent page's `importance` frontmatter from structural PageRank over the backlink graph — not retrieval frequency, not a fresh guess. Scoped to `agent/` pages only, in keeping with this skill's charter — it never touches the user's own vault pages.
- Pages with zero inbound links get the PageRank floor. That is expected; use `vault_graph_report` to see whether the page is an isolate, sink, or toehold, and fix connectivity in Steps 4 and 7 rather than hand-editing importance.
- Review the reported deltas for outliers: a page that jumped or dropped sharply is worth a second look, since it usually means the link graph shifted, not that the page itself changed.
- Call `vault_frontmatter_audit` to catch malformed YAML, missing `summary`/`keywords`/`tags`/`importance`, oversized pages, and pages missing `> tl;dr:`. Its stale-summary overlap hint is advisory only — review the page before rewriting it.
- Flag pages that are both orphaned (zero inbound links in `vault_graph_report`) and rarely retrieved (low importance after recompute) — these are strong split/merge/delete candidates for a future pass, not something to act on unilaterally.
- Also flag weakly-linked pages (one or two thin connections) as candidates for Step 4's "add missing connections" work next time around.

## Finishing Up

End with a short narrative summary of what you tidied: pages merged, links fixed, summaries added, and **pages deleted with explicit counts** (including any deletion refused by the inbound-link guard and why). If the vault was already in good shape and nothing needed attention, begin your summary with `HEARTBEAT_OK` on its own line followed by a brief quiet-cycle note — the leading marker lets the scheduler log a tidy line, and the narrative keeps the archive readable for the newsletter.
