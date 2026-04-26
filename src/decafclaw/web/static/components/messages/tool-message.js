import { LitElement, html, nothing } from 'lit';

import '../widgets/widget-host.js';

/** Tool result message — collapsible row with tool name and truncated preview. */
export class ToolMessage extends LitElement {
  static properties = {
    tool: { type: String },
    content: { type: String },
    icon: { type: String },
    display_short_text: { type: String },
    /** Array of {text, timestamp} status history entries from the tool_call phase */
    statusHistory: { type: Array, attribute: false },
    /** Optional widget payload: {widget_type, target, data} */
    widget: { type: Object, attribute: false },
    /** True once an input widget has been submitted (live or reloaded) */
    submitted: { type: Boolean },
    /** Response data for a submitted input widget: {selected, ...} */
    response: { type: Object, attribute: false },
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
    /** @type {{widget_type: string, target: string, data: object}|null} */
    this.widget = null;
    this.submitted = false;
    /** @type {object|null} */
    this.response = null;
    this._expanded = false;
    // Tracks whether we've auto-expanded for this widget yet, so user
    // collapse choices aren't overridden on re-render.
    this._autoExpanded = false;
  }

  /** @param {Map<string, any>} changed */
  willUpdate(changed) {
    // Auto-expand on the first render that has a widget present — so
    // input widgets are visible without requiring a click. User can
    // still collapse afterward.
    if (changed.has('widget') && this.widget && !this._autoExpanded) {
      this._expanded = true;
      this._autoExpanded = true;
    }
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
    const hasWidget = !!this.widget;
    const expandable = hasContent || hasHistory || hasWidget;
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
          ${this.widget ? html`
            <div class="tool-result-detail">
              <dc-widget-host
                .widgetType=${this.widget.widget_type}
                .data=${this.widget.data}
                .fallbackText=${this.content}
                .submitted=${this.submitted}
                .response=${this.response}
              ></dc-widget-host>
              ${hasContent ? html`
                <details class="tool-result-raw">
                  <summary>Show raw result</summary>
                  <pre>${this.content}</pre>
                </details>
              ` : nothing}
            </div>
          ` : hasContent ? html`
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
