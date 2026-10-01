/**
 * Widget catalog: fetches /api/widgets once per session, memoizes results,
 * and hands back descriptors so dc-widget-host can dynamic-import them.
 */

import { DefaultService } from './api-client/index.js';

/** @typedef {import('./api-client/index.js').WidgetDescriptorResponse} WidgetDescriptor */

/** @type {Promise<Map<string, WidgetDescriptor>>|null} */
let _catalogPromise = null;
/** @type {Map<string, WidgetDescriptor>} */
let _byName = new Map();

/**
 * Fetch the widget catalog (memoized). Returns a map of name → descriptor.
 */
export function getCatalog() {
  if (!_catalogPromise) {
    _catalogPromise = DefaultService.listWidgetsApiWidgetsGet()
      .then(body => {
        _byName = new Map(body.widgets.map(w => [w.name, w]));
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
