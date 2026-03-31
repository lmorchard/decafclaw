import { LitElement, html } from 'lit';
import './messages/user-message.js';
import './messages/assistant-message.js';
import './messages/tool-message.js';
import './messages/tool-call-message.js';

export class ChatMessage extends LitElement {
  static properties = {
    role: { type: String },
    content: { type: String },
    streaming: { type: Boolean },
    tool: { type: String },
    display_short_text: { type: String, attribute: false },
    toolCalls: { type: Array, attribute: false },
    toolCallId: { type: String, attribute: false },
    usage: { type: Object, attribute: false },
    timestamp: { type: String },
    attachments: { type: Array, attribute: false },
  };

  createRenderRoot() { return this; }

  constructor() {
    super();
    this.role = 'user';
    this.content = '';
    this.streaming = false;
    this.tool = '';
    this.display_short_text = '';
    /** @type {Array|null} */
    this.toolCalls = null;
    this.toolCallId = '';
    /** @type {object|null} */
    this.usage = null;
    this.timestamp = '';
    /** @type {Array|null} */
    this.attachments = null;
  }

  render() {
    if (this.role === 'compaction') {
      return html`<div class="compaction-notice">${this.content}</div>`;
    }

    if (this.role === 'reflection') {
      return html`<tool-message .tool=${this.tool || 'reflection'} .content=${this.content} .icon=${'\u{1f441}'}></tool-message>`;
    }

    if (this.role === 'memory_context') {
      return html`<tool-message .tool=${'memory_context'} .content=${this.content} .icon=${'\u{1f9e0}'}></tool-message>`;
    }

    if (this.role === 'wiki_context') {
      return html`<tool-message .tool=${'wiki_context'} .content=${this.content} .icon=${'\u{1f4d6}'}></tool-message>`;
    }

    if (this.role === 'tool_call') {
      return html`<tool-call-message variant="tool_call" .content=${this.content}></tool-call-message>`;
    }

    if (this.role === 'tool') {
      return html`<tool-message .tool=${this.tool} .content=${this.content} .display_short_text=${this.display_short_text || ''} .timestamp=${this.timestamp}></tool-message>`;
    }

    if (this.role === 'assistant' && this.toolCalls?.length) {
      return html`<tool-call-message
        variant="tool_calls_history"
        .content=${this.content || ''}
        .toolCalls=${this.toolCalls}
      ></tool-call-message>`;
    }

    if (this.role === 'assistant') {
      return html`<assistant-message
        .content=${this.content}
        .streaming=${this.streaming}
        .usage=${this.usage}
        .timestamp=${this.timestamp}
      ></assistant-message>`;
    }

    return html`<user-message .content=${this.content} .timestamp=${this.timestamp} .attachments=${this.attachments}></user-message>`;
  }
}

customElements.define('chat-message', ChatMessage);
