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
};

function _publish() {
  const snap = snapshot();
  for (const cb of _state.subscribers) {
    try { cb(snap); } catch (err) { console.error('bookmarks subscriber failed', err); }
  }
}

/** @returns {BookmarksPayload} */
function snapshot() {
  return _state.data ?? { files: [], vault: [] };
}

/**
 * @param {BookmarkTarget} target
 * @param {string} path
 * @returns {boolean}
 */
function isBookmarked(target, path) {
  return snapshot()[target].some(b => b.path === path);
}

/**
 * Fetch (and cache) the bookmark payload. Reuses an in-flight request so
 * several components mounting at once trigger a single GET.
 * @returns {Promise<BookmarksPayload>}
 */
function load() {
  if (_state.inFlight) return _state.inFlight;
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
    _publish();
    return snapshot();
  })();
  return _state.inFlight;
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
async function toggle(target, path, kind, label) {
  await load();
  if (isBookmarked(target, path)) {
    _state.data = await DefaultService.wrapperApiBookmarksDelete(target, path);
  } else {
    _state.data = await DefaultService.wrapperApiBookmarksPost({
      target,
      path,
      label: label ?? null,
      kind,
    });
  }
  _publish();
  return snapshot();
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
}


export { load, toggle, isBookmarked, snapshot, subscribe, reset };
