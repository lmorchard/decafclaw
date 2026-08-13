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

- Look for pages that cover similar or overlapping topics.
- If two pages are about the same thing, consolidate into one well-organized page.
- Redirect the merged page's content and update any `[[wiki-links]]` that pointed to it.

## Step 2.5: Reorganize Clusters into Folders

- Use `vault_reorganize_folders` to detect clusters of 3+ related agent pages and move them into dedicated subdirectories.
- The tool automatically updates `[[wiki-links]]` pointing to the moved pages.
- Review the proposed or executed moves. (If dry_run is true, the tool will just report proposed moves without executing them.)

## Step 3: Fix Broken Links

- Scan pages for `[[wiki-links]]` that point to non-existent pages.
- For each broken link, decide:
  - Create a stub page if the topic deserves one
  - Remove the link if it's not useful
  - Fix a typo in the link if the target exists under a different name

## Step 4: Add Missing Connections

- Read through pages and look for topics mentioned in the text that have their own pages but aren't linked.
- Add `[[wiki-links]]` where they're missing.
- Use `vault_backlinks` on key pages to check their connectivity.

## Step 5: Update tl;dr Summaries

- For pages longer than ~20 lines, check if they have a `> tl;dr:` summary after the title.
- Add one if missing, update if the page content has changed significantly.

## Step 6: Split Oversized Pages

- If a page has grown very long (100+ lines), consider splitting into sub-pages.
- Create a summary parent page that links to the sub-pages.
- Move detailed sections into their own pages.

## Step 7: Review Orphan Pages

- Use `vault_backlinks` to find pages with no incoming links.
- For each orphan, find related pages and add links to it.
- If a page is truly disconnected and has little value, note it for review.

## Step 8: Recompute Importance Scores

- Call `vault_recompute_importance` to deterministically refresh every agent page's `importance` frontmatter from measured signals (retrieval frequency, inbound-link count) — not a fresh guess. Scoped to `agent/` pages only, in keeping with this skill's charter — it never touches the user's own vault pages.
- Pages with no measured signal yet (new pages, or pages nobody has retrieved or linked to) are left untouched rather than zeroed — any existing importance (e.g. dream's initial guess) stays put until real signal accumulates.
- Review the reported deltas for outliers: a page that jumped or dropped sharply is worth a second look, since it usually means the retrieval or link graph shifted, not that the page itself changed.
- Spot-check frontmatter consistency on a few pages while you're in there — `summary`/`keywords`/`tags` that no longer match the body content.
- Flag pages that are both orphaned (zero inbound links via `vault_backlinks`) and rarely retrieved (low importance after recompute) — these are strong split/merge/delete candidates for a future pass, not something to act on unilaterally.
- Also flag weakly-linked pages (one or two thin connections) as candidates for Step 4's "add missing connections" work next time around.

## Finishing Up

End with a short narrative summary of what you tidied: pages merged, links fixed, summaries added, etc. If the vault was already in good shape and nothing needed attention, begin your summary with `HEARTBEAT_OK` on its own line followed by a brief quiet-cycle note — the leading marker lets the scheduler log a tidy line, and the narrative keeps the archive readable for the newsletter.
