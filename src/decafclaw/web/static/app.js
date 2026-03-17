/**
 * DecafClaw Web Gateway — application entry point.
 *
 * Instantiates services, wires up components, manages auth state.
 */

import { AuthClient } from './lib/auth-client.js';
import { WebSocketClient } from './lib/websocket-client.js';
import { ConversationStore } from './lib/conversation-store.js';

// Import components (registers custom elements)
import './components/login-view.js';
import './components/conversation-sidebar.js';
import './components/chat-view.js';
import './components/chat-message.js';
import './components/chat-input.js';
import './components/theme-toggle.js';

// -- Services -----------------------------------------------------------------

const auth = new AuthClient();

/** @returns {string} */
function getWsUrl() {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${proto}//${location.host}/ws/chat`;
}

const ws = new WebSocketClient(getWsUrl());
const store = new ConversationStore(ws);

// -- DOM references -----------------------------------------------------------

const loginView = /** @type {import('./components/login-view.js').LoginView} */ (
  document.querySelector('login-view')
);
const chatLayout = document.getElementById('chat-layout');
const sidebar = /** @type {any} */ (document.querySelector('conversation-sidebar'));
const chatView = /** @type {any} */ (document.querySelector('chat-view'));
const chatInput = /** @type {any} */ (document.querySelector('chat-input'));

// -- Wire services to components ----------------------------------------------

if (loginView) loginView.authClient = auth;
if (sidebar) { sidebar.store = store; sidebar.authClient = auth; }
if (chatView) chatView.store = store;

// Update chat-input disabled/busy state from store, auto-focus when ready
let wasBusy = false;
store.addEventListener('change', () => {
  if (chatInput) {
    chatInput.disabled = store.isBusy;
    chatInput.busy = store.isBusy;
    // Focus when agent finishes or conversation changes
    if ((wasBusy && !store.isBusy) || store.currentConvId) {
      requestAnimationFrame(() => chatInput.focus());
    }
    wasBusy = store.isBusy;
  }
});

// Handle send events from chat-input
document.addEventListener('send', (e) => {
  const text = /** @type {CustomEvent} */ (e).detail?.text;
  if (text) store.sendMessage(text);
});

// Handle stop events from chat-input
document.addEventListener('stop', () => store.cancelTurn());

// Keyboard shortcuts
document.addEventListener('keydown', (e) => {
  const mod = e.metaKey || e.ctrlKey;
  if (mod && e.key === 'k') {
    e.preventDefault();
    store.createConversation();
  } else if (mod && e.key === '/') {
    e.preventDefault();
    chatInput?.focus();
  }
});

// -- Toast notifications ------------------------------------------------------

/** @param {string} message @param {number} [duration] */
function showToast(message, duration = 5000) {
  const container = document.getElementById('toast-container');
  if (!container) return;
  const toast = document.createElement('div');
  toast.className = 'toast';
  toast.textContent = message;
  container.appendChild(toast);
  setTimeout(() => toast.remove(), duration);
}

// Surface store errors as toasts
store.addEventListener('change', () => {
  // The store clears errors after emitting, so we check via a custom approach
});

// Listen for WebSocket error messages directly
ws.addEventListener('message', (e) => {
  const msg = /** @type {CustomEvent} */ (e).detail;
  if (msg?.type === 'error' && msg?.message) {
    showToast(msg.message);
  }
});

// -- Connection status --------------------------------------------------------

const connectionBanner = document.getElementById('connection-banner');
ws.addEventListener('close', () => {
  if (connectionBanner) connectionBanner.classList.remove('hidden');
});
ws.addEventListener('open', () => {
  if (connectionBanner) connectionBanner.classList.add('hidden');
});

// -- Auth state management ----------------------------------------------------

function showChat() {
  if (loginView) loginView.classList.add('hidden');
  if (chatLayout) chatLayout.classList.remove('hidden');
  ws.connect();
}

function showLogin() {
  if (loginView) loginView.classList.remove('hidden');
  if (chatLayout) chatLayout.classList.add('hidden');
  ws.disconnect();
}

auth.addEventListener('login', () => showChat());
auth.addEventListener('logout', () => showLogin());

// -- URL state ----------------------------------------------------------------

/** Update ?conv= query param when conversation changes */
store.addEventListener('change', () => {
  const convId = store.currentConvId;
  const params = new URLSearchParams(location.search);
  if (convId && params.get('conv') !== convId) {
    params.set('conv', convId);
    history.replaceState(null, '', '?' + params.toString());
  }
});

/** Read ?conv= on startup and select that conversation */
function getConvFromUrl() {
  return new URLSearchParams(location.search).get('conv') || null;
}

// -- Init ---------------------------------------------------------------------

async function init() {
  const username = await auth.checkSession();
  if (username) {
    showChat();
    // Once WebSocket is connected and conversation list is loaded,
    // select the bookmarked conversation if any
    const savedConvId = getConvFromUrl();
    if (savedConvId) {
      const onOpen = () => {
        // Wait for conv_list to arrive, then select
        const onFirstChange = () => {
          store.removeEventListener('change', onFirstChange);
          if (store.conversations.some(c => c.conv_id === savedConvId)) {
            store.selectConversation(savedConvId);
          }
        };
        store.addEventListener('change', onFirstChange);
        ws.removeEventListener('open', onOpen);
      };
      ws.addEventListener('open', onOpen);
    }
  } else {
    showLogin();
  }
}

init();

// Sidebar drag resize
const resizeHandle = document.getElementById('sidebar-resize-handle');
const MIN_WIDTH = 160;
const MAX_WIDTH = 480;

const savedWidth = localStorage.getItem('sidebar-width');
if (savedWidth) document.documentElement.style.setProperty('--sidebar-width', savedWidth + 'px');

if (resizeHandle) {
  let dragging = false;
  resizeHandle.addEventListener('mousedown', (e) => {
    dragging = true;
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    e.preventDefault();
  });
  document.addEventListener('mousemove', (e) => {
    if (!dragging) return;
    const layoutRect = chatLayout.getBoundingClientRect();
    const newWidth = Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, e.clientX - layoutRect.left));
    document.documentElement.style.setProperty('--sidebar-width', newWidth + 'px');
  });
  document.addEventListener('mouseup', () => {
    if (!dragging) return;
    dragging = false;
    document.body.style.cursor = '';
    document.body.style.userSelect = '';
    const w = getComputedStyle(document.documentElement).getPropertyValue('--sidebar-width').trim();
    localStorage.setItem('sidebar-width', String(parseInt(w)));
  });
}
