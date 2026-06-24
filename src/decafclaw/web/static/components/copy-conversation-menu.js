import { LitElement, html, nothing } from 'lit';
import { showToast } from '../lib/toast.js';
import { copyToClipboard } from '../lib/utils.js';

/**
 * Floating "Copy ▾" menu for the active conversation. Fetches the
 * server-rendered export and writes it to the clipboard.
 *
 * Mounts inside .dc-chat-actions (alongside the Canvas resummon pill).
 * Hides itself when convId is falsy so empty / unselected conversations
 * don't show a useless menu.
 */
export class CopyConversationMenu extends LitElement {
  static properties = {
    convId: { type: String, attribute: 'conv-id' },
    _open: { type: Boolean, state: true },
  };

  createRenderRoot() { return this; }

  constructor() {
    super();
    this.convId = '';
    this._open = false;
    this._onDocClick = this._onDocClick.bind(this);
  }

  connectedCallback() {
    super.connectedCallback();
    document.addEventListener('click', this._onDocClick);
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    document.removeEventListener('click', this._onDocClick);
  }

  /** Close the menu on outside click. */
  _onDocClick(/** @type {MouseEvent} */ e) {
    if (!this._open) return;
    if (e.target instanceof Node && this.contains(e.target)) return;
    this._open = false;
  }

  _toggle(/** @type {Event} */ e) {
    e.stopPropagation();
    this._open = !this._open;
  }

  /** @param {'jsonl' | 'markdown'} format */
  async _copy(format) {
    this._open = false;
    if (!this.convId) return;
    try {
      const url = `/api/conversations/${encodeURIComponent(this.convId)}/export?format=${format}`;
      const res = await fetch(url);
      if (!res.ok) {
        showToast(`Copy failed: server returned ${res.status}`);
        return;
      }
      const text = await res.text();
      await copyToClipboard(text);
      showToast(`Copied as ${format}`);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      showToast(`Copy failed: ${msg}`);
    }
  }

  render() {
    if (!this.convId) return nothing;
    return html`
      <div class="copy-menu-wrap">
        <button
          class="copy-menu-trigger dc-floating-btn"
          type="button"
          aria-haspopup="menu"
          aria-expanded=${this._open}
          aria-label="Copy conversation"
          @click=${this._toggle}
        >\u{1F4CB}<span class="copy-label"> Copy</span><span class="copy-caret"> ▾</span></button>
        ${this._open ? html`
          <ul class="copy-menu-list" role="menu">
            <li role="none">
              <button
                type="button"
                role="menuitem"
                class="copy-menu-item"
                @click=${() => this._copy('jsonl')}
              >Copy as JSONL</button>
            </li>
            <li role="none">
              <button
                type="button"
                role="menuitem"
                class="copy-menu-item"
                @click=${() => this._copy('markdown')}
              >Copy as markdown</button>
            </li>
          </ul>
        ` : nothing}
      </div>
    `;
  }
}

customElements.define('copy-conversation-menu', CopyConversationMenu);
