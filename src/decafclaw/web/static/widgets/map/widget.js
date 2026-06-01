import { LitElement, html } from 'lit';
import L from 'leaflet';   // default export normalized by leaflet-entry.js

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

// Build an element holding agent text as textContent (never HTML), for
// Leaflet popup/tooltip binding. Leaflet renders string content as HTML;
// passing a DOM node forces text rendering and closes the injection path.
function _textNode(tag, text) {
  const el = document.createElement(tag);
  el.textContent = String(text);
  return el;
}

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
    // max_zoom is server-injected from config.widgets.map (default 19).
    const maxZoom = (this.data && typeof this.data.max_zoom === 'number')
      ? this.data.max_zoom : 19;
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
      // Bind agent-supplied text as DOM nodes with textContent, NOT as
      // strings: Leaflet renders string popup/tooltip content as HTML, and
      // this widget runs in the host page's light DOM (not a sandboxed
      // iframe), so a string path would let agent data inject markup/JS.
      if (m.popup) marker.bindPopup(_textNode('div', m.popup));
      if (m.label) marker.bindTooltip(_textNode('span', m.label));
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
