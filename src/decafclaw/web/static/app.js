/**
 * DecafClaw Web Gateway — application entry point.
 *
 * Instantiates services, wires up components, manages auth state.
 */

import { AuthClient } from './lib/auth-client.js';
import { WebSocketClient } from './lib/websocket-client.js';
import { ConversationStore } from './lib/conversation-store.js';
import { getConvFromUrl } from './lib/conversation-url.js';
import { MESSAGE_TYPES, KNOWN_MESSAGE_TYPES } from './lib/message-types.js';
import { SIDE_EFFECT_COMMANDS } from './lib/constants.js';
import { load as loadBookmarks } from './lib/bookmarks-store.js';
import { handleConversationLinkClick } from './lib/conversation-links.js';
import { setupResizeHandle } from './lib/utils.js';
import { isMobileKeyboardDevice, shouldFocusChatInput } from './lib/chat-focus.js';
import { applyPageTitle } from './lib/page-title.js';
import { updateFaviconBadge } from './lib/favicon-badge.js';
import { showToast } from './lib/toast.js';
import {
  setActiveConv,
  applyEvent,
  dismiss as canvasDismiss,
  currentSnapshot as canvasSnapshot,
  resummon,
} from './lib/canvas-state.js';
import {
  setActiveConv as stickySetActiveConv,
  applyEvent as stickyApplyEvent,
} from './lib/sticky-state.js';

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
import './components/schedule-page.js';
import './components/copy-conversation-menu.js';
import './components/chat-controls.js';
import './components/side-question-drawer.js';

// -- Services -----------------------------------------------------------------

const auth = new AuthClient();

/** @returns {string} */
function getWsUrl() {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${proto}//${location.host}/ws/chat`;
}

const ws = new WebSocketClient(getWsUrl());
const store = new ConversationStore(ws);

// Browser tab title: selected session + its activity state, so several open
// tabs are distinguishable and a pending approval or running turn is visible
// from another tab (#1503). `change` fires for selection, listing refreshes,
// renames, and turn-status transitions — every input the title reads.
function syncPageTitle() {
  applyPageTitle(
    store.currentConversationTitle,
    store.getConversationStatus(store.currentConvId || ''),
  );
}
store.addEventListener('change', syncPageTitle);
syncPageTitle();

// Tab favicon: mirrors the selected conversation's activity, like the title,
// so several open tabs each show their own turn's state instead of one tab's
// background work lighting up every other tab (#1288, revised after multi-tab
// use). The helper no-ops on unchanged status and restores the default icons
// when the selected conversation is idle.
function syncFavicon() {
  updateFaviconBadge(store.getConversationStatus(store.currentConvId || ''));
}
store.addEventListener('change', syncFavicon);
syncFavicon();

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
const chatControls = /** @type {any} */ (document.querySelector('chat-controls'));
if (chatControls) chatControls.store = store;
const modelCatalog = /** @type {any} */ (document.querySelector('model-catalog'));
if (modelCatalog) modelCatalog.store = store;
if (chatInput) chatInput.store = store;
// Update chat-input disabled/busy state from store, auto-focus when ready
let wasBusy = false;
let lastConvId = null;
store.addEventListener('change', () => {
  const cur = store.currentConvId || null;
  const convChanged = cur !== lastConvId;
  if (chatInput) {
    chatInput.busy = store.isBusy;
    chatInput.disabled = store.isReadOnly;
    chatInput.convId = store.currentConvId || '';
    chatInput.placeholder = store.isReadOnly ? 'Read-only conversation' : 'Type a message...';
    chatInput.commands = store.commands;
    // `change` fires on every WebSocket message, so the focus decision has to
    // name the transitions it cares about — see lib/chat-focus.js. On mobile
    // the composer never takes focus on its own: the soft keyboard would
    // cover the transcript, so the user taps the field first.
    const focusOpts = {
      readOnly: store.isReadOnly,
      convChanged: convChanged && !!cur,
      turnFinished: wasBusy && !store.isBusy,
      chatInputEl: chatInput,
      isMobile: isMobileKeyboardDevice(),
    };
    if (shouldFocusChatInput(focusOpts)) {
      // Re-check after layout: an in-flight click may land focus elsewhere
      // between the store event and the frame.
      requestAnimationFrame(() => {
        if (shouldFocusChatInput(focusOpts)) chatInput.focus();
      });
    }
    wasBusy = store.isBusy;
  }
  if (convChanged) {
    lastConvId = cur;
    setActiveConv(cur);
    stickySetActiveConv(cur);
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
// Handle model changes from chat-controls
document.addEventListener('set-model', (e) => {
  const { model } = /** @type {CustomEvent} */ (e).detail;
  if (model && store) store.setModel(model);
});

// Conversation actions menu (issue #1192): canvas resummon / terminal picked
// from the composer's conversation-actions menu. Context and side-question
// have their own document-level listeners (context: conversation-sidebar,
// side-question: app.js setupSideQuestion).
document.addEventListener('conversation-action', (e) => {
  const { action } = /** @type {CustomEvent} */ (e).detail;
  if (action === 'canvas') {
    resummon();
  } else if (action === 'terminal') {
    store.sendMessage?.(SIDE_EFFECT_COMMANDS.TERMINAL);
  }
});

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
const schedulePageEl = /** @type {any} */ (document.querySelector('#wiki-main schedule-page'));

/** Show a wiki page alongside chat. */
function showWikiPage(page, { replace = false } = {}) {
  if (wikiPageEl) wikiPageEl.page = page;
  wikiPageEl?.classList.remove('hidden');
  // Mutual exclusion: close any open file-page / config-panel / schedule-page.
  filePageEl?.classList.add('hidden');
  if (filePageEl) filePageEl.path = '';
  configPanelEl?.classList.add('hidden');
  schedulePageEl?.classList.add('hidden');
  if (schedulePageEl) schedulePageEl.name = '';
  if (sidebar) sidebar.clearOpenSchedule();
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
  // Dropping ?file= and ?schedule= in favor of ?vault= when switching to a wiki page
  params.delete('file');
  params.delete('schedule');
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
  // Mutual exclusion: close any open wiki-page / config-panel / schedule-page.
  wikiPageEl?.classList.add('hidden');
  if (wikiPageEl) wikiPageEl.page = '';
  configPanelEl?.classList.add('hidden');
  schedulePageEl?.classList.add('hidden');
  if (schedulePageEl) schedulePageEl.name = '';
  if (sidebar) sidebar.clearOpenSchedule();
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
  params.delete('schedule');
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
  schedulePageEl?.classList.add('hidden');
  if (schedulePageEl) schedulePageEl.name = '';
  if (sidebar) sidebar.clearOpenFile();
  if (sidebar) sidebar.clearOpenSchedule();
  configPanelEl?.classList.remove('hidden');
  wikiMainEl?.classList.remove('hidden');
  wikiResizeHandle?.classList.remove('hidden');
}

/** Show a schedule in the wiki/side panel area. */
function showSchedulePage(name, { replace = false } = {}) {
  if (!schedulePageEl) return;
  if (schedulePageEl.name === name && !schedulePageEl.classList.contains('hidden')) {
    return; // already showing this schedule
  }
  schedulePageEl.name = name;
  schedulePageEl.classList.remove('hidden');
  // Mutual exclusion: close any open wiki-page / file-page / config-panel.
  wikiPageEl?.classList.add('hidden');
  if (wikiPageEl) wikiPageEl.page = '';
  filePageEl?.classList.add('hidden');
  if (filePageEl) filePageEl.path = '';
  configPanelEl?.classList.add('hidden');
  if (sidebar) sidebar.clearOpenPage();
  if (sidebar) sidebar.clearOpenFile();
  wikiMainEl?.classList.remove('hidden');
  wikiResizeHandle?.classList.remove('hidden');
  if (sidebar) sidebar.setOpenSchedule(name);
  // Update URL for bookmarking
  const params = new URLSearchParams(location.search);
  params.delete('vault');
  params.delete('file');
  if (params.get('schedule') !== name) {
    params.set('schedule', name);
    const url = '?' + params.toString();
    if (replace) {
      history.replaceState(null, '', url);
    } else {
      history.pushState(null, '', url);
    }
  }
}

/** Hide the schedule page. */
function hideSchedulePage() {
  schedulePageEl?.classList.add('hidden');
  if (schedulePageEl) schedulePageEl.name = '';
  if (sidebar) sidebar.clearOpenSchedule();
  const params = new URLSearchParams(location.search);
  if (params.has('schedule')) {
    params.delete('schedule');
    const qs = params.toString();
    history.replaceState(null, '', qs ? '?' + qs : location.pathname);
  }
  // If nothing else is visible in the side panel, hide it entirely.
  const wikiVisible = wikiPageEl && !wikiPageEl.classList.contains('hidden');
  const fileVisible = filePageEl && !filePageEl.classList.contains('hidden');
  const configVisible = configPanelEl && !configPanelEl.classList.contains('hidden');
  if (!wikiVisible && !fileVisible && !configVisible) {
    wikiMainEl?.classList.add('hidden');
    wikiResizeHandle?.classList.add('hidden');
  }
}

/** Hide the config panel and the side panel area. */
function hideConfigPanel() {
  configPanelEl?.classList.add('hidden');
  hideWikiView();
}

// Intercept clicks on conversation links to vault pages and workspace files so
// they open in the current session's side panel. Modified clicks fall through
// to the link href (new tab).
document.addEventListener('click', (e) => {
  handleConversationLinkClick(e, {
    showWikiPage,
    showFilePage: (path) => showFilePage({ path }),
  });
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

// Handle browser back/forward for wiki/file/schedule navigation
window.addEventListener('popstate', () => {
  const params = new URLSearchParams(location.search);
  const wiki = params.get('vault');
  const file = params.get('file');
  const schedule = params.get('schedule');
  if (wiki) {
    showWikiPage(wiki, { replace: true });
  } else if (file) {
    showFilePage({ path: file }, { replace: true });
  } else if (schedule) {
    showSchedulePage(schedule, { replace: true });
  } else {
    hideWikiView();
    hideFileView();
    hideSchedulePage();
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
    hideSchedulePage();
  }
  if ((tab === 'wiki' || tab === 'files' || tab === 'tags')
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

// Open schedule page from sidebar schedule row click
document.addEventListener('schedule-open', (e) => {
  const name = /** @type {CustomEvent} */ (e).detail?.name;
  if (name) showSchedulePage(name);
});

// Close schedule page via back button
schedulePageEl?.addEventListener('close', () => hideSchedulePage());

// Listen for WebSocket error messages directly
ws.addEventListener('message', (e) => {
  const msg = /** @type {CustomEvent} */ (e).detail;
  if (msg && typeof msg.type === 'string' && !KNOWN_MESSAGE_TYPES.has(msg.type)) {
    console.warn('[ws] unknown message type from server:', msg.type, msg);
  }
  if (msg?.type === MESSAGE_TYPES.ERROR && msg?.message) {
    showToast(msg.message);
  }
  if (msg?.type === MESSAGE_TYPES.SWITCH_CONVERSATION && msg?.title) {
    showToast(`Switched to ${msg.title}`);
  }
  // Fan out turn-complete as a window event so sidebars can silently refresh
  // (e.g. files-sidebar auto-refetches so agent-written files appear).
  if (msg?.type === MESSAGE_TYPES.TURN_COMPLETE) {
    window.dispatchEvent(new CustomEvent('turn-complete', {
      detail: { conv_id: msg.conv_id },
    }));
  }
  // Notification push events — the bell component listens at the window
  // level so we don't have to hand it the raw ws instance. Payloads mirror
  // the event bus shape (see docs/notifications.md).
  if (msg?.type === MESSAGE_TYPES.NOTIFICATION_CREATED) {
    window.dispatchEvent(new CustomEvent('notification-created', { detail: msg }));
  }
  if (msg?.type === MESSAGE_TYPES.NOTIFICATION_READ) {
    window.dispatchEvent(new CustomEvent('notification-read', { detail: msg }));
  }
  if (msg?.type === MESSAGE_TYPES.VAULT_CHANGED) {
    window.dispatchEvent(new CustomEvent('vault-changed', { detail: msg }));
  }
  if (msg?.type === MESSAGE_TYPES.CANVAS_UPDATE) {
    applyEvent(msg);
  }
  if (msg?.type === MESSAGE_TYPES.STICKY_SET || msg?.type === MESSAGE_TYPES.STICKY_CLEAR) {
    stickyApplyEvent(msg);
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
  // No conversation is displayed while signed out, so the tab must not keep
  // naming the last one (#1503). The next login's listing refresh restores it.
  applyPageTitle('');
}

auth.addEventListener('login', () => {
  showChat();
  // The signed-out mount-time bookmark GETs 401'd and left the shared cache
  // empty; refresh now that the session is (re)established so pinned
  // bookmarks and toolbar stars appear without a manual reload.
  void loadBookmarks();
  selectSavedConversation(getConvFromUrl());
});
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

/** Read ?vault= on startup and open that vault page */
function getWikiFromUrl() {
  return new URLSearchParams(location.search).get('vault') || null;
}

/** Read ?file= on startup and open that workspace file */
function getFileFromUrl() {
  return new URLSearchParams(location.search).get('file') || null;
}

/** Read ?schedule= on startup and open that schedule page */
function getScheduleFromUrl() {
  return new URLSearchParams(location.search).get('schedule') || null;
}

function selectSavedConversation(savedConvId) {
  if (!savedConvId) return;
  const selectWhenReady = () => {
    if (store.conversations.length > 0) {
      store.selectConversation(savedConvId);
      return;
    }
    const onFirstChange = () => {
      store.removeEventListener('change', onFirstChange);
      store.selectConversation(savedConvId);
    };
    store.addEventListener('change', onFirstChange);
  };

  if (ws.connected) {
    selectWhenReady();
  } else {
    const onOpen = () => {
      selectWhenReady();
      ws.removeEventListener('open', onOpen);
    };
    ws.addEventListener('open', onOpen);
  }
}

// Handle hash changes (e.g. clicking #conv= links while app is open)
window.addEventListener('hashchange', () => {
  const convId = getConvFromUrl();
  if (convId && convId !== store.currentConvId) {
    store.selectConversation(convId);
  }
});

// -- Init ---------------------------------------------------------------------

async function init() {
  const username = await auth.checkSession();
  if (username) {
    showChat();
    // Once WebSocket is connected and conversation list is loaded,
    // select the bookmarked conversation if any
    const savedWiki = getWikiFromUrl();
    const savedFile = getFileFromUrl();
    const savedSchedule = getScheduleFromUrl();
    if (savedWiki) showWikiPage(savedWiki, { replace: true });
    else if (savedFile) showFilePage({ path: savedFile }, { replace: true });
    else if (savedSchedule) showSchedulePage(savedSchedule, { replace: true });
    selectSavedConversation(getConvFromUrl());
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
const getSidebarWidth = () =>
  parseInt(getComputedStyle(document.documentElement).getPropertyValue('--sidebar-width')) || 280;

if (wikiResizeHandle) {
  setupResizeHandle({
    handle: wikiResizeHandle,
    container: chatLayout,
    minWidth: WIKI_MIN_WIDTH,
    maxWidth: (rect) => (rect.width - getSidebarWidth()) * WIKI_MAX_WIDTH_PCT,
    offset: () => getSidebarWidth() + 4, // 4px for the sidebar handle
    storageKey: 'wiki-width',
    cssVar: '--wiki-width',
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
if (canvasResizeHandle) {
  setupResizeHandle({
    handle: canvasResizeHandle,
    container: chatLayout,
    edge: 'right',
    minWidth: 280,
    maxWidth: (rect) => rect.width * 0.7,
    storageKey: 'canvas-width',
    cssVar: '--canvas-width',
  });
}

function setupSideQuestion() {
  const drawer = /** @type {any} */ (document.querySelector('side-question-drawer'));
  if (drawer) {
    drawer.convId = store.currentConvId || '';
    store.addEventListener('change', () => {
      drawer.convId = store.currentConvId || '';
    });
  }

  document.addEventListener('open-side-question', () => {
    drawer?.openDrawer();
  });

  document.addEventListener('send-to-main', (e) => {
    const detail = /** @type {CustomEvent} */ (e).detail;
    if (detail?.text) {
      const input = /** @type {any} */ (document.querySelector('chat-input'));
      input?.setText?.(detail.text);
    }
  });
}

setupSideQuestion();
