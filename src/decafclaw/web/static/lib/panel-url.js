/**
 * Side-panel URL state for the web gateway.
 *
 * The #wiki-main side panel hosts at most ONE of <wiki-page>, <file-page>,
 * or <schedule-page> (mutual exclusion). Each open panel is bookmarkable via
 * a query param — ?vault=, ?file=, or ?schedule= — and opening one drops the
 * other two. This module owns that URL dance (set / get / delete / hide
 * siblings) so the app's show/hide methods stay declarative. The config panel
 * has no URL state of its own; it is only a sibling to hide.
 */

/** Panel name → query param, in popstate priority order (wiki > file > schedule). */
export const PANEL_PARAMS = Object.freeze({
  wiki: 'vault',
  file: 'file',
  schedule: 'schedule',
});

/** @param {string} name @returns {string} the query param for `name` */
function paramFor(name) {
  const param = PANEL_PARAMS[name];
  if (!param) throw new Error(`unknown panel: ${name}`);
  return param;
}

/** @param {string} name @returns {string[]} the other panels' query params */
function siblingParams(name) {
  const self = paramFor(name);
  return Object.values(PANEL_PARAMS).filter((p) => p !== self);
}

/**
 * Set `name`'s query param to `value`, dropping the sibling params (mutual
 * exclusion). Pushes a history entry unless `replace` is set or the value is
 * unchanged — in which case it replaces, normalizing param order without a
 * dead history entry.
 *
 * @param {keyof typeof PANEL_PARAMS} name
 * @param {string} value
 * @param {{replace?: boolean}} [opts]
 */
export function setPanel(name, value, { replace = false } = {}) {
  const param = paramFor(name);
  const params = new URLSearchParams(location.search);
  for (const sibling of siblingParams(name)) params.delete(sibling);
  if (params.get(param) !== value) {
    params.set(param, value);
    const url = '?' + params.toString();
    if (replace) {
      history.replaceState(null, '', url);
    } else {
      history.pushState(null, '', url);
    }
  } else {
    history.replaceState(null, '', '?' + params.toString());
  }
}

/** Remove `name`'s query param (replaceState). No-op when the param is absent.
 * @param {keyof typeof PANEL_PARAMS} name */
export function clearPanel(name) {
  const param = paramFor(name);
  const params = new URLSearchParams(location.search);
  if (!params.has(param)) return;
  params.delete(param);
  const qs = params.toString();
  history.replaceState(null, '', qs ? '?' + qs : location.pathname);
}

/** Read `name`'s query param from the current URL, or null when absent.
 * @param {keyof typeof PANEL_PARAMS} name
 * @returns {string | null} */
export function getPanel(name) {
  return new URLSearchParams(location.search).get(paramFor(name)) || null;
}

/** The first open panel in popstate priority order, or null when none.
 * @returns {{name: keyof typeof PANEL_PARAMS, value: string} | null} */
export function getOpenPanel() {
  for (const key of Object.keys(PANEL_PARAMS)) {
    const name = /** @type {keyof typeof PANEL_PARAMS} */ (key);
    const value = getPanel(name);
    if (value) return { name, value };
  }
  return null;
}

/**
 * Hide every registered panel except `self`, resetting each hidden panel's
 * content prop so a later re-open starts clean. Panels with no element (not
 * present in the DOM) are skipped, as is `self`.
 *
 * @param {Record<string, {el: any | null, reset?: () => void}>} panels
 * @param {string} self the panel to leave visible
 */
export function hideSiblingPanels(panels, self) {
  for (const [name, panel] of Object.entries(panels)) {
    if (name === self || !panel?.el) continue;
    panel.el.classList.add('hidden');
    panel.reset?.();
  }
}
