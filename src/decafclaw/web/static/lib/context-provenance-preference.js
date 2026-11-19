/**
 * Client preference for the turn provenance capsule row (#1563).
 *
 * The switch lives in the conversation settings menu; the transcript listens
 * through this module and announces changes on a document event instead of
 * sharing a store. The pre-#1563 key only held recalled facts; it is read once
 * for migration so an existing user's choice carries over.
 */

export const STORAGE_KEY = 'decafclaw-show-context-provenance';
export const LEGACY_STORAGE_KEY = 'decafclaw-show-recalled-facts';
export const PREFERENCE_EVENT = 'context-provenance-preference-changed';

/** Default is on: passive context visibility is the feature, not an opt-in extra. */
export function loadShowContextProvenance() {
  try {
    const current = localStorage.getItem(STORAGE_KEY);
    if (current !== null) return current !== 'false';
    const legacy = localStorage.getItem(LEGACY_STORAGE_KEY);
    return legacy !== 'false';
  } catch {
    return true;
  }
}

/** @param {boolean} enabled */
export function storeShowContextProvenance(enabled) {
  try { localStorage.setItem(STORAGE_KEY, enabled ? 'true' : 'false'); } catch { /* unavailable */ }
}

/** @param {boolean} enabled */
export function broadcastShowContextProvenance(enabled) {
  document.dispatchEvent(new CustomEvent(PREFERENCE_EVENT, { detail: { enabled } }));
}
