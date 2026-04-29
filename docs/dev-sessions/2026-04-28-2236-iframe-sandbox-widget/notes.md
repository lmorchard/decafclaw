# iframe_sandbox session — notes

## What shipped

Closes #358 part B. Part A (workspace-tier widgets with first-use approval) stays deferred.

A bundled `iframe_sandbox` widget that renders agent-authored HTML/CSS/JS in a CSP-locked, sandboxed iframe with no network access. Same WidgetRequest plumbing as existing widgets — no new tool needed; the agent uses `canvas_new_tab(widget_type="iframe_sandbox", data={"body": "...", "title": "..."})`.

## Design decisions worth remembering

- **Two layers of defense.** `sandbox="allow-scripts"` (NOT `allow-same-origin`) on the iframe + `<meta http-equiv="Content-Security-Policy">` injected backend-side. Either alone wouldn't be enough: sandbox without CSP allows network, CSP without sandbox allows storage/cookie access from inside the frame.
- **CSP injection lives in Python.** `_normalize_iframe_sandbox` in `widgets.py` produces the wrapped doc. The backend sends the wrapped HTML over WS; widget.js just sets `iframe.srcdoc`. This means even a buggy or malicious admin/workspace-tier `widget.js` can't drop the CSP — by the time wrapped HTML reaches the browser the meta is already in the document.
- **Per-widget normalization hook on the registry.** Added `WidgetRegistry.normalize(name, data)` + a module-level `_NORMALIZERS` dispatch dict. Pure functions, idempotent. Currently only `iframe_sandbox` registers one. Easy to extend later without touching the class.
- **Idempotent normalization.** A stale `html` field in input is overwritten by re-wrapping from `body`, so a `canvas_read` → `canvas_update` round-trip can't compound or replay a poisoned document. Verified by `test_iframe_sandbox_normalize_idempotent`.
- **Data shape diverges between input and stored.** Input: `{body, title?}`. Stored/transmitted: `{body, title?, html}`. The schema lists `html` in `properties` (so round-trip doesn't fail validation under `additionalProperties: false`) but doesn't require it. The schema validates input shape; widget.js consumes the post-normalization shape.
- **256 KB body cap.** Enforced via `maxLength` in the data_schema. Big enough for non-trivial demos (small p5.js sketches, charts) without unbounded archive growth.
- **No widget-response in v1.** `accepts_input: false`. To add later: validate `event.source === iframe.contentWindow` in a postMessage handler — origin will be `null` for sandboxed-without-same-origin so source-equality is the trust check.

## Gotchas hit

- **Multiple fake registries.** Three test files define their own registry stand-ins (`tests/test_canvas.py`, `tests/test_canvas_tools.py`, `tests/test_web_canvas.py`). All three needed a `normalize(name, data) -> data` shim added when canvas.py started calling `registry.normalize`. First test run failed with `AttributeError: '_Reg' object has no attribute 'normalize'` — fixed by adding the no-op shim to each. Worth a note: when adding a new method to the registry interface, grep for fakes.

## Files touched

- `src/decafclaw/widgets.py` — `WidgetRegistry.normalize()`, `_NORMALIZERS` dispatch, `_normalize_iframe_sandbox`.
- `src/decafclaw/agent.py` — `_resolve_widget` calls `registry.normalize` after validate.
- `src/decafclaw/canvas.py` — `new_tab` and `update_tab` call `registry.normalize` after validate.
- `src/decafclaw/web/static/widgets/iframe_sandbox/widget.json` (new)
- `src/decafclaw/web/static/widgets/iframe_sandbox/widget.js` (new)
- `src/decafclaw/tools/canvas_tools.py` — `canvas_new_tab` description enumerates iframe_sandbox.
- `tests/test_widgets.py`, `tests/test_canvas.py`, `tests/test_canvas_tools.py`, `tests/test_web_canvas.py` — new tests + fake-registry shims.
- `docs/widgets.md` — full iframe_sandbox section + updated out-of-scope notes.

## Smoke testing

`make check` clean (ruff, pyright, tsc). `make test` clean (2240 passed). New tests at top of `--durations=25` but all under 0.05s.

Browser smoke test: pending — needs a running web UI session. Ask agent to drop a small demo into the canvas via `canvas_new_tab(widget_type="iframe_sandbox", data={"body": "<h1>hi</h1><script>document.body.style.background='lime'</script>", "title": "demo"})` and verify (a) it renders, (b) the script runs, (c) `fetch('https://example.com')` from inside the iframe is blocked by CSP, (d) `parent.location` from inside the iframe throws (sandbox null-origin).
