import { LitElement, html, nothing } from 'lit';

/** Tool result message — collapsible row with tool name and truncated preview. */
export class ToolMessage extends LitElement {
  static properties = {
    tool: { type: String },
    content: { type: String },
    icon: { type: String },
    display_short_text: { type: String },
    _expanded: { type: Boolean, state: true },
  };

  createRenderRoot() { return this; }

  constructor() {
    super();
    this.tool = '';
    this.content = '';
    this.icon = '\u{1f527}';
    this.display_short_text = '';
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

  render() {
    const hasContent = this.content && this.content.length > 0;
    return html`
      <div class="message tool">
        <div class="tool-result-header" @click=${hasContent ? this.#toggleExpand : nothing}>
          <span class="tool-icon">${this.icon}</span>
          <span class="tool-name">${this.tool}</span>
          ${hasContent ? html`
            <span class="tool-preview">${this.display_short_text || this.#truncate(this.content)}</span>
            <span class="expand-toggle">${this._expanded ? '\u25b2' : '\u25bc'}</span>
          ` : nothing}
        </div>
        ${this._expanded ? html`
          <div class="tool-result-detail">
            <pre>${this.content}</pre>
          </div>
        ` : nothing}
      </div>
    `;
  }
}

customElements.define('tool-message', ToolMessage);
