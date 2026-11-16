import { LitElement, html, nothing } from 'lit';
import { ApiError, DefaultService } from '../lib/api-client/index.js';
import { showToast } from '../lib/toast.js';
import { copyToClipboard } from '../lib/utils.js';

/**
 * Conversation export buttons for the active conversation. Fetches the
 * server-rendered export and writes it to the clipboard.
 *
 * Mounts inside the context inspector raw pane (issue #1192; it moved out of
 * the chat-control strip), alongside the raw JSON copy. Renders two adjacent
 * plain buttons — "Copy MD" and "Copy JSONL" (issue #1273: the floating menu
 * this used to be had no reason to exist). Hides itself when convId is falsy
 * so empty / unselected conversations don't show useless buttons.
 */
export class CopyConversationMenu extends LitElement {
  static properties = {
    convId: { type: String, attribute: 'conv-id' },
  };

  createRenderRoot() { return this; }

  constructor() {
    super();
    this.convId = '';
  }

  /** @param {'jsonl' | 'markdown'} format */
  async _copy(format) {
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
    return html`
      <div class="copy-conv-actions">
        <button
          type="button"
          class="dc-small-btn copy-conv-btn"
          @click=${() => this._copy('markdown')}
        >Copy MD</button>
        <button
          type="button"
          class="dc-small-btn copy-conv-btn"
          @click=${() => this._copy('jsonl')}
        >Copy JSONL</button>
      </div>
    `;
  }
}

customElements.define('copy-conversation-menu', CopyConversationMenu);
