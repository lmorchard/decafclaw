import { LitElement, html, nothing } from 'lit';
import './chat-message.js';
import './confirm-view.js';
import {
  PREFERENCE_EVENT,
  loadShowContextProvenance,
} from '../lib/context-provenance-preference.js';
import { provenanceHasItems } from './turn-provenance-row.js';

export class ChatView extends LitElement {
  static properties = {
    store: { type: Object, attribute: false },
    _messages: { type: Array, state: true },
    _streamingText: { type: String, state: true },
    _streamingThinking: { type: String, state: true },
    _busy: { type: Boolean, state: true },
    _toolStatus: { type: String, state: true },
    _pendingConfirms: { type: Array, state: true },
    _convId: { type: String, state: true },
    _showScrollBtn: { type: Boolean, state: true },
    _hasMore: { type: Boolean, state: true },
    _showContextProvenance: { type: Boolean, state: true },
    _streamingProvenance: { type: Object, state: true },
  };

  createRenderRoot() { return this; }

  constructor() {
    super();
    this.store = null;
    this._messages = [];
    this._streamingText = '';
    this._streamingThinking = '';
    this._streamingProvenance = {};
    this._busy = false;
    this._toolStatus = null;
    this._showScrollBtn = false;
    this._pendingConfirms = [];
    this._convId = null;
    this._scrollOnLoad = false;
    this._hasMore = false;
    this._loadingMore = false;
    this._savedScrollHeight = 0;
    this._resizeObserver = null;
    this._showContextProvenance = loadShowContextProvenance();
  }

  connectedCallback() {
    super.connectedCallback();
    // Re-scroll when images load (they change height after initial render).
    // Only skip if the user has deliberately scrolled up (scroll button visible).
    this.addEventListener('load', (e) => {
      if (/** @type {Element} */ (e.target).tagName === 'IMG' && !this._showScrollBtn) {
        this.#scrollToBottom();
      }
    }, true);  // capture phase to catch img loads inside child components
    this._onStoreChange = () => {
      const prevConvId = this._convId;
      const prevMsgCount = this._messages.length;
      this._messages = this.store?.currentMessages || [];
      this._streamingText = this.store?.streamingText || '';
      this._streamingThinking = this.store?.streamingThinking || '';
      this._streamingProvenance = this.store?.streamingProvenance || {};
      this._busy = this.store?.isBusy || false;
      this._toolStatus = this.store?.toolStatus;
      this._pendingConfirms = [...(this.store?.pendingConfirms || [])];
      this._convId = this.store?.currentConvId;
      this._hasMore = this.store?.hasMore || false;

      if (prevConvId !== this._convId) {
        // Conversation switched — scroll to bottom once messages arrive
        this._scrollOnLoad = true;
        this._showScrollBtn = false;
        this._savedScrollHeight = 0;
        this._loadingMore = false;
      }

      // Older messages were prepended (load more) — anchor scroll position
      if (this._savedScrollHeight > 0 && this._messages.length > prevMsgCount) {
        const saved = this._savedScrollHeight;
        this._savedScrollHeight = 0;
        this._loadingMore = false;
        requestAnimationFrame(() => {
          this.scrollTop = this.scrollHeight - saved;
        });
        return;
      }

      if (this._scrollOnLoad && this._messages.length > 0) {
        this._scrollOnLoad = false;
        this.#scrollToBottom();
      } else if (this.#isNearBottom()) {
        this.#scrollToBottom();
      } else {
        this._showScrollBtn = true;
      }
    };
    // Track scroll position + auto-load older messages
    this.addEventListener('scroll', () => {
      this._showScrollBtn = !this.#isNearBottom();
      if (this._hasMore && !this._loadingMore && this.scrollTop < 100) {
        this.#handleLoadMore();
      }
    });
    if (typeof ResizeObserver !== 'undefined') {
      this._resizeObserver = new ResizeObserver(() => {
        if (!this._showScrollBtn) {
          this.#scrollToBottom();
        }
      });
      this._resizeObserver.observe(this);
    }
    this._onContextProvenancePreference = (e) => {
      this._showContextProvenance = Boolean(e.detail?.enabled);
    };
    document.addEventListener(PREFERENCE_EVENT, this._onContextProvenancePreference);
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    this.store?.removeEventListener('change', this._onStoreChange);
    this._resizeObserver?.disconnect();
    this._resizeObserver = null;
    document.removeEventListener(PREFERENCE_EVENT, this._onContextProvenancePreference);
  }

  updated(changedProps) {
    if (changedProps.has('store')) {
      const oldStore = changedProps.get('store');
      if (oldStore) oldStore.removeEventListener('change', this._onStoreChange);
      if (this.store) {
        this.store.addEventListener('change', this._onStoreChange);
        this._onStoreChange();
      }
    }
  }

  #scrollToBottom() {
    this.scrollTop = this.scrollHeight;
    this._showScrollBtn = false;
    requestAnimationFrame(() => {
      this.scrollTop = this.scrollHeight;
      this._showScrollBtn = false;
    });
  }

  #isNearBottom() {
    const threshold = 100;
    return this.scrollHeight - this.scrollTop - this.clientHeight < threshold;
  }

  #handleLoadMore() {
    if (this._loadingMore) return;
    this._loadingMore = true;
    this._savedScrollHeight = this.scrollHeight;
    this.store?.loadMoreHistory();
  }

  /**
   * Provenance for one message, honoring the visibility preference.
   * Legacy rows archived before #1563 carry only `recalled_facts`; derive the
   * facts capsule from them so history reloads keep their disclosure.
   * @param {import('../lib/conversation-store.js').ChatMessage} m
   * @returns {Record<string, object[]>}
   */
  #provenanceFor(m) {
    if (!this._showContextProvenance) return {};
    const provenance = (m.provenance && typeof m.provenance === 'object') ? m.provenance : {};
    if (provenanceHasItems(provenance)) return provenance;
    const facts = Array.isArray(m.recalled_facts) ? m.recalled_facts : [];
    if (!facts.length) return {};
    return { facts: facts.map((fact) => ({ fact })) };
  }

  render() {
    if (!this._convId) {
      return html`<div class="empty-state">Select or create a conversation to start chatting.</div>`;
    }

    return html`
      ${this._loadingMore ? html`
        <div class="load-more"><small>Loading...</small></div>
      ` : nothing}

      ${this._messages.filter(m => {
        // Thinking renders as its own transcript row (#1627).
        if (m.role === 'thinking') return (m.content || '').trim().length > 0;
        // Suppress empty assistant bubbles — happens when the LLM returns
        // 0 output tokens with no tool calls (rare; Anthropic and Gemini
        // both occasionally do this on confusing input). The agent loop
        // logs a warning and records the empty turn for context fidelity,
        // but there's nothing for the UI to show.
        if (m.role !== 'assistant') return true;
        const hasContent = (m.content || '').trim().length > 0;
        const hasToolCalls = Array.isArray(m.tool_calls) && m.tool_calls.length > 0;
        // A silent wake is a status row even when its body is empty (#1852).
        return hasContent || hasToolCalls || !!m.suppress_user_message;
      }).map(m => html`
        <chat-message
          .role=${m.role}
          .content=${m.content || ''}
          .provenance=${this.#provenanceFor(m)}
          .tool=${m.tool || ''}
          .command=${m.command || ''}
          .display_short_text=${m.display_short_text || ''}
          .links=${m.links || null}
          .toolCalls=${m.tool_calls || null}
          .toolCallId=${m.tool_call_id || ''}
          .statusHistory=${m.statusHistory || null}
          .usage=${m.usage || null}
          .timestamp=${m.timestamp || ''}
          .attachments=${m.attachments || null}
          .record=${m.record || m.data || null}
          .widget=${m.widget || null}
          .submitted=${!!m.submitted}
          .response=${m.response || null}
          .isError=${!!m.is_error}
          .timedOut=${!!m.timed_out}
          .timeoutSec=${m.timeout_sec ?? null}
          .failed=${!!m.failed}
          .suppressUserMessage=${!!m.suppress_user_message}
        ></chat-message>
      `)}

      ${(() => {
        // Thinking and answer stream as separate transcript widgets (#1627).
        // Provenance rides the first element of the turn.
        const streamingProvenance = this._showContextProvenance ? this._streamingProvenance : {};
        const hasThinking = Boolean(this._streamingThinking);
        const hasText = Boolean(this._streamingText);
        if (hasThinking || hasText) {
          return html`
            ${hasThinking ? html`
              <chat-message
                .role=${'thinking'}
                .content=${this._streamingThinking}
                .provenance=${streamingProvenance}
                .streaming=${!hasText}
              ></chat-message>
            ` : ''}
            ${hasText ? html`
              <chat-message
                .role=${'assistant'}
                .content=${this._streamingText}
                .provenance=${hasThinking ? {} : streamingProvenance}
                .streaming=${true}
              ></chat-message>
            ` : ''}
          `;
        }
        if (this._busy && !this._toolStatus) {
          return html`
            <div class="thinking-indicator">
              <span></span><span></span><span></span>
            </div>
          `;
        }
        return '';
      })()}

      <confirm-view .store=${this.store} .confirms=${this._pendingConfirms}></confirm-view>

      ${this._showScrollBtn ? html`
        <button
          class="scroll-to-bottom dc-floating-btn"
          @click=${() => this.#scrollToBottom()}
          aria-label="Scroll to new messages"
        >\u2193 New messages</button>
      ` : ''}
    `;
  }
}

customElements.define('chat-view', ChatView);
