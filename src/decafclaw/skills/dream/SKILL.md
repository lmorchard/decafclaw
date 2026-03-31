---
name: dream
description: Review recent journal entries and conversations, distill insights into vault pages
schedule: "0 */3 * * *"
effort: strong
required-skills:
  - vault
user-invocable: true
context: fork
---

# Memory Consolidation

Review recent journal entries and conversations, then distill insights into curated pages in your vault. Only read and write within `agent/`. Work through these phases:

## Phase 1: Orient

1. Use `current_time` to note the current date and time.
2. Use `vault_list` with `folder=agent/pages` to see what pages exist in your knowledge base.
3. For longer pages, read the tl;dr summaries to understand the current state of knowledge.
4. Note what topics are already well-covered vs sparse.

## Phase 2: Gather

1. Use `vault_search` with `source_type=journal` and `days=1` to get recent journal entries.
2. Use `vault_search` with broader queries related to active page topics — look for entries that should be integrated.
3. Use `conversation_search` to scan past conversations for content not yet captured in journal entries. Look for:
   - Corrections or updates to known facts
   - New preferences, opinions, or decisions
   - Project context and status changes
   - Recurring themes across conversations
   - Insights that were overlooked in the moment
4. For anything worth preserving from conversations, create journal entries via `vault_journal_append` before distilling into pages.
5. Make a mental list of findings worth integrating into pages.

## Phase 3: Consolidate

For each finding from the gather phase:

1. Use `vault_search` to find existing pages about the topic.
2. If a relevant page exists:
   - `vault_read` the page
   - Revise the page with new information — rewrite and restructure, don't just append
   - Update the `## Sources` section with where the new information came from
   - `vault_write` the updated page
3. If no relevant page exists:
   - Create a new page in `agent/pages/` with a descriptive title
   - Use `[[wiki-links]]` to connect to related pages
   - Include a `## Sources` section
   - `vault_write` the new page
4. Convert any relative dates ("yesterday", "last week") to absolute dates.
5. For pages that have grown longer than ~20 lines, add or update a `> tl;dr:` summary blockquote after the title.

## Phase 4: Prune

1. Check for contradictions between new information and existing page content.
2. Resolve contradictions in favor of newer, more authoritative information.
3. Note corrections in the Sources section (e.g. "Updated 2026-03-23: corrected per conversation").
4. Check for `[[wiki-links]]` that could be added between pages you've touched.

## Finishing Up

- If you made changes, summarize what you consolidated and any new pages created.
- If there was nothing new to consolidate, respond with HEARTBEAT_OK.
