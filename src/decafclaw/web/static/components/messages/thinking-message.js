import { LitElement, html, nothing } from 'lit';
import { unsafeHTML } from 'lit/directives/unsafe-html.js';
import { renderMarkdown } from '../../lib/markdown.js';
import '../turn-provenance-row.js';

/**
 * Thinking transcript element (#1627) — a standalone row, so reasoning survives
 * reload whether or not the assistant produced text or tool calls.
 */
export class ThinkingMessage extends LitElement {
  static properties = {
    content: { type: String },
    provenance: { type: Object, attribute: false },
    streaming: { type: Boolean },
  };

  createRenderRoot() { return this; }

  constructor() {
    super();
    this.content = '';
    /** @type {Record<string, object[]>} */
    this.provenance = {};
    this.streaming = false;
  }

  render() {
    if (!this.content || !this.content.trim()) return nothing;
    return html`
      <div class="message assistant thinking-message">
        <div class="role">assistant</div>
        <turn-provenance-row .provenance=${this.provenance || {}}></turn-provenance-row>
        <details class="thought-block" ?open=${this.streaming}>
          <summary>
            Thought process
            ${this.streaming ? html`<span class="thought-spinner"></span>` : nothing}
          </summary>
          <div class="thought-body">
            ${unsafeHTML(renderMarkdown(this.content))}
          </div>
        </details>
      </div>
    `;
  }
}

customElements.define('thinking-message', ThinkingMessage);
