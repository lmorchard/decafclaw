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

/** User message — plain text, right-aligned. */
export class UserMessage extends LitElement {
  static properties = {
    content: { type: String },
    timestamp: { type: String },
  };
  createRenderRoot() { return this; }
  constructor() { super(); this.content = ''; this.timestamp = ''; }

  render() {
    return html`
      <div class="message user">
        <div class="role">user</div>
        <div class="content">${this.content}</div>
        ${this.timestamp ? html`<div class="msg-time">${formatTime(this.timestamp)}</div>` : nothing}
      </div>
    `;
  }
}

customElements.define('user-message', UserMessage);
