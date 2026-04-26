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
    statusHistory: { type: Array, attribute: false },
    usage: { type: Object, attribute: false },
    timestamp: { type: String },
    attachments: { type: Array, attribute: false },
    record: { type: Object, attribute: false },
    widget: { type: Object, attribute: false },
    submitted: { type: Boolean },
    response: { type: Object, attribute: false },
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
    /** @type {Array<{text: string, timestamp: string}>|null} */
    this.statusHistory = null;
    /** @type {object|null} */
    this.usage = null;
    this.timestamp = '';
    /** @type {Array|null} */
    this.attachments = null;
    /** @type {object|null} */
    this.record = null;
    /** @type {object|null} */
    this.widget = null;
    this.submitted = false;
    /** @type {object|null} */
    this.response = null;
  }

  connectedCallback() {
    super.connectedCallback();
    // Input widgets bubble `widget-response` CustomEvents when the user
    // submits. Forward them to the store with our tool_call_id so the
    // store can look up the matching confirmation and send the WS.
    this.addEventListener('widget-response', (ev) => {
      if (this.role !== 'tool' || !this.toolCallId) return;
      const detail = /** @type {CustomEvent} */ (ev).detail || {};
      // Get the store via the chat-view ancestor (keeps chat-message
      // prop-free of the store reference).
      const host = /** @type {any} */ (this.closest('chat-view'));
      const store = host?.store;
      store?.respondToWidget?.(this.toolCallId, detail);
    });
  }

  render() {
    if (this.role === 'background_event') {
      const rec = this.record || {};
      const status = rec.status || 'completed';
      const exitCode = rec.exit_code ?? '';
      const command = rec.command || '';
      const stdoutTail = rec.stdout_tail || '';
      const stderrTail = rec.stderr_tail || '';
      const icon = status === 'completed' ? '✅' : '❌';
      const exitLabel = exitCode !== '' ? ` (exit ${exitCode})` : '';
      return html`
        <div class="chat-event background-event">
          <span class="icon">${icon}</span>
          <span class="title">Background job ${status}${exitLabel}</span>
          ${command ? html`<code class="command">${command}</code>` : ''}
          ${stdoutTail ? html`<pre class="stdout">${stdoutTail}</pre>` : ''}
          ${stderrTail ? html`<pre class="stderr">${stderrTail}</pre>` : ''}
        </div>
      `;
    }

    if (this.role === 'command') {
      return html`<tool-message .tool=${this.content} .content=${''} .icon=${'\u{2699}\u{fe0f}'}></tool-message>`;
    }

    if (this.role === 'compaction') {
      return html`<div class="compaction-notice">${this.content}</div>`;
    }

    if (this.role === 'reflection') {
      return html`<tool-message .tool=${this.tool || 'reflection'} .content=${this.content} .icon=${'\u{1f441}'}></tool-message>`;
    }

    if (this.role === 'vault_retrieval') {
      return html`<tool-message .tool=${'vault_retrieval'} .content=${this.content} .icon=${'\u{1f9e0}'}></tool-message>`;
    }

    if (this.role === 'vault_references') {
      return html`<tool-message .tool=${'vault_references'} .content=${this.content} .icon=${'\u{1f4d6}'}></tool-message>`;
    }

    if (this.role === 'tool_call') {
      return html`<tool-call-message variant="tool_call" .content=${this.content} .statusHistory=${this.statusHistory}></tool-call-message>`;
    }

    if (this.role === 'tool') {
      return html`<tool-message .tool=${this.tool} .content=${this.content} .display_short_text=${this.display_short_text || ''} .statusHistory=${this.statusHistory} .timestamp=${this.timestamp} .widget=${this.widget} .submitted=${this.submitted} .response=${this.response}></tool-message>`;
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
