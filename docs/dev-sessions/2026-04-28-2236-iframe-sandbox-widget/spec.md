# iframe_sandbox widget — spec

Closes part B of #358. Part A (workspace-tier widgets with first-use approval) stays deferred.

## Goal

Ship a single bundled widget, `iframe_sandbox`, that lets the agent render arbitrary HTML/JS/CSS inside a CSP-locked, sandboxed iframe. Same WidgetRequest protocol, just a different renderer. Once shipped, the agent can drop interactive demos into the canvas via `canvas_new_tab(widget_type="iframe_sandbox", data={...})` — no new tool needed.

## Non-goals (v1)

- postMessage→widget-response (input widgets in iframes). Doable later.
- Workspace-tier widget catalog with first-use approval. Tracked separately in #358 part A.
- Network-permitted variants (`fetch`, external scripts). Out of scope; default CSP blocks all network.
- Hot-reload of widget catalog.

## Trust model

The agent supplies HTML; we treat it as untrusted. Two layers of defense:

1. **iframe sandbox attribute.** `sandbox="allow-scripts"`. Critically NOT `allow-same-origin` — that combination would let scripts in the iframe break the sandbox by removing the attr from the parent. Without `allow-same-origin`, the iframe's origin is the special "null" origin: no access to parent's cookies, localStorage, or same-origin XHR. We also omit `allow-forms`, `allow-top-navigation`, `allow-popups`, `allow-modals`.
2. **Content Security Policy meta tag.** `default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:; font-src data:;`. Blocks all network (no fetch, no remote scripts/images/fonts). Permits inline `<script>` and `<style>` so the agent's content can be self-contained.

The CSP meta is **prepended on the backend** as part of normalization — frontend receives the already-wrapped HTML and just sets it as `srcdoc`. This is the safer split: even a buggy or malicious widget.js can't drop the CSP, because by the time wrapped HTML reaches the browser the meta is already in the document.

## Data shape

**Input** (what the agent provides on `WidgetRequest.data` or `canvas_new_tab(data=...)`):

```json
{
  "body": "<h1>Hello</h1><script>document.body.style.background='lime'</script>",
  "title": "Optional title"
}
```

- `body` (required, string, maxLength 262144 / 256 KB) — HTML body content. Inline `<style>` and `<script>` allowed.
- `title` (optional, string, maxLength 200) — sets `<title>`.

256 KB cap leaves headroom for non-trivial interactive demos (small p5.js sketches, charts, etc.) while bounding archive/canvas-state size.

**Stored / transmitted** (post-normalization — what flows over WS, gets archived, persists in `canvas.json`):

```json
{
  "body": "<h1>Hello</h1>...",
  "title": "Optional title",
  "html": "<!doctype html><html><head><meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:; font-src data:;\"><meta charset=\"utf-8\"><title>Optional title</title><style>html,body{margin:0;padding:0;...}</style></head><body><h1>Hello</h1>...</body></html>"
}
```

`body` and `title` round-trip back to the agent (so re-reading state via `canvas_read` is intelligible). `html` is what `widget.js` sets as `srcdoc`. Normalization is idempotent: re-normalizing data that already has `html` regenerates it from `body` and `title` (so a stale `html` from a round-trip can't mislead).

`data_schema` declares only `body` (required) and `title` (optional). `additionalProperties` is allowed so a round-tripped `html` doesn't fail re-validation.

## Backend normalization hook

Add a per-widget normalizer dispatch to `widgets.py`:

```python
_NORMALIZERS: dict[str, Callable[[dict], dict]] = {}

class WidgetRegistry:
    def normalize(self, name: str, data: dict) -> dict:
        fn = _NORMALIZERS.get(name)
        return fn(data) if fn else data
```

Register `_normalize_iframe_sandbox` at module load. Call sites that should invoke `normalize` (after successful `validate`):

- `_resolve_widget` in `agent.py` (post-validate, pre-`payload` build, replaces `widget.data` and the payload's `data` field).
- `canvas.new_tab` (post-validate, pre-state-write).
- `canvas.update_tab` (post-validate, pre-state-write).

The normalizer is pure: takes data, returns a new dict with `html` key added.

## Frontend widget

`src/decafclaw/web/static/widgets/iframe_sandbox/`:

- `widget.json` — descriptor: `modes: ["inline", "canvas"]`, `accepts_input: false`, schema as above.
- `widget.js` — Lit component `<dc-widget-iframe-sandbox>`. Light DOM. Renders `<iframe sandbox="allow-scripts" srcdoc=...>` plus a thin header with the title (when present). Inline mode constrains height to 24rem with vertical scroll fallback; canvas mode fills available space.

The widget.js does NOT inject CSP — it trusts the backend wrapper. (Defense-in-depth: we could double-inject as a sanity check, but for v1 we keep one canonical wrapper to avoid divergence.)

## canvas_new_tab tool description

Update the description to enumerate `iframe_sandbox` as a supported widget_type with its data shape, and call out the security stance:

> `widget_type='iframe_sandbox'` with `data={body: <html>, title?: <string>}` — renders arbitrary HTML/JS/CSS in a sandboxed iframe with no network access (CSP blocks fetch, external scripts, images, fonts). Use for interactive demos, charts, small visualizations.

## Tests

`tests/test_widgets.py` (extend):
- `test_bundled_iframe_sandbox_is_registered` — descriptor parses, modes are `inline` + `canvas`, `accepts_input: false`.
- Schema validation: rejects missing `body`, accepts body-only, accepts body+title, rejects oversized body.
- Schema permits `html` round-trip without rejecting.

New file `tests/test_widget_normalize.py` (or extend existing):
- `test_iframe_sandbox_normalize_injects_csp` — wrapped html starts with doctype, contains exact CSP meta tag, contains body content, contains title when supplied.
- `test_iframe_sandbox_normalize_idempotent` — normalize(normalize(data)) == normalize(data) for same inputs (regenerates html from body, doesn't compound).
- `test_iframe_sandbox_normalize_no_title` — works without title.
- `test_iframe_sandbox_normalize_escapes_title` — title with `</title>` doesn't break the doc (HTML-escape).

`tests/test_canvas.py` (extend):
- `test_new_tab_iframe_sandbox_normalizes` — adding an iframe_sandbox tab stores normalized data with `html` populated.

JS-side: no automated DOM tests (no browser harness in CI). Smoke-test in browser before merge.

## Files touched

- `src/decafclaw/widgets.py` — add `_NORMALIZERS` dispatch + `WidgetRegistry.normalize()`. Register `_normalize_iframe_sandbox`.
- `src/decafclaw/agent.py` — `_resolve_widget` calls `registry.normalize` after validate.
- `src/decafclaw/canvas.py` — `new_tab` and `update_tab` call `registry.normalize` after `_validate_widget_for_canvas`.
- `src/decafclaw/web/static/widgets/iframe_sandbox/widget.json` (new)
- `src/decafclaw/web/static/widgets/iframe_sandbox/widget.js` (new)
- `src/decafclaw/tools/canvas_tools.py` — extend `canvas_new_tab` description.
- `tests/test_widgets.py`, `tests/test_canvas.py` — extend.
- `docs/widgets.md` — document iframe_sandbox section.
- `docs/dev-sessions/.../notes.md` — retro at end.

## Phasing

Single phase, single commit. ~1 hour of work.

## Risks

- **CSP-meta sandbox interaction.** `<meta http-equiv="CSP">` only applies to subresources fetched after the meta is parsed. Inline scripts run after parsing the meta if it appears in `<head>` before any `<script>`. Wrapper places CSP first in `<head>`, before any other element.
- **`allow-same-origin` accidentally added.** Tests assert the exact `sandbox` attr value.
- **HTML injection via title.** Backend escapes title before injecting into `<title>`.
- **Size DoS.** 256 KB hard cap via JSON schema `maxLength`.
- **Future postMessage support.** When we add input-widget mode, we'll validate `event.source === iframe.contentWindow` (origin is `null` for sandboxed-without-same-origin iframes, so we can't use origin checks).
