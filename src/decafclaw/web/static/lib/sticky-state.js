/**
 * Sticky-slot state — per-conversation single-widget cache + collapse flag.
 *
 * State per conv: { widgetType, data, collapsed }
 * Snapshot: { widgetType, data, collapsed, visible }
 *
 * `collapsed` persists per-conv in localStorage (sticky-collapsed.{convId});
 * its first value is derived from the viewport (collapsed on mobile ≤639px,
 * expanded on desktop). Survives reload and conv-switch.
 */

const COLLAPSE_KEY_PREFIX = 'sticky-collapsed.';
const MOBILE_MAX = 639;

const _state = { byConv: new Map(), active: null, subscribers: new Set() };

function _collapseKey(convId) { return COLLAPSE_KEY_PREFIX + convId; }

function _loadCollapsed(convId) {
  try {
    const v = localStorage.getItem(_collapseKey(convId));
    if (v === 'true') return true;
    if (v === 'false') return false;
  } catch { /* unavailable */ }
  // First visit: default from viewport.
  return window.matchMedia(`(max-width: ${MOBILE_MAX}px)`).matches;
}

function _saveCollapsed(convId, value) {
  try { localStorage.setItem(_collapseKey(convId), value ? 'true' : 'false'); }
  catch { /* unavailable */ }
}

function _ensure(convId) {
  if (!_state.byConv.has(convId)) {
    _state.byConv.set(convId, {
      widgetType: null, data: null, collapsed: _loadCollapsed(convId),
    });
  }
  return _state.byConv.get(convId);
}

function _publish() {
  const snap = currentSnapshot();
  for (const cb of _state.subscribers) {
    try { cb(snap); } catch (err) { console.error('sticky subscriber failed', err); }
  }
}

export function currentSnapshot() {
  if (!_state.active) {
    return { widgetType: null, data: null, collapsed: false, visible: false };
  }
  const s = _ensure(_state.active);
  return {
    widgetType: s.widgetType,
    data: s.data,
    collapsed: s.collapsed,
    visible: !!s.widgetType,
  };
}

export function subscribe(callback) {
  _state.subscribers.add(callback);
  return () => _state.subscribers.delete(callback);
}

export async function setActiveConv(convId) {
  _state.active = convId;
  if (!convId) { _publish(); return; }
  const s = _ensure(convId);
  try {
    const resp = await fetch(`/api/sticky/${encodeURIComponent(convId)}`,
                             { credentials: 'same-origin' });
    if (resp.ok) {
      const data = await resp.json();
      s.widgetType = data.widget_type || null;
      s.data = data.data || null;
    }
  } catch (err) {
    console.warn('sticky state load failed', err);
  }
  _publish();
}

/** Apply an incoming sticky_set / sticky_clear WS event. */
export function applyEvent(evt) {
  const convId = evt.conv_id;
  if (!convId) return;
  const s = _ensure(convId);
  if (evt.type === 'sticky_set') {
    s.widgetType = evt.widget_type || null;
    s.data = evt.data || null;
  } else if (evt.type === 'sticky_clear') {
    s.widgetType = null;
    s.data = null;
  }
  if (convId === _state.active) _publish();
}

export function toggleCollapsed() {
  if (!_state.active) return;
  const s = _ensure(_state.active);
  s.collapsed = !s.collapsed;
  _saveCollapsed(_state.active, s.collapsed);
  _publish();
}
