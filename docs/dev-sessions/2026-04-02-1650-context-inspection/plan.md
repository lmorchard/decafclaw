# Context Inspection — Plan

## Overview

Surface context composer diagnostics through a sidecar file, REST endpoint, and web UI popover with a waffle chart visualization. 5 steps, each building on the previous.

Key files we're modifying/creating:
- `src/decafclaw/context_composer.py` — store recency + per-candidate tokens during scoring
- `src/decafclaw/agent.py` — write sidecar after each turn
- `src/decafclaw/http_server.py` — REST endpoint for context diagnostics
- `src/decafclaw/web/static/components/context-inspector.js` — new Lit component
- `src/decafclaw/web/static/components/conversation-sidebar.js` — click handler on context bar
- `src/decafclaw/web/static/styles/context-inspector.css` — styles

---

## Step 1: Enrich scoring data and write sidecar file

Store `recency` and per-candidate `tokens_estimated` during scoring. Write a JSON sidecar file after each turn with the full diagnostics.

### Prompt

```
Step 1: Enrich candidate scoring data and write context diagnostics sidecar.

In `context_composer.py`, modify `_score_candidates()`:
- Store `recency` on each candidate dict (alongside `composite_score`)
- After scoring in `_compose_memory_context`, compute per-candidate
  `tokens_estimated` using `estimate_tokens(r.get("entry_text", ""))`
  on each selected result

Add a method `_build_diagnostics(self, config, composed: ComposedContext) -> dict`:
- Build the full diagnostics dict matching the spec's JSON schema:
  - timestamp (ISO format)
  - total_tokens_estimated (from composed)
  - total_tokens_actual (from state.last_prompt_tokens_actual)
  - context_window_size (from _get_context_window_size)
  - compaction_threshold (from config.compaction.max_tokens)
  - sources (from composed.sources, serialized as list of dicts)
  - memory_candidates (from composed.memory_results, each with
    file_path, source_type, composite_score, similarity, recency,
    importance, modified_at, linked_from, tokens_estimated)

Add a module-level function `write_context_sidecar(config, conv_id: str, diagnostics: dict)`:
- Write to `workspace/conversations/{conv_id}.context.json`
- Use the same directory as archive_path
- Fail-open: log warning on error, never raise

In `agent.py`, after the LLM response and `record_actuals()`, call:
```python
diagnostics = composer._build_diagnostics(config, composed)
write_context_sidecar(config, conv_id, diagnostics)
```

Also add a `read_context_sidecar(config, conv_id: str) -> dict | None`:
- Read and parse the JSON file. Return None if missing or malformed.

Write tests:
- Test _build_diagnostics produces valid dict with all expected keys
- Test write_context_sidecar creates file with correct content
- Test read_context_sidecar reads it back
- Test read_context_sidecar returns None for missing file
- Test write_context_sidecar is fail-open (mock open to raise)

Run `make check && make test`.
```

---

## Step 2: REST endpoint

Add a GET endpoint that returns the sidecar diagnostics for a conversation.

### Prompt

```
Step 2: Add REST endpoint for context diagnostics.

In `http_server.py`, add a new route handler:

```python
@_authenticated
async def get_context_diagnostics(request: Request, username: str) -> JSONResponse:
    conv_id = request.path_params["id"]
    from .context_composer import read_context_sidecar
    data = read_context_sidecar(config, conv_id)
    if data is None:
        return JSONResponse({"error": "no context data"}, status_code=404)
    return JSONResponse(data)
```

Add the route:
```python
Route("/api/conversations/{id}/context", get_context_diagnostics, methods=["GET"]),
```

Place it near the other `/api/conversations/{id}/*` routes.

Write tests:
- Test endpoint returns 200 with diagnostics when sidecar exists
- Test endpoint returns 404 when no sidecar
- Test endpoint returns 401 when not authenticated

Run `make check && make test`.
```

---

## Step 3: Web UI popover component — waffle chart and source breakdown

Create the context-inspector Lit component with the waffle chart visualization and source breakdown table.

### Prompt

```
Step 3: Create the context-inspector web component.

Create `src/decafclaw/web/static/components/context-inspector.js`:

A Lit component that fetches and displays context diagnostics.

Properties:
- convId: String — the conversation ID to fetch diagnostics for
- open: Boolean — whether the popover is visible

Methods:
- async fetchData() — fetch `/api/conversations/${this.convId}/context`,
  store result in internal state
- _renderWaffleChart(sources, contextWindowSize) — render the grid
- _renderSourceTable(sources) — render the breakdown table
- _renderMemoryCandidates(candidates) — render the candidates list

Waffle chart:
- Calculate total cells = Math.ceil(contextWindowSize / tokensPerCell)
  where tokensPerCell is chosen to keep the grid reasonable (e.g.
  aim for ~200-400 cells: tokensPerCell = Math.ceil(contextWindowSize / 300))
- For each source, fill cells proportional to tokens_estimated
- Remaining cells are "unused" capacity
- Each cell is a small colored div (8x8px or similar)
- Colors: system_prompt=#4A90D9, history=#888, tools=#9B59B6,
  wiki=#27AE60, memory=#E67E22, unused=#eee
- Grid wraps via CSS flexbox
- Tooltip on hover showing source name + token count

Source breakdown table:
- Row per source: color swatch, name, tokens, items included/truncated
- For memory source: show score range and candidates considered
- For tools: show deferred mode status

Memory candidates list:
- Only shown if memory_candidates array is non-empty
- Each entry: truncated file_path, source type label, composite score
- Score breakdown as small colored inline bars:
  similarity (blue), recency (green), importance (orange)
  proportional to their contribution (weight * value)
- Graph expansion provenance ("← linked from X") if linked_from present

Popover positioning:
- Absolutely positioned, anchored near the context bar
- Dismiss on click outside (use a backdrop or document click listener)
- Max height with overflow scroll for long candidate lists

Create `src/decafclaw/web/static/styles/context-inspector.css` with styles.

No tests needed for the component (visual, tested manually).

Run `make check` (including check-js for type checking).
```

---

## Step 4: Wire popover into sidebar

Connect the context bar click to open the inspector popover.

### Prompt

```
Step 4: Wire the context inspector into the conversation sidebar.

In `conversation-sidebar.js`:

1. Import the context-inspector component (dynamic import or static)

2. Add a click handler on the context-usage div:
   ```javascript
   @click=${() => this.#toggleContextInspector()}
   ```

3. Add a `#toggleContextInspector()` method:
   - If inspector is open, close it
   - If closed, open it with the current conversation ID
   - The conversation ID comes from the conversation store's current selection

4. Add the context-inspector element to the sidebar template,
   positioned near the context bar:
   ```html
   <context-inspector
     .convId=${this._currentConvId}
     .open=${this._contextInspectorOpen}
     @close=${() => this._contextInspectorOpen = false}
   ></context-inspector>
   ```

5. Add state property: `_contextInspectorOpen: { type: Boolean, state: true }`

6. Style the context-usage bar with `cursor: pointer` to indicate clickability

7. The inspector should dismiss when:
   - User clicks outside it
   - User clicks the context bar again (toggle)
   - User switches conversations

Run `make check` (check-js).
Test manually in the browser.
```

---

## Step 5: Documentation and polish

Update docs, CLAUDE.md, handle edge cases.

### Prompt

```
Step 5: Documentation and edge case handling.

Edge cases to handle:
- No context data yet (first message not sent) — show "No context data
  yet" in the popover instead of the chart
- Context data from a previous session (conv loaded from archive) —
  stale data is fine, show timestamp so user knows when it's from
- Very small context windows — ensure waffle chart still renders
  reasonably with few cells

In `context-inspector.js`:
- Show loading state while fetching
- Show error state if fetch fails
- Show "No context data" if 404

Update CLAUDE.md:
- Add context-inspector.js to key files
- Add convention note about context diagnostics sidecar

Update docs/context-composer.md with the new inspection feature.
Update docs/index.md if needed.

Run `make check && make test`.
Commit all remaining changes.
```

---

## Summary of changes per step

| Step | New/Modified Files | Tests |
|------|-------------------|-------|
| 1 | `context_composer.py`, `agent.py` | `test_context_composer.py` |
| 2 | `http_server.py` | `test_http_server.py` or inline |
| 3 | `context-inspector.js` (new), `context-inspector.css` (new) | Manual browser testing |
| 4 | `conversation-sidebar.js` | Manual browser testing |
| 5 | `CLAUDE.md`, `docs/`, edge case fixes | Existing tests |

## Risk notes

- **Step 1 is the only backend change** — enriching scoring data and writing the sidecar. Low risk since it's additive and fail-open.
- **Step 3 is the largest** — the waffle chart component. Pure frontend, no backend risk. The visual design will need iteration.
- **Sidecar file size** — typically <5KB, written once per turn. Negligible I/O.
- **No auth bypass risk** — the REST endpoint uses the same `_authenticated` decorator as all other conversation endpoints.
