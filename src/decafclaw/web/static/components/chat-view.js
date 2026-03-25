import { LitElement, html, nothing } from 'lit';
import './chat-message.js';
import './confirm-view.js';

export class ChatView extends LitElement {
  static properties = {
    store: { type: Object, attribute: false },
    _messages: { type: Array, state: true },
    _streamingText: { type: String, state: true },
    _busy: { type: Boolean, state: true },
    _toolStatus: { type: String, state: true },
    _pendingConfirms: { type: Array, state: true },
    _convId: { type: String, state: true },
    _showScrollBtn: { type: Boolean, state: true },
    _hasMore: { type: Boolean, state: true },
  };

  createRenderRoot() { return this; }

  constructor() {
    super();
    this.store = null;
    this._messages = [];
    this._streamingText = '';
    this._busy = false;
    this._toolStatus = null;
    this._showScrollBtn = false;
    this._pendingConfirms = [];
    this._convId = null;
    this._scrollOnLoad = false;
    this._hasMore = false;
    this._loadingMore = false;
    this._savedScrollHeight = 0;
  }

  connectedCallback() {
    super.connectedCallback();
    this._onStoreChange = () => {
      const prevConvId = this._convId;
      const prevMsgCount = this._messages.length;
      this._messages = this.store?.currentMessages || [];
      this._streamingText = this.store?.streamingText || '';
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
    this.store?.addEventListener('change', this._onStoreChange);
    // Track scroll position + auto-load older messages
    this.addEventListener('scroll', () => {
      this._showScrollBtn = !this.#isNearBottom();
      if (this._hasMore && !this._loadingMore && this.scrollTop < 100) {
        this.#handleLoadMore();
      }
    });
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    this.store?.removeEventListener('change', this._onStoreChange);
  }

  updated(changedProps) {
    if (changedProps.has('store') && this.store) {
      this.store.addEventListener('change', this._onStoreChange);
      this._onStoreChange();
    }
  }

  #scrollToBottom() {
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

  render() {
    if (!this._convId) {
      return html`<div class="empty-state">Select or create a conversation to start chatting.</div>`;
    }

    return html`
      ${this._loadingMore ? html`
        <div class="load-more"><small>Loading...</small></div>
      ` : nothing}

      ${this._messages.map(m => html`
        <chat-message
          .role=${m.role}
          .content=${m.content || ''}
          .tool=${m.tool || ''}
          .display_short_text=${m.display_short_text || ''}
          .toolCalls=${m.tool_calls || null}
          .toolCallId=${m.tool_call_id || ''}
          .usage=${m.usage || null}
          .timestamp=${m.timestamp || ''}
        ></chat-message>
      `)}

      ${this._streamingText ? html`
        <chat-message
          .role=${'assistant'}
          .content=${this._streamingText}
          .streaming=${true}
        ></chat-message>
      ` : ''}

      ${this._busy && !this._streamingText && !this._toolStatus ? html`
        <div class="thinking-indicator">
          <span></span><span></span><span></span>
        </div>
      ` : ''}

      <confirm-view .store=${this.store} .confirms=${this._pendingConfirms}></confirm-view>

      ${this._showScrollBtn ? html`
        <button
          class="scroll-to-bottom"
          @click=${() => this.#scrollToBottom()}
        >\u2193 New messages</button>
      ` : ''}
    `;
  }
}

customElements.define('chat-view', ChatView);
