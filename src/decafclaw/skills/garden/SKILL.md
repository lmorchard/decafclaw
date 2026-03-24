---
name: garden
description: Wiki gardening sweep — merge, link, split, and tidy wiki pages
schedule: "0 3 * * 0"
effort: strong
required-skills:
  - wiki
user-invocable: true
context: fork
---

# Wiki Gardening Sweep

Perform a holistic maintenance pass over the wiki knowledge base. This is about structural quality, not adding new information.

## Step 1: Survey

1. Use `wiki_list` to get all pages.
2. Read through pages, noting structural issues.

## Step 2: Merge Overlapping Pages

- Look for pages that cover similar or overlapping topics.
- If two pages are about the same thing, consolidate into one well-organized page.
- Redirect the merged page's content and update any `[[wiki-links]]` that pointed to it.

## Step 3: Fix Broken Links

- Scan pages for `[[wiki-links]]` that point to non-existent pages.
- For each broken link, decide:
  - Create a stub page if the topic deserves one
  - Remove the link if it's not useful
  - Fix a typo in the link if the target exists under a different name

## Step 4: Add Missing Connections

- Read through pages and look for topics mentioned in the text that have their own wiki pages but aren't linked.
- Add `[[wiki-links]]` where they're missing.
- Use `wiki_backlinks` on key pages to check their connectivity.

## Step 5: Update tl;dr Summaries

- For pages longer than ~20 lines, check if they have a `> tl;dr:` summary after the title.
- Add one if missing, update if the page content has changed significantly.

## Step 6: Split Oversized Pages

- If a page has grown very long (100+ lines), consider splitting into sub-pages.
- Create a summary parent page that links to the sub-pages.
- Move detailed sections into their own pages.

## Step 7: Review Orphan Pages

- Use `wiki_backlinks` to find pages with no incoming links.
- For each orphan, find related pages and add links to it.
- If a page is truly disconnected and has little value, note it for review.

## Finishing Up

- Summarize what you tidied: pages merged, links fixed, summaries added, etc.
- If the wiki is already in good shape, respond with HEARTBEAT_OK.
