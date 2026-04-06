import { LitElement, html, nothing } from 'lit';
import { unsafeHTML } from 'lit/directives/unsafe-html.js';
import { renderMarkdown } from '../../lib/markdown.js';

/**
 * Tool call message — either a live in-progress indicator (role=tool_call)
 * or a history entry showing tool names from an assistant message with tool_calls.
 */
export class ToolCallMessage extends LitElement {
  static properties = {
    /** 'tool_call' for live/in-progress, 'tool_calls_history' for completed assistant tool calls */
    variant: { type: String },
    /** Summary text for live tool_call, or pre-call assistant text content */
    content: { type: String },
    /** Array of tool call objects from assistant message history */
    toolCalls: { type: Array, attribute: false },
    /** Array of {text, timestamp} status history entries */
    statusHistory: { type: Array, attribute: false },
    _expanded: { type: Boolean, state: true },
  };

  createRenderRoot() { return this; }

  constructor() {
    super();
    this.variant = 'tool_call';
    this.content = '';
    /** @type {Array|null} */
    this.toolCalls = null;
    /** @type {Array<{text: string, timestamp: string}>|null} */
    this.statusHistory = null;
    this._expanded = false;
  }

  #toggleExpand() {
    if (this.statusHistory?.length > 1) {
      this._expanded = !this._expanded;
    }
  }

  render() {
    if (this.variant === 'tool_call') {
      const hasHistory = this.statusHistory?.length > 1;
      return html`
        <div class="message tool">
          <div class="tool-result-header" @click=${hasHistory ? this.#toggleExpand : nothing}>
            <span class="tool-icon">\u{1f527}</span>
            <span class="tool-summary">${this.content}</span>
            <span class="streaming-indicator"></span>
            ${hasHistory ? html`
              <span class="status-history-badge" title="Click to show progress history">${this.statusHistory.length}</span>
              <span class="expand-toggle">${this._expanded ? '\u25b2' : '\u25bc'}</span>
            ` : nothing}
          </div>
          ${this._expanded ? html`
            <div class="status-history">
              ${this.statusHistory.map(entry => html`
                <div class="status-history-entry">
                  <span class="status-history-time">${this.#formatTime(entry.timestamp)}</span>
                  <span class="status-history-text">${entry.text}</span>
                </div>
              `)}
            </div>
          ` : nothing}
        </div>
      `;
    }

    // History: assistant message that contained tool calls
    const toolNames = (this.toolCalls || []).map(tc => tc.function?.name || 'tool').join(', ');
    return html`
      ${this.content ? html`
        <div class="message assistant">
          <div class="role">assistant</div>
          <div class="content">${unsafeHTML(renderMarkdown(this.content))}</div>
        </div>
      ` : nothing}
      <div class="message tool">
        <div class="tool-result-header">
          <span class="tool-icon">\u{1f527}</span>
          <span class="tool-name">${toolNames}</span>
        </div>
      </div>
    `;
  }

  /** @param {string} isoStr */
  #formatTime(isoStr) {
    try {
      const d = new Date(isoStr);
      return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    } catch {
      return '';
    }
  }
}

customElements.define('tool-call-message', ToolCallMessage);
