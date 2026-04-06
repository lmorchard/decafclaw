import { LitElement, html, nothing } from 'lit';

/** Tool result message — collapsible row with tool name and truncated preview. */
export class ToolMessage extends LitElement {
  static properties = {
    tool: { type: String },
    content: { type: String },
    icon: { type: String },
    display_short_text: { type: String },
    /** Array of {text, timestamp} status history entries from the tool_call phase */
    statusHistory: { type: Array, attribute: false },
    _expanded: { type: Boolean, state: true },
  };

  createRenderRoot() { return this; }

  constructor() {
    super();
    this.tool = '';
    this.content = '';
    this.icon = '\u{1f527}';
    this.display_short_text = '';
    /** @type {Array<{text: string, timestamp: string}>|null} */
    this.statusHistory = null;
    this._expanded = false;
  }

  #toggleExpand() {
    this._expanded = !this._expanded;
  }

  /** @param {string} content */
  #truncate(content, maxLen = 100) {
    if (!content || content.length <= maxLen) return content;
    return content.substring(0, maxLen) + '...';
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

  render() {
    const hasContent = this.content && this.content.length > 0;
    const hasHistory = this.statusHistory?.length > 0;
    const expandable = hasContent || hasHistory;
    return html`
      <div class="message tool">
        <div class="tool-result-header" @click=${expandable ? this.#toggleExpand : nothing}>
          <span class="tool-icon">${this.icon}</span>
          <span class="tool-name">${this.tool}</span>
          ${hasContent ? html`
            <span class="tool-preview">${this.display_short_text || this.#truncate(this.content)}</span>
          ` : nothing}
          ${hasHistory && !hasContent ? html`
            <span class="tool-preview">${this.statusHistory.length} progress updates</span>
          ` : nothing}
          ${expandable ? html`
            ${hasHistory ? html`<span class="status-history-badge">${this.statusHistory.length}</span>` : nothing}
            <span class="expand-toggle">${this._expanded ? '\u25b2' : '\u25bc'}</span>
          ` : nothing}
        </div>
        ${this._expanded ? html`
          ${hasHistory ? html`
            <div class="status-history">
              ${this.statusHistory.map(entry => html`
                <div class="status-history-entry">
                  <span class="status-history-time">${this.#formatTime(entry.timestamp)}</span>
                  <span class="status-history-text">${entry.text}</span>
                </div>
              `)}
            </div>
          ` : nothing}
          ${hasContent ? html`
            <div class="tool-result-detail">
              <pre>${this.content}</pre>
            </div>
          ` : nothing}
        ` : nothing}
      </div>
    `;
  }
}

customElements.define('tool-message', ToolMessage);
