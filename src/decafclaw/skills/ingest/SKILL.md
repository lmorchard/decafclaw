---
name: ingest
description: Fetch a URL, workspace file, or attachment and integrate its content into the vault — one primary page plus cross-linked updates to related pages
user-invocable: true
context: inline
required-skills:
  - tabstack
allowed-tools: tabstack_extract_markdown, web_fetch, workspace_read, list_attachments, get_attachment, vault_search, vault_read, vault_write, vault_list, vault_backlinks, current_time
---

# Ingest

You are processing a single source into the vault. One source in, a handful of pages out. Stop when done — do not re-engage with the prior conversation unless the user asks in a new turn.

The source is in `$ARGUMENTS`. Parse it:

- Starts with `http://` or `https://` → URL.
- Starts with `workspace/`, or otherwise contains a `/` and therefore names a relative path → workspace file.
- No `$ARGUMENTS` at all, or a bare filename with no `/` → attachment. Call `list_attachments`; if there is exactly one, use it; if there are several, use the most recent and mention the choice; if none, report the error and stop.
- A ` — ` separator splits the source from an optional focus instruction (e.g. `https://... — focus on the security angle`). The focus shapes the content emphasis of what you write; it does NOT override the output folder.

Announce the source in one short line before you start (e.g. "Ingesting: https://example.com/article").

## Step 1: Fetch

Pick the right tool for the source type:

- **URL**: try `tabstack_extract_markdown` first. If it errors (rate limit, unsupported site, credits exhausted), fall back to `web_fetch`. Inspect the returned content — if it looks like an error page, a stub, or unrelated boilerplate, report the failure and stop. Do not fabricate content.
- **Workspace file**: `workspace_read(path=<path>)`. The `path` argument is relative to the workspace root — strip any leading `workspace/` prefix before calling. Example: `/ingest workspace/imports/paper.md` → `workspace_read(path="imports/paper.md")`.
- **Attachment**: `get_attachment(filename=<name>)`. For text files this returns the content. For images it returns media. For other binary formats (PDF, etc.) it returns only base64 metadata — in that case, report "binary attachment not directly ingestible — please extract text first" and stop.

For large sources, extract the core claims and notable details; don't try to mirror everything. A long paper becomes a page or two of distilled knowledge, not a wholesale copy.

## Step 2: Understand

Read the fetched content. Note:

- The primary topic (what the source is fundamentally about).
- Key entities (people, projects, tools, concepts) it references.
- Specific claims, findings, or recommendations worth preserving.
- Any focus instruction from `$ARGUMENTS` — let it shape what you foreground.

## Step 3: Search the vault

Use `vault_search` (and `vault_list` if you need to browse a folder) to find existing pages related to this source's topics. Cast a net wide enough to find:

- A page on the primary topic (may exist under a different slug).
- Pages on the major entities referenced.
- Pages on the broader concepts it touches.

**If a page on the exact source or its primary topic already exists under `agent/pages/`, treat it as the primary page and update in place.** If a strong match exists elsewhere in the vault (e.g. a user note), leave that page alone — Step 4 covers how to handle it.

## Step 4: Plan the updates

Decide what pages to touch:

**All writes from this skill stay under `agent/pages/` — do not write to admin pages, user pages, or anywhere else in the vault.** If `vault_search` surfaces a strong match that lives outside `agent/pages/`, do NOT edit it; instead create or update a page under `agent/pages/` that links to it.

- **One primary page** (required):
  - If a strong match under `agent/pages/` exists, update it. Keep the existing path.
  - Otherwise, create a new page under `agent/pages/`. Choose the subfolder based on content — prefer existing folders where they fit (`agent/pages/tools/`, `agent/pages/papers/`, `agent/pages/people/`, `agent/pages/projects/`, etc.). Create a new subfolder only when the topic is distinct and likely to grow.
- **Zero or more secondary pages** (optional): related pages under `agent/pages/` that should get a sentence or a cross-link added. Do not rewrite them wholesale. Skip any related page that lives outside `agent/pages/`.
- **Cap total pages at ~5** (primary + secondary). If more seem relevant, list them in the summary as candidates for the next garden pass rather than touching them this turn.

## Step 5: Write

For each planned update (all paths must start with `agent/pages/` — if you catch yourself about to write elsewhere, stop and reconsider):

- `vault_read` the existing page, then revise and `vault_write` the updated content. Keep the existing path (still under `agent/pages/`).
- For the new primary page, `vault_write(page=agent/pages/<subfolder>/<name>, content=...)` with:
  - YAML frontmatter:
    ```yaml
    ---
    tags: [ingested, <topic-tags>]
    summary: <one-line summary of the page>
    ---
    ```
  - Body that synthesizes the source in your own words. Add `[[wiki-links]]` to related pages you touched in this ingest.
  - `## Sources` section at the bottom with the URL or file path and the current date (use `current_time`).

Prefer synthesis over quotation. If you do quote, keep quotes short and attribute inline.

## Step 6: Summarize and stop

Deliver a change summary to the user in this shape:

```
Ingested: <source>
Primary page: [[Name]] (new | updated)
Secondary updates: [[A]], [[B]]
(Candidates for the next garden pass: [[C]], [[D]])
```

Only include the parenthetical line if there are deferred candidates.

Then stop. The user will tell you in a new turn if they want to expand a page, add more sources, or kick off a garden run.
