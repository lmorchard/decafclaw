/**
 * Standalone canvas page controller.
 *
 * Reads conv_id from the URL path, fetches initial state via REST,
 * mounts the active widget into <dc-widget-host>, and subscribes to
 * canvas_update events over WebSocket for live updates.
 */

const PATH_RE = /^\/canvas\/([^/?#]+)/;
const m = location.pathname.match(PATH_RE);
let convId = '';
if (m) {
  try {
    convId = decodeURIComponent(m[1]);
  } catch {
    convId = '';  // malformed percent-encoding — fall through to error UI
  }
}
if (!convId) {
  document.body.innerHTML = '<p>Invalid canvas URL.</p>';
  throw new Error('no conv_id');
}

const host = /** @type {HTMLElement & {widgetType: string, data: any, mode: string}} */ (
  document.getElementById('canvas-standalone-host')
);
const empty = document.getElementById('canvas-empty-state');
const labelEl = document.getElementById('canvas-label');
const backLink = /** @type {HTMLAnchorElement} */ (document.getElementById('canvas-back-link'));
backLink.href = `/?conv=${encodeURIComponent(convId)}`;

/** @param {any} tab */
function applyTab(tab) {
  if (!tab) {
    host.hidden = true;
    if (empty) empty.hidden = false;
    if (labelEl) labelEl.textContent = 'Canvas (empty)';
    document.title = 'Canvas';
    return;
  }
  if (empty) empty.hidden = true;
  host.hidden = false;
  if (labelEl) labelEl.textContent = tab.label || 'Canvas';
  document.title = `Canvas — ${tab.label || 'Canvas'}`;

  host.widgetType = tab.widget_type;
  host.mode = 'canvas';
  host.data = tab.data;
}

async function loadInitial() {
  try {
    const resp = await fetch(`/api/canvas/${encodeURIComponent(convId)}`,
                             { credentials: 'same-origin' });
    if (!resp.ok) {
      console.warn('canvas load failed', resp.status);
      applyTab(null);
      return;
    }
    const data = await resp.json();
    const tabs = data.tabs || [];
    const tab = tabs.find((/** @type {any} */ t) => t.id === data.active_tab) || null;
    applyTab(tab);
  } catch (err) {
    console.error('canvas load error', err);
    applyTab(null);
  }
}

function openWebSocket() {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const ws = new WebSocket(`${proto}//${location.host}/ws/chat`);
  ws.addEventListener('open', () => {
    ws.send(JSON.stringify({ type: 'select_conv', conv_id: convId }));
  });
  ws.addEventListener('message', (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch { return; }
    if (msg.type !== 'canvas_update') return;
    if (msg.conv_id && msg.conv_id !== convId) return;
    applyTab(msg.tab);
  });
  ws.addEventListener('close', () => {
    // Reconnect/backoff out-of-scope per spec.
    console.info('canvas WS closed');
  });
}

await loadInitial();
openWebSocket();
