/**
 * DecafClaw Web Gateway — application entry point.
 *
 * Instantiates services, wires up components, manages auth state.
 */

import { AuthClient } from './lib/auth-client.js';
import { WebSocketClient } from './lib/websocket-client.js';
import { ConversationStore } from './lib/conversation-store.js';
import { setupResizeHandle } from './lib/utils.js';

// Import components (registers custom elements)
import './components/login-view.js';
import './components/conversation-sidebar.js';
import './components/chat-view.js';
import './components/chat-message.js';
import './components/chat-input.js';
import './components/theme-toggle.js';
import './components/wiki-page.js';
import './components/config-panel.js';

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
    chatInput.busy = store.isBusy;
    chatInput.disabled = store.isReadOnly;
    chatInput.convId = store.currentConvId || '';
    chatInput.placeholder = store.isReadOnly ? 'Read-only conversation' : 'Type a message...';
    // Focus when agent finishes or conversation changes
    if (!store.isReadOnly && ((wasBusy && !store.isBusy) || store.currentConvId)) {
      requestAnimationFrame(() => chatInput.focus());
    }
    wasBusy = store.isBusy;
  }
});

// Handle send events from chat-input
document.addEventListener('send', (e) => {
  const detail = /** @type {CustomEvent} */ (e).detail;
  if (detail?.text || detail?.attachments?.length) {
    store.sendMessage(detail.text || '', detail.attachments || []);
  }
});

// Handle stop events from chat-input
document.addEventListener('stop', () => store.cancelTurn());

// Drop files anywhere in the chat area → forward to chat-input
const chatMain = document.getElementById('chat-main');
if (chatMain) {
  chatMain.addEventListener('dragover', (e) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'copy';
  });
  chatMain.addEventListener('drop', (e) => {
    e.preventDefault();
    if (e.dataTransfer?.files?.length && chatInput) {
      chatInput.addFiles(e.dataTransfer.files);
    }
  });
}

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

// -- Wiki view ----------------------------------------------------------------

const chatMainEl = document.getElementById('chat-main');
const wikiMainEl = document.getElementById('wiki-main');
const wikiResizeHandle = document.getElementById('wiki-resize-handle');
const wikiPageEl = /** @type {any} */ (document.querySelector('#wiki-main wiki-page'));
const configPanelEl = /** @type {any} */ (document.querySelector('#wiki-main config-panel'));

/** Show a wiki page alongside chat. */
function showWikiPage(page, { replace = false } = {}) {
  if (wikiPageEl) wikiPageEl.page = page;
  wikiPageEl?.classList.remove('hidden');
  configPanelEl?.classList.add('hidden');
  wikiMainEl?.classList.remove('hidden');
  wikiResizeHandle?.classList.remove('hidden');
  // Switch sidebar to wiki tab
  if (sidebar) sidebar.switchToWiki();
  // Update URL for bookmarking — push history so back button works
  const params = new URLSearchParams(location.search);
  if (params.get('wiki') !== page) {
    params.set('wiki', page);
    const url = '?' + params.toString();
    if (replace) {
      history.replaceState(null, '', url);
    } else {
      history.pushState(null, '', url);
    }
  }
}

/** Hide the wiki view. */
function hideWikiView() {
  wikiMainEl?.classList.add('hidden');
  wikiResizeHandle?.classList.add('hidden');
  // Clear wiki param from URL
  const params = new URLSearchParams(location.search);
  if (params.has('wiki')) {
    params.delete('wiki');
    const qs = params.toString();
    history.replaceState(null, '', qs ? '?' + qs : location.pathname);
  }
}

/** Show the config panel in the wiki/side panel area. */
function showConfigPanel() {
  wikiPageEl?.classList.add('hidden');
  configPanelEl?.classList.remove('hidden');
  wikiMainEl?.classList.remove('hidden');
  wikiResizeHandle?.classList.remove('hidden');
}

/** Hide the config panel and the side panel area. */
function hideConfigPanel() {
  configPanelEl?.classList.add('hidden');
  hideWikiView();
}

// Intercept clicks on .wiki-link elements anywhere in the document
document.addEventListener('click', (e) => {
  const link = /** @type {HTMLElement} */ (e.target).closest('a.wiki-link');
  if (!link) return;
  // In #wiki-main, let wiki-page handle navigation
  if (link.closest('#wiki-main')) return;
  e.preventDefault();
  const page = link.getAttribute('data-wiki-page');
  if (page) showWikiPage(page);
});

// Open wiki page from sidebar wiki tab
document.addEventListener('wiki-open', (e) => {
  const detail = /** @type {CustomEvent} */ (e).detail;
  const page = detail?.page;
  if (page) {
    showWikiPage(page);
    if (detail.editing && wikiPageEl) {
      // Start editing after the page loads
      wikiPageEl.startEditing();
    }
  }
});

// Navigate within wiki-main (wiki-page dispatches wiki-navigate)
wikiMainEl?.addEventListener('wiki-navigate', (e) => {
  const page = /** @type {CustomEvent} */ (e).detail?.page;
  if (page) showWikiPage(page);
});

// Handle browser back/forward for wiki navigation
window.addEventListener('popstate', () => {
  const wiki = new URLSearchParams(location.search).get('wiki');
  if (wiki) {
    showWikiPage(wiki, { replace: true });
  } else {
    hideWikiView();
  }
});

// Switch back to chat when sidebar switches to Chats tab
document.addEventListener('sidebar-tab-change', (e) => {
  const tab = /** @type {CustomEvent} */ (e).detail?.tab;
  if (tab === 'conversations') hideWikiView();
});

// Open config panel from sidebar gear button
document.addEventListener('config-open', () => showConfigPanel());

// Close config panel
configPanelEl?.addEventListener('close', () => hideConfigPanel());

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

/** Read ?wiki= on startup and open that wiki page */
function getWikiFromUrl() {
  return new URLSearchParams(location.search).get('wiki') || null;
}

// -- Init ---------------------------------------------------------------------

async function init() {
  const username = await auth.checkSession();
  if (username) {
    showChat();
    // Once WebSocket is connected and conversation list is loaded,
    // select the bookmarked conversation if any
    const savedWiki = getWikiFromUrl();
    if (savedWiki) showWikiPage(savedWiki, { replace: true });
    const savedConvId = getConvFromUrl();
    if (savedConvId) {
      const onOpen = () => {
        // Wait for conv_list to arrive, then select
        const onFirstChange = () => {
          store.removeEventListener('change', onFirstChange);
          // Try web conversations first, then select directly (works for system convs too)
          store.selectConversation(savedConvId);
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

// Mobile sidebar open/close
const hamburgerBtn = document.getElementById('hamburger-btn');
const sidebarBackdrop = document.getElementById('sidebar-backdrop');

hamburgerBtn?.addEventListener('click', () => sidebar?.openMobile());
sidebarBackdrop?.addEventListener('click', () => {
  sidebar?.closeMobile();
  sidebarBackdrop.classList.remove('visible');
});

// Keep backdrop in sync with sidebar mobile state
if (sidebar) {
  const observer = new MutationObserver(() => {
    const isOpen = sidebar.hasAttribute('mobile-open');
    sidebarBackdrop?.classList.toggle('visible', isOpen);
  });
  observer.observe(sidebar, { attributes: true, attributeFilter: ['mobile-open'] });
}

// Wiki drag resize
const WIKI_MIN_WIDTH = 250;
const WIKI_MAX_WIDTH_PCT = 0.7; // max 70% of layout

const savedWikiWidth = localStorage.getItem('wiki-width');
if (savedWikiWidth) document.documentElement.style.setProperty('--wiki-width', savedWikiWidth + 'px');

if (wikiResizeHandle) {
  let wikiDragging = false;
  wikiResizeHandle.addEventListener('mousedown', (e) => {
    wikiDragging = true;
    wikiResizeHandle.classList.add('dragging');
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    e.preventDefault();
  });
  document.addEventListener('mousemove', (e) => {
    if (!wikiDragging) return;
    if (!wikiMainEl || !chatLayout) return;
    const layoutRect = chatLayout.getBoundingClientRect();
    const sidebarWidth = parseInt(getComputedStyle(document.documentElement).getPropertyValue('--sidebar-width')) || 280;
    const wikiLeft = layoutRect.left + sidebarWidth + 4; // 4px for sidebar handle
    const maxWidth = (layoutRect.width - sidebarWidth) * WIKI_MAX_WIDTH_PCT;
    const newWidth = Math.min(maxWidth, Math.max(WIKI_MIN_WIDTH, e.clientX - wikiLeft));
    document.documentElement.style.setProperty('--wiki-width', newWidth + 'px');
  });
  document.addEventListener('mouseup', () => {
    if (!wikiDragging) return;
    wikiDragging = false;
    wikiResizeHandle.classList.remove('dragging');
    document.body.style.cursor = '';
    document.body.style.userSelect = '';
    const w = getComputedStyle(document.documentElement).getPropertyValue('--wiki-width').trim();
    localStorage.setItem('wiki-width', String(parseInt(w)));
  });
}

// Sidebar drag resize
const sidebarResizeHandle = document.getElementById('sidebar-resize-handle');
if (sidebarResizeHandle) {
  setupResizeHandle({
    handle: sidebarResizeHandle,
    container: chatLayout,
    minWidth: 160,
    maxWidth: 480,
    storageKey: 'sidebar-width',
    cssVar: '--sidebar-width',
  });
}
