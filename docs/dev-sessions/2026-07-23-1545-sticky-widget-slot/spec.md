# Spec — Sticky widget slot (#419)

## Goal

Add a third widget surface — **`sticky`** — a single-slot, pinned mini-canvas
directly above the chat input. It answers "what is the agent doing right now?"
at a glance while a workflow is in flight, without the status scrolling away
(inline) or hiding in the canvas panel.

This session delivers the **sticky infrastructure only**. The first real
occupant (`progress_tracker` widget) and its checklist-driven auto-emit are
[#414](https://github.com/lmorchard/decafclaw/issues/414), built next on top of
this.

## Background / current architecture

- Widgets are declared by `widget.json` files (meta-schema in `widgets.py`,
  `modes` enum currently `["inline", "canvas"]`). A `WidgetDescriptor` carries
  `modes`, `accepts_input`, `data_schema`, `js_path`.
- **Inline** widgets ride on the `tool_end` WS event and render in the tool
  bubble (`messages/tool-message.js`). The `target` field on `WidgetRequest`
  is validated in `resolve_widget` but the web client **ignores it** — it is
  effectively vestigial for anything but inline.
- **Canvas** is the only working non-inline surface. It is populated
  exclusively by explicit `canvas_*` tools (`canvas_tools.py`) calling
  `canvas.py` primitives that write a per-conversation `canvas.json` sidecar
  and emit `canvas_update` WS events consumed by `canvas-state.js` /
  `canvas-panel.js`. `<dc-widget-host>` mounts `dc-widget-<type>` by naming
  convention.

**Implication:** the sticky slot mirrors the *canvas* pattern (explicit
primitive + tools + dedicated sidecar + dedicated WS events + Lit state module
+ Lit component), not the vestigial `target` field.

## Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Emission mechanism | Explicit `set_sticky`/`clear_sticky` primitive + tools | Mirrors the only working non-inline surface (canvas). `target:"sticky"` would require inventing cross-surface routing that doesn't exist. |
| Sidecar | New `sticky.json` (not folded into `canvas.json`) | Canvas schema is tab-centric (`next_tab_id`, `tabs[]`); sticky is single-slot. Folding in fights the canvas invariants for no gain. |
| WS events | Dedicated `sticky_set` / `sticky_clear` | `canvas_update` is a tab-shaped kind-switch; overloading it forces conditional branches through forwarder + reducer. Matches how `notification_*` got their own forwarders. |
| Slot count | Single slot; new pin replaces old | Per issue. |
| Input | Display-only for v1 | Input widgets stay inline so the "pause and ask" flow is unambiguous. |
| Collapse default | Expanded desktop / collapsed mobile | Show progress where there's room; conserve space on mobile. |
| Collapsed summary | Widget-provided `summary` field, fallback to title/type | Keeps the slot generic across widget types. |
| Dismiss | Collapse only, no dismiss | Lifetime is workflow-owned; avoids a "dismissed but still running" limbo. |

## Divergences from the issue text

(Explicitly sanctioned — we learned new things reading the code.)

1. **Ship a `widget_pin_sticky` tool.** The issue leaned against a free-form
   "pin anything" verb (wanting workflow-owned lifetime only). But single-slot +
   display-only + explicit `widget_unpin_sticky` bounds the "mess," and it makes
   #419 self-contained, demoable, and immediately useful instead of dead code
   until #414 lands. Workflow-driven lifetime (checklist) still arrives in #414
   by calling the same `set_sticky` primitive.
2. **Explicit `set_sticky` primitive**, not `target:"sticky"` routing (target is
   vestigial).
3. **Separate `sticky.json`** (issue left sidecar TBD).
4. **Dedicated `sticky_set`/`sticky_clear`** WS types (issue left WS TBD).

## Scope

### In scope (#419)

- `sticky` mode value in the widget meta-schema; opt-in via `widget.json`.
- `sticky.py` backend: sidecar read/write, `set_sticky`, `clear_sticky`,
  sticky-mode validation, WS emit.
- `sticky_tools.py`: `widget_pin_sticky`, `widget_unpin_sticky` agent tools.
- `sticky_set` / `sticky_clear` WS message types + forwarders.
- `GET /api/sticky/{conv_id}` for reload recovery.
- Frontend: `sticky-state.js`, `<sticky-slot>` component, `app.js` dispatch, CSS
  (collapse affordance, mobile breakpoint).
- Enable `sticky` mode on `markdown_document` as the test/demo occupant.
- Tests + docs.

### Out of scope (deferred to #414)

- The `progress_tracker` widget itself.
- Checklist auto-emit/auto-clear (making `checklist_tools` async + emit).
- `delegate_task` / scheduled-task / approval-flow occupants.

## Data shapes

`sticky.json`:
```json
{ "schema_version": 1, "widget_type": "markdown_document", "data": { "...": "..." } }
```
Empty/cleared state: `{ "schema_version": 1, "widget_type": null, "data": null }`.

WS `sticky_set`: `{ conv_id, widget_type, data }`. WS `sticky_clear`: `{ conv_id }`.

Frontend `sticky-state` per conv: `{ widgetType, data, collapsed }`; `collapsed`
persisted in `localStorage` (`sticky-collapsed.{convId}`), first-value derived
from breakpoint.

## Acceptance criteria

- `widget_pin_sticky(widget_type="markdown_document", data=…)` writes
  `sticky.json`, emits `sticky_set`, and the slot appears above the input.
- `widget_unpin_sticky()` clears the sidecar, emits `sticky_clear`, slot hides.
- Pinning a second widget replaces the first (single slot).
- Pinning a widget that does not declare `sticky` mode is rejected (validation).
- Slot state survives reload (recovered via `GET /api/sticky/{conv_id}`).
- Collapse toggle works; default expanded on desktop, collapsed on mobile;
  collapsed line shows the widget `summary` (fallback title/type).
- `make check` (lint + typecheck + message-types drift) and `make test` pass.

## Testing

- Backend unit tests mirroring `test_canvas.py`: sidecar round-trip, fail-open on
  missing/corrupt, `set_sticky` mode-validation reject, emit assertions,
  clear, single-slot replace, REST recovery.
- `make check-message-types` drift check for the new WS types.
- `make check-js` for the Lit components.
- Manual web-UI QA via a local web-only server (cannot run alongside `make dev`).

## Docs

- Sticky-mode section in the widget/canvas doc (`docs/web-ui-design.md` or the
  canvas doc) + WS message doc regenerated.
- `CLAUDE.md` key-files: add `sticky.py`, `sticky_tools.py`,
  `web/static/components/sticky-slot.js`, `web/static/lib/sticky-state.js`.
