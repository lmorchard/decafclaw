# Web UI Mentions Autocomplete & Context Injection Implementation Plan

**Goal:** Allow users to mention workspace files, MCP resources, and vault pages in the composer with `@` autocomplete, and inject their full content into the LLM conversation context with smart size truncation.

**Approach:**
1. Implement `/api/autocomplete?q=...` backend endpoint that searches workspace files, active MCP resources, and vault pages.
2. Update `ContextComposer` to parse `@` references from the user message, retrieve their contents, and inject them as system/user-reference messages, keeping them under an 8KB (~200 lines) limit and appending truncation warnings.
3. Update the frontend `chat-input.js` element to trigger an autocomplete dropdown when a `@` token is typed, fetch matches from `/api/autocomplete`, and insert the chosen reference with correct syntax.

**Tech stack:** Python, Starlette, Lit (frontend Web Components), Pytest, Vitest.

---

## Phase 1: Backend Autocomplete Endpoint

This phase delivers the unified backend endpoint `/api/autocomplete?q=...` that prefix-matches and searches across workspace files, active MCP resources, and vault pages, returning formatted JSON for the UI to render.

**Files:**
- Modify: `src/decafclaw/http_server.py` — Register the autocomplete route, import/write matching logic for workspace, MCP, and vault pages.
- Create: `tests/test_autocomplete.py` — Comprehensive pytest unit tests for the `/api/autocomplete` endpoint.

**Key changes:**
- `autocomplete(request: Request, username: str) -> JSONResponse`: The Starlette route handler for `/api/autocomplete`.
- In `http_server.py`:
  ```python
  Route("/api/autocomplete", autocomplete, methods=["GET"]),
  ```

### Non-trivial Logic Snippet

```python
@_authenticated
async def autocomplete(request: Request, username: str) -> JSONResponse:
    # ...
```

**Verification — automated:**
- [x] `make check` passes — **Passed perfectly**
- [x] `pytest tests/test_autocomplete.py` passes — **5 passed**

**Verification — manual:**
- [x] None for this backend phase (fully covered by unit tests).

---

## Phase 2: Context Injection and Truncation in ContextComposer

Update the context composer to extract `@path/to/file` and `@mcp/server/resource` mentions, load their content, truncate if larger than 8KB, and inject them as `workspace_references` and `mcp_references` roles.

**Files:**
- Modify: `src/decafclaw/context_composer.py` — Update `ROLE_REMAP` and add bare mentions parsing & injection logic.
- Create: `tests/test_mentions_injection.py` — Comprehensive pytest unit tests for the parsing and content injection of bare mentions.

**Key changes:**
- Update `ROLE_REMAP` to include `workspace_references` and `mcp_references`.
- Implement `_compose_mentions_references` method on `ContextComposer`.
- Update `ContextComposer.compose` to call `_compose_mentions_references` and merge results.

**Verification — automated:**
- [x] `make check` passes — **Passed perfectly**
- [x] `pytest tests/test_mentions_injection.py` passes — **4 passed**

**Verification — manual:**
- [x] None for this logic phase (fully covered by unit tests).

---

## Phase 3: Frontend Autocomplete UI

Implement the trigger context, fetching from `/api/autocomplete`, selection commit, and dropdown menu rendering in the Web UI.

**Files:**
- Modify: `src/decafclaw/web/static/components/chat-input.js` — Update the autocomplete menu to handle `@` mentions.
- Modify: `src/decafclaw/web/static/styles/chat-input.css` — Add any necessary styling if needed for mention hints.
- Modify: `src/decafclaw/web/static/components/chat-input.test.js` — Add Vitest tests for the `@` autocomplete feature.

**Key changes:**
- Add `_mentionMatches` property.
- Modify `#triggerContext()` to detect `MENTION_TRIGGER_RE`.
- Implement `_mentionMatches` fetching.
- Update `#commitAutocomplete` to format and replace selected references.
- Update `render()` to draw the mention dropdown list.

**Verification — automated:**
- [x] `make check-js` passes — **Passed perfectly**
- [x] `make test-js` passes — **123 passed**

**Verification — manual:**
- [x] Open the Web UI, type `@` in the chat input, and verify the dropdown lists workspace files, MCP resources, and vault pages.
- [x] Select a workspace file (e.g. `@CLAUDE.md`), type a prompt, and verify that the backend correctly injects its content.
