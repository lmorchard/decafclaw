---
name: linkding-ingest
description: Fetch recent Linkding bookmarks, read their content, and record insights to the vault
schedule: "45 */4 * * *"
effort: default
required-skills:
  - tabstack
allowed-tools: shell($SKILL_DIR/fetch.sh), vault_read, vault_write, vault_search, vault_list, vault_backlinks, tabstack_extract_markdown, vault_journal_append, current_time, delegate_task
user-invocable: true
---

# Linkding Bookmark Ingestion

Fetch recent bookmarks from Linkding, then delegate each bookmark to a child agent for content extraction and vault integration.

## Output Folder

Write all bookmark-derived pages into `agent/pages/bookmarks/`. Use sub-organization by topic when it makes sense (e.g. `agent/pages/bookmarks/programming/`, `agent/pages/bookmarks/tools/`). Include `[[wiki-links]]` back to related pages elsewhere in the vault.

## Configuration

Required environment variables (set in `.env` or `config.json` env section):

| Env Var | Description |
|---------|-------------|
| `LINKDING_URL` | Linkding instance URL (e.g. `https://links.example.com`) |
| `LINKDING_TOKEN` | API token (Linkding Settings > Integrations) |

## Process

### 1. Fetch the bookmark list

Run the fetch script:

```
$SKILL_DIR/fetch.sh
```

This outputs ALL recent bookmarks as markdown. Read the entire output to get the full list.

### 2. Delegate each bookmark

For EACH bookmark in the list, use `delegate_task` to spawn a child agent that will:
- Fetch the full article content
- Analyze it for key insights
- Update the wiki

The task description for each delegate should include:

```
Process this bookmark and update the wiki knowledge base:

URL: {bookmark_url}
Title: {bookmark_title}
Tags: {bookmark_tags}
Description: {bookmark_description}

You have these tools available:
- tabstack_extract_markdown(url) — fetch full article content
- vault_search(query) — search vault pages by name/content
- vault_read(page) — read a vault page (parameter is "page", not "path")
- vault_write(page, content) — create or overwrite a vault page (parameters are "page" and "content")
- vault_backlinks(page) — find pages linking to a page

Instructions:
1. Use tabstack_extract_markdown(url="{bookmark_url}") to fetch the full content. If it fails (paywall, dead link), work with just the title, tags, and description above.
2. Analyze the content for key facts, insights, technologies, people, projects, or concepts.
3. Use vault_search(query="relevant topic") to find existing vault pages.
4. If a relevant page exists: vault_read(page="Page Name") to get it, revise with new info, vault_write(page="Page Name", content="...") to save.
5. If no relevant page exists and the topic is substantial: vault_write(page="agent/pages/bookmarks/New Page", content="...") with [[wiki-links]] and a ## Sources section.
6. Include the original URL and {bookmark_date} in ## Sources.
7. Extract knowledge — "X uses Y approach for Z problem" is better than "bookmarked an article about X".
8. Prefer adding to existing vault pages over creating new ones.
9. Do NOT use tool_search — it is not available. Use only the tools listed above.
```

You can delegate multiple bookmarks concurrently — `delegate_task` runs them in parallel.

Skip obviously low-signal bookmarks (duplicates, ephemeral content) — don't waste a delegate on them.

### 3. Finish

After all delegates complete, summarize what was processed and what wiki pages were updated or created.
If there was nothing interesting to ingest, respond with HEARTBEAT_OK.

## Rules

- **Delegate each bookmark** — don't try to fetch and process articles yourself, your context will fill up
- **Group related content** — include guidance in the delegate task to add to existing pages when possible
- Convert relative dates to absolute dates
- Include original URLs so sources can be revisited
