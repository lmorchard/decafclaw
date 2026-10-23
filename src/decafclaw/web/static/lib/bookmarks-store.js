/**
 * Bookmarks state — per-agent pinned file and vault locations (#1086).
 *
 * The server is the source of truth (`/api/bookmarks`, persisted at
 * `data/{agent_id}/bookmarks.json`). This module keeps a client-side cache
 * so the sidebar sections and the viewer toolbar toggles share one fetch;
 * mutations replace the cache with the server's returned payload and notify
 * subscribers (same shape as sticky-state.js / canvas-state.js).
 */

import { ApiError, DefaultService } from './api-client/index.js';

/** @typedef {'files'|'vault'} BookmarkTarget */
/** @typedef {import('./api-client/index.js').BookmarksResponse} BookmarksPayload */
/** @typedef {import('./api-client/index.js').BookmarkEntry} BookmarkEntry */

const _state = {
  /** @type {BookmarksPayload|null} */
  data: null,
  /** @type {Promise<BookmarksPayload>|null} */
  inFlight: null,
  /** @type {Set<(snap: BookmarksPayload) => void>} */
  subscribers: new Set(),
  /** Monotonic token for the latest logical bookmark operation. */
  seq: 0,
};

function _publish() {
  const snap = snapshot();
  for (const cb of _state.subscribers) {
    try { cb(snap); } catch (err) { console.error('bookmarks subscriber failed', err); }
  }
}

/**
 * Entries without a non-empty string path are dropped so subscribers can
 * never see a payload shaped by a different endpoint's response.
 * @returns {BookmarksPayload}
 */
function snapshot() {
  const d = _state.data;
  /** @param {unknown} list */
  const pick = (list) =>
    Array.isArray(list)
      ? list.filter(b => b && typeof b.path === 'string' && b.path)
      : [];
  return { files: pick(d?.files), vault: pick(d?.vault) };
}

/**
 * @param {BookmarkTarget} target
 * @param {string} path
 * @returns {boolean}
 */
function isBookmarked(target, path) {
  return snapshot()[target].some(b => b.path === path);
}

let _tail = Promise.resolve();

/**
 * Serialize bookmark operations so overlapping toggles can't interleave a
 * read/mutate cycle. Each task checks its own logical-operation token before
 * applying state, so a superseded task (or a slow older response) is a no-op.
 * @param {() => Promise<BookmarksPayload>} task
 * @returns {Promise<BookmarksPayload>}
 */
function _runExclusive(task) {
  const result = _tail.then(task, task);
  _tail = result.then(() => undefined, () => undefined);
  return result;
}

/**
 * Fetch the bookmark payload (reusing an in-flight GET) and resolve with it,
 * publishing only when the supplied logical-operation token is still current.
 * @param {number} seq
 * @returns {Promise<BookmarksPayload>}
 */
async function fetchLatest(seq) {
  if (!_state.inFlight) {
    _state.inFlight = (async () => {
      try {
        _state.data = await DefaultService.wrapperApiBookmarksGet();
      } catch (e) {
        if (e instanceof ApiError) {
          console.debug('bookmarks: fetch got non-ok response', e.status);
        } else {
          console.debug('bookmarks: fetch failed', e);
        }
      } finally {
        _state.inFlight = null;
      }
      if (seq === _state.seq) _publish();
      return snapshot();
    })();
  }
  const snap = await _state.inFlight;
  if (seq !== _state.seq) return snapshot();
  return snap;
}

/**
 * Fetch (and cache) the bookmark payload. Reuses an in-flight request so
 * several components mounting at once trigger a single GET. Responses from
 * superseded operations are dropped so a slow older response can't clobber
 * newer state.
 * @returns {Promise<BookmarksPayload>}
 */
function load() {
  const seq = ++_state.seq;
  if (_state.inFlight) return _state.inFlight;
  return fetchLatest(seq);
}

/**
 * Toggle a bookmark: remove it when present, add it otherwise.
 *
 * @param {BookmarkTarget} target
 * @param {string} path
 * @param {'file'|'dir'|'page'|'folder'} kind
 * @param {string} [label] - defaults server-side to the path's last segment
 * @returns {Promise<BookmarksPayload>}
 */
function toggle(target, path, kind, label) {
  const seq = ++_state.seq;
  const result = _runExclusive(async () => {
    if (seq !== _state.seq) return snapshot();
    const data = await fetchLatest(seq);
    const next = isBookmarked(target, path)
      ? await DefaultService.wrapperApiBookmarksDelete(target, path)
      : await DefaultService.wrapperApiBookmarksPost({
          target,
          path,
          label: label ?? null,
          kind,
        });
    if (seq === _state.seq) _state.data = next;
    _publish();
    return snapshot();
  });
  return result;
}

/**
 * Subscribe to payload changes.
 * @param {(snap: BookmarksPayload) => void} callback
 * @returns {() => void} unsubscribe
 */
function subscribe(callback) {
  _state.subscribers.add(callback);
  return () => _state.subscribers.delete(callback);
}

/** Clear the cache and subscribers (test teardown / agent switch). */
function reset() {
  _state.data = null;
  _state.inFlight = null;
  _state.subscribers.clear();
  _state.seq += 1;
}

export { load, toggle, isBookmarked, snapshot, subscribe, reset };
