# Wiki Editor — Implementation Plan

**Branch:** `feat/wiki-editor`
**Spec:** `spec.md`
**Approach:** 3 phases, ~18 steps. Commit after each phase.

---

## Phase 1: Foundation

Get a working editor end-to-end: bundle Milkdown, create the component, add the write API, wire the edit toggle.

---

### Step 1.1: Add Milkdown to vendor bundle

**Context:** The frontend uses esbuild to bundle vendor deps into individual ESM files. Each lib gets its own file in `vendor/bundle/`, mapped via import maps in `index.html` and `wiki.html`. Milkdown is distributed as `@milkdown/kit` which re-exports sub-paths.

**Prompt:**

> Read `src/decafclaw/web/static/package.json` and `src/decafclaw/web/static/build-vendor.mjs`.
>
> 1. Add `"@milkdown/kit": "^7.19.0"` to `package.json` dependencies.
>
> 2. Create `src/decafclaw/web/static/milkdown-entry.js` — a thin entry point that re-exports what we need:
>    ```js
>    export { Editor, rootCtx, defaultValueCtx, editorViewCtx } from '@milkdown/kit/core';
>    export { commonmark } from '@milkdown/kit/preset/commonmark';
>    export { gfm } from '@milkdown/kit/preset/gfm';
>    export { history } from '@milkdown/kit/plugin/history';
>    export { listener, listenerCtx } from '@milkdown/kit/plugin/listener';
>    export { clipboard } from '@milkdown/kit/plugin/clipboard';
>    export { getMarkdown, replaceAll, $node, $remark, $inputRule, $command, $useKeymap } from '@milkdown/kit/utils';
>    export { InputRule } from '@milkdown/kit/prose/inputrules';
>    export { callCommand } from '@milkdown/kit/core';
>    export {
>      toggleStrongCommand, toggleEmphasisCommand,
>      wrapInBulletListCommand, wrapInOrderedListCommand,
>      wrapInBlockquoteCommand, insertHrCommand,
>      turnIntoTextCommand, createCodeBlockCommand,
>      toggleInlineCodeCommand, insertImageCommand,
>      updateLinkCommand, toggleStrikethroughCommand,
>      toggleTaskListCommand,
>    } from '@milkdown/kit/preset/commonmark';
>    ```
>    NOTE: Verify the exact export names — they may differ between commonmark and gfm presets. Check the Milkdown docs/source for the heading command names too. The goal is to export everything the editor component will need so it can all come from a single import.
>
> 3. Add a Milkdown bundle entry in `build-vendor.mjs`:
>    ```js
>    {
>      name: '@milkdown/kit',
>      entry: './milkdown-entry.js',
>      outfile: join(outdir, 'milkdown.js'),
>      external: [],  // self-contained
>    },
>    ```
>
> 4. Add Milkdown to the import map in `index.html`:
>    ```
>    "@milkdown/kit": "/static/vendor/bundle/milkdown.js"
>    ```
>
> 5. Add the same import map entry in `wiki.html`.
>
> 6. Run `cd src/decafclaw/web/static && npm install && npm run build` to verify the bundle builds.
>
> Don't commit the `vendor/bundle/milkdown.js` output yet — we'll do that after verifying it works.

---

### Step 1.2: Add `PUT /api/wiki/{page}` endpoint

**Context:** The wiki read routes exist (`GET /api/wiki`, `GET /api/wiki/{page}`). The wiki tool has `tool_wiki_write` that writes files and updates embeddings. We need an HTTP endpoint that does the same thing for the web UI.

**Prompt:**

> Read `src/decafclaw/http_server.py`, focusing on the existing wiki routes (wiki_list, wiki_read, _resolve_wiki_page) and the `@_authenticated` decorator.
>
> Also read `src/decafclaw/skills/wiki/tools.py` lines 60-81 for the wiki_write tool's file writing and embeddings update logic.
>
> 1. Add a `wiki_write` route handler with the `@_authenticated` decorator:
>    ```python
>    @_authenticated
>    async def wiki_write(request: Request, username: str) -> JSONResponse:
>    ```
>    - Extract `page` from path params, `content` and `modified` from JSON body
>    - Validate page path (block `..`, paths starting with `/`, ensure within wiki dir)
>    - If `modified` is provided, check file mtime — return 409 if file was modified since that timestamp (tolerance of ~1 second for float precision)
>    - Create parent directories if needed
>    - Write content to `workspace/wiki/{page}.md`
>    - Update embeddings index (same pattern as wiki_write tool — delete old, index new)
>    - Return `{ "ok": true, "modified": <new file mtime> }`
>
> 2. Register the route: `Route("/api/wiki/{page:path}", wiki_write, methods=["PUT"])`
>    - Make sure it's registered BEFORE the GET route for the same path so both methods work
>    - Or combine them into a single route that dispatches on method
>
> 3. Also update `wiki_list` and `wiki_read` to use the `@_authenticated` decorator (they currently use the old manual pattern).
>
> Run `make check && make test`.

---

### Step 1.3: Create `wiki-editor.js` — basic Milkdown Lit component

**Context:** This is the core new component. It wraps Milkdown in a Lit web component with auto-save behavior.

**Prompt:**

> Create `src/decafclaw/web/static/components/wiki-editor.js` — a Lit web component that wraps the Milkdown editor.
>
> **Properties:**
> - `page` (String) — wiki page name (for save API path)
> - `content` (String) — initial markdown content
> - `modified` (Number) — file mtime from last read
> - `saveEndpoint` (String) — API endpoint pattern, defaults to `/api/wiki/` (will be overridden for config files later)
>
> **Internal state:**
> - `_status` — one of: `'idle'`, `'editing'`, `'saving'`, `'saved'`, `'error'`, `'conflict'`
> - `_error` — error message string
> - `_editor` — Milkdown Editor instance
> - `_saveTimer` — debounce timer ID
> - `_lastSavedContent` — to avoid saving unchanged content
>
> **Lifecycle:**
> - `firstUpdated()`: Create a container div in the shadow DOM (or light DOM via `createRenderRoot() { return this; }`). Initialize Milkdown:
>   ```js
>   import { Editor, rootCtx, defaultValueCtx, editorViewCtx,
>            commonmark, gfm, history, listener, listenerCtx, clipboard }
>     from '@milkdown/kit';
>
>   this._editor = await Editor.make()
>     .config(ctx => {
>       ctx.set(rootCtx, this.querySelector('.milkdown-root'));
>       ctx.set(defaultValueCtx, this.content || '');
>       ctx.get(listenerCtx).markdownUpdated((ctx, md, prev) => {
>         if (md !== prev) this._onContentChange(md);
>       });
>     })
>     .use(commonmark).use(gfm).use(history).use(listener).use(clipboard)
>     .create();
>   ```
> - `disconnectedCallback()`: Flush pending save, destroy editor.
>
> **Auto-save:**
> - `_onContentChange(markdown)`: Set `_status = 'editing'`, reset debounce timer to 1000ms
> - `_scheduleSave()`: `clearTimeout(this._saveTimer); this._saveTimer = setTimeout(() => this._save(), 1000);`
> - `_save()`: Extract markdown via `getMarkdown()`, skip if same as `_lastSavedContent`. PUT to `${this.saveEndpoint}${encodeURIComponent(this.page)}` with `{ content, modified: this.modified }`. On success: update `this.modified` from response, set `_status = 'saved'`, emit `saved` event. On error: set `_status = 'error'`. On 409: set `_status = 'conflict'`.
> - Also save on blur: `this._editor` container gets a `focusout` handler.
>
> **close() method:** Flush pending save (clear timer, call `_save()` if dirty), then emit `close` event.
>
> **Render:**
> ```js
> render() {
>   return html`
>     <div class="wiki-editor-container">
>       <div class="wiki-editor-header">
>         <span class="wiki-editor-title">${this.page}</span>
>         <span class="wiki-editor-status">${this._statusText()}</span>
>         <button class="wiki-editor-close" @click=${() => this.close()}>Done</button>
>       </div>
>       <div class="milkdown-root"></div>
>     </div>
>   `;
> }
> ```
>
> Register: `customElements.define('wiki-editor', WikiEditor);`
>
> Import this component in `app.js`.
>
> Run `make check-js`.

---

### Step 1.4: Wire edit toggle in wiki-page.js

**Context:** The `wiki-page` component currently renders read-only markdown. Add an edit button that swaps to `<wiki-editor>`.

**Prompt:**

> Read `src/decafclaw/web/static/components/wiki-page.js`.
>
> 1. Add internal state `_editing` (Boolean, default false).
>
> 2. Add an "Edit" button in the header (next to the "open in new tab" link):
>    ```js
>    <button class="wiki-edit-btn" @click=${() => this._startEditing()} title="Edit page">&#9998;</button>
>    ```
>
> 3. When `_editing` is true, render `<wiki-editor>` instead of the read-only body:
>    ```js
>    ${this._editing
>      ? html`<wiki-editor
>          page=${this.page}
>          .content=${this._content}
>          .modified=${this._modified}
>          @saved=${this._onSaved}
>          @close=${this._onEditorClose}
>        ></wiki-editor>`
>      : html`<div class="wiki-page-body" @click=${this._handleClick}>
>          ${unsafeHTML(renderMarkdown(this._content))}
>        </div>`
>    }
>    ```
>
> 4. `_startEditing()`: Set `_editing = true`.
>
> 5. `_onSaved(e)`: Update `this._modified = e.detail.modified`. Optionally re-fetch to get latest content.
>
> 6. `_onEditorClose()`: Set `_editing = false`. Re-fetch page content to show the saved version.
>
> 7. Import `'./wiki-editor.js'` at the top of the file.
>
> 8. Add CSS for `.wiki-edit-btn` in `style.css` — small icon button matching the existing `.wiki-open-tab` style.
>
> Run `make check-js`.

---

### Step 1.5: Add formatting toolbar to wiki-editor

**Context:** The editor needs a formatting toolbar above the Milkdown editing area. Milkdown commands are dispatched via `editor.action(callCommand(commandKey))`.

**Prompt:**

> Read `src/decafclaw/web/static/components/wiki-editor.js` (created in Step 1.3).
>
> Add a formatting toolbar between the header and the editor root:
>
> 1. Define toolbar buttons as a data structure:
>    ```js
>    const TOOLBAR_BUTTONS = [
>      { label: 'B', title: 'Bold (Ctrl+B)', command: 'ToggleStrongCommand' },
>      { label: 'I', title: 'Italic (Ctrl+I)', command: 'ToggleEmphasisCommand' },
>      { label: 'S', title: 'Strikethrough', command: 'ToggleStrikethroughCommand' },
>      { type: 'separator' },
>      { label: 'H', title: 'Heading', command: 'WrapInHeadingCommand' },
>      { label: '•', title: 'Bullet List', command: 'WrapInBulletListCommand' },
>      { label: '1.', title: 'Ordered List', command: 'WrapInOrderedListCommand' },
>      { label: '☐', title: 'Task List', command: 'ToggleTaskListCommand' },
>      { type: 'separator' },
>      { label: '<>', title: 'Inline Code', command: 'ToggleInlineCodeCommand' },
>      { label: '```', title: 'Code Block', command: 'CreateCodeBlockCommand' },
>      { label: '>', title: 'Blockquote', command: 'WrapInBlockquoteCommand' },
>      { label: '—', title: 'Horizontal Rule', command: 'InsertHrCommand' },
>    ];
>    ```
>    NOTE: Verify the exact command key names from Milkdown's exports. They may be `toggleStrongCommand` (camelCase) not `ToggleStrongCommand`. Check what was exported in the milkdown-entry.js.
>
> 2. Render the toolbar:
>    ```js
>    <div class="wiki-editor-toolbar">
>      ${TOOLBAR_BUTTONS.map(btn =>
>        btn.type === 'separator'
>          ? html`<span class="toolbar-separator"></span>`
>          : html`<button class="toolbar-btn" title=${btn.title}
>              @click=${() => this._runCommand(btn.command)}>${btn.label}</button>`
>      )}
>    </div>
>    ```
>
> 3. `_runCommand(commandKey)`: Call `this._editor.action(callCommand(commandKey))`.
>    Import `callCommand` from `@milkdown/kit`.
>
> 4. Add CSS for the toolbar in `style.css`:
>    - Horizontal flex bar with small buttons
>    - Grouped with separators
>    - Match existing Pico CSS color scheme
>    - `.toolbar-btn` — small, borderless, hover highlight
>    - `.toolbar-separator` — thin vertical line
>
> Run `make check-js`.

---

### Step 1.6: Build vendor bundle and test end-to-end

**Prompt:**

> Run `cd src/decafclaw/web/static && npm install && npm run build` to build the Milkdown vendor bundle.
>
> Verify:
> - `vendor/bundle/milkdown.js` exists and is non-empty
> - `make check` passes (Python + JS type checks)
> - `make test` passes
>
> This is the integration checkpoint — all Phase 1 code should be wired together.

---

### Step 1.7: Commit Phase 1

> Run `make check && make test`. Stage all changes including the vendor bundle.
> Commit with message: "feat: wiki editor — Milkdown integration, auto-save, formatting toolbar"

---

## Phase 2: Polish

Wiki-link round-trip, conflict handling, keyboard shortcuts, styling, navigation guards.

---

### Step 2.1: Wiki-link plugin for Milkdown

**Context:** The editor needs to handle `[[wiki-link]]` syntax — parse it from markdown, render it as a clickable link in the editor, and serialize it back to `[[link]]` on save.

**Prompt:**

> Create `src/decafclaw/web/static/lib/milkdown-wiki-link.js` — a custom Milkdown plugin for `[[wiki-link]]` syntax.
>
> The plugin needs three parts:
>
> 1. **Remark plugin** — Parse `[[target]]` in the markdown AST during deserialization. Use `$remark` from Milkdown utils. Walk the AST, find text nodes containing `[[...]]`, split them into text + wikiLink nodes.
>
> 2. **ProseMirror node** — Define an inline atomic node `wikiLink` with `$node`:
>    - Attrs: `{ target: { default: '' } }`
>    - DOM rendering: `<a class="wiki-link" data-wiki-page="${target}" href="/wiki/${target}">${target}</a>`
>    - parseMarkdown: match nodes of type `wikiLink`, create the ProseMirror node
>    - toMarkdown: serialize back as `[[target]]` text
>
> 3. **Input rule** — Transform `[[text]]` as the user types the closing `]]`:
>    - Regex: `/\[\[([^\]]+)\]\]$/`
>    - Replace the matched range with a wikiLink node
>
> Export as: `export const wikiLinkPlugin = [remarkPlugin, wikiLinkNode, wikiLinkInputRule].flat();`
>
> Then in `wiki-editor.js`, import and add `.use(wikiLinkPlugin)` to the editor initialization chain.
>
> Run `make check-js`.

---

### Step 2.2: Conflict detection

**Context:** Auto-save sends the `modified` mtime with each PUT. The server returns 409 if the file changed since last read.

**Prompt:**

> Read `src/decafclaw/web/static/components/wiki-editor.js` and `src/decafclaw/http_server.py` (the wiki_write route).
>
> 1. In the editor's `_save()` method, handle 409 responses:
>    ```js
>    if (resp.status === 409) {
>      this._status = 'conflict';
>      this._error = 'Page was modified externally.';
>      // Pause auto-save — don't overwrite
>      clearTimeout(this._saveTimer);
>      return;
>    }
>    ```
>
> 2. Add conflict UI to the render method — when `_status === 'conflict'`, show an inline banner:
>    ```js
>    ${this._status === 'conflict' ? html`
>      <div class="wiki-editor-conflict">
>        <span>Page was modified externally.</span>
>        <button @click=${() => this._reload()}>Reload</button>
>        <button @click=${() => this._forceSave()}>Overwrite</button>
>      </div>
>    ` : nothing}
>    ```
>
> 3. `_reload()`: Re-fetch page content from GET API, replace editor content via `replaceAll()`, reset `_status`.
>
> 4. `_forceSave()`: Call `_save()` with `modified` set to `null` (server skips mtime check), reset `_status`.
>
> 5. In the server's `wiki_write` route, if `modified` is null/missing, skip the mtime check (allow force overwrite).
>
> Run `make check-js && make check && make test`.

---

### Step 2.3: Keyboard shortcuts and navigation guard

**Prompt:**

> Read `src/decafclaw/web/static/components/wiki-editor.js`.
>
> 1. Add Ctrl+S / Cmd+S keyboard shortcut for immediate save:
>    ```js
>    // In firstUpdated(), after editor creation:
>    this.addEventListener('keydown', (e) => {
>      if ((e.ctrlKey || e.metaKey) && e.key === 's') {
>        e.preventDefault();
>        this._flushSave();
>      }
>    });
>    ```
>    Where `_flushSave()` clears the debounce timer and calls `_save()` immediately.
>
> 2. Save on blur — add to firstUpdated():
>    ```js
>    this.querySelector('.milkdown-root')?.addEventListener('focusout', (e) => {
>      // Only if focus left the editor entirely (not moving between editor elements)
>      if (!this.contains(e.relatedTarget)) this._flushSave();
>    });
>    ```
>
> 3. In `wiki-page.js`, when the page property changes while editing, flush the current editor's save before loading the new page:
>    ```js
>    willUpdate(changed) {
>      if (changed.has('page') && this.page) {
>        if (this._editing) {
>          // Flush current editor, then switch
>          this.querySelector('wiki-editor')?.close();
>          this._editing = false;
>        }
>        this._fetchPage();
>      }
>    }
>    ```
>
> Run `make check-js`.

---

### Step 2.4: Style the editor

**Prompt:**

> Read `src/decafclaw/web/static/style.css` for the existing wiki page styles.
>
> Add CSS for the wiki editor. The editor content should visually match the read-only wiki page rendering:
>
> 1. `.wiki-editor-container` — full height of the wiki panel, flex column layout
>
> 2. `.wiki-editor-header` — flex row, page title left, status center, close button right. Match `.wiki-page-header` style.
>
> 3. `.wiki-editor-toolbar` — horizontal flex bar below header. Small buttons with hover states. Separator dividers between groups. Sticky at top when scrolling.
>
> 4. `.milkdown-root` — flex-grow to fill remaining space, overflow-y auto for scrolling. Apply the same typography as `.wiki-page-body` (scaled headings, paragraph spacing, code block styles, list styles).
>
> 5. ProseMirror-specific styles:
>    - `.ProseMirror` — min-height, padding, outline: none for focus
>    - `.ProseMirror:focus` — subtle border or background change
>    - `.ProseMirror p.is-editor-empty:first-child::before` — placeholder text
>    - Code blocks, blockquotes, tables should match the read-only styles
>    - Task list checkboxes should be clickable
>
> 6. `.wiki-editor-status` — small text, color-coded: grey for idle, blue for editing, green for saved, red for error
>
> 7. `.wiki-editor-conflict` — warning banner, yellow/orange background, inline with reload/overwrite buttons
>
> 8. `.wiki-edit-btn` — small icon button in wiki page header, matches `.wiki-open-tab` style
>
> 9. Dark mode support — use existing CSS variables or Pico dark mode selectors
>
> Run `make check-js` (for CSS syntax via tsc).

---

### Step 2.5: Commit Phase 2

> Run `make check && make test`. Commit with message:
> "feat: wiki editor polish — wiki-link plugin, conflict detection, keyboard shortcuts, styling"

---

## Phase 3: New Pages & System Prompt Editing

---

### Step 3.1: New page creation

**Prompt:**

> 1. Add `POST /api/wiki` endpoint in `http_server.py`:
>    - Body: `{ "name": "PageName", "content": "# PageName\n" }`
>    - Validate name (same rules as PUT path validation)
>    - Return 409 if page already exists
>    - Create the file, index in embeddings
>    - Return `{ "ok": true, "page": "PageName", "modified": <mtime> }`
>
> 2. In `conversation-sidebar.js`, add a "New Page" button at the top of the wiki tab page list:
>    ```js
>    <button class="wiki-new-page-btn" @click=${() => this._createWikiPage()}>+ New Page</button>
>    ```
>
> 3. `_createWikiPage()`: Prompt for page name, POST to API, then emit `wiki-open` event with the new page name and an `editing: true` flag.
>
> 4. In `app.js`, handle the `wiki-open` event's `editing` flag — if true, set the wiki-page into editing mode immediately after it loads.
>
> Run `make check && make test`.

---

### Step 3.2: Config file list API

**Context:** The config file editing reuses the same `<wiki-editor>` component but with different API endpoints and a defined list of editable files.

**Prompt:**

> Read `src/decafclaw/http_server.py` and `src/decafclaw/config.py` (to understand `agent_path` and `workspace_path`).
>
> 1. Define the list of editable config files as a constant in `http_server.py`:
>    ```python
>    _CONFIG_FILES = [
>        {"name": "SOUL.md", "path": "SOUL.md", "description": "Core identity prompt", "scope": "admin"},
>        {"name": "AGENT.md", "path": "AGENT.md", "description": "Behavioral instructions", "scope": "admin"},
>        {"name": "USER.md", "path": "workspace/USER.md", "description": "User-specific context", "scope": "workspace"},
>        {"name": "HEARTBEAT.md", "path": "HEARTBEAT.md", "description": "Heartbeat check sections", "scope": "admin"},
>        {"name": "COMPACTION.md", "path": "COMPACTION.md", "description": "Compaction prompt override", "scope": "admin"},
>    ]
>    ```
>
> 2. Add `GET /api/config/files` endpoint:
>    - Returns the list with actual `modified` timestamps (check if file exists on disk)
>    - For each file: resolve path relative to `config.agent_path`, check existence, get mtime
>    - Include `exists: bool` field — if false, the bundled default would be used
>    - Also discover `schedules/*.md` files dynamically and append them
>
> 3. Add `GET /api/config/files/{path:path}` endpoint:
>    - Validate path against allowed patterns (must match a known config file or `schedules/*.md`)
>    - Read and return `{ "content": "...", "modified": <mtime>, "name": "..." }`
>    - If file doesn't exist but has a bundled default, return the default content with `"default": true`
>
> 4. Add `PUT /api/config/files/{path:path}` endpoint:
>    - Same validation as GET
>    - Same mtime conflict detection as wiki PUT
>    - Write file to `config.agent_path / path`
>    - Return `{ "ok": true, "modified": <mtime> }`
>
> All three endpoints use `@_authenticated`.
>
> Run `make check && make test`.

---

### Step 3.3: Config file list UI — gear button + panel

**Prompt:**

> Read `src/decafclaw/web/static/components/conversation-sidebar.js` (for the theme toggle location at the bottom of the sidebar).
>
> 1. Add a gear button next to `<theme-toggle>` in the sidebar footer:
>    ```js
>    <button class="config-btn" title="Agent Config" @click=${() => this._openConfig()}>&#9881;</button>
>    <theme-toggle></theme-toggle>
>    ```
>
> 2. `_openConfig()`: Dispatch a `config-open` event (bubbles, composed).
>
> 3. Create `src/decafclaw/web/static/components/config-panel.js` — a Lit component that:
>    - Fetches `GET /api/config/files` on load
>    - Renders a list of config files with name, description, and modified date
>    - Shows "default" badge if file doesn't exist on disk yet
>    - Clicking a file emits `config-edit` event with `{ path, name }`
>    - Register as `<config-panel>`
>
> 4. In `app.js`, handle `config-open` event:
>    - Show the config panel in the wiki panel area (reuse `#wiki-main`)
>    - Hide the wiki page, show config panel
>
> 5. Handle `config-edit` event:
>    - Fetch the config file content
>    - Show `<wiki-editor>` with `saveEndpoint="/api/config/files/"` and the file's path/content/modified
>
> 6. Add CSS for `.config-btn` (gear icon, matches theme toggle size) and `.config-panel` (file list styling).
>
> Import config-panel.js in app.js.
>
> Run `make check-js`.

---

### Step 3.4: Wire config editing with wiki-editor

**Prompt:**

> Read `src/decafclaw/web/static/components/wiki-editor.js`.
>
> The `wiki-editor` component already has a `saveEndpoint` property. Verify it works for config files:
>
> 1. When used for config files, the save URL should be `${this.saveEndpoint}${this.page}` where `saveEndpoint` is `/api/config/files/` and `page` is the file path (e.g., `SOUL.md`).
>
> 2. In `config-panel.js`, when a file is selected, switch the panel content to show the editor:
>    ```js
>    <wiki-editor
>      page=${this._selectedFile.path}
>      .content=${this._fileContent}
>      .modified=${this._fileModified}
>      saveEndpoint="/api/config/files/"
>      @close=${() => this._selectedFile = null}
>    ></wiki-editor>
>    ```
>
> 3. Add a "back to list" behavior when the editor emits `close` — return to the file list view.
>
> 4. Show the file description and scope (admin vs workspace) in the editor header for config files.
>
> Run `make check-js && make check && make test`.

---

### Step 3.5: Commit Phase 3

> Run `make check && make test`. Commit with message:
> "feat: new page creation + system prompt config editing via wiki editor"

---

## Final Step

> Run full `make check && make test` one final time.
> Push branch, create PR against main referencing #169.
