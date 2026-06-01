# Map Canvas Widget Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a bundled `map` canvas widget the agent drives with structured data (markers + popups), rendered by vendored Leaflet over OSM tiles, plus tighten `iframe_sandbox` no-network guidance so the agent stops loading map libs from CDNs.

**Architecture:** Trusted first-party Lit widget (same class as `data_table`), light-DOM. Agent supplies structured data; tile URL/attribution are server-controlled config injected via a widget normalizer (same mechanism `iframe_sandbox` uses for its CSP). Leaflet is vendored through the existing esbuild + import-map pipeline. No app-level CSP exists, so tiles load freely; the `iframe_sandbox` CSP stays locked.

**Tech Stack:** Python (dataclass config, jsonschema widget validation), Lit (web component), Leaflet 1.9 (vendored via esbuild), OpenStreetMap tiles.

---

## File Structure

**Create:**
- `src/decafclaw/web/static/widgets/map/widget.json` — widget metadata + data_schema
- `src/decafclaw/web/static/widgets/map/widget.js` — Lit renderer (light DOM, Leaflet)

**Modify:**
- `src/decafclaw/config_types.py` — add `MapWidgetConfig` + `WidgetsConfig`
- `src/decafclaw/config.py` — add `widgets` field to `Config`; load it in `load_config`
- `src/decafclaw/widgets.py` — store config on `WidgetRegistry`; widen normalizer contract to `(data, config)`; add `_normalize_map`; update `_normalize_iframe_sandbox` signature
- `src/decafclaw/tools/canvas_tools.py` — enumerate `map` + no-network steering in `canvas_new_tab` description
- `src/decafclaw/web/static/widgets/iframe_sandbox/widget.json` — explicit no-network warning
- `src/decafclaw/web/static/package.json` — add `leaflet` dependency
- `src/decafclaw/web/static/build-vendor.mjs` — bundle `leaflet.js`, copy `leaflet.css`
- `src/decafclaw/web/static/index.html` — import-map entry for leaflet
- `src/decafclaw/web/static/canvas-page.html` — import-map entry for leaflet
- `src/decafclaw/web/static/vault.html` — import-map entry for leaflet
- `docs/widgets.md` — document `map` widget + reinforce iframe_sandbox boundary
- `evals/tool_choice/` — new `map` vs `iframe_sandbox` case

**Test:**
- `tests/test_config.py` (or wherever config tests live — confirm in Task 1) — `widgets.map` defaults + env override
- `tests/test_widgets.py` — registry loads `map`; schema validation; `_normalize_map` injection/idempotency; widened normalize contract

---

## Task 1: Config — `MapWidgetConfig` + `WidgetsConfig`

**Files:**
- Modify: `src/decafclaw/config_types.py` (add after `PreemptiveSearchConfig`, ~line 152)
- Modify: `src/decafclaw/config.py:179` (add `widgets` field), `config.py:453` area (load it), `config.py:505` area (pass to `Config(...)`)
- Test: find the config test module first.

- [ ] **Step 1: Locate the config test module**

Run: `ls tests/ | grep -i config`
Expected: a file like `tests/test_config.py`. Use it for the test below. If multiple, pick the one testing `load_config` / nested groups (grep for `load_config`).

- [ ] **Step 2: Write the failing config test**

Add to the config test module:

```python
def test_widgets_map_config_defaults():
    from decafclaw.config_types import WidgetsConfig, MapWidgetConfig
    w = WidgetsConfig()
    assert isinstance(w.map, MapWidgetConfig)
    assert w.map.tile_url == "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
    assert "OpenStreetMap" in w.map.tile_attribution
    assert w.map.max_zoom == 19


def test_widgets_map_config_env_override(monkeypatch, tmp_path):
    # Systematic env prefix: Config.widgets (WIDGETS) → map (WIDGETS_MAP) → field.
    monkeypatch.setenv("WIDGETS_MAP_TILE_URL", "https://example.test/{z}/{x}/{y}.png")
    monkeypatch.setenv("DATA_HOME", str(tmp_path))
    from decafclaw.config import load_config
    cfg = load_config()
    assert cfg.widgets.map.tile_url == "https://example.test/{z}/{x}/{y}.png"
```

- [ ] **Step 3: Run to verify it fails**

Run: `uv run pytest tests/test_config.py -k widgets_map -v`
Expected: FAIL with `ImportError` / `AttributeError` (no `WidgetsConfig`).

- [ ] **Step 4: Add the dataclasses**

In `src/decafclaw/config_types.py`, after `PreemptiveSearchConfig` (around line 152):

```python
@dataclass
class MapWidgetConfig:
    """Tile source for the bundled `map` canvas widget. Server-controlled —
    injected into widget data by the registry normalizer so the agent can't
    author it. Default is OpenStreetMap's public tile server (fine for
    low-volume personal use; point at your own server for anything heavier)."""
    tile_url: str = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
    tile_attribution: str = (
        '&copy; <a href="https://www.openstreetmap.org/copyright">'
        'OpenStreetMap</a> contributors'
    )
    max_zoom: int = 19


@dataclass
class WidgetsConfig:
    """Per-widget server-side configuration."""
    map: MapWidgetConfig = field(default_factory=MapWidgetConfig)
```

- [ ] **Step 5: Wire into `Config`**

In `src/decafclaw/config.py`, add the import to the existing `config_types` import block (find `from .config_types import (`) — add `WidgetsConfig`. Then add the field to `Config` after `background` (line 179):

```python
    widgets: WidgetsConfig = field(default_factory=WidgetsConfig)
```

In `load_config`, after the `background = load_sub_config(...)` block (~line 453):

```python
    widgets = load_sub_config(
        WidgetsConfig, file_data.get("widgets", {}), "WIDGETS")
```

And in the `Config(...)` constructor call (~line 505), add:

```python
        widgets=widgets,
```

- [ ] **Step 6: Run to verify it passes**

Run: `uv run pytest tests/test_config.py -k widgets_map -v`
Expected: PASS (both tests).

- [ ] **Step 7: Commit**

```bash
git add src/decafclaw/config_types.py src/decafclaw/config.py tests/test_config.py
git commit -m "feat(config): add widgets.map tile config (MapWidgetConfig)"
```

---

## Task 2: Widget registry — widen normalizer contract to `(data, config)`

The `map` normalizer needs config to read the tile URL. Normalizers are currently `(data) -> data`. Widen them to `(data, config) -> data`, store config on the registry, and update the existing `iframe_sandbox` normalizer (which ignores config). This task keeps all existing tests green; the `map` normalizer arrives in Task 3.

**Files:**
- Modify: `src/decafclaw/widgets.py` — `WidgetRegistry.__init__`, `load_widget_registry`, `WidgetRegistry.normalize`, `_normalize_iframe_sandbox`
- Test: `tests/test_widgets.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_widgets.py`:

```python
def test_normalize_passes_config_to_normalizer(fake_config):
    """Registry threads its config into normalizers."""
    import decafclaw.widgets as widgets_mod
    seen = {}

    def _spy(data, config):
        seen["config"] = config
        return data

    reg = load_widget_registry(fake_config,
                               admin_dir=Path("/nonexistent/admin"))
    # iframe_sandbox is bundled, so its normalizer runs; temporarily swap it.
    orig = widgets_mod._NORMALIZERS.get("iframe_sandbox")
    widgets_mod._NORMALIZERS["iframe_sandbox"] = _spy
    try:
        reg.normalize("iframe_sandbox", {"body": "x"})
    finally:
        widgets_mod._NORMALIZERS["iframe_sandbox"] = orig
    assert seen["config"] is fake_config
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_widgets.py -k normalize_passes_config -v`
Expected: FAIL — `_normalize_iframe_sandbox()` / spy called with 1 arg, `TypeError`, or `seen` empty.

- [ ] **Step 3: Store config on the registry**

In `src/decafclaw/widgets.py`, update `WidgetRegistry.__init__` (line 62):

```python
    def __init__(self, descriptors: dict[str, WidgetDescriptor] | None = None,
                 config=None):
        self._descriptors: dict[str, WidgetDescriptor] = descriptors or {}
        self._config = config
```

- [ ] **Step 4: Pass config through `load_widget_registry`**

At the end of `load_widget_registry` (line ~199), change:

```python
    return WidgetRegistry(merged)
```

to:

```python
    return WidgetRegistry(merged, config=config)
```

- [ ] **Step 5: Pass config into the normalizer call**

In `WidgetRegistry.normalize` (line ~96-102), change the final lines:

```python
        fn = _NORMALIZERS.get(name)
        if fn is None:
            return data
        desc = self._descriptors.get(name)
        if desc is not None and desc.tier != "bundled":
            return data
        return fn(data, self._config)
```

- [ ] **Step 6: Update the iframe_sandbox normalizer signature**

In `src/decafclaw/widgets.py`, change `_normalize_iframe_sandbox` (line 263):

```python
def _normalize_iframe_sandbox(data: dict, config=None) -> dict:
```

(Body unchanged — it doesn't use config. Add `config` to the docstring's note that normalizers now receive config.)

- [ ] **Step 7: Update the normalizer-contract comment**

In the normalizer header comment block (line ~230), change:

```
# A normalizer is a pure function ``(input_data) -> normalized_data`` invoked
```

to:

```
# A normalizer is a pure function ``(input_data, config) -> normalized_data``
# invoked
```

- [ ] **Step 8: Run the full widgets test file**

Run: `uv run pytest tests/test_widgets.py -v`
Expected: PASS — the new test plus all existing iframe_sandbox normalize tests (which call `reg.normalize(name, data)` and get config from the registry).

- [ ] **Step 9: Commit**

```bash
git add src/decafclaw/widgets.py tests/test_widgets.py
git commit -m "refactor(widgets): thread registry config into normalizers"
```

---

## Task 3: `map` widget registration + `_normalize_map`

**Files:**
- Create: `src/decafclaw/web/static/widgets/map/widget.json`
- Modify: `src/decafclaw/widgets.py` — add `_normalize_map` + register it
- Test: `tests/test_widgets.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_widgets.py`:

```python
def test_bundled_map_is_registered(fake_config):
    reg = load_widget_registry(fake_config,
                               admin_dir=Path("/nonexistent/admin"))
    desc = reg.get("map")
    assert desc is not None
    assert desc.tier == "bundled"
    assert "inline" in desc.modes
    assert "canvas" in desc.modes
    assert desc.accepts_input is False

    # Valid: a single marker.
    ok, err = reg.validate("map", {"markers": [{"lat": 37.8, "lng": -122.3}]})
    assert ok is True, err
    # Valid: marker with label + popup, explicit center/zoom.
    ok, err = reg.validate("map", {
        "markers": [{"lat": 1.0, "lng": 2.0, "label": "A", "popup": "hi"}],
        "center": {"lat": 1.0, "lng": 2.0}, "zoom": 10, "title": "Map",
    })
    assert ok is True, err
    # Valid: empty map (no markers, no center) — forgiving.
    ok, err = reg.validate("map", {"markers": []})
    assert ok is True, err
    # Invalid: latitude out of range.
    bad, _ = reg.validate("map", {"markers": [{"lat": 200, "lng": 0}]})
    assert not bad
    # Invalid: longitude out of range.
    bad, _ = reg.validate("map", {"markers": [{"lat": 0, "lng": 999}]})
    assert not bad
    # Invalid: unknown top-level key (additionalProperties: false).
    bad, _ = reg.validate("map", {"markers": [], "nope": 1})
    assert not bad


def _map_config_stub(tmp_path, tile_url, attribution):
    """Config stub carrying a custom widgets.map config — proves the tile
    URL threads from config through the registry into the normalizer
    (rather than coincidentally matching the OSM fallback defaults)."""
    from decafclaw.config_types import WidgetsConfig, MapWidgetConfig

    class _Cfg:
        agent_path = tmp_path / "agent"
        widgets = WidgetsConfig(
            map=MapWidgetConfig(tile_url=tile_url, tile_attribution=attribution))
    return _Cfg()


def test_map_normalize_injects_tile_config(tmp_path):
    cfg = _map_config_stub(tmp_path,
                           "https://custom.test/{z}/{x}/{y}.png", "custom attr")
    reg = load_widget_registry(cfg, admin_dir=Path("/nonexistent/admin"))
    out = reg.normalize("map", {"markers": [{"lat": 1.0, "lng": 2.0}]})
    assert out["tile_url"] == "https://custom.test/{z}/{x}/{y}.png"
    assert out["tile_attribution"] == "custom attr"
    # Markers preserved.
    assert out["markers"] == [{"lat": 1.0, "lng": 2.0}]


def test_map_normalize_overwrites_agent_supplied_tile(tmp_path):
    cfg = _map_config_stub(tmp_path,
                           "https://custom.test/{z}/{x}/{y}.png", "custom attr")
    out = load_widget_registry(
        cfg, admin_dir=Path("/nonexistent/admin")
    ).normalize("map", {
        "markers": [],
        "tile_url": "https://evil.test/{z}/{x}/{y}.png",
        "tile_attribution": "spoof",
    })
    assert out["tile_url"] == "https://custom.test/{z}/{x}/{y}.png"
    assert out["tile_attribution"] == "custom attr"


def test_map_normalize_idempotent(fake_config):
    reg = load_widget_registry(fake_config, admin_dir=Path("/nonexistent/admin"))
    once = reg.normalize("map", {"markers": [{"lat": 1.0, "lng": 2.0}]})
    twice = reg.normalize("map", once)
    assert once == twice


def test_map_normalize_falls_back_to_defaults_without_config():
    """Registry built without config (e.g. WidgetRegistry()) still injects
    sane OSM defaults rather than crashing."""
    import decafclaw.widgets as widgets_mod
    from decafclaw.config_types import MapWidgetConfig
    out = widgets_mod._normalize_map({"markers": []}, None)
    assert out["tile_url"] == MapWidgetConfig().tile_url
    assert out["tile_attribution"] == MapWidgetConfig().tile_attribution
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_widgets.py -k map -v`
Expected: FAIL — `map` widget not registered, `_normalize_map` missing.

- [ ] **Step 3: Create `widget.json`**

Create `src/decafclaw/web/static/widgets/map/widget.json`:

```json
{
  "name": "map",
  "description": "Render an interactive geographic map with pin markers. Use when the user wants to see locations, places, or coordinates on a map. The agent supplies structured data (markers with lat/lng and optional popup text); the map renders with real tiles over the network. Do NOT hand-build a map with iframe_sandbox — that widget has no network access.",
  "modes": ["inline", "canvas"],
  "accepts_input": false,
  "data_schema": {
    "type": "object",
    "properties": {
      "markers": {
        "type": "array",
        "items": {
          "type": "object",
          "required": ["lat", "lng"],
          "properties": {
            "lat": { "type": "number", "minimum": -90, "maximum": 90 },
            "lng": { "type": "number", "minimum": -180, "maximum": 180 },
            "label": { "type": "string", "maxLength": 200 },
            "popup": { "type": "string", "maxLength": 2000 }
          },
          "additionalProperties": false
        }
      },
      "center": {
        "type": "object",
        "required": ["lat", "lng"],
        "properties": {
          "lat": { "type": "number", "minimum": -90, "maximum": 90 },
          "lng": { "type": "number", "minimum": -180, "maximum": 180 }
        },
        "additionalProperties": false
      },
      "zoom": { "type": "integer", "minimum": 0, "maximum": 20 },
      "title": { "type": "string", "maxLength": 200 },
      "tile_url": { "type": "string" },
      "tile_attribution": { "type": "string" }
    },
    "additionalProperties": false
  }
}
```

Note: `tile_url`/`tile_attribution` are in `properties` (so round-tripped values pass validation) but are always overwritten by the normalizer — same pattern as iframe_sandbox's `html`.

- [ ] **Step 4: Add `_normalize_map` and register it**

In `src/decafclaw/widgets.py`, after `_NORMALIZERS["iframe_sandbox"] = _normalize_iframe_sandbox` (line ~305):

```python
def _normalize_map(data: dict, config=None) -> dict:
    """Inject server-controlled tile config into a map widget's data.

    The agent supplies markers/center/zoom/title; the tile source is NOT
    agent-authorable. Overwrites any agent-supplied ``tile_url`` /
    ``tile_attribution`` with the resolved config values. Idempotent —
    regenerates the injected fields from config every call.

    Falls back to ``MapWidgetConfig`` defaults when no config is available
    (e.g. a registry built without one), so it never crashes.
    """
    from .config_types import MapWidgetConfig
    map_cfg = getattr(getattr(config, "widgets", None), "map", None) \
        or MapWidgetConfig()
    out = dict(data)
    out["tile_url"] = map_cfg.tile_url
    out["tile_attribution"] = map_cfg.tile_attribution
    return out


_NORMALIZERS["map"] = _normalize_map
```

(`getattr` here reads `config.widgets.map` defensively only because `config` may be `None` for bare registries — `widgets`/`map` are declared dataclass fields, not undeclared attributes, so this respects the "no getattr on undeclared attributes" rule.)

- [ ] **Step 5: Run to verify they pass**

Run: `uv run pytest tests/test_widgets.py -k map -v`
Expected: PASS (all 5 map tests).

- [ ] **Step 6: Run the full widgets suite**

Run: `uv run pytest tests/test_widgets.py -v`
Expected: PASS (no regressions).

- [ ] **Step 7: Commit**

```bash
git add src/decafclaw/web/static/widgets/map/widget.json src/decafclaw/widgets.py tests/test_widgets.py
git commit -m "feat(widgets): register map widget + tile-config normalizer"
```

---

## Task 4: `map` widget.js (Leaflet renderer)

This is browser code; verified manually in Task 7 (no JS unit harness for widgets in this repo). Provide complete, correct code.

**Files:**
- Create: `src/decafclaw/web/static/widgets/map/widget.js`

- [ ] **Step 1: Write the full widget**

Create `src/decafclaw/web/static/widgets/map/widget.js`:

```javascript
import { LitElement, html } from 'lit';
import L from 'leaflet';   // default export normalized by leaflet-entry.js (see Task 5)

const INLINE_HEIGHT = '24rem';
const LEAFLET_CSS_HREF = '/static/vendor/bundle/leaflet.css';
const LEAFLET_CSS_ID = 'dc-leaflet-css';

// SVG pin used for markers — avoids Leaflet's default PNG icon, whose paths
// break when the library is bundled. Rendered via L.divIcon.
const PIN_SVG =
  '<svg xmlns="http://www.w3.org/2000/svg" width="26" height="38" viewBox="0 0 26 38">' +
  '<path d="M13 0C5.8 0 0 5.8 0 13c0 9.2 13 25 13 25s13-15.8 13-25C26 5.8 20.2 0 13 0z" ' +
  'fill="#e64a3b" stroke="#992d24" stroke-width="1"/>' +
  '<circle cx="13" cy="13" r="5" fill="#fff"/></svg>';

/**
 * map widget. Renders an interactive Leaflet map from structured data:
 *   { markers: [{lat, lng, label?, popup?}], center?, zoom?, title?,
 *     tile_url, tile_attribution }   // tile_* injected server-side
 *
 * Trusted first-party widget (NOT agent-authored HTML) — renders directly
 * in the page light DOM, like data_table. Modes: inline (fixed height),
 * canvas (fills available height).
 */
export class MapWidget extends LitElement {
  static properties = {
    data: { type: Object },
    mode: { type: String },
  };

  constructor() {
    super();
    this.data = {};
    this.mode = 'inline';
    this._map = null;
    this._markerLayer = null;
  }

  createRenderRoot() { return this; }

  connectedCallback() {
    super.connectedCallback();
    this._ensureLeafletCss();
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    if (this._map) {
      this._map.remove();
      this._map = null;
      this._markerLayer = null;
    }
  }

  // Leaflet ships its own CSS (panes, controls, zoom buttons). Inject the
  // vendored stylesheet once into the document head, shared by all maps.
  _ensureLeafletCss() {
    if (document.getElementById(LEAFLET_CSS_ID)) return;
    const link = document.createElement('link');
    link.id = LEAFLET_CSS_ID;
    link.rel = 'stylesheet';
    link.href = LEAFLET_CSS_HREF;
    document.head.appendChild(link);
  }

  firstUpdated() {
    this._initMap();
  }

  updated(changed) {
    if (changed.has('data') && this._map) {
      this._renderData();
    }
    if (changed.has('mode') && this._map) {
      // Container size changes when switching inline/canvas; let Leaflet
      // re-measure after layout settles.
      requestAnimationFrame(() => this._map && this._map.invalidateSize());
    }
  }

  _mapEl() {
    return this.querySelector('.dc-map-canvas');
  }

  _initMap() {
    const el = this._mapEl();
    if (!el || this._map) return;
    const maxZoom = 19;
    this._map = L.map(el, { zoomControl: true });
    const tileUrl = (this.data && this.data.tile_url)
      || 'https://tile.openstreetmap.org/{z}/{x}/{y}.png';
    const attribution = (this.data && this.data.tile_attribution) || '';
    L.tileLayer(tileUrl, { maxZoom, attribution }).addTo(this._map);
    this._markerLayer = L.layerGroup().addTo(this._map);
    this._renderData();
    // First paint often happens before the container has its final size.
    requestAnimationFrame(() => this._map && this._map.invalidateSize());
  }

  _renderData() {
    if (!this._map || !this._markerLayer) return;
    this._markerLayer.clearLayers();
    const d = this.data || {};
    const markers = Array.isArray(d.markers) ? d.markers : [];
    const icon = L.divIcon({
      className: 'dc-map-pin',
      html: PIN_SVG,
      iconSize: [26, 38],
      iconAnchor: [13, 38],
      popupAnchor: [0, -34],
    });
    const latlngs = [];
    for (const m of markers) {
      if (typeof m.lat !== 'number' || typeof m.lng !== 'number') continue;
      const marker = L.marker([m.lat, m.lng], { icon });
      if (m.popup) marker.bindPopup(String(m.popup));
      if (m.label) marker.bindTooltip(String(m.label));
      marker.addTo(this._markerLayer);
      latlngs.push([m.lat, m.lng]);
    }
    // View resolution: explicit center+zoom → fit markers → world view.
    if (d.center && typeof d.center.lat === 'number'
        && typeof d.center.lng === 'number' && typeof d.zoom === 'number') {
      this._map.setView([d.center.lat, d.center.lng], d.zoom);
    } else if (latlngs.length > 1) {
      this._map.fitBounds(L.latLngBounds(latlngs), { padding: [30, 30] });
    } else if (latlngs.length === 1) {
      this._map.setView(latlngs[0], 13);
    } else if (d.center && typeof d.center.lat === 'number'
        && typeof d.center.lng === 'number') {
      this._map.setView([d.center.lat, d.center.lng], 10);
    } else {
      this._map.setView([0, 0], 1);
    }
  }

  render() {
    const isCanvas = this.mode === 'canvas';
    const title = this.data && this.data.title;
    const wrapperStyle = isCanvas
      ? 'display:flex; flex-direction:column; flex:1 1 auto; min-height:0; height:100%;'
      : 'display:flex; flex-direction:column;';
    const mapStyle = isCanvas
      ? 'flex:1 1 auto; min-height:12rem; width:100%;'
      : `height:${INLINE_HEIGHT}; width:100%;`;
    return html`
      <div class="dc-map ${isCanvas ? 'dc-map-canvas-mode' : 'dc-map-inline'}"
           style=${wrapperStyle}>
        ${title ? html`<header class="dc-map-header" style="flex:0 0 auto;"><span class="dc-map-title">${title}</span></header>` : ''}
        <div class="dc-map-canvas" style=${mapStyle}></div>
      </div>
    `;
  }
}

customElements.define('dc-widget-map', MapWidget);
```

- [ ] **Step 2: Commit (renderer; vendoring lands next so it can't run yet)**

```bash
git add src/decafclaw/web/static/widgets/map/widget.js
git commit -m "feat(widgets): map widget Leaflet renderer (light DOM)"
```

---

## Task 5: Vendor Leaflet

**Files:**
- Create: `src/decafclaw/web/static/leaflet-entry.js` (interop wrapper, matching the hljs/codemirror/milkdown entry pattern)
- Modify: `src/decafclaw/web/static/package.json`
- Modify: `src/decafclaw/web/static/build-vendor.mjs`
- Modify: `src/decafclaw/web/static/index.html`, `canvas-page.html`, `vault.html`

- [ ] **Step 1: Add the dependency**

In `src/decafclaw/web/static/package.json`, add to `dependencies` (alphabetical, after `highlight.js`):

```json
    "leaflet": "^1.9.4",
```

- [ ] **Step 2: Create the interop entry wrapper**

Leaflet ships both an ESM build (named exports, no default) and a UMD build (a default-like `module.exports`). Which one esbuild resolves — and therefore whether `import L from 'leaflet'` or `import * as L` is correct — is ambiguous. Normalize to a single default export, exactly like `hljs-entry.js`. Create `src/decafclaw/web/static/leaflet-entry.js`:

```javascript
/**
 * Leaflet vendor entry — normalizes Leaflet's ESM/UMD exports to a single
 * default export so consumers can `import L from 'leaflet'` regardless of
 * which build esbuild resolves.
 *
 * Bundled into vendor/bundle/leaflet.js by build-vendor.mjs.
 * Imported via the importmap entry "leaflet".
 */
import * as leaflet from 'leaflet';

// ESM build: named exports live on the namespace (leaflet.map, etc.).
// UMD build via esbuild interop: the L object lands on `.default`.
const L = leaflet.default && leaflet.default.map ? leaflet.default : leaflet;
export default L;
```

- [ ] **Step 3: Bundle leaflet.js + copy leaflet.css**

In `src/decafclaw/web/static/build-vendor.mjs`, add a bundle entry to the `bundles` array (after the `highlight.js` entry) — use the entry wrapper, like the other `*-entry.js` bundles:

```javascript
  {
    name: 'leaflet',
    entry: join(__dirname, 'leaflet-entry.js'),
    outfile: join(outdir, 'leaflet.js'),
  },
```

And after the pico.min.css copy block (before the final `console.log('Vendor bundle complete!')`):

```javascript
// Copy Leaflet CSS (panes, controls, zoom buttons; not imported by the JS).
const leafletCssSrc = join(__dirname, 'node_modules', 'leaflet', 'dist', 'leaflet.css');
const leafletCssDst = join(outdir, 'leaflet.css');
cpSync(leafletCssSrc, leafletCssDst);
console.log('Copied leaflet.css');
```

- [ ] **Step 4: Add the import-map entry to all three HTML pages**

In each of `index.html`, `canvas-page.html`, and `vault.html`, find the `<script type="importmap">` block and add to `"imports"` (after the `"hljs"` line):

```json
      "leaflet": "/static/vendor/bundle/leaflet.js",
```

Verify all three: `grep -L '"leaflet"' src/decafclaw/web/static/index.html src/decafclaw/web/static/canvas-page.html src/decafclaw/web/static/vault.html` should print nothing (every file contains it).

- [ ] **Step 5: Build the vendor bundle**

Run: `make vendor`
Expected: log lines including `Bundling leaflet...`, `Copied leaflet.css`, `Vendor bundle complete!`. New files `src/decafclaw/web/static/vendor/bundle/leaflet.js` and `leaflet.css` exist.

Run: `ls -la src/decafclaw/web/static/vendor/bundle/leaflet.*`
Expected: both files present, non-zero size.

- [ ] **Step 6: Commit (including the built bundle, which is git-tracked)**

```bash
git add src/decafclaw/web/static/leaflet-entry.js \
        src/decafclaw/web/static/package.json src/decafclaw/web/static/package-lock.json \
        src/decafclaw/web/static/build-vendor.mjs \
        src/decafclaw/web/static/index.html src/decafclaw/web/static/canvas-page.html \
        src/decafclaw/web/static/vault.html \
        src/decafclaw/web/static/vendor/bundle/leaflet.js \
        src/decafclaw/web/static/vendor/bundle/leaflet.css
git commit -m "build(web): vendor Leaflet (bundle + css + import map)"
```

(If `package-lock.json` isn't present/changed, drop it from the `git add`.)

---

## Task 6: iframe_sandbox guidance (part b)

**Files:**
- Modify: `src/decafclaw/tools/canvas_tools.py:127-143` (canvas_new_tab description)
- Modify: `src/decafclaw/web/static/widgets/iframe_sandbox/widget.json` (description)
- Modify: `docs/widgets.md`

- [ ] **Step 1: Update the `canvas_new_tab` description**

In `src/decafclaw/tools/canvas_tools.py`, replace the description string (lines 127-143) so it enumerates `map` and steers network-dependent visualizations. Replace:

```python
                "Currently supports widget_type='markdown_document' "
                "with data={content: <markdown>}, widget_type='code_block' "
                "with data={code: <string>, language?: <string>, filename?: <string>}, "
                "and widget_type='iframe_sandbox' with data={body: <html>, title?: <string>} — "
```

with:

```python
                "Currently supports widget_type='markdown_document' "
                "with data={content: <markdown>}, widget_type='code_block' "
                "with data={code: <string>, language?: <string>, filename?: <string>}, "
                "widget_type='map' with data={markers: [{lat, lng, label?, popup?}], center?: {lat, lng}, zoom?: <int>, title?: <string>} "
                "for showing locations/places on an interactive geographic map, "
                "and widget_type='iframe_sandbox' with data={body: <html>, title?: <string>} — "
```

And append to the end of the description (after the existing CSP sentence, inside the closing parens):

```python
                " For maps or anything needing the network (map tiles, external "
                "data), use widget_type='map' or another purpose-built widget — "
                "iframe_sandbox CANNOT load external scripts, stylesheets, "
                "images, fonts, or fetch from CDNs (e.g. unpkg); everything in it "
                "must be inline or a data: URI."
```

- [ ] **Step 2: Update the iframe_sandbox widget.json description**

In `src/decafclaw/web/static/widgets/iframe_sandbox/widget.json`, append to the `description` value (before the closing quote):

` NO NETWORK: external scripts/CSS/images/fonts (including CDNs like unpkg) and fetch are all blocked by CSP — content must be fully inline or data: URI. For maps, use widget_type='map'.`

- [ ] **Step 3: Document in docs/widgets.md**

In `docs/widgets.md`: (a) add a `map` bullet to the widget list near the `iframe_sandbox` bullet (line ~67), e.g.:

```markdown
- **`map`** — interactive geographic map (Leaflet + OSM tiles). Agent supplies structured data; tile source is server config (`config.widgets.map`), injected server-side. Data shape (input): `{markers: [{lat, lng, label?, popup?}], center?: {lat, lng}, zoom?, title?}`. See [map](#map-widget).
```

(b) Add a `### map widget` section describing the data shape, view resolution (explicit center+zoom → fit markers → world view), server-injected tile config, and the SVG-pin/no-PNG-icon note. (c) In the `iframe_sandbox` section, add one sentence reinforcing that for network-dependent content (maps, remote data) the agent should use a purpose-built widget like `map`, not iframe_sandbox.

- [ ] **Step 4: Commit**

```bash
git add src/decafclaw/tools/canvas_tools.py \
        src/decafclaw/web/static/widgets/iframe_sandbox/widget.json docs/widgets.md
git commit -m "docs(widgets): enumerate map + sharpen iframe_sandbox no-network guidance"
```

---

## Task 7: Eval — map vs iframe_sandbox tool choice

Per the eval convention: a sharpened tool description needs a `tool_choice` guard. Confirm the format from an existing case first.

**Files:**
- Inspect: an existing file in `evals/tool_choice/`
- Create: `evals/tool_choice/map_widget.yaml` (match the actual schema observed)

- [ ] **Step 1: Read an existing tool_choice case for the exact schema**

Run: `ls evals/tool_choice/ && sed -n '1,60p' "$(ls evals/tool_choice/*.yaml | head -1)"`
Expected: see the fields used (`prompt`/`messages`, `expect_tool`, `expect_no_tool`, `max_tool_calls`, `max_tool_errors`, setup keys). Mirror them exactly.

- [ ] **Step 2: Write the case**

Create `evals/tool_choice/map_widget.yaml` modeled on the observed schema. Intent (adapt field names to the real schema):

- A user asks to "show these three locations on a map" with a few city coordinates or names.
- `expect_tool`: `canvas_new_tab` with `widget_type='map'` (assert on args if the harness supports it; otherwise assert `canvas_new_tab` is called and pair with the no-tool below).
- Add a guard against the old failure mode: assert the agent does NOT emit `iframe_sandbox`. Per the eval convention, prefer positive `expect_tool` + tight `max_tool_calls` over `expect_no_tool` where reflection might retry — use `expect_no_tool` for `iframe_sandbox` only if the observed schema/other cases use it reliably.
- Bound with `max_tool_calls` (e.g. 3) and `max_tool_errors` (e.g. 1).

- [ ] **Step 3: Run the case**

Run: `uv run python -m decafclaw.eval evals/tool_choice/map_widget.yaml`
Expected: PASS. If the model still reaches for `iframe_sandbox`, sharpen the `canvas_new_tab` / widget.json wording (Task 6) and re-run — that's the wording acting as a control surface.

- [ ] **Step 4: Commit**

```bash
git add evals/tool_choice/map_widget.yaml
git commit -m "test(eval): map vs iframe_sandbox tool-choice guard"
```

---

## Task 8: Full check + finalize

- [ ] **Step 1: Lint + typecheck (Python + JS)**

Run: `make check`
Expected: clean. Fix any warnings (zero-tolerance).

- [ ] **Step 2: Full test suite**

Run: `make test`
Expected: PASS. Check `uv run pytest --durations=25` if anything looks slow.

- [ ] **Step 3: Manual smoke (web UI)**

Confirm with Les that no other bot instance is running, then drive the app per the `run`/`verify` skills:
- Agent emits a `map` widget inline (chat) and in canvas; markers + popups render; tiles load.
- Auto-fit (multiple markers), single-marker default zoom, and explicit center+zoom all behave.
- Resize / inline↔canvas reflow works (no gray tiles — `invalidateSize` firing).
- Standalone `/canvas/{id}` page renders the map.

- [ ] **Step 4: Update dev-session notes**

Write `docs/dev-sessions/2026-05-31-1155-map-widget/notes.md` with a session summary (what shipped, decisions, manual-test results), then commit.

- [ ] **Step 5: Open PR + request Copilot review**

```bash
git push -u origin feat/map-widget
gh pr create --fill --base main
gh pr edit <N> --add-reviewer copilot-pull-request-reviewer
```

(Verify the reviewer took via REST `/requested_reviewers` per the known reference.)

---

## Self-Review

**Spec coverage:**
- Goal 1 (map widget): Tasks 1 (config), 2–3 (registry + normalizer + registration), 4 (renderer), 5 (vendoring). ✓
- Goal 2 (iframe_sandbox guidance): Task 6. ✓
- v1 = markers + popups only; no shapes/GeoJSON: schema in Task 3 enforces it. ✓
- Tile URL server-controlled via normalizer: Tasks 2–3. ✓
- OSM default, configurable: Task 1. ✓
- Light-DOM Lit widget, SVG pins: Task 4. ✓
- Vendoring via esbuild + import map (all 3 HTML pages): Task 5. ✓
- Testing (unit normalize/validation/config + eval): Tasks 1, 2, 3, 7. ✓

**Placeholder scan:** Task 7 intentionally defers exact YAML field names to the observed schema (Step 1 reads a real case first) rather than guessing the eval format — this is a "confirm then mirror" step, not a content placeholder. All code steps include complete code.

**Type/name consistency:** `MapWidgetConfig`/`WidgetsConfig`, `config.widgets.map.{tile_url,tile_attribution,max_zoom}`, `_normalize_map(data, config)`, normalizer key `"map"`, widget data keys (`markers`/`center`/`zoom`/`title`/`tile_url`/`tile_attribution`), CSS class `.dc-map-canvas`, element `dc-widget-map`, css id `dc-leaflet-css` — used consistently across tasks. `WidgetRegistry.__init__(descriptors, config=None)` matches the `WidgetRegistry()` bare-construction test and `load_widget_registry` call.

**Deviation from spec:** env var names use the framework's systematic prefix (`WIDGETS_MAP_TILE_URL`) rather than the spec's illustrative `MAP_TILE_URL`. This keeps config resolution consistent with every other group; noted here so it's a conscious choice, not drift.
