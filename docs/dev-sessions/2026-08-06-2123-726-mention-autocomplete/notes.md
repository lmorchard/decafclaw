# Notes: 726-mention-autocomplete

## Retrospective

- **Recap:** We designed, implemented, and verified mentions autocomplete for workspace files, MCP resources, and vault pages, along with inlining, chunked reading, and truncation logic in the context composer.
- **Scope drift:**
  - Added lookbehind `(?<!\w)@` in the frontend regex trigger to match the backend's lookup boundaries perfectly, enabling triggers after punctuation like `(@file)`.
  - Added secret-file checking to avoid leaking files like `*.env` and `*.key` into LLM prompts.
  - Added a limit of 20 workspace matches in disk walking to keep the search O(1) instead of walking the entire filesystem on every keystroke.
  - Optimized workspace file reading to load only up to 8193 characters from disk rather than reading large files fully.
- **Surprises:**
  - Finding that `config.workspace_path` is a read-only property computed from `config.agent_path`, which requires setting `config.agent.data_home` in testing fixtures instead.
  - Discovering that the vault directory itself is located inside the workspace, which would cause duplicate workspace matches for vault pages without directory pruning in `os.walk`.
- **Workflow Friction:** Extremely smooth session. The vertical-slicing plan and automated test targets kept the development incredibly fast and robust.
- **Misses:** None, the vertical slice plan successfully guided the entire development.
- **Memory candidates:**
  - When testing Starlette endpoints, `ASGITransport` with `AsyncClient` from `httpx` is the preferred clean pattern.
  - Always prune `config.vault_root` during recursive workspace file searches to avoid duplicate/stale matches.
