/**
 * Presets state — client-side store for shell auto-approval presets (#1158).
 *
 * Backed by /api/shell-approval/presets.
 */

import { ApiError, DefaultService } from './api-client/index.js';

/** @typedef {import('./api-client/index.js').ShellPresetItem} PresetItem */
/** @typedef {import('./api-client/index.js').ShellPresetMutateResponse} PresetMutateResponse */

const _state = {
  /** @type {PresetItem[]} */
  presets: [],
  /** @type {Set<(presets: PresetItem[]) => void>} */
  subscribers: new Set(),
  /** @type {Promise<PresetItem[]>|null} */
  inFlight: null,
};

function notify() {
  const snap = [..._state.presets];
  for (const cb of _state.subscribers) {
    try {
      cb(snap);
    } catch (err) {
      console.error('presets subscriber failed', err);
    }
  }
}

/**
 * Return current snapshot of presets.
 * @returns {PresetItem[]}
 */
export function snapshot() {
  return [..._state.presets];
}

/**
 * Subscribe to preset changes.
 * @param {(presets: PresetItem[]) => void} callback
 * @returns {() => void} unsubscribe
 */
export function subscribe(callback) {
  _state.subscribers.add(callback);
  callback(snapshot());
  return () => {
    _state.subscribers.delete(callback);
  };
}

/**
 * Fetch presets list from server.
 * @returns {Promise<PresetItem[]>}
 */
export async function load() {
  if (_state.inFlight) return _state.inFlight;
  _state.inFlight = (async () => {
    try {
      const data = await DefaultService.wrapperApiShellApprovalPresetsGet();
      _state.presets = data;
      notify();
      return _state.presets;
    } catch (e) {
      if (e instanceof ApiError) {
        console.debug('presets: fetch failed', e.status);
      } else {
        console.debug('presets: fetch failed', e);
      }
      throw e;
    } finally {
      _state.inFlight = null;
    }
  })();
  return _state.inFlight;
}

/**
 * Create or update a custom preset.
 * @param {string} name
 * @param {string[]} rules
 * @returns {Promise<PresetMutateResponse>}
 */
export async function savePreset(name, rules) {
  const res = await DefaultService.wrapperApiShellApprovalPresetsNamePut(name, { rules });
  await load();
  return res;
}

/**
 * Delete a custom preset.
 * @param {string} name
 * @returns {Promise<PresetMutateResponse>}
 */
export async function deletePreset(name) {
  const res = await DefaultService.wrapperApiShellApprovalPresetsNameDelete(name);
  await load();
  return res;
}

/** Test utility: reset internal state. */
export function reset() {
  _state.presets = [];
  _state.subscribers.clear();
  _state.inFlight = null;
}
