import { LitElement, html, nothing } from 'lit';
import { getConvFromUrl } from '../lib/conversation-url.js';
import './context-inspector.js';
import './notification-inbox.js';
import './model-picker.js';
import './vault-sidebar.js';
import './files-sidebar.js';
import './schedules-sidebar.js';
import './calendar-sidebar.js';
import './tags-sidebar.js';
import './conversation-list.js';

/** Per-conversation sidebar collapse state: `sidebar-collapsed:<conv_id>` */
const COLLAPSE_STORAGE_PREFIX = 'sidebar-collapsed:';
/** Pre-per-conversation global key, migrated to the open conversation. */
const LEGACY_COLLAPSE_STORAGE_KEY = 'sidebar-collapsed';

const SIDEBAR_SECTIONS = [
  { value: 'conversations', label: 'Chats' },
  { value: 'wiki', label: 'Vault' },
  { value: 'files', label: 'Files' },
  { value: 'schedules', label: 'Schedules' },
  { value: 'calendar', label: 'Calendar' },
  { value: 'tags', label: 'Tags' },
];

/**
 * The sidebar shell. Owns tab state (which section is shown), the per-
 * conversation collapse width, the model picker, the context usage meter and
 * inspector, and the config footer. The conversation hierarchy itself — folders,
 * session groups, subagent rows, status badges, search, and the create actions —
 * lives in {@link ConversationList}.
 */
export class ConversationSidebar extends LitElement {
  static properties = {
    store: { type: Object, attribute: false },
    authClient: { type: Object, attribute: false },
    _collapsed: { type: Boolean, state: true },
    _mobileOpen: { type: Boolean, state: true },
    _sidebarSection: { type: String, state: true },
    _contextInspectorOpen: { type: Boolean, state: true },
    _contextVersion: { type: Number, state: true },
    _openSchedule: { type: String, state: true },
    _hasControlsStrip: { type: Boolean, state: true },
  };

  createRenderRoot() { return this; }

  // Change detection for #onStoreChange only — the rendered values are read
  // straight from the store, never mirrored into reactive state (#1408).
  /** @type {string|null} */
  #lastConvId = null;
  /** @type {number} */
  #lastContextUsage = 0;

  constructor() {
    super();
    /** @type {import("../lib/conversation-store.js").ConversationStore | null} */
    this.store = null;
    this.authClient = null;
    this._collapsed = false;
    // Read the URL's conversation before the store delivers one, so a
    // reload of a collapsed conversation does not flash the expanded
    // sidebar while the WebSocket connects.
    const urlConvId = getConvFromUrl();
    if (urlConvId) this.#applyCollapsed(urlConvId);
    this._mobileOpen = false;
    this._sidebarSection = 'conversations';
    this._contextInspectorOpen = false;
    this._contextVersion = 0;
    this._openSchedule = '';
    this._hasControlsStrip = document.querySelector('chat-controls') !== null;
  }

  openMobile() {
    this._mobileOpen = true;
  }

  closeMobile() {
    this._mobileOpen = false;
  }

  connectedCallback() {
    super.connectedCallback();
    this._onToggleInspector = () => {
      this._contextInspectorOpen = !this._contextInspectorOpen;
    };
    document.addEventListener('toggle-context-inspector', this._onToggleInspector);
    // The store is the single source of truth: render() reads its getters
    // directly, so this handler only runs the per-change side effects and
    // asks Lit to re-render (#1408). The #last* fields detect transitions;
    // they are bookkeeping, not rendered state.
    this._onStoreChange = () => {
      const newId = this.store?.currentConvId;
      if (newId !== this.#lastConvId) {
        this._contextInspectorOpen = false;
        // Collapse is per conversation: each tab reflects the conversation
        // it is showing instead of one global width for the whole browser.
        this.#applyCollapsed(newId);
      }
      this.#lastConvId = newId;
      // Bump the inspector's version only when the usage actually changed,
      // so a turn's streaming frames do not refetch diagnostics.
      const newUsage = this.store?.contextUsage || 0;
      if (newUsage !== this.#lastContextUsage) {
        this._contextVersion++;
        this.#lastContextUsage = newUsage;
      }
      this.requestUpdate();
    };
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    document.removeEventListener('toggle-context-inspector', this._onToggleInspector);
    this.store?.removeEventListener('change', this._onStoreChange);
  }

  /** Selected conversation id, read straight from the store ('' when none). */
  get #activeId() {
    return this.store?.currentConvId || '';
  }

  get _sidebarTab() {
    return this._sidebarSection;
  }

  set _sidebarTab(section) {
    this._sidebarSection = section;
  }

  /** @param {Map} changedProps */
  updated(changedProps) {
    if (changedProps.has('store')) {
      const oldStore = changedProps.get('store');
      if (oldStore) oldStore.removeEventListener('change', this._onStoreChange);
      if (this.store) {
        this.store.addEventListener('change', this._onStoreChange);
        this._onStoreChange();
      }
    }
    // [collapsed] drives the desktop width-clamp CSS — suppress it when
    // mobile-open so the hamburger flow shows the full sidebar even if
    // the desktop session left _collapsed=true.
    this.toggleAttribute('collapsed', this._collapsed && !this._mobileOpen);
    this.toggleAttribute('mobile-open', this._mobileOpen);
    this.#syncSectionSelectValue();
  }

  #syncSectionSelectValue() {
    const select = this.querySelector('.sidebar-section-select');
    if (select instanceof HTMLSelectElement && select.value !== this._sidebarSection) {
      select.value = this._sidebarSection;
    }
  }

  /** @param {string} section */
  #switchSection(section, { notify = false } = {}) {
    this._sidebarSection = section;
    if (!notify) return;
    this.dispatchEvent(new CustomEvent('sidebar-section-change', {
      detail: { section },
      bubbles: true,
      composed: true,
    }));
  }

  /** @param {Event} e */
  #onSectionSelectChange(e) {
    const section = /** @type {HTMLSelectElement | null} */ (e.target)?.value;
    if (!SIDEBAR_SECTIONS.some((option) => option.value === section)) return;
    this.#switchSection(section, { notify: true });
  }

  /** Public: switch to wiki section (called from app.js when a wiki link is clicked). */
  switchToWiki() {
    this.#switchSection('wiki');
  }

  /** Public: switch to files section. */
  switchToFiles() {
    this.#switchSection('files');
  }

  /**
   * Public: navigate the sidebar to a specific folder.
   * @param {string} folderPath
   */
  navigateToFolder(folderPath) {
    const vs = /** @type {any} */ (this.querySelector('vault-sidebar'));
    vs?.navigateToFolder(folderPath);
  }

  /**
   * Navigate the sidebar to the folder containing a given page path.
   * Called when a page is opened (e.g. via wiki-link) to keep sidebar in sync.
   * @param {string} pagePath
   */
  navigateToPageFolder(pagePath) {
    this.#switchSection('wiki');
    const vs = /** @type {any} */ (this.querySelector('vault-sidebar'));
    vs?.navigateToPageFolder(pagePath);
    const ts = /** @type {any} */ (this.querySelector('tags-sidebar'));
    if (ts) ts.openPage = pagePath;
  }

  /** Clear the open page highlight (called when wiki pane is closed). */
  clearOpenPage() {
    const vs = /** @type {any} */ (this.querySelector('vault-sidebar'));
    vs?.clearOpenPage();
    const ts = /** @type {any} */ (this.querySelector('tags-sidebar'));
    if (ts) ts.openPage = null;
  }

  /**
   * Public: navigate the files-sidebar to a specific folder.
   * @param {string} folderPath
   */
  navigateToFilesFolder(folderPath) {
    this.#switchSection('files');
    const fs = /** @type {any} */ (this.querySelector('files-sidebar'));
    fs?.navigateToFolder(folderPath);
  }

  /**
   * Navigate the files-sidebar to the folder containing a given file path,
   * and mark that file as open (for row highlighting).
   * @param {string} filePath
   */
  navigateToFileFolder(filePath) {
    this.#switchSection('files');
    const fs = /** @type {any} */ (this.querySelector('files-sidebar'));
    fs?.navigateToFileFolder(filePath);
  }

  /** Clear the open file highlight (called when file pane is closed). */
  clearOpenFile() {
    const fs = /** @type {any} */ (this.querySelector('files-sidebar'));
    fs?.clearOpenFile();
  }

  /** Public: switch to schedules section and highlight the given schedule name. */
  setOpenSchedule(name) {
    this._openSchedule = name;
    this.switchToSchedules();
  }

  /** Public: clear the open schedule highlight (called when schedule pane is closed). */
  clearOpenSchedule() {
    this._openSchedule = '';
  }

  /** Public: switch to the schedules section. */
  switchToSchedules() {
    this.#switchSection('schedules');
  }

  /** @param {CustomEvent} _e */
  #handleWikiOpen(_e) {
    // wiki-open bubbles + composed from <vault-sidebar>, so app.js already receives it.
    // Parent only needs to close the mobile drawer (previously done inside #handleWikiSelect).
    this.closeMobile();
  }

  /** @param {CustomEvent} _e */
  #handleFileOpen(_e) {
    // file-open bubbles + composed from <files-sidebar>, so app.js already receives it.
    // Parent only needs to close the mobile drawer.
    this.closeMobile();
  }

  /** Load the stored collapse state for a conversation. */
  #applyCollapsed(convId) {
    this._collapsed = convId ? this.#readCollapsed(convId) : false;
  }

  /** @param {string} convId */
  #readCollapsed(convId) {
    const key = `${COLLAPSE_STORAGE_PREFIX}${convId}`;
    const stored = localStorage.getItem(key);
    if (stored !== null) return stored === 'true';
    const legacy = localStorage.getItem(LEGACY_COLLAPSE_STORAGE_KEY);
    if (legacy === null) return false;
    // One-time upgrade: the old global preference applies to the
    // conversation open when it was read.
    localStorage.setItem(key, legacy);
    localStorage.removeItem(LEGACY_COLLAPSE_STORAGE_KEY);
    return legacy === 'true';
  }

  #toggleCollapse() {
    this._collapsed = !this._collapsed;
    const convId = this.#activeId || getConvFromUrl();
    if (convId) localStorage.setItem(`${COLLAPSE_STORAGE_PREFIX}${convId}`, String(this._collapsed));
  }

  #openConfig() {
    this.dispatchEvent(new CustomEvent('config-open', {
      bubbles: true,
      composed: true,
    }));
  }

  /** @param {CustomEvent} e */
  #onNotificationNavigate(e) {
    const convId = e.detail?.convId;
    if (!convId) return;
    this.store?.selectConversation(convId);
    this.closeMobile();
  }

  /** @param {CustomEvent} e */
  #onNotificationNavigateVault(e) {
    const page = e.detail?.page;
    if (!page) return;
    this.dispatchEvent(new CustomEvent('wiki-open', {
      detail: { page },
      bubbles: true,
      composed: true,
    }));
    this.closeMobile();
  }

  render() {
    // Collapsed-narrow view is desktop-only. When the user opens the
    // sidebar via hamburger on mobile, render the full content even if
    // `_collapsed` was set from a previous desktop session.
    if (this._collapsed && !this._mobileOpen) {
      return html`
        <div class="sidebar-header">
          <button class="collapse-btn dc-icon-btn" @click=${this.#toggleCollapse} title="Expand sidebar" aria-label="Expand sidebar">›</button>
        </div>
      `;
    }

    return html`
      <div class="sidebar-header dc-overlay-header">
        <select
          class="sidebar-section-select"
          aria-label="Sidebar section"
          @change=${this.#onSectionSelectChange}
        >
          ${SIDEBAR_SECTIONS.map(({ value, label }) => html`
            <option value=${value} ?selected=${this._sidebarSection === value}>${label}</option>
          `)}
        </select>
        <button class="mobile-close-btn dc-overlay-close-x" @click=${() => this.closeMobile()} title="Close sidebar" aria-label="Close sidebar">×</button>
        <button class="collapse-btn dc-icon-btn" @click=${this.#toggleCollapse} title="Collapse sidebar" aria-label="Collapse sidebar">‹</button>
      </div>
      <vault-sidebar
        .active=${this._sidebarSection === 'wiki'}
        style="${this._sidebarSection !== 'wiki' ? 'display:none' : ''}"
        @wiki-open=${(e) => this.#handleWikiOpen(e)}
      ></vault-sidebar>
      <files-sidebar
        .active=${this._sidebarSection === 'files'}
        style="${this._sidebarSection !== 'files' ? 'display:none' : ''}"
        @file-open=${(e) => this.#handleFileOpen(e)}
      ></files-sidebar>
      <schedules-sidebar
        .active=${this._sidebarSection === 'schedules'}
        .openName=${this._openSchedule}
        style="${this._sidebarSection !== 'schedules' ? 'display:none' : ''}"
      ></schedules-sidebar>
      <calendar-sidebar
        .active=${this._sidebarSection === 'calendar'}
        style="${this._sidebarSection !== 'calendar' ? 'display:none' : ''}"
      ></calendar-sidebar>
      <tags-sidebar
        .active=${this._sidebarSection === 'tags'}
        style="${this._sidebarSection !== 'tags' ? 'display:none' : ''}"
        @wiki-open=${(e) => this.#handleWikiOpen(e)}
      ></tags-sidebar>
      <conversation-list
        .store=${this.store}
        .active=${this._sidebarSection === 'conversations'}
        style="${this._sidebarSection !== 'conversations' ? 'display:none' : ''}"
      ></conversation-list>
      ${!this._hasControlsStrip && (this.store?.availableModels || []).length > 0 ? html`
      <div class="model-picker">
        <label class="model-picker-label">Model</label>
        <model-picker
          .groups=${this.store?.modelGroups || []}
          .activeModel=${this.store?.activeModel || ''}
          .defaultModel=${this.store?.defaultModel || ''}
          ?disabled=${Boolean(this.store?.isBusy || this.store?.isReadOnly)}
          .convId=${this.#activeId}
        ></model-picker>
      </div>
      ` : nothing}
      ${!this._hasControlsStrip && (this.store?.contextLimit || 0) > 0 ? html`
        ${(() => {
          const limit = this.store?.contextLimit || 0;
          const pct = Math.round((this.store?.contextUsage || 0) / limit * 100);
          const cls = pct >= 100 ? 'full' : pct >= 80 ? 'warn' : '';
          return html`
            <div class="context-usage ${cls}" title="Click for details"
                 style="cursor:pointer;position:relative"
                 role="button"
                 tabindex="0"
                 aria-expanded=${this._contextInspectorOpen}
                 @click=${() => { this._contextInspectorOpen = !this._contextInspectorOpen; }}
                 @keydown=${(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); this._contextInspectorOpen = !this._contextInspectorOpen; }}}>
               <div class="context-usage-label">
                 <span>Context</span>
                 <span>${pct}%</span>
               </div>
               <div class="context-usage-bar">
                 <div class="context-usage-fill" style="width: ${Math.min(100, pct)}%"></div>
               </div>
            </div>
          `;
        })()}
      ` : nothing}
      <context-inspector
        .convId=${this.#activeId}
        .open=${this._contextInspectorOpen}
        .contextVersion=${this._contextVersion}
        @close=${() => { this._contextInspectorOpen = false; }}
      ></context-inspector>
      <div class="sidebar-footer">
        <div class="footer-icons">
          <theme-toggle></theme-toggle>
          <notification-inbox
            @navigate-conversation=${(e) => this.#onNotificationNavigate(e)}
            @navigate-vault=${(e) => this.#onNotificationNavigateVault(e)}
          ></notification-inbox>
          <button class="config-btn" title="Agent Config" aria-label="Agent Config" @click=${() => this.#openConfig()}>&#9881;</button>
        </div>
        <button class="logout-btn" @click=${() => this.authClient?.logout()} title="Sign out">Sign out</button>
      </div>
    `;
  }
}

customElements.define('conversation-sidebar', ConversationSidebar);
