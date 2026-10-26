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
  /** Monotonic sequence token to sequence reads and mutations. */
  seq: 0,
};

/** @type {Promise<any>} */
let _tail = Promise.resolve();

/**
 * @template T
 * @param {() => Promise<T>} task
 * @returns {Promise<T>}
 */
function _runExclusive(task) {
  const result = _tail.then(task, task);
  _tail = result.then(() => undefined, () => undefined);
  return result;
}

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
 * Fetch latest presets, publishing only if the token matches the latest operation.
 * @param {number} seq
 * @returns {Promise<PresetItem[]>}
 */
async function fetchLatest(seq) {
  if (!_state.inFlight) {
    _state.inFlight = (async () => {
      try {
        const data = await DefaultService.wrapperApiShellApprovalPresetsGet();
        if (seq === _state.seq) {
          _state.presets = data;
          notify();
        }
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
      return snapshot();
    })();
  }
  await _state.inFlight;
  return snapshot();
}

/**
 * Fetch presets list from server.
 * @returns {Promise<PresetItem[]>}
 */
export function load() {
  if (_state.inFlight) return _state.inFlight;
  const seq = ++_state.seq;
  return fetchLatest(seq);
}
/**
 * Create or update a custom preset.
 * @param {string} name
 * @param {string[]} rules
 * @returns {Promise<PresetMutateResponse>}
 */
export function savePreset(name, rules) {
  return _runExclusive(async () => {
    const res = await DefaultService.wrapperApiShellApprovalPresetsNamePut(name, { rules });
    // Invalidate in-flight and bump seq so older fetches are ignored
    _state.inFlight = null;
    const seq = ++_state.seq;
    await fetchLatest(seq);
    return res;
  });
}

/**
 * Delete a custom preset.
 * @param {string} name
 * @returns {Promise<PresetMutateResponse>}
 */
export function deletePreset(name) {
  return _runExclusive(async () => {
    const res = await DefaultService.wrapperApiShellApprovalPresetsNameDelete(name);
    _state.inFlight = null;
    const seq = ++_state.seq;
    await fetchLatest(seq);
    return res;
  });
}

/** Test utility: reset internal state. */
export function reset() {
  _state.presets = [];
  _state.subscribers.clear();
  _state.inFlight = null;
  _state.seq = 0;
  _tail = Promise.resolve();
}
