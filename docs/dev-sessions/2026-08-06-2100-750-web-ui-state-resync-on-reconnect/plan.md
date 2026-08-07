# Plan — 750-web-ui-state-resync-on-reconnect

## Phase 1: Setup
- Create git worktree for isolated branch development under `fix/issue-750`.
- Install dependencies via `uv sync`.
- Verify the Javascript and Python baseline tests pass successfully.

## Phase 2: Implementation
- **Refetch History in `conversation-store.js`:**
  - Clear `#messageStore` and `#toolStatusStore` and send `LOAD_HISTORY` to the socket in the `#resubscribe()` reconnection callback.
- **Auto-refetch Canvas State in `canvas-state.js`:**
  - Add a window listener on the `ws-connected` event. If there is an active conversation, call `setActiveConv(_state.active)` to refetch and publish.
- **Auto-refetch Sticky State in `sticky-state.js`:**
  - Add a window listener on the `ws-connected` event. If there is an active conversation, call `setActiveConv(_state.active)` to refetch and publish.

## Phase 3: Verification
- Update the reconnection test in `conversation-store.test.js` to align with the new full-refetch history behavior.
- Run `make test-js` and verify all tests pass.
- Run `make check` and verify typechecking and linting are fully green.
- Run `make test` and verify Python unit tests pass cleanly.
