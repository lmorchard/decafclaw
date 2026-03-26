import { LitElement, html, nothing } from 'lit';

/**
 * @param {string} ts
 * @returns {string}
 */
function formatTime(ts) {
  if (!ts) return '';
  const d = new Date(ts);
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

/** User message — plain text with optional attachments, right-aligned. */
export class UserMessage extends LitElement {
  static properties = {
    content: { type: String },
    timestamp: { type: String },
    attachments: { type: Array },
  };
  createRenderRoot() { return this; }
  constructor() { super(); this.content = ''; this.timestamp = ''; this.attachments = null; }

  #renderAttachment(att) {
    const isImage = att.mime_type?.startsWith('image/');
    // Encode each path segment to handle spaces, #, etc. in filenames
    const url = '/api/workspace/' + att.path.split('/').map(encodeURIComponent).join('/');
    if (isImage) {
      return html`
        <a href=${url} target="_blank" rel="noopener noreferrer" class="attachment-image-link">
          <img src=${url} alt=${att.filename} class="attachment-image">
        </a>
      `;
    }
    return html`
      <a href=${url} target="_blank" rel="noopener noreferrer" download=${att.filename} class="attachment-file-link">
        <span class="attachment-file-chip">${att.filename}</span>
      </a>
    `;
  }

  render() {
    const atts = this.attachments;
    return html`
      <div class="message user">
        <div class="role">user</div>
        ${this.content ? html`<div class="content">${this.content}</div>` : nothing}
        ${atts?.length ? html`
          <div class="attachment-list">
            ${atts.map(a => this.#renderAttachment(a))}
          </div>
        ` : nothing}
        ${this.timestamp ? html`<div class="msg-time">${formatTime(this.timestamp)}</div>` : nothing}
      </div>
    `;
  }
}

customElements.define('user-message', UserMessage);
