# Vault left-pane live refresh

**Goal:** Refresh the web UI's vault sidebar automatically whenever a vault page or folder is created, edited, deleted, renamed, moved, or otherwise mutated — so the user sees agent-driven changes without manually clicking the Vault tab.

**Source:** User request from 2026-05-07 (chat session).

## Current state

The vault sidebar (`src/decafclaw/web/static/components/vault-sidebar.js`) refreshes only on two triggers:

1. The user opens the Vault tab (`active` property goes false→true; `updated()` hook re-fetches).
2. A `vault-page-deleted` DOM CustomEvent dispatched by `wiki-page.js:197` after the user deletes a page from the in-app editor.

**Nothing happens when the agent mutates the vault.** A user editing in conversation A can switch to conversation B's Vault tab and see a stale view; an agent-driven `vault_write` is invisible until the user manually clicks the tab.

The vault mutation surface (full list in `research.md`):

- **6 agent tools** in `src/decafclaw/skills/vault/tools.py`: `vault_write`, `vault_delete`, `vault_rename`, `vault_journal_append`, `vault_section`, `vault_move_lines`.
- **5 REST handlers** in `src/decafclaw/http_server.py`: `vault_create`, `vault_create_folder`, `vault_write` (PUT), `_vault_rename` (via PUT with `rename_to`), `vault_delete` (DELETE).

Existing infrastructure we'll reuse (no extension needed):

- **EventBus** (`src/decafclaw/events.py`) for `ctx.publish(event_type, payload)`. The vault tools already publish `tool_status`; precedent established.
- **Centralized WebSocket message types** (`src/decafclaw/web/message_types.json` + `make gen-message-types`).
- **`canvas_update` precedent** for "tool changes disk → server pushes to client → component re-renders" — but per-conversation. Vault is global.

## Desired end state

### Behavior

After any of the 11 mutation paths above completes successfully, every connected web UI client receives a WebSocket message of type `vault_changed`. The vault sidebar (and any other future components that care) listens for it; on receipt, it re-fetches its currently active view (browse or recent) using the existing REST endpoints. No incremental tree mutation; the full-refresh signal is enough.

### Wire shape

New message type in `src/decafclaw/web/message_types.json`:

```json
"VAULT_CHANGED": {
  "value": "vault_changed",
  "direction": "server_to_client",
  "description": "A vault page or folder was created, edited, deleted, renamed, or moved. Clients showing vault content should re-fetch.",
  "payload": {
    "path": "vault-relative path of the changed item, '' if unknown or multi-path",
    "kind": "one of: create | update | delete | rename | move | section | journal"
  }
}
```

Run `make gen-message-types` after editing the JSON.

`path` and `kind` are advisory — the client uses them only for logging/debugging in v1. The full-refresh design means the client doesn't gate on `path` (we picked option (a) over (c) in brainstorm).

### Server-side: publish on every mutation

A small helper, e.g. `src/decafclaw/skills/vault/_events.py` (or co-located in `tools.py` near the existing path helpers), exposes:

```python
async def publish_vault_changed(
    event_bus, config, kind: str, path: str | Path | None = None,
) -> None:
    """Publish a vault_changed EventBus event for the web UI to broadcast.

    `kind` is one of "create" | "update" | "delete" | "rename" | "move" | "section" | "journal".
    `path` is vault-relative (str) or absolute Path; helper normalizes against
    `config.vault_root` to a vault-relative string. Pass None for operations that
    span multiple files (e.g., a single-event variant for vault_move_lines), in which
    case `path` becomes "" in the payload.
    """
```

Implementation publishes a dict event consistent with the existing `event_bus.publish({...})` call sites at `http_server.py:252` and `:298`:

```python
await event_bus.publish({
    "type": "vault_changed",
    "kind": kind,
    "path": str(rel) if rel else "",
})
```

Each of the 11 mutation paths calls the helper after the file system operation succeeds (and after embedding-index updates, since failed writes shouldn't fire). Both surfaces have an EventBus available:

- **Tools** access it via `ctx.event_bus` (the same EventBus injected into the publish helper).
- **REST handlers** access it via `request.app.state.event_bus` (existing pattern at `http_server.py:202`, `:282`, `:728`).

The helper is fail-open: if the event bus publish raises, log at debug level and return — never break the calling tool or REST handler.

### Server-side: broadcast subscriber

A new subscriber in `src/decafclaw/web/websocket.py` (or a new module per the `notification_channels/` pattern) listens for `vault_changed` events on the global EventBus and fans out to all live WebSocket connections. The connection manager exposes a `broadcast(payload)` method (or equivalent) iterating its connection set.

Subscriber lives at module init alongside the other EventBus subscribers. Mirrors how `notification_channels/` register themselves.

### Client-side: receive and dispatch

In `web/static/app.js`, the WebSocket message handler gets a new branch:

```js
if (msg?.type === MESSAGE_TYPES.VAULT_CHANGED) {
  window.dispatchEvent(new CustomEvent('vault-changed', { detail: msg }));
}
```

`vault-sidebar.js` adds a listener in `connectedCallback`:

```js
this._onVaultChanged = () => {
  if (!this._everActivated) return;       // gate same as existing vault-page-deleted listener
  if (this.view === 'recent') this.#fetchRecentPages();
  else this.#fetchWikiPages();
};
window.addEventListener('vault-changed', this._onVaultChanged);
```

And cleans up in `disconnectedCallback`.

### Removing the redundant DOM event

The existing `vault-page-deleted` CustomEvent at `wiki-page.js:197` becomes redundant: the user's delete now hits the REST `DELETE /api/vault/{page}` handler, which fires `vault_changed` server-side, which round-trips to the same client and refreshes the sidebar. Two refreshes for one delete is wasteful.

Remove the dispatch in `wiki-page.js:197` and the `vault-page-deleted` listener in `vault-sidebar.js` (`connectedCallback`/`disconnectedCallback`/`_onVaultPageDeleted`/`_everActivated` checks). The new `vault-changed` mechanism replaces both behaviors.

### Tests

- Unit: `publish_vault_changed` posts the expected payload to a mock event bus; fail-open on raise.
- Integration: at least one mutation per layer fires the event:
  - Tool layer: `tool_vault_write` test asserts a `vault_changed` event was published.
  - REST layer: `DELETE /api/vault/{page}` test asserts a `vault_changed` event was published.
- WebSocket: the broadcast subscriber sends `VAULT_CHANGED` to all live connections on receipt of a `vault_changed` event. Use the existing test fixtures for WS connections.
- Client: a Lit-component test (if the codebase has one for vault-sidebar) verifies that `window.dispatchEvent('vault-changed')` triggers a re-fetch. If no JS test infra exists, manual verification only.

## Design decisions

- **Decision:** Fire on all 11 mutation paths uniformly.
  - **Why:** A user can have multiple browser tabs open; the agent can mutate the vault from any conversation. Source-based gating (e.g., "only fire on agent tool changes, not REST changes") leaves a real failure mode where edits in tab A don't show up in tab B.
  - **Rejected:** Fire only on `vault_write`/`delete`/`rename` (the canonical three). Misses journal entries, section edits, line moves, folder creation, REST-driven edits — every one of which changes what the sidebar should display.

- **Decision:** Full-refresh signal (the WS message means "re-fetch your view"); no incremental tree updates.
  - **Why:** REST endpoints are cheap (one directory listing or top-N mtime sort). Implementation is a few lines of client code: just call the existing `#fetchWikiPages` / `#fetchRecentPages`. Robust to ordering bugs and dropped events.
  - **Rejected:** Incremental client-side tree mutation. Adds complexity and a class of ordering/sequence bugs for a performance gain that doesn't matter at expected change rates.

- **Decision:** Broadcast delivery (every connected client receives every `vault_changed` event).
  - **Why:** The vault is global — there's one vault per agent, and every conversation's UI shows the same files. Per-conversation scoping (the canvas precedent) doesn't fit.
  - **Rejected:** Per-user or per-conversation scoping. Per-user is moot today (single-user app); reusing per-conversation subscriptions with a sentinel `conv_id` adds indirection without benefit.

- **Decision:** Publish via EventBus, with a dedicated subscriber in the WebSocket layer that broadcasts to all connections.
  - **Why:** Established pattern in the codebase: `tool_status`, `canvas_update`, and the `notification_channels/` modules all use `ctx.publish(...)` + subscriber. Keeps the mutation paths transport-agnostic — they don't know about WS.
  - **Rejected:** Direct WS broadcast from each mutation path. Couples vault tools to web transport and breaks the "transports subscribe to events" architecture from CLAUDE.md.

- **Decision:** Remove the existing `vault-page-deleted` DOM CustomEvent and its listener.
  - **Why:** Redundant once the new server-side path exists. Two refreshes for one delete is wasteful and confusing for future readers.
  - **Rejected:** Keep both for a transition period. No rollback need; the diff is small enough to flip atomically.

- **Decision:** Helper is fail-open — `publish_vault_changed` errors are logged at debug and swallowed.
  - **Why:** Vault writes are the load-bearing operation; a publish failure shouldn't break a tool that already wrote the file. Same fail-open posture as embedding-index updates and notification producers.

## Patterns to follow

- **Tool publishes EventBus event after disk operation:** `tool_status` events in `src/decafclaw/skills/vault/tools.py` (existing). The new `vault_changed` follows the same shape (`ctx.publish(event_type, payload)`).
- **EventBus subscriber broadcasts via WebSocket:** `canvas_update` handling at `src/decafclaw/web/websocket.py:555-566` — read the event in a per-conversation handler, send to that connection. We need a *broadcast* variant; the connection manager will get a `broadcast(payload)` (or equivalent) method.
- **Centralized message types:** `src/decafclaw/web/message_types.json` is the source of truth; `make gen-message-types` regenerates the Python and JS stubs. CLAUDE.md "WebSocket message types are centralized" — never bare string literals.
- **Client custom-event dispatch:** `app.js` dispatches a `vault-changed` `CustomEvent` to `window`; components listen and react. Mirrors the existing `vault-page-deleted` mechanism we're removing, just with a different (server-driven) source.
- **Notification channel pattern:** `src/decafclaw/notification_channels/` — each subscriber lives in its own module with a clear interface, registered at boot. Our broadcast subscriber can follow the same shape.

## What we're NOT doing

- **No debouncing or throttling.** A 50-page batch fires 50 events; client refreshes 50 times. If this becomes annoying, debounce later — don't optimize until friction shows up.
- **No conversation scoping.** Vault is global; no per-conversation filtering on the message.
- **No per-user scoping.** Single-user app today. If multi-user ever lands, swap `broadcast` for `broadcast_to_user` at one call site.
- **No incremental tree mutation on the client.** Pure full-refresh.
- **No new data in the message beyond `path` and `kind`.** No frontmatter, no diff, no preview — the client re-fetches and renders fresh.
- **No coalescing in the WebSocket subscriber.** One mutation = one WS message. Coalescing belongs in a future debounce pass if needed.
- **No changes to `wiki-page.js` editor's open-page state.** If the user has page X open in the editor and the agent edits it from another tab, the editor doesn't auto-reload — that's a different feature (live collaborative editing). This spec only refreshes the sidebar tree view.
- **No retroactive event publication.** Mutations that happened before the server started don't fire any event.
- **No persistence of vault_changed events.** They're transient. The client is responsible for being connected to receive them.
- **No new auth surface.** The broadcast subscriber relies on existing WS connection authentication — only authenticated clients have live WS connections.

## Open questions

- **Should `vault_section` / `vault_move_lines` fire one event or multiple?** `vault_move_lines` writes to two pages; `vault_section` writes to one. Default answer: **one event per file write.** `vault_move_lines` fires twice (once per affected page), `vault_section` fires once. Simpler than introducing a "multi-path" semantics.

- **Does `path` in the payload include the `.md` extension?** Default answer: **yes.** Vault-relative paths in the codebase generally include the extension (`agent/pages/Foo.md`). The client does its own display formatting and can strip the extension if needed. Consistent with the path strings already returned by `/api/vault`.

- **What does the `kind` field add for the v1 client?** Default answer: **nothing functional** — the client always re-fetches regardless. `kind` is included for logging, debug visibility (browser console / server logs), and forward-compat if we ever add per-kind UI behavior (e.g., a "page deleted" toast).
