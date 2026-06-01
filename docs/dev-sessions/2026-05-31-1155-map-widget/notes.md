# Notes — map canvas widget + iframe_sandbox guidance

## What shipped

A bundled `map` canvas widget plus sharpened `iframe_sandbox` guidance, in
response to the agent failing to build a Leaflet map inside `iframe_sandbox`
(the locked CSP blocked the unpkg scripts/CSS, and tiles would have been
blocked too — working as intended).

Commits on `feat/map-widget` (off `origin/main`):

1. `feat(config)` — `MapWidgetConfig` + `WidgetsConfig` (`config.widgets.map`).
2. `refactor(widgets)` — widened the normalizer contract from `(data)` to
   `(data, config)`; registry stores its config and threads it through.
3. `feat(widgets)` — `map` widget.json + `_normalize_map` (server-injects
   tile config) + the Lit renderer.
4. `build(web)` — vendored Leaflet (entry wrapper + esbuild bundle + CSS copy
   + import map in index.html and canvas-page.html).
5. `docs(widgets)` — enumerated `map` in `canvas_new_tab`, sharpened the
   iframe_sandbox no-network warning, documented the widget.
6. `test(eval)` — `expect_tool_args` runner assertion + a behavioral case
   guarding the map-vs-iframe widget choice.
7. `fix(widgets)` — pyright type for the widened normalizer signature.

## Key decisions

- **Widget, not a relaxed sandbox.** The `map` widget is trusted first-party
  code; the agent supplies structured data only, so it renders in the page
  light DOM (like `data_table`) and may hit the network for tiles. The
  `iframe_sandbox` CSP is untouched. Threat model = same as `data_table`.
- **Tile source is server-controlled**, injected by `_normalize_map` from
  `config.widgets.map` — the agent can't author `tile_url`. OSM public tiles
  by default; configurable via `WIDGETS_MAP_TILE_URL`.
- **SVG `divIcon` markers**, not Leaflet's default PNG icons — sidesteps the
  bundled-icon path breakage. Only `leaflet.css` needs serving.
- **Leaflet ESM/UMD interop** handled by `leaflet-entry.js` (normalizes to a
  default export, like `hljs-entry.js`). The built bundle's tail confirms the
  interop logic survived: `Gt.default && Gt.default.map ? Gt.default : Gt`.

## Deviations from the plan

- **Import map: 2 pages, not 3.** `vault.html` is the standalone wiki and
  renders no canvas widgets (its import map lacks hljs/codemirror too), so
  Leaflet went into `index.html` + `canvas-page.html` only.
- **Eval: needed a new assertion.** The `tool_choice` harness and `expect_tool`
  both match tool *names*, but map vs iframe_sandbox is a `widget_type` within
  the same `canvas_new_tab` tool. Rather than write a fake name-level guard, I
  added `expect_tool_args` (the only arg-level assertion) to the eval runner
  with its own unit tests, then wrote the behavioral case. Documented in
  `docs/eval-loop.md`.
- **Env var naming.** Framework's systematic prefix yields `WIDGETS_MAP_TILE_URL`
  (the spec's `MAP_TILE_URL` was illustrative). Also discovered `load_sub_config`
  only recurses into a nested-dataclass field when its key is present in JSON,
  so the doubly-nested `widgets.map` leaf is built explicitly in `load_config`
  to make `WIDGETS_MAP_*` env vars resolve.

## Verification

- `make check` — clean (ruff + pyright + tsc + message-types drift).
- `make test` — 2720 passed.
- `evals/map_widget.yaml` — PASS against vertex-gemini-flash (agent picks
  `widget_type='map'`, 1 tool call).
- Leaflet bundle built (150 KB JS + 15 KB CSS); default-export shape confirmed
  in the bundle output.

## Still pending (needs Les)

- **Live rendering smoke in the web UI.** Not done in-session: can't run a
  second bot instance while `make dev` is up, and Playwright MCP collides with
  dev's Chrome cache dir. Needs a human pass: agent emits a `map` widget inline
  and in canvas; markers + popups render; tiles load; auto-fit / single-marker
  / explicit center+zoom all behave; inline↔canvas resize reflows (no gray
  tiles); standalone `/canvas/{id}` renders. This is the one gap unit/eval
  coverage can't close.
