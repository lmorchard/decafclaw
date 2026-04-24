/**
 * Widget catalog: fetches /api/widgets once per session, memoizes results,
 * and hands back descriptors so dc-widget-host can dynamic-import them.
 */

/** @typedef {{
 *   name: string,
 *   tier: string,
 *   description: string,
 *   modes: string[],
 *   accepts_input: boolean,
 *   data_schema: object,
 *   js_url: string,
 * }} WidgetDescriptor
 */

let _catalogPromise = null;
/** @type {Map<string, WidgetDescriptor>} */
let _byName = new Map();

/**
 * Fetch the widget catalog (memoized). Returns a map of name → descriptor.
 */
export function getCatalog() {
  if (!_catalogPromise) {
    _catalogPromise = fetch('/api/widgets', { credentials: 'include' })
      .then(resp => {
        if (!resp.ok) throw new Error(`widget catalog fetch ${resp.status}`);
        return resp.json();
      })
      .then(body => {
        const list = body?.widgets || [];
        _byName = new Map(list.map(w => [w.name, w]));
        return _byName;
      })
      .catch(err => {
        console.warn('[widget-catalog] fetch failed:', err);
        _catalogPromise = null;  // allow retry on next use
        _byName = new Map();
        return _byName;
      });
  }
  return _catalogPromise;
}

/**
 * Look up a widget descriptor by name. Returns null if the catalog isn't
 * loaded yet or the name is unknown.
 * @param {string} name
 * @returns {WidgetDescriptor|null}
 */
export function getDescriptor(name) {
  return _byName.get(name) || null;
}

/**
 * For tests — clear the memoized catalog.
 */
export function _resetForTests() {
  _catalogPromise = null;
  _byName = new Map();
}
