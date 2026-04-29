/**
 * Canvas state — per-conversation multi-tab cache + dismiss flag + unread flag.
 *
 * State per conv:
 *   { tabs: [{id, label, widget_type, data}, ...], activeTabId, dismissed, unreadDot }
 *
 * Subscribers receive snapshots:
 *   { tabs, activeTabId, activeTab, visible, unreadDot }
 *
 * Dismiss persists per-conv in localStorage (canvas-dismissed.{convId});
 * cleared on new_tab / close_tab (when panel becomes empty) / clear /
 * resummon click. Survives reload, conv-switch, and update events.
 */

const DISMISS_KEY_PREFIX = 'canvas-dismissed.';

const _state = {
  byConv: new Map(),  // convId -> { tabs, activeTabId, dismissed, unreadDot }
  active: null,
  subscribers: new Set(),
};

function _dismissKey(convId) { return DISMISS_KEY_PREFIX + convId; }
function _loadDismissed(convId) {
  try { return localStorage.getItem(_dismissKey(convId)) === 'true'; }
  catch { return false; }
}
function _saveDismissed(convId, value) {
  try {
    if (value) localStorage.setItem(_dismissKey(convId), 'true');
    else localStorage.removeItem(_dismissKey(convId));
  } catch { /* localStorage unavailable */ }
}

function _ensure(convId) {
  if (!_state.byConv.has(convId)) {
    _state.byConv.set(convId, {
      tabs: [],
      activeTabId: null,
      dismissed: _loadDismissed(convId),
      unreadDot: false,
    });
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
    return { tabs: [], activeTabId: null, activeTab: null, visible: false, unreadDot: false };
  }
  const s = _ensure(_state.active);
  const activeTab = s.tabs.find(t => t.id === s.activeTabId) || null;
  return {
    tabs: s.tabs.slice(),
    activeTabId: s.activeTabId,
    activeTab,
    visible: s.tabs.length > 0 && !s.dismissed,
    unreadDot: s.unreadDot,
  };
}

export function subscribe(callback) {
  _state.subscribers.add(callback);
  return () => _state.subscribers.delete(callback);
}

export function getActiveConvId() { return _state.active; }

export async function setActiveConv(convId) {
  _state.active = convId;
  if (!convId) { _publish(); return; }
  const s = _ensure(convId);
  s.unreadDot = false;
  try {
    const resp = await fetch(`/api/canvas/${encodeURIComponent(convId)}`,
                             { credentials: 'same-origin' });
    if (resp.ok) {
      const data = await resp.json();
      s.tabs = (data.tabs || []).map(t => ({...t}));
      s.activeTabId = data.active_tab || null;
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
  const kind = evt.kind || 'update';

  if (kind === 'clear') {
    s.tabs = [];
    s.activeTabId = null;
    s.unreadDot = false;
    s.dismissed = false;
    _saveDismissed(convId, false);
  } else if (kind === 'new_tab') {
    if (evt.tab) s.tabs.push({...evt.tab});
    s.activeTabId = evt.active_tab;
    s.dismissed = false;
    s.unreadDot = false;
    _saveDismissed(convId, false);
  } else if (kind === 'update') {
    if (evt.tab) {
      const idx = s.tabs.findIndex(t => t.id === evt.tab.id);
      if (idx >= 0) s.tabs[idx] = {...evt.tab};
    }
    if (s.dismissed) s.unreadDot = true;
  } else if (kind === 'close_tab') {
    s.tabs = s.tabs.filter(t => t.id !== evt.closed_tab_id);
    s.activeTabId = evt.active_tab;
    if (s.tabs.length === 0) {
      // Last tab closed — clear dismiss flag too so a future new_tab reveals.
      s.dismissed = false;
      _saveDismissed(convId, false);
    }
  } else if (kind === 'set_active') {
    s.activeTabId = evt.active_tab;
  }
  if (convId === _state.active) _publish();
}

export function dismiss() {
  if (!_state.active) return;
  const s = _ensure(_state.active);
  s.dismissed = true;
  _saveDismissed(_state.active, true);
  _publish();
}

export function resummon() {
  if (!_state.active) return;
  const s = _ensure(_state.active);
  s.dismissed = false;
  s.unreadDot = false;
  _saveDismissed(_state.active, false);
  _publish();
}

/** User clicks a tab — switch active. POSTs to server, optimistic UI. */
export async function switchToTab(tabId) {
  const convId = _state.active;
  if (!convId) return;
  // Optimistic local update so UI feels responsive.
  const s = _ensure(convId);
  s.activeTabId = tabId;
  _publish();
  try {
    await fetch(`/api/canvas/${encodeURIComponent(convId)}/active_tab`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({ tab_id: tabId }),
    });
  } catch (err) {
    console.warn('canvas active_tab POST failed', err);
  }
}

/** User clicks [×] on a tab — close it via REST. Server emits canvas_update kind=close_tab. */
export async function closeTabFromUi(tabId) {
  const convId = _state.active;
  if (!convId) return;
  try {
    await fetch(`/api/canvas/${encodeURIComponent(convId)}/close_tab`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({ tab_id: tabId }),
    });
  } catch (err) {
    console.warn('canvas close_tab POST failed', err);
  }
}
