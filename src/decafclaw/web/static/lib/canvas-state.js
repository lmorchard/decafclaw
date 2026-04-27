/**
 * Canvas state — per-conversation canvas tab cache + dismiss flag + unread flag.
 *
 * Memory-only state; reload returns the user to the default visible state
 * (any non-empty canvas is shown by default).
 *
 * Subscribers receive `(state)` snapshots after every mutation so the
 * panel and resummon UI re-render. Snapshot shape:
 *   { tab: {id,label,widget_type,data}|null,
 *     visible: boolean,
 *     unreadDot: boolean }
 */

const _state = {
  byConv: new Map(),  // convId -> { tab, dismissed, unreadDot }
  active: null,
  subscribers: new Set(),
};

function _ensure(convId) {
  if (!_state.byConv.has(convId)) {
    _state.byConv.set(convId, { tab: null, dismissed: false, unreadDot: false });
  }
  return _state.byConv.get(convId);
}

function _publish() {
  const snap = currentSnapshot();
  for (const cb of _state.subscribers) {
    try { cb(snap); } catch (err) { console.error('canvas subscriber failed', err); }
  }
}

export function currentSnapshot() {
  if (!_state.active) {
    return { tab: null, visible: false, unreadDot: false };
  }
  const s = _ensure(_state.active);
  return {
    tab: s.tab,
    visible: !!s.tab && !s.dismissed,
    unreadDot: s.unreadDot,
  };
}

export function subscribe(callback) {
  _state.subscribers.add(callback);
  return () => _state.subscribers.delete(callback);
}

/** Return the current active conversation id, or null. */
export function getActiveConvId() {
  return _state.active;
}

/** Switch to a different conversation. Loads state from the server. */
export async function setActiveConv(convId) {
  _state.active = convId;
  if (!convId) { _publish(); return; }
  _ensure(convId);
  try {
    const resp = await fetch(`/api/canvas/${encodeURIComponent(convId)}`,
                             { credentials: 'same-origin' });
    if (resp.ok) {
      const data = await resp.json();
      const tabs = data.tabs || [];
      const activeId = data.active_tab;
      const tab = tabs.find(t => t.id === activeId) || null;
      const s = _ensure(convId);
      s.tab = tab;
      s.dismissed = false;
      s.unreadDot = false;
    }
  } catch (err) {
    console.warn('canvas state load failed', err);
  }
  _publish();
}

/** Apply an incoming canvas_update WS event. */
export function applyEvent(evt) {
  const convId = evt.conv_id;
  if (!convId) return;
  const s = _ensure(convId);
  const kind = evt.kind || 'set';

  if (kind === 'clear') {
    s.tab = null;
    s.unreadDot = false;
    s.dismissed = false;
  } else if (kind === 'set') {
    s.tab = evt.tab || null;
    s.dismissed = false;
    s.unreadDot = false;
  } else if (kind === 'update') {
    s.tab = evt.tab || s.tab;
    if (s.dismissed) {
      s.unreadDot = true;
    } else {
      s.unreadDot = false;
    }
  }
  if (convId === _state.active) _publish();
}

export function dismiss() {
  if (!_state.active) return;
  const s = _ensure(_state.active);
  s.dismissed = true;
  _publish();
}

export function resummon() {
  if (!_state.active) return;
  const s = _ensure(_state.active);
  s.dismissed = false;
  s.unreadDot = false;
  _publish();
}
