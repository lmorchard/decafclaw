/**
 * Standalone canvas page controller.
 *
 * Two URL forms:
 *   /canvas/{conv_id}            → bare; renders active tab; follows active changes via WS.
 *   /canvas/{conv_id}/{tab_id}   → tab-locked; renders one specific tab; ignores active changes.
 */

import { MESSAGE_TYPES } from './lib/message-types.js';

const PATH_RE = /^\/canvas\/([^/?#]+)(?:\/([^/?#]+))?/;
const m = location.pathname.match(PATH_RE);
let convId = '';
let lockedTabId = null;
if (m) {
  try { convId = decodeURIComponent(m[1]); } catch { convId = ''; }
  if (m[2]) {
    try { lockedTabId = decodeURIComponent(m[2]); } catch { lockedTabId = null; }
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

let currentTab = null;
let allTabs = [];
let serverActiveTabId = null;

function showEmpty(msg) {
  if (host) host.hidden = true;
  if (empty) {
    empty.hidden = false;
    empty.textContent = msg;
  }
  if (labelEl) labelEl.textContent = 'Canvas';
  document.title = 'Canvas';
  currentTab = null;
}

function showTab(tab) {
  if (!tab) {
    showEmpty(lockedTabId ? `Tab "${lockedTabId}" no longer exists.` : 'No canvas content yet.');
    return;
  }
  if (empty) empty.hidden = true;
  host.hidden = false;
  host.widgetType = tab.widget_type;
  host.mode = 'canvas';
  host.data = tab.data;
  if (labelEl) labelEl.textContent = tab.label || 'Canvas';
  document.title = `Canvas — ${tab.label || 'Canvas'}`;
  currentTab = tab;
}

function pickTabForRender() {
  if (lockedTabId) {
    return allTabs.find(t => t.id === lockedTabId) || null;
  }
  return allTabs.find(t => t.id === serverActiveTabId) || null;
}

async function loadInitial() {
  try {
    const resp = await fetch(`/api/canvas/${encodeURIComponent(convId)}`,
                             { credentials: 'same-origin' });
    if (!resp.ok) {
      console.warn('canvas load failed', resp.status);
      showEmpty('No canvas content yet.');
      return;
    }
    const data = await resp.json();
    allTabs = data.tabs || [];
    serverActiveTabId = data.active_tab || null;
    showTab(pickTabForRender());
  } catch (err) {
    console.error('canvas load error', err);
    showEmpty('No canvas content yet.');
  }
}

function openWebSocket() {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const ws = new WebSocket(`${proto}//${location.host}/ws/chat`);
  ws.addEventListener('open', () => {
    ws.send(JSON.stringify({ type: MESSAGE_TYPES.SELECT_CONV, conv_id: convId }));
  });
  ws.addEventListener('message', (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch { return; }
    if (msg.type !== MESSAGE_TYPES.CANVAS_UPDATE) return;
    if (msg.conv_id && msg.conv_id !== convId) return;

    const kind = msg.kind || 'update';
    if (kind === 'clear') {
      allTabs = [];
      serverActiveTabId = null;
      showTab(null);
      return;
    }
    if (kind === 'new_tab') {
      if (msg.tab) allTabs.push(msg.tab);
      serverActiveTabId = msg.active_tab;
      if (lockedTabId) {
        // Locked URL: only react if the new tab matches our locked id
        // (e.g. page loaded before the tab existed, then it was created).
        if (msg.tab && msg.tab.id === lockedTabId) showTab(msg.tab);
      } else {
        showTab(pickTabForRender());
      }
      return;
    }
    if (kind === 'update') {
      if (msg.tab) {
        const idx = allTabs.findIndex(t => t.id === msg.tab.id);
        if (idx >= 0) allTabs[idx] = msg.tab;
        if (lockedTabId && msg.tab.id === lockedTabId) {
          showTab(msg.tab);
        } else if (currentTab && currentTab.id === msg.tab.id) {
          showTab(msg.tab);
        }
      }
      return;
    }
    if (kind === 'close_tab') {
      const closed = msg.closed_tab_id;
      allTabs = allTabs.filter(t => t.id !== closed);
      serverActiveTabId = msg.active_tab;
      if (lockedTabId && closed === lockedTabId) {
        showEmpty(`Tab "${lockedTabId}" no longer exists.`);
        return;
      }
      if (!lockedTabId) showTab(pickTabForRender());
      return;
    }
    if (kind === 'set_active') {
      serverActiveTabId = msg.active_tab;
      if (!lockedTabId) showTab(pickTabForRender());
      // Locked URL: ignore.
      return;
    }
  });
  ws.addEventListener('close', () => {
    console.info('canvas WS closed');
  });
}

await loadInitial();
openWebSocket();
