# Web UI: Wiki Links and Page Viewer — Implementation Plan

## Step 1: Wiki API endpoints

**Files:** `src/decafclaw/http_server.py`

Add two new routes:

- `GET /api/wiki` — list all wiki pages
  - Walk `workspace/wiki/` for `*.md` files
  - Return `[{title, modified, path}]` sorted alphabetically
  - Authenticated via `_require_auth`

- `GET /api/wiki/{page:path}` — get single wiki page
  - Reuse `_resolve_page()` logic from wiki tools (import or inline)
  - Return `{title, content, modified}` as JSON
  - 404 if not found, path sandboxed to wiki dir

Add the standalone page route:

- `GET /wiki/{page:path}` — serve `wiki.html` (new template page)
  - Same HTML shell as `index.html` but loads wiki-page component
  - Page name extracted from URL by the component

**Test:** curl the endpoints, verify JSON responses.

## Step 2: Extract shared markdown rendering

**Files:** `src/decafclaw/web/static/lib/markdown.js` (new)

Extract `renderMarkdown()` and the custom renderer from `assistant-message.js` into a shared module so it can be reused by the wiki-page component.

- Move `marked` renderer setup (workspace:// rewriting) to shared module
- Add `[[wiki-link]]` detection as a `marked` extension (inline tokenizer + renderer)
  - Tokenizer regex: `/^\[\[([^\]]+)\]\]/` (match `[[Page Name]]`)
  - Renderer: emit `<a href="/wiki/Page%20Name" class="wiki-link" data-page="Page Name">Page Name</a>`
- Export `renderMarkdown(text)` function
- Update `assistant-message.js` to import from shared module

## Step 3: Wiki page component

**Files:** `src/decafclaw/web/static/components/wiki-page.js` (new)

A `<wiki-page>` Lit component that fetches and displays a wiki page.

- Properties: `page` (string — page name), `standalone` (boolean — controls link behavior)
- On `page` change: fetch `/api/wiki/{page}`, render markdown content
- Show title as `<h1>`, modified date, loading state, 404 state
- `[[wiki-links]]` inside the page:
  - In standalone mode: regular links to `/wiki/PageName`
  - In panel mode: click handler dispatches `wiki-navigate` event (panel catches it)
- "Open in new tab" link: `/wiki/{page}` with `target="_blank"`

## Step 4: Wiki side panel

**Files:** `src/decafclaw/web/static/components/wiki-panel.js` (new)

A `<wiki-panel>` component that hosts the wiki page viewer alongside chat.

- Properties: `open` (boolean), `page` (string)
- Contains a header with: title, wiki list button, close button (X)
- Body embeds `<wiki-page>` component
- Wiki list mode: fetches `/api/wiki`, shows clickable page list
- Listens for `wiki-navigate` events from child `<wiki-page>` to replace current page
- Close on Escape key

## Step 5: Layout integration

**Files:** `src/decafclaw/web/static/index.html`, `src/decafclaw/web/static/app.js`, `src/decafclaw/web/static/style.css`

- Add `<wiki-panel>` to the layout in `index.html` (right of `#chat-main`)
- Wire up global `wiki-link-click` events in `app.js` to open the panel
- CSS: panel sits right of chat, similar to sidebar but on the right
  - `--wiki-panel-width: 400px` CSS variable
  - Desktop: panel alongside chat, chat area shrinks
  - Mobile (<640px): panel slides over chat as overlay with backdrop
- Escape key closes panel

## Step 6: Standalone wiki page

**Files:** `src/decafclaw/web/static/wiki.html` (new)

Minimal HTML page that:
- Loads the same vendor bundles (lit, marked, dompurify, pico)
- Imports and renders `<wiki-page standalone>` component
- Extracts page name from URL path (`/wiki/Page%20Name`)
- Simple centered layout, good reading experience
- Auth check (redirect to login if not authenticated)

## Step 7: Styles for wiki links and panel

**Files:** `src/decafclaw/web/static/style.css`

- `.wiki-link` — styled distinctly from regular links (subtle background, maybe dotted underline)
- `wiki-panel` — right-side panel styles, close button, header, page list
- Mobile overlay styles with backdrop
- Wiki page content styles (headings, paragraphs within the viewer)

## Step 8: Lint, typecheck, test, commit

- `make check` (lint + typecheck + JS check)
- Manual testing in browser
- Commit and create PR
