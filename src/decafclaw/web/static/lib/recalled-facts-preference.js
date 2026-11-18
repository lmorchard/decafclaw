/**
 * Client preference for the in-conversation recalled-facts disclosure (#1514).
 *
 * The switch lives in the desktop control strip and the mobile conversation
 * menu, so both surfaces persist through this module and announce changes on
 * a document event instead of sharing a store.
 */

export const STORAGE_KEY = 'decafclaw-show-recalled-facts';
export const PREFERENCE_EVENT = 'recalled-facts-preference-changed';

/** Default is on: recall visibility is the feature, not an opt-in extra. */
export function loadShowRecalledFacts() {
  try { return localStorage.getItem(STORAGE_KEY) !== 'false'; } catch { return true; }
}

/** @param {boolean} enabled */
export function storeShowRecalledFacts(enabled) {
  try { localStorage.setItem(STORAGE_KEY, enabled ? 'true' : 'false'); } catch { /* unavailable */ }
}

/** @param {boolean} enabled */
export function broadcastShowRecalledFacts(enabled) {
  document.dispatchEvent(new CustomEvent(PREFERENCE_EVENT, { detail: { enabled } }));
}
