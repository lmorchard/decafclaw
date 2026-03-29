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
  };

  createRenderRoot() { return this; }

  constructor() {
    super();
    this.variant = 'tool_call';
    this.content = '';
    /** @type {Array|null} */
    this.toolCalls = null;
  }

  render() {
    if (this.variant === 'tool_call') {
      // Live tool call in progress
      return html`
        <div class="message tool">
          <div class="tool-result-header">
            <span class="tool-icon">\u{1f527}</span>
            <span class="tool-summary">${this.content}</span>
            <span class="streaming-indicator"></span>
          </div>
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
}

customElements.define('tool-call-message', ToolCallMessage);
