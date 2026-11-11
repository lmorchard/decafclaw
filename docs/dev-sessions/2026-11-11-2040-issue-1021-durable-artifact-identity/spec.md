# Durable Artifact Identity from Canvas Tabs — Design

**Status:** drafted for delivery (burndown run 2026-11-11)
**Date:** 2026-11-11
**Owner:** Les (via agent delivery)
**Audience:** human + agent implementer
**Issue:** [#1021](https://github.com/lmorchard/decafclaw/issues/1021)

## Problem

Canvas tabs currently own their content inside one conversation sidecar.

- `close_tab` removes the tab (and, for terminals, kills the PTY) from
  `workspace/conversations/{conv_id}/canvas.json`.
- Closing a tab therefore removes that content from the canvas state.
- Reopening means creating a new tab and re-supplying content; there is no
  durable handle that survives tab closure.
- Standalone URLs (`/canvas/{conv_id}` and `/canvas/{conv_id}/{tab_id}`) are
  tab-locked: the tab id is the handle, so a closed tab's URL selects nothing
  usable and a recreated tab with a reused id would alias unrelated content.

An artifact is a durable document or application; a tab is one view of it.
The gap: artifact identity is implicit in tab ids and per-conversation state,
so useful artifacts cannot be reopened from another permitted conversation
without copying content.

## Goals

- Give an artifact an identity that survives closing its tab.
- Let permitted conversations open the same artifact without copying its content.
- Distinguish hiding a view from deleting the underlying artifact.
- Keep existing tab-specific URLs from silently selecting unrelated content.

## Non-goals

- Cross-conversation / user-global terminals (separate treatment: terminal
  sessions are process-scoped, not artifact-scoped).
- Turning every inline widget, input prompt, or progress display into a durable
  artifact.
- Universal artifact registry / cross-user sharing in this slice.

## Current state (as of base `origin/main@1f00ac8`)

- Canvas state sidecar:
  `workspace/conversations/{conv_id}/canvas.json` with
  `{schema_version, active_tab, next_tab_id, tabs: [{id, label, widget_type, data}]}`.
- Tab ids are `canvas_N` (`next_tab_id` monotonic counter).
- `close_tab` pops the tab, picks a neighbor as active, preserves
  `next_tab_id` (so a closed id is never rebound).
- `clear_canvas` empties tabs but preserves `next_tab_id`.
- Standalone URL: `/canvas/{conv_id}` (active tab) and
  `/canvas/{conv_id}/{tab_id}` (tab-locked). Auth-gated per conversation.
- Agent tools: `canvas_new_tab`, `canvas_update`, `canvas_close_tab`,
  `canvas_clear`, `canvas_read` — all tab-id addressed within one conversation.
- `markdown_document` widget: `{content: string}`; label derived from first H1.
- Sticky surface: `markdown_document` also in sticky mode.

## Design proposal

### 1. Artifact handle vs. tab handle

- Introduce a durable artifact handle (`artifact_id`) that is distinct from the
  tab handle (`tab_id`).
- A tab is a *view* of an artifact. `tab_id` remains the per-conversation,
  per-session view handle used by the agent tools and the client.
- `artifact_id` is globally (or per-owner) unique and stable; it is what survives
  tab closure.

### 2. Lifecycle

1. **Create**: an artifact is created with content (e.g. a markdown document)
   and gets an `artifact_id`.
2. **Tab**: a tab (`canvas_N`) is opened referencing that `artifact_id`. The tab
   id is a view handle; multiple tabs may view the same artifact (or none).
3. **Close**: closing the tab hides the view; the artifact (content) stays.
   `next_tab_id` handling is unchanged (view handles are ephemeral).
4. **Reopen**: a permitted conversation opens the artifact via `artifact_id`,
   creating a fresh tab handle that references the same artifact content.

### 3. Cross-conversation access

- Content is stored once (under the owning conversation's artifact store) and
  shared read-only to permitted conversations; it is not copied.
- Access control: only the owning conversation and explicitly permitted
  conversations may open the artifact. References cannot expose another user's
  conversation content.

### 4. Hiding vs. deleting

- Tab close = hide. Artifact persists.
- Explicit artifact delete = remove content + revoke handles. Destructive,
  confirmation-gated.

### 5. Terminal handling

- Terminal tabs are process-scoped; closing the tab stops the PTY. Terminal
  artifacts need separate (process-aware) identity treatment; out of scope here.

## Acceptance criteria mapping

- Trace one document through creation, tab closure, reopening, and access from
  another permitted conversation — covered by §2–3 above.
- Specify deletion and ownership behavior without exposing another user's
  conversation data — §3–4.
- Give a migration example for an existing canvas sidecar and standalone URL —
  §2 (tab ids become views; content keyed by artifact id).
- Preserve the human-only terminal creation boundary and session cleanup — §5.
- Identify a small first implementation and proposed tests — §6.

## First implementation slice and proposed tests

- Slice: introduce an artifact handle in canvas state for
  `markdown_document` tabs (content keyed by artifact id; tab close preserves
  content; reopen via artifact id).
- Tests: canvas state op tests for create/close/reopen with artifact handles;
  standalone URL tab-lock behavior; no cross-conversation content leak.

## Open design decisions

- Artifact store location and naming (per-conversation sidecar key vs. artifact
  registry file).
- Whether artifact content is mutable in place (live binding) or snapshotted.
- Cross-conversation grant mechanism (explicit allow-list vs. derived).

## Evidence

- `src/decafclaw/canvas.py` (state ops), `src/decafclaw/tools/canvas_tools.py`,
  `src/decafclaw/http_server.py` (canvas routes).
- `docs/web-ui.md` §Canvas panel, `docs/web-ui-design.md`, `docs/widgets.md` Phase 3/4.
- Television artifact spec: `https://github.com/telepath-computer/television/blob/67dd739cc27e871149169005c6912b4a07ff6e36/specs/product/artifacts.md`.
