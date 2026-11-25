import { LitElement, html, nothing } from 'lit';
import './messages/user-message.js';
import './messages/assistant-message.js';
import './messages/tool-message.js';
import './messages/tool-call-message.js';
import './messages/thinking-message.js';

export class ChatMessage extends LitElement {
  static properties = {
    role: { type: String },
    content: { type: String },
    provenance: { type: Object, attribute: false },
    streaming: { type: Boolean },
    tool: { type: String },
    command: { type: String },
    display_short_text: { type: String, attribute: false },
    details: { type: Object, attribute: false },
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
    isError: { type: Boolean, attribute: false },
    timedOut: { type: Boolean, attribute: false },
    timeoutSec: { type: Number, attribute: false },
    suppressUserMessage: { type: Boolean, attribute: false },
  };

  createRenderRoot() { return this; }

  constructor() {
    super();
    this.role = 'user';
    this.content = '';
    /** @type {Record<string, object[]>} */
    this.provenance = {};
    this.streaming = false;
    this.tool = '';
    this.command = '';
    this.display_short_text = '';
    /** @type {object|null} */
    this.details = null;
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
    this.isError = false;
    this.timedOut = false;
    /** @type {number|null} */
    this.timeoutSec = null;
    this.suppressUserMessage = false;
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
      const rec = this.record?.data || this.record || {};
      const beforeMsgs = rec.before_messages;
      const afterMsgs = rec.after_messages;
      const beforeTokens = rec.history_tokens_before ?? rec.estimated_tokens_before;
      const afterTokens = rec.history_tokens_after ?? rec.estimated_tokens_after;
      const elapsed = rec.elapsed_sec;
      let tokenReduction = null;
      if (beforeTokens && afterTokens && beforeTokens > afterTokens) {
        tokenReduction = Math.round(((beforeTokens - afterTokens) / beforeTokens) * 100);
      }
      const hasMetrics = beforeMsgs != null || tokenReduction != null || elapsed != null;
      return html`
        <div class="compaction-notice">
          <span class="compaction-icon">📦</span>
          <span class="compaction-text">${this.content}</span>
          ${hasMetrics ? html`
            <div class="compaction-metrics">
              ${beforeMsgs != null && afterMsgs != null ? html`<span class="metric-chip">${beforeMsgs} → ${afterMsgs} msgs</span>` : ''}
              ${tokenReduction != null ? html`<span class="metric-chip">-${tokenReduction}% tokens</span>` : ''}
              ${elapsed != null ? html`<span class="metric-chip">${elapsed}s</span>` : ''}
            </div>
          ` : ''}
        </div>
      `;
    }

    if (this.role === 'cancel_marker') {
      return html`
        <div class="chat-event cancel-marker">
          <span class="icon">🛑</span>
          <span class="title">Turn cancelled</span>
          ${this.content ? html`<span class="detail">${this.content}</span>` : ''}
        </div>
      `;
    }

    if (this.role === 'turn_aborted') {
      return html`
        <div class="chat-event turn-aborted">
          <span class="icon">⚠️</span>
          <span class="title">Turn aborted</span>
          ${this.content ? html`<span class="detail">${this.content}</span>` : ''}
        </div>
      `;
    }

    if (this.role === 'shell_approval') {
      const isDeclined = this.tool && this.tool.includes('DECLINED');
      const icon = isDeclined ? '\u{1f6d1}' : '\u{1f6e1}\u{fe0f}'; // STOP sign or SHIELD
      return html`<tool-message .tool=${this.tool || 'shell_approval'} .content=${this.content} .icon=${icon}></tool-message>`;
    }

    if (this.role === 'reflection') {
      return html`<tool-message .tool=${this.tool || 'reflection'} .content=${this.content} .icon=${'\u{1f441}'}></tool-message>`;
    }

    if (this.role === 'vault_references') {
      return html`<tool-message .tool=${'vault_references'} .content=${this.content} .icon=${'\u{1f4d6}'}></tool-message>`;
    }

    if (this.role === 'workspace_references') {
      return html`<tool-message .tool=${'workspace_references'} .content=${this.content} .icon=${'📁'}></tool-message>`;
    }

    if (this.role === 'mcp_references') {
      return html`<tool-message .tool=${'mcp_references'} .content=${this.content} .icon=${'🔌'}></tool-message>`;
    }

    if (this.role === 'tool_call') {
      return html`<tool-call-message variant="tool_call" .content=${this.content} .statusHistory=${this.statusHistory}></tool-call-message>`;
    }

    if (this.role === 'tool') {
      return html`<tool-message .tool=${this.tool} .command=${this.command} .content=${this.content} .display_short_text=${this.display_short_text || ''} .details=${this.details} .statusHistory=${this.statusHistory} .timestamp=${this.timestamp} .widget=${this.widget} .submitted=${this.submitted} .response=${this.response} .isError=${this.isError} .timedOut=${this.timedOut} .timeoutSec=${this.timeoutSec}></tool-message>`;
    }

    if (this.role === 'thinking') {
      return html`<thinking-message
        .content=${this.content}
        .provenance=${this.provenance}
        .streaming=${this.streaming}
      ></thinking-message>`;
    }

    if (this.role === 'assistant' && this.suppressUserMessage) {
      return html`
        <details class="chat-event wake-status">
          <summary>
            <span class="icon">◇</span>
            <span class="title">wake · silent</span>
          </summary>
          ${this.content ? html`<div class="detail">${this.content}</div>` : ''}
        </details>
      `;
    }

    if (this.role === 'assistant' && this.toolCalls?.length) {
      return html`<tool-call-message
        variant="tool_calls_history"
        .content=${this.content || ''}
        .toolCalls=${this.toolCalls}
      ></tool-call-message>`;
    }

    if (this.role === 'assistant') {
      return html`<div class="message-row assistant-row">
        <assistant-message
          .content=${this.content}
          .provenance=${this.provenance}
          .streaming=${this.streaming}
          .usage=${this.usage}
          .timestamp=${this.timestamp}
        ></assistant-message>
        ${this.#branchButton()}
      </div>`;
    }

    return html`<div class="message-row user-row">
      <user-message .content=${this.content} .timestamp=${this.timestamp} .attachments=${this.attachments}></user-message>
      ${this.#branchButton()}
    </div>`;
  }

  /**
   * "Branch from here" (#1581): fork the conversation at this message. Only
   * user and assistant rows anchor a fork; tool rows and event banners are
   * part of a turn, not a boundary. The store is reached through the
   * chat-view ancestor (same pattern as widget responses) so this component
   * stays prop-free.
   * @returns {unknown}
   */
  #branchButton() {
    if (this.role !== 'user' && this.role !== 'assistant') return nothing;
    if (!this.timestamp) return nothing;
    return html`
      <button
        class="branch-btn"
        type="button"
        title="Branch a new conversation from this message"
        @click=${(ev) => {
          ev.preventDefault();
          ev.stopPropagation();
          const host = /** @type {any} */ (this.closest('chat-view'));
          host?.store?.forkConversation?.(this.timestamp);
        }}
      >\u2442 Branch</button>
    `;
  }
}

customElements.define('chat-message', ChatMessage);
