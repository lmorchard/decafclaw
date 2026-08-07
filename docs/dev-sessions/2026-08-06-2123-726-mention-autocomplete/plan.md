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
    query = request.query_params.get("q", "").strip()
    if query.startswith("@"):
        query = query[1:]
    
    results = []
    
    # 1. Search Vault Pages
    config = request.app.state.config
    vault_root = config.vault_root
    if vault_root.is_dir():
        # Look for .md files in the vault (excluding hidden directories/files starting with .)
        try:
            # We run in a thread because rglob can block
            def _find_vault_pages():
                matches = []
                for p in vault_root.rglob("*.md"):
                    if any(part.startswith(".") for part in p.parts):
                        continue
                    rel = p.relative_to(vault_root)
                    page_name = str(rel.with_suffix(""))
                    if not query or query.lower() in page_name.lower():
                        matches.append({
                            "type": "vault",
                            "id": page_name,
                            "label": page_name,
                            "description": f"Vault Page"
                        })
                matches.sort(key=lambda x: x["label"].lower())
                return matches[:20]
            
            vault_matches = await asyncio.to_thread(_find_vault_pages)
            results.extend(vault_matches)
        except Exception as e:
            log.warning("Autocomplete vault page search failed: %s", e)

    # 2. Search MCP Resources
    from .mcp_client import get_registry
    registry = get_registry()
    if registry:
        try:
            mcp_resources = registry.get_resources()
            mcp_matches = []
            for server_name, res in mcp_resources:
                uri = str(getattr(res, "uri", ""))
                name = str(getattr(res, "name", uri))
                desc = str(getattr(res, "description", ""))
                # Match against server name, resource name, or uri
                mcp_id = f"{server_name}/{name}"
                if not query or any(query.lower() in val.lower() for val in [server_name, name, uri]):
                    mcp_matches.append({
                        "type": "mcp",
                        "id": mcp_id,
                        "label": f"mcp/{server_name}/{name}",
                        "description": desc or f"MCP Resource: {uri}"
                    })
            mcp_matches.sort(key=lambda x: x["label"].lower())
            results.extend(mcp_matches[:20])
        except Exception as e:
            log.warning("Autocomplete MCP search failed: %s", e)

    # 3. Search Workspace Files
    workspace_root = config.workspace_path
    if workspace_root.is_dir():
        try:
            def _find_workspace_files():
                matches = []
                workspace_resolved = workspace_root.resolve()
                for dirpath, dirnames, filenames in os.walk(workspace_root):
                    # Prune hidden or ignored dirs in-place
                    dirnames[:] = [d for d in dirnames if not d.startswith(".") and d != "node_modules"]
                    for fname in filenames:
                        if fname.startswith("."):
                            continue
                        fpath = Path(dirpath) / fname
                        try:
                            resolved = fpath.resolve()
                            rel = resolved.relative_to(workspace_resolved)
                            rel_str = rel.as_posix()
                        except (OSError, ValueError):
                            continue
                        
                        if not query or query.lower() in rel_str.lower():
                            matches.append({
                                "type": "file",
                                "id": rel_str,
                                "label": rel_str,
                                "description": f"Workspace File"
                            })
                matches.sort(key=lambda x: x["label"].lower())
                return matches[:20]

            workspace_matches = await asyncio.to_thread(_find_workspace_files)
            results.extend(workspace_matches)
        except Exception as e:
            log.warning("Autocomplete workspace search failed: %s", e)

    return JSONResponse({"results": results[:50]})
```

**Verification — automated:**
- [ ] `make check` passes
- [ ] `pytest tests/test_autocomplete.py` passes

**Verification — manual:**
- [ ] None for this backend phase (fully covered by unit tests).

---

## Phase 2: Context Injection and Truncation in ContextComposer

Update the context composer to extract `@path/to/file` and `@mcp/server/resource` mentions, load their content, truncate if larger than 8KB, and inject them as `workspace_references` and `mcp_references` roles.

**Files:**
- Modify: `src/decafclaw/context_composer.py` — Update `ROLE_REMAP` and add bare mentions parsing & injection logic.
- Create: `tests/test_mentions_injection.py` — Comprehensive pytest unit tests for the parsing and content injection of bare mentions.

**Key changes:**
- Update `ROLE_REMAP` to include:
  ```python
  "workspace_references": "user",
  "mcp_references": "user",
  ```
- Implement `_compose_mentions_references(self, ctx, config, user_message: str, history: list, mode: ComposerMode) -> tuple[list[dict], SourceEntry | None]` method on `ContextComposer`.
- Update `ContextComposer.compose` to call `_compose_mentions_references` and merge results into `combined` list of messages.

### Non-trivial Logic Snippet

```python
_MENTION_RE = re.compile(r'(?<!\w)@([a-zA-Z0-9_./+-]+)')

def parse_bare_mentions(user_message: str) -> list[dict]:
    """Parse bare mentions starting with @, distinguishing between files and MCP.
    
    Ignores vault mentions like @[[Page]].
    """
    # Exclude @[[PageName]] pattern from matching first
    # This is easily handled since _MENTION_RE won't match [ character
    results = []
    seen = set()
    for match in _MENTION_RE.finditer(user_message):
        val = match.group(1).strip()
        if val.startswith("[["): # double bracket
            continue
        if val in seen:
            continue
        seen.add(val)
        
        if val.startswith("mcp/"):
            parts = val.split("/", 2)
            if len(parts) >= 3:
                results.append({
                    "type": "mcp",
                    "server": parts[1],
                    "resource": parts[2],
                    "raw": val
                })
        else:
            results.append({
                "type": "file",
                "path": val,
                "raw": val
            })
    return results
```

**Verification — automated:**
- [ ] `make check` passes
- [ ] `pytest tests/test_mentions_injection.py` passes

**Verification — manual:**
- [ ] None for this logic phase (fully covered by unit tests).

---

## Phase 3: Frontend Autocomplete UI

Implement the trigger context, fetching from `/api/autocomplete`, selection commit, and dropdown menu rendering in the Web UI.

**Files:**
- Modify: `src/decafclaw/web/static/components/chat-input.js` — Update the autocomplete menu to handle `@` mentions.
- Modify: `src/decafclaw/web/static/styles/chat-input.css` — Add any necessary styling if needed for mention hints.
- Modify: `src/decafclaw/web/static/components/chat-input.test.js` — Add Vitest tests for the `@` autocomplete feature.

**Key changes:**
- Add `_mentionMatches: { type: Array, state: true }` in `properties` and initialize it.
- Modify `#triggerContext()` to detect `MENTION_TRIGGER_RE`.
- Implement `_mentionMatches` fetching in `#syncMenu()`.
- Update `#commitAutocomplete(item)` to format and replace selected references.
- Update `render()` to draw the mention dropdown list using `_mentionMatches`.

**Verification — automated:**
- [ ] `make check-js` passes
- [ ] `make test-js` passes

**Verification — manual:**
- [ ] Open the Web UI, type `@` in the chat input, and verify the dropdown lists workspace files, MCP resources, and vault pages.
- [ ] Select a workspace file (e.g. `@CLAUDE.md`), type a prompt, and verify that the backend correctly injects its content and replies with context of the file.
