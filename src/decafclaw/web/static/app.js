/**
 * DecafClaw Web Gateway — application entry point.
 *
 * Instantiates services, wires up components, manages auth state.
 */

import { AuthClient } from './lib/auth-client.js';
import { WebSocketClient } from './lib/websocket-client.js';
import { ConversationStore } from './lib/conversation-store.js';
import { setupResizeHandle } from './lib/utils.js';
import {
  setActiveConv,
  applyEvent,
  subscribe as subscribeCanvas,
  resummon,
  dismiss as canvasDismiss,
  currentSnapshot as canvasSnapshot,
} from './lib/canvas-state.js';

// Import components (registers custom elements)
import './components/login-view.js';
import './components/conversation-sidebar.js';
import './components/chat-view.js';
import './components/chat-message.js';
import './components/chat-input.js';
import './components/theme-toggle.js';
import './components/wiki-page.js';
import './components/file-page.js';
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
let lastConvId = null;
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
  const cur = store.currentConvId || null;
  if (cur !== lastConvId) {
    lastConvId = cur;
    setActiveConv(cur);
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

// -- Wiki / file / config side panel -----------------------------------------
//
// The #wiki-main element is a shared side-panel area that hosts at most ONE
// of: <wiki-page>, <file-page>, or <config-panel>. Opening one hides the
// others (mutual exclusion). The side panel itself is only visible when any
// of them is shown.

const wikiMainEl = document.getElementById('wiki-main');
const wikiResizeHandle = document.getElementById('wiki-resize-handle');
const wikiPageEl = /** @type {any} */ (document.querySelector('#wiki-main wiki-page'));
const filePageEl = /** @type {any} */ (document.querySelector('#wiki-main file-page'));
const configPanelEl = /** @type {any} */ (document.querySelector('#wiki-main config-panel'));

/** Show a wiki page alongside chat. */
function showWikiPage(page, { replace = false } = {}) {
  if (wikiPageEl) wikiPageEl.page = page;
  wikiPageEl?.classList.remove('hidden');
  // Mutual exclusion: close any open file-page / config-panel.
  filePageEl?.classList.add('hidden');
  if (filePageEl) filePageEl.path = '';
  configPanelEl?.classList.add('hidden');
  if (sidebar) sidebar.clearOpenFile();
  wikiMainEl?.classList.remove('hidden');
  wikiResizeHandle?.classList.remove('hidden');
  // Switch sidebar to wiki tab and navigate to page's folder
  if (sidebar) {
    sidebar.switchToWiki();
    sidebar.navigateToPageFolder(page);
  }
  // Update URL for bookmarking — push history so back button works
  const params = new URLSearchParams(location.search);
  // Dropping ?file= in favor of ?vault= when switching to a wiki page
  params.delete('file');
  if (params.get('vault') !== page) {
    params.set('vault', page);
    const url = '?' + params.toString();
    if (replace) {
      history.replaceState(null, '', url);
    } else {
      history.pushState(null, '', url);
    }
  } else {
    const url = '?' + params.toString();
    history.replaceState(null, '', url);
  }
}

/** Get the currently open wiki page name, or null if none.
 * @type {() => string | null} */
// @ts-ignore — global function accessed from conversation-store.js
window.getOpenWikiPage = function() {
  const params = new URLSearchParams(location.search);
  return params.get('vault') || null;
};

/** Hide the wiki view. */
function hideWikiView() {
  wikiMainEl?.classList.add('hidden');
  wikiResizeHandle?.classList.add('hidden');
  if (sidebar) sidebar.clearOpenPage();
  // Clear wiki param from URL
  const params = new URLSearchParams(location.search);
  if (params.has('vault')) {
    params.delete('vault');
    const qs = params.toString();
    history.replaceState(null, '', qs ? '?' + qs : location.pathname);
  }
}

// Kind inference mirrors backend detect_kind's extension-based fast path
// (src/decafclaw/web/workspace_paths.py). Keep both lists in sync.
const TEXT_EXTS = new Set([
  '.md', '.py', '.json', '.yaml', '.yml', '.sh',
  '.js', '.ts', '.css', '.html', '.txt',
  '.toml', '.ini', '.cfg', '.conf', '.log', '.csv', '.sql',
]);
const IMAGE_EXTS = new Set([
  '.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.bmp', '.ico',
]);

/** Infer kind from extension (mirrors backend detect_kind fast path). */
function inferFileKindFromPath(path) {
  const lower = String(path || '').toLowerCase();
  const dotIdx = lower.lastIndexOf('.');
  if (dotIdx < 0) return 'text'; // no extension → assume text
  const ext = lower.slice(dotIdx);
  if (IMAGE_EXTS.has(ext)) return 'image';
  if (TEXT_EXTS.has(ext)) return 'text';
  return 'binary';
}

/**
 * Show a workspace file in the side panel.
 * @param {{path: string, kind?: string, readonly?: boolean}} detail
 * @param {{replace?: boolean}} [opts]
 */
function showFilePage(detail, { replace = false } = {}) {
  if (!detail?.path || !filePageEl) return;
  // For rename-triggered file-open / popstate / init-from-?file=, kind and
  // readonly may be omitted. Infer kind from the path (extension) so a
  // rename across kinds (e.g. foo.txt → foo.png) picks the correct renderer.
  // Readonly still falls back to the current value; #fetchFile corrects it
  // from the server response as soon as the text fetch completes.
  const sameFile = filePageEl.path === detail.path
    && !filePageEl.classList.contains('hidden');
  filePageEl.path = detail.path;
  filePageEl.kind = detail.kind != null ? detail.kind : inferFileKindFromPath(detail.path);
  filePageEl.readonly = detail.readonly != null ? !!detail.readonly : !!filePageEl.readonly;
  // Re-clicking the currently-open file is a manual refresh: willUpdate
  // only fires #fetchFile on path *change*, so force it here.
  if (sameFile && typeof filePageEl.reload === 'function') {
    void filePageEl.reload();
  }
  filePageEl.classList.remove('hidden');
  // Mutual exclusion: close any open wiki-page / config-panel.
  wikiPageEl?.classList.add('hidden');
  if (wikiPageEl) wikiPageEl.page = '';
  configPanelEl?.classList.add('hidden');
  if (sidebar) sidebar.clearOpenPage();
  wikiMainEl?.classList.remove('hidden');
  wikiResizeHandle?.classList.remove('hidden');
  // Sync sidebar tab + file highlight
  if (sidebar) {
    sidebar.navigateToFileFolder(detail.path);
  }
  // Update URL for bookmarking
  const params = new URLSearchParams(location.search);
  params.delete('vault');
  if (params.get('file') !== detail.path) {
    params.set('file', detail.path);
    const url = '?' + params.toString();
    if (replace) {
      history.replaceState(null, '', url);
    } else {
      history.pushState(null, '', url);
    }
  } else {
    const url = '?' + params.toString();
    history.replaceState(null, '', url);
  }
}

/** Hide the file view. */
function hideFileView() {
  filePageEl?.classList.add('hidden');
  if (filePageEl) filePageEl.path = '';
  // If nothing else is visible in the side panel, hide it entirely.
  const wikiVisible = wikiPageEl && !wikiPageEl.classList.contains('hidden');
  const configVisible = configPanelEl && !configPanelEl.classList.contains('hidden');
  if (!wikiVisible && !configVisible) {
    wikiMainEl?.classList.add('hidden');
    wikiResizeHandle?.classList.add('hidden');
  }
  if (sidebar) sidebar.clearOpenFile();
  const params = new URLSearchParams(location.search);
  if (params.has('file')) {
    params.delete('file');
    const qs = params.toString();
    history.replaceState(null, '', qs ? '?' + qs : location.pathname);
  }
}

/** Show the config panel in the wiki/side panel area. */
function showConfigPanel() {
  wikiPageEl?.classList.add('hidden');
  filePageEl?.classList.add('hidden');
  if (filePageEl) filePageEl.path = '';
  if (sidebar) sidebar.clearOpenFile();
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

// Navigate sidebar to folder from editor breadcrumbs
wikiMainEl?.addEventListener('wiki-navigate-folder', (e) => {
  const folder = /** @type {CustomEvent} */ (e).detail?.folder ?? '';
  if (sidebar) {
    sidebar.switchToWiki();
    sidebar.navigateToFolder(folder);
  }
});

// Close wiki pane via X button
wikiMainEl?.addEventListener('wiki-close', () => hideWikiView());

// Open a workspace file from the files-sidebar (or file-page rename)
document.addEventListener('file-open', (e) => {
  const detail = /** @type {CustomEvent} */ (e).detail;
  if (detail?.path) showFilePage(detail);
});

// Close file pane via file-page X button
wikiMainEl?.addEventListener('file-close', () => hideFileView());

// Navigate files-sidebar to folder from file-page breadcrumbs
wikiMainEl?.addEventListener('file-navigate-folder', (e) => {
  const folder = /** @type {CustomEvent} */ (e).detail?.folder ?? '';
  if (sidebar) sidebar.navigateToFilesFolder(folder);
});

// Handle browser back/forward for wiki/file navigation
window.addEventListener('popstate', () => {
  const params = new URLSearchParams(location.search);
  const wiki = params.get('vault');
  const file = params.get('file');
  if (wiki) {
    showWikiPage(wiki, { replace: true });
  } else if (file) {
    showFilePage({ path: file }, { replace: true });
  } else {
    hideWikiView();
    hideFileView();
  }
});

// Switch back to chat when sidebar switches to Chats tab.
// On mobile, opening wiki/files auto-closes the canvas overlay (mutual
// exclusion with the canvas full-screen overlay; reverse direction is
// handled in canvas-panel._reflectVisibility).
document.addEventListener('sidebar-tab-change', (e) => {
  const tab = /** @type {CustomEvent} */ (e).detail?.tab;
  if (tab === 'conversations') {
    hideWikiView();
    hideFileView();
  }
  if ((tab === 'wiki' || tab === 'files')
      && window.matchMedia('(max-width: 639px)').matches) {
    if (canvasSnapshot().visible) {
      canvasDismiss();
    }
  }
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
  // Fan out turn-complete as a window event so sidebars can silently refresh
  // (e.g. files-sidebar auto-refetches so agent-written files appear).
  if (msg?.type === 'turn_complete') {
    window.dispatchEvent(new CustomEvent('turn-complete', {
      detail: { conv_id: msg.conv_id },
    }));
  }
  // Notification push events — the bell component listens at the window
  // level so we don't have to hand it the raw ws instance. Payloads mirror
  // the event bus shape (see docs/notifications.md).
  if (msg?.type === 'notification_created') {
    window.dispatchEvent(new CustomEvent('notification-created', { detail: msg }));
  }
  if (msg?.type === 'notification_read') {
    window.dispatchEvent(new CustomEvent('notification-read', { detail: msg }));
  }
  if (msg?.type === 'canvas_update') {
    applyEvent(msg);
  }
});

// -- Connection status --------------------------------------------------------

const connectionBanner = document.getElementById('connection-banner');
ws.addEventListener('close', () => {
  if (connectionBanner) connectionBanner.classList.remove('hidden');
});
ws.addEventListener('open', () => {
  if (connectionBanner) connectionBanner.classList.add('hidden');
  // Signal for components that need to re-seed on (re)connect, e.g. the
  // notification bell fetching the unread count after a WS drop+reconnect.
  window.dispatchEvent(new CustomEvent('ws-connected'));
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

/** Read ?vault= on startup and open that vault page */
function getWikiFromUrl() {
  return new URLSearchParams(location.search).get('vault') || null;
}

/** Read ?file= on startup and open that workspace file */
function getFileFromUrl() {
  return new URLSearchParams(location.search).get('file') || null;
}

// -- Init ---------------------------------------------------------------------

async function init() {
  const username = await auth.checkSession();
  if (username) {
    showChat();
    // Once WebSocket is connected and conversation list is loaded,
    // select the bookmarked conversation if any
    const savedWiki = getWikiFromUrl();
    const savedFile = getFileFromUrl();
    if (savedWiki) showWikiPage(savedWiki, { replace: true });
    else if (savedFile) showFilePage({ path: savedFile }, { replace: true });
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

// Visual-viewport tracker — keeps a `--vh` CSS variable in sync with the
// visible viewport height. Without this, on iOS Safari and Firefox Android
// the soft keyboard pushes `100vh`-sized layout below the keyboard so the
// chat input goes off-screen. Falls back to window.innerHeight on browsers
// without the visualViewport API. See docs/web-ui-mobile.md.
//
// Throttled via requestAnimationFrame because visualViewport `scroll`
// fires very frequently during address-bar / keyboard transitions.
// Skip writing the CSS var when the height hasn't actually changed.
function setupVisualViewportTracking() {
  const root = document.documentElement;
  let lastHeight = null;
  let frameId = null;

  const applyHeight = () => {
    frameId = null;
    const h = window.visualViewport?.height ?? window.innerHeight;
    if (h !== lastHeight) {
      lastHeight = h;
      root.style.setProperty('--vh', `${h}px`);
    }
  };

  const scheduleUpdate = () => {
    if (frameId !== null) return;
    frameId = window.requestAnimationFrame(applyHeight);
  };

  if (window.visualViewport) {
    window.visualViewport.addEventListener('resize', scheduleUpdate);
    window.visualViewport.addEventListener('scroll', scheduleUpdate);
  } else {
    window.addEventListener('resize', scheduleUpdate);
  }
  applyHeight();
}
setupVisualViewportTracking();

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

// Canvas drag resize (right-side panel: handle is to the left of the panel,
// so dragging left grows the canvas — compute from right edge of layout).
const canvasResizeHandle = document.getElementById('canvas-resize-handle');
const canvasMainEl = document.getElementById('canvas-main');

const savedCanvasWidth = localStorage.getItem('canvas-width');
if (savedCanvasWidth) document.documentElement.style.setProperty('--canvas-width', savedCanvasWidth + 'px');

if (canvasResizeHandle && canvasMainEl) {
  const CANVAS_MIN_WIDTH = 280;
  const CANVAS_MAX_WIDTH_PCT = 0.7;
  let canvasDragging = false;
  canvasResizeHandle.addEventListener('mousedown', (e) => {
    canvasDragging = true;
    canvasResizeHandle.classList.add('dragging');
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    e.preventDefault();
  });
  document.addEventListener('mousemove', (e) => {
    if (!canvasDragging) return;
    if (!chatLayout) return;
    const layoutRect = chatLayout.getBoundingClientRect();
    const maxWidth = layoutRect.width * CANVAS_MAX_WIDTH_PCT;
    const newWidth = Math.min(maxWidth, Math.max(CANVAS_MIN_WIDTH, layoutRect.right - e.clientX));
    document.documentElement.style.setProperty('--canvas-width', newWidth + 'px');
  });
  document.addEventListener('mouseup', () => {
    if (!canvasDragging) return;
    canvasDragging = false;
    canvasResizeHandle.classList.remove('dragging');
    document.body.style.cursor = '';
    document.body.style.userSelect = '';
    // Persist only on real pixel values. parseInt() of the CSS var can
    // yield NaN (var unset) or 45 (CSS default '45%'), neither of which
    // is a valid pixel width to round-trip on next load.
    const px = canvasMainEl.getBoundingClientRect().width;
    if (Number.isFinite(px) && px >= CANVAS_MIN_WIDTH) {
      localStorage.setItem('canvas-width', String(Math.round(px)));
    }
  });
}

function setupCanvasResummonPill() {
  // Desktop: pill floats absolutely in the upper-right of #chat-main
  // (no dedicated header strip — keeps the chat area uncluttered when
  // there's no canvas state). Mobile: lives inside #mobile-header.
  const desktopHost = document.getElementById('chat-main');
  const mobileHost = document.getElementById('mobile-header');
  if (!desktopHost) return;

  /**
   * @param {HTMLElement} host
   * @param {{tab: any, visible: boolean, unreadDot: boolean}} snapshot
   */
  const renderTo = (host, snapshot) => {
    host.querySelector('.canvas-resummon-pill')?.remove();
    if (!snapshot.tab) return;
    if (snapshot.visible) return;
    const btn = document.createElement('button');
    btn.className = 'canvas-resummon-pill';
    btn.type = 'button';
    btn.textContent = '📄 Canvas';
    if (snapshot.unreadDot) {
      btn.dataset.unread = 'true';
      // The dot is purely visual (CSS ::after); pair it with an
      // accessible name so screen readers get the same state signal.
      btn.setAttribute('aria-label', 'Canvas (unread update)');
    } else {
      btn.setAttribute('aria-label', 'Canvas');
    }
    btn.addEventListener('click', () => resummon());
    host.appendChild(btn);
  };

  const isMobile = () => window.matchMedia('(max-width: 639px)').matches;
  const renderForViewport = (snap) => {
    // Only mount the pill in the visible host. mobile-header uses
    // `display: contents` on desktop, which would still render its
    // children inline inside chat-main — producing a duplicate pill.
    if (isMobile()) {
      desktopHost.querySelector('.canvas-resummon-pill')?.remove();
      if (mobileHost) renderTo(mobileHost, snap);
    } else {
      mobileHost?.querySelector('.canvas-resummon-pill')?.remove();
      renderTo(desktopHost, snap);
    }
  };

  subscribeCanvas(renderForViewport);
  // Re-render on viewport-class changes so a desktop ↔ mobile resize
  // moves the pill to the right host.
  window.matchMedia('(max-width: 639px)').addEventListener('change', () => {
    const snap = currentSnapshotForResummon();
    renderForViewport(snap);
  });
}

function currentSnapshotForResummon() {
  // Avoid importing the canvas-state module twice; surface a tiny helper.
  return canvasSnapshot();
}

setupCanvasResummonPill();
