# Spec: `map` canvas widget + iframe_sandbox guidance

## Problem

The agent tried to build an interactive Leaflet map by emitting HTML into an
`iframe_sandbox` widget that loaded `leaflet.js` / `leaflet.css` from unpkg.
The sandbox's locked CSP (`default-src 'none'`) blocked the external script
and stylesheet — working as intended. There is no first-class way for the
agent to render a map, and the existing tool/widget descriptions don't warn
that `iframe_sandbox` has no network access, so the agent keeps trying CDNs.

## Goals

1. A dedicated, bundled `map` widget the agent drives with structured data —
   no on-the-fly HTML, no relaxing the `iframe_sandbox` CSP.
2. Clearer guidance in tool/widget descriptions and docs about the
   `iframe_sandbox` no-network boundary, steering map/network-dependent
   visualizations to the right widget.

## Non-goals

- Relaxing or adding network-permitted variants to `iframe_sandbox`. It stays
  locked exactly as-is.
- Polylines/polygons, GeoJSON overlays, routing, draw/edit, clustering,
  geolocation. v1 is markers + popups only. These are plausible later
  additions but explicitly out of scope here (YAGNI).
- Keyed/commercial tile providers as the default. OSM public tiles are the
  default; pointing elsewhere is a config change.

## Design

### Why a widget, not a relaxed sandbox

The `map` widget is a **trusted first-party Lit component** (same class as
`data_table`), not agent-authored HTML. The agent supplies *structured data
only*; vendored Leaflet renders it. The web app sets no app-level CSP, so map
tiles load over the network with no CSP fight. The threat model is identical
to `data_table` — the agent never injects code. This is strictly safer than
poking holes in `iframe_sandbox`'s CSP.

### Data contract (v1: markers + popups)

`widgets/map/widget.json` data_schema:

```jsonc
{
  "type": "object",
  "properties": {
    "markers": {                          // 0+ markers
      "type": "array",
      "items": {
        "type": "object",
        "required": ["lat", "lng"],
        "properties": {
          "lat":   { "type": "number", "minimum": -90,  "maximum": 90 },
          "lng":   { "type": "number", "minimum": -180, "maximum": 180 },
          "label": { "type": "string", "maxLength": 200 },   // optional tooltip/title
          "popup": { "type": "string", "maxLength": 2000 }   // optional popup text
        },
        "additionalProperties": false
      }
    },
    "center": {                           // optional explicit center
      "type": "object",
      "required": ["lat", "lng"],
      "properties": {
        "lat": { "type": "number", "minimum": -90,  "maximum": 90 },
        "lng": { "type": "number", "minimum": -180, "maximum": 180 }
      },
      "additionalProperties": false
    },
    "zoom":  { "type": "integer", "minimum": 0, "maximum": 20 },
    "title": { "type": "string", "maxLength": 200 },

    // Server-injected during normalization; agent must NOT set these.
    "tile_url":         { "type": "string" },
    "tile_attribution": { "type": "string" }
  },
  "additionalProperties": false
}
```

View resolution (client-side, in order):
1. If `center` and `zoom` are both present → use them.
2. Else if `markers` is non-empty → auto-fit bounds to the markers (with a
   small padding; if a single marker, use a reasonable default zoom).
3. Else → fall back to a sane world view (center `{0,0}`, zoom `1`).

Forgiving by design: no field beyond valid coordinates is strictly required.

### Tile URL — server-controlled, injected via normalizer

Tile config is server-controlled and injected the same way `iframe_sandbox`
injects its CSP-wrapped HTML: a `_normalize_map` normalizer overwrites any
agent-supplied `tile_url` / `tile_attribution` with the resolved config
values. The agent cannot author them.

**Contract change:** normalizers are currently `(data: dict) -> dict`. `map`
needs config, so widen `WidgetRegistry.normalize` to pass the resolved config
to the normalizer function (signature `(data, config) -> dict`). The registry
already receives config at `load_widget_registry(config)` time — store it on
the instance and pass it through. The existing `iframe_sandbox` normalizer
accepts and ignores the new argument (no behavior change).

New config group `config.widgets.map` (`MapWidgetConfig` dataclass under a
`WidgetsConfig`):

- `tile_url` — default `https://tile.openstreetmap.org/{z}/{x}/{y}.png`
- `tile_attribution` — default OSM attribution string
- (optional) `max_zoom` — default `19`

Resolution order per project convention: dataclass defaults → `config.json` →
env (`MAP_TILE_URL`, `MAP_TILE_ATTRIBUTION`). Use `dataclasses.replace` for
forks; never enumerate fields.

### Rendering — light-DOM Lit widget

`widgets/map/widget.js`, light DOM (`createRenderRoot() { return this; }`),
mirroring `data_table` / `iframe_sandbox`:

- Properties: `data` (object), `mode` (`inline` | `canvas`).
- On `firstUpdated` and on `data`/`mode` change: (re)initialize the Leaflet
  map into a container div — set up the tile layer from `data.tile_url` with
  `data.tile_attribution`, add markers (with popups/labels), apply the view
  resolution above.
- Call `map.invalidateSize()` after layout / mode change / resize (Leaflet
  requires a sized container; canvas mode flexes).
- Destroy the map (`map.remove()`) in `disconnectedCallback` to avoid leaks.
- Inline mode = fixed height (e.g. 24rem, matching `iframe_sandbox`); canvas
  mode = flex-fill to the bottom of the surface.
- Optional `title` renders a header above the map (same shape as
  `iframe_sandbox`'s header).

**Marker icons:** use a CSS/SVG `L.divIcon`, *not* Leaflet's default PNG
markers. This sidesteps Leaflet's well-known bundled marker-icon path
breakage entirely. Only `leaflet.css` (panes, controls, zoom buttons) needs
to be served.

### Vendoring Leaflet

Matches the existing esbuild + import-map mechanism:

- `npm i leaflet` → add to `web/static/package.json`.
- Add a bundle entry in `web/static/build-vendor.mjs` producing
  `vendor/bundle/leaflet.js`, and copy `leaflet.css` →
  `vendor/bundle/leaflet.css` (same pattern as the existing `pico.min.css`
  copy step).
- Add `"leaflet": "/static/vendor/bundle/leaflet.js"` to the import map in
  **all three** widget-hosting HTML pages: `index.html`, `canvas-page.html`,
  `vault.html`. (Easy to miss one — the standalone `/canvas/{id}` page is
  `canvas-page.html`.)
- `widget.js` injects the `leaflet.css` `<link>` once on first render (a
  global light-DOM stylesheet; Leaflet's rules are namespaced under
  `.leaflet-*`).
- `make vendor` regenerates the bundle; output is committed to git.

### Part (b): iframe_sandbox guidance (same PR)

Tighten wording in three places so the agent stops reaching for CDN scripts:

- `iframe_sandbox` **widget.json description** — add an explicit no-network
  warning: external scripts/CSS/images/fonts (e.g. unpkg and other CDNs) are
  blocked; content must be inline or data-URI; for maps use
  `widget_type='map'`.
- `canvas_new_tab` **tool description** in `tools/canvas_tools.py` — enumerate
  `map` as a supported `widget_type` with its data shape, and add a one-liner
  steering network-dependent visualizations to the right widget.
- `docs/widgets.md` — document the `map` widget and reinforce the
  `iframe_sandbox` no-network boundary.

## Testing

Unit (`tests/`):
- `_normalize_map` injects the configured `tile_url` / `tile_attribution`,
  overwrites any agent-supplied values, and is idempotent.
- Schema validation: good coords pass; out-of-range lat/lng rejected; extra
  properties rejected.
- Registry loads the `map` widget (descriptor present, normalizer registered,
  bundled tier).
- The widened `normalize(data, config)` contract: `iframe_sandbox` normalizer
  still behaves identically; `map` receives config.
- Config: `config.widgets.map` resolves defaults and honors the env override.

Eval (`evals/`):
- A `tool_choice` case: for "show these locations on a map" the agent picks
  `map` and does **not** reach for `iframe_sandbox` + a Leaflet CDN. This is
  the behavior change part (b) is meant to produce, so it needs a guard per
  the eval convention (new/sharpened tool description → add a `tool_choice`
  case). Bound with `max_tool_calls` / `max_tool_errors`.

Manual:
- Web UI: agent emits a `map` widget inline and in canvas; markers + popups
  render; auto-fit and explicit center/zoom both work; resize/mode switch
  reflows correctly; standalone `/canvas/{id}` page renders.

## Open questions

None blocking. Future extensions (shapes, GeoJSON, clustering) are explicitly
deferred.
