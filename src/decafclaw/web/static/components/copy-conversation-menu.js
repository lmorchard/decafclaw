import { LitElement, html, nothing } from 'lit';
import { ApiError, DefaultService } from '../lib/api-client/index.js';
import { showToast } from '../lib/toast.js';
import { copyToClipboard } from '../lib/utils.js';

/**
 * Floating "Copy ▾" menu for the active conversation. Fetches the
 * server-rendered export and writes it to the clipboard.
 *
 * Mounts inside the context inspector raw pane (issue #1192; it moved out of
 * the chat-control strip), alongside the raw JSON copy. Hides itself when
 * convId is falsy so empty / unselected conversations don't show a useless
 * menu.
 */
export class CopyConversationMenu extends LitElement {
  static properties = {
    convId: { type: String, attribute: 'conv-id' },
    _open: { type: Boolean, state: true },
    _menuPosition: { type: Object, state: true },
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
    if (this._open) {
      // Pin it fixed so it escapes any clipping ancestor and floats over the
      // surface; anchor it via the wrap's bounding rect (the trigger may sit
      // near the bottom of the viewport, so downward-open menus can run past
      // the end of the page).
      const wrap = this.querySelector('.copy-menu-wrap');
      if (wrap) {
        // Anchor on the trigger button, not the wrap: the wrap is a block-level
        // div, so its rect spans the container instead of hugging the button.
        const btn = wrap.querySelector('.copy-menu-trigger');
        const r = (btn ?? wrap).getBoundingClientRect();
        // Open ABOVE the trigger (the trigger can sit near the bottom of the
        // viewport, so a downward menu can run past the end of the page). For
        // position: fixed, `bottom` is measured from the viewport bottom, so the
        // element's bottom edge lands at innerHeight - bottom — anchor just
        // above the trigger's top edge.
        const bottom = Math.max(0, window.innerHeight - r.top);
        // For position: fixed, `right` is measured from the viewport's right
        // edge, so the element's right edge lands at innerWidth - right — anchor
        // at the trigger's right edge.
        const right = Math.max(0, window.innerWidth - r.right);
        this._menuPosition = { bottom, right };
      }
    } else {
      this._menuPosition = null;
    }
  }

  /** @param {'jsonl' | 'markdown'} format */
  async _copy(format) {
    this._open = false;
    if (!this.convId) return;
    try {
      const text = await DefaultService.exportConversationApiConversationsIdExportGet(this.convId, format);
      await copyToClipboard(text);
      showToast(`Copied as ${format}`);
    } catch (err) {
      if (err instanceof ApiError) {
        showToast(`Copy failed: server returned ${err.status}`);
        return;
      }
      const msg = err instanceof Error ? err.message : String(err);
      showToast(`Copy failed: ${msg}`);
    }
  }

  render() {
    if (!this.convId) return nothing;
    const menuStyle = this._open && this._menuPosition
      ? `position: fixed; bottom: ${this._menuPosition.bottom}px; right: ${this._menuPosition.right}px;`
      : '';
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
          <ul class="copy-menu-list" role="menu" style=${menuStyle}>
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
