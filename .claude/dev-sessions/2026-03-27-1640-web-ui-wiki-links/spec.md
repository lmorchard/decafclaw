# Web UI: Wiki Links and Page Viewer

## Problem

The agent creates wiki pages and references them in chat with `[[wiki-links]]` syntax (e.g., "You can review the updated page here: [[Garden Pond Project]]"). In the web UI, these render as plain text — they're not clickable and users can't view the pages.

## Current State

- Wiki pages live at `workspace/wiki/*.md` as Obsidian-compatible markdown
- The markdown renderer already rewrites `workspace://` URLs to `/api/workspace/` paths
- The `/api/workspace/` endpoint serves files but forces download for non-images (including `.md`)
- Wiki tools (`wiki_read`, `wiki_list`, `wiki_backlinks`) exist on the backend

## Proposed Changes

### 1. Render `[[wiki-links]]` as clickable links in all messages

Extend the `marked` renderer to detect `[[Page Name]]` in raw markdown text and convert them to clickable links.

- Use a `marked` extension (tokenizer + renderer) to match `[[...]]` patterns
- Render as styled anchor tags that open the wiki side panel
- Style distinctly from regular links (e.g., subtle background or wiki-specific styling)
- Works in both assistant and user messages (shared markdown renderer)

### 2. Wiki page component (shared between panel and standalone page)

A reusable `wiki-page` component that fetches and renders a wiki page:

- Fetches page content from `/api/wiki/{page_name}`
- Renders markdown with the same pipeline (including nested `[[wiki-links]]`)
- Shows page title, last modified time
- "Open in new tab" link pointing to standalone page route

### 3. Wiki side panel

A collapsible side panel (right side of chat) — designed as the foundation for a future artifacts panel.

- Opens when clicking a `[[wiki-link]]` in chat
- Embeds the `wiki-page` component
- Closable via X button or Escape key
- Clicking a `[[wiki-link]]` inside the panel replaces the current page
- Wiki page browser: header button to list all pages, click to view
- **Desktop:** panel sits alongside chat, resizable or fixed width
- **Mobile (<640px):** panel slides over the chat area as an overlay

### 4. Standalone wiki page route

- `GET /wiki/{page_name}` — full HTML page rendering a single wiki page
- Reuses the same `wiki-page` component inside a minimal page shell
- Provides a good reading experience when opened in a new tab
- `[[wiki-links]]` within the standalone page link to other standalone pages

### 5. Wiki API endpoints

New endpoints for wiki content (JSON, not file download):

- `GET /api/wiki` — returns list of all wiki pages `[{title, modified}]`
- `GET /api/wiki/{page_name}` — returns `{title, content, modified}` as JSON
- Reuses path resolution logic from `wiki_read` tool (including subdirectory search)
- Authenticated (same session cookie as other endpoints)
- Path sandboxed to `workspace/wiki/`

## Out of Scope

- Editing wiki pages from the web UI (future feature)
- Backlink display in the viewer
- Search across wiki pages from the UI
- Full artifacts panel (this panel is a stepping stone toward that)
- Wiki list pagination
