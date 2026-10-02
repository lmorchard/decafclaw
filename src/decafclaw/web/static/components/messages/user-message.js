import { LitElement, html, nothing } from 'lit';
import { formatTime } from '../../lib/utils.js';
import { buildNativeWorkspaceUrl } from '../../lib/api-client/index.js';
/** @typedef {import('../../lib/api-client/index.js').AttachmentResponse} AttachmentResponse */

/** User message — plain text with optional attachments, right-aligned. */
export class UserMessage extends LitElement {
  static properties = {
    content: { type: String },
    timestamp: { type: String },
    attachments: { type: Array },
  };
  createRenderRoot() { return this; }
  constructor() {
    super();
    this.content = '';
    this.timestamp = '';
    /** @type {AttachmentResponse[] | null} */
    this.attachments = null;
  }

  /** @param {AttachmentResponse} att */
  #renderAttachment(att) {
    const isImage = att.mime_type?.startsWith('image/');
    const url = buildNativeWorkspaceUrl(att.path);
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
