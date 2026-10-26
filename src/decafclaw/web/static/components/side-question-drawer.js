import { LitElement, html, nothing } from 'lit';
import { unsafeHTML } from 'lit/directives/unsafe-html.js';
import { renderMarkdown } from '../lib/markdown.js';
import { DefaultService } from '../lib/api-client/index.js';

function sideQuestionUrl(convId) {
  return `/api/conversations/${encodeURIComponent(convId)}/side-question`;
}


/**
 * Side Question Drawer (issue #1141).
 *
 * Out-of-band ephemeral explanation thread against the current conversation snapshot.
 * Strips tools, does not touch conversation history or agent loop state, and allows
 * copying explanations into the main chat composer without auto-submitting.
 */
export class SideQuestionDrawer extends LitElement {
  static properties = {
    open: { type: Boolean, reflect: true },
    convId: { type: String, attribute: 'conv-id' },
    _thread: { state: true },
    _generating: { state: true },
    _streamingText: { state: true },
    _error: { state: true },
    _statusFeedback: { state: true },
  };

  createRenderRoot() {
    return this;
  }

  constructor() {
    super();
    this.open = false;
    this.convId = '';
    /** @type {Array<{role: 'user'|'assistant', content: string}>} */
    this._thread = [];
    this._generating = false;
    this._streamingText = '';
    this._error = '';
    this._statusFeedback = '';
    /** @type {AbortController|null} */
    this._abortController = null;
    /** @type {Map<string, Array<{role: 'user'|'assistant', content: string}>>} */
    this._threadsByConv = new Map();
  }

  connectedCallback() {
    super.connectedCallback();
    this.setAttribute('role', 'region');
    this.setAttribute('aria-label', 'Side question explanation drawer');
  }

  updated(changedProperties) {
    super.updated(changedProperties);
    if (changedProperties.has('convId')) {
      const prev = changedProperties.get('convId');
      if (prev && prev !== this.convId) {
        if (this._generating) {
          this.stopGeneration();
        }
        this._threadsByConv.set(prev, this._thread);
      }
      this._thread = this._threadsByConv.get(this.convId) || [];
      this._error = '';
      this._streamingText = '';
    }
  }

  openDrawer() {
    this.open = true;
    setTimeout(() => {
      const ta = this.querySelector('textarea');
      if (ta) ta.focus();
    }, 50);
  }

  closeDrawer() {
    this.open = false;
  }

  toggle() {
    if (this.open) {
      this.closeDrawer();
    } else {
      this.openDrawer();
    }
  }

  clearThread() {
    this._thread = [];
    if (this.convId) {
      this._threadsByConv.set(this.convId, []);
    }
    this._error = '';
    this._streamingText = '';
  }

  /**
   * @param {string} text
   */
  async sendQuestion(text) {
    const question = (text || '').trim();
    if (!question || this._generating) return;

    const convId = this.convId || this.#resolveActiveConvId();
    if (!convId) {
      this._error = 'No active conversation selected.';
      return;
    }

    const sideHistory = this._thread.map(m => ({
      role: m.role,
      content: m.content,
    }));

    this._thread = [...this._thread, { role: 'user', content: question }];
    this._generating = true;
    this._streamingText = '';
    this._error = '';
    this._statusFeedback = '';
    this._abortController = new AbortController();

    const textarea = this.querySelector('textarea');
    if (textarea) textarea.value = '';

    this.#scrollToBottom();

    try {
      const url = sideQuestionUrl(convId);
      const resp = await fetch(url, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          question,
          side_history: sideHistory,
        }),
        signal: this._abortController.signal,
      });

      if (!resp.ok) {
        let errMessage = `Request failed (${resp.status})`;
        try {
          const errData = await resp.json();
          if (errData?.error) errMessage = errData.error;
        } catch {
          // Keep default message
        }
        throw new Error(errMessage);
      }

      if (!resp.body) {
        throw new Error('Response has no stream body');
      }

      const reader = resp.body.getReader();
      const signal = this._abortController.signal;
      const abortHandler = () => {
        reader.cancel().catch(() => {});
      };
      signal.addEventListener('abort', abortHandler, { once: true });
      const decoder = new TextDecoder();
      let buffer = '';
      let fullAssistantText = '';

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        let currentEvent = '';
        for (const line of lines) {
          const trimmed = line.trim();
          if (!trimmed) continue;
          if (trimmed.startsWith('event:')) {
            currentEvent = trimmed.slice(6).trim();
          } else if (trimmed.startsWith('data:')) {
            const dataStr = trimmed.slice(5).trim();
            try {
              const data = JSON.parse(dataStr);
              const eventType = data.type || currentEvent;
              if (eventType === 'chunk') {
                const chunkText = data.text || '';
                fullAssistantText += chunkText;
                this._streamingText = fullAssistantText;
                this.#scrollToBottom();
              } else if (eventType === 'done') {
                if (data.text) fullAssistantText = data.text;
              } else if (eventType === 'error') {
                this._error = data.error || 'Side question failed';
              }
            } catch {
              // Ignore non-json lines
            }
          }
        }
      }
      signal.removeEventListener('abort', abortHandler);
      if (signal.aborted) {
        throw new DOMException('The operation was aborted', 'AbortError');
      }

      if (fullAssistantText.trim()) {
        this._thread = [...this._thread, { role: 'assistant', content: fullAssistantText.trim() }];
      }
      this._streamingText = '';
    } catch (err) {
      if (/** @type {any} */ (err).name === 'AbortError') {
        if (this._streamingText.trim()) {
          this._thread = [
            ...this._thread,
            { role: 'assistant', content: this._streamingText.trim() + ' [cancelled]' },
          ];
        }
        this._streamingText = '';
      } else {
        this._error = /** @type {Error} */ (err).message || 'Request failed';
      }
    } finally {
      this._generating = false;
      this._abortController = null;
      if (convId) {
        this._threadsByConv.set(convId, this._thread);
      }
      this.#scrollToBottom();
    }
  }

  stopGeneration() {
    if (this._abortController) {
      this._abortController.abort();
    }
    const convId = this.convId || this.#resolveActiveConvId();
    if (convId) {
      DefaultService.handleCancelSideQuestionApiConversationsIdSideQuestionCancelPost(convId).catch(() => {});
    }
  }

  /**
   * Copy the explanation text to the main chat composer without auto-submitting (criterion C5).
   * @param {string} text
   */
  sendToMain(text) {
    if (!text) return;
    const chatInput = /** @type {any} */ (document.querySelector('chat-input'));
    if (chatInput && typeof chatInput.setText === 'function') {
      chatInput.setText(text);
    }
    this.dispatchEvent(
      new CustomEvent('send-to-main', {
        bubbles: true,
        composed: true,
        detail: { text },
      })
    );
    this._statusFeedback = 'Copied to chat composer!';
    setTimeout(() => {
      this._statusFeedback = '';
    }, 2000);
  }

  #resolveActiveConvId() {
    const chatView = /** @type {any} */ (document.querySelector('chat-view'));
    if (chatView?._convId) return chatView._convId;
    const sidebar = /** @type {any} */ (document.querySelector('conversation-sidebar'));
    if (sidebar?._activeId) return sidebar._activeId;
    return '';
  }

  #scrollToBottom() {
    setTimeout(() => {
      const body = this.querySelector('.drawer-body');
      if (body) body.scrollTop = body.scrollHeight;
    }, 20);
  }

  #handleKeydown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      const ta = this.querySelector('textarea');
      if (ta && ta.value.trim()) {
        this.sendQuestion(ta.value);
      }
    }
  }

  #handleAskClick() {
    const ta = this.querySelector('textarea');
    if (ta && ta.value.trim()) {
      this.sendQuestion(ta.value);
    }
  }

  render() {
    if (!this.open) return nothing;

    return html`
      <div class="drawer-header dc-overlay-header">
        <div class="drawer-title">
          <span>&#x2753; Side Question</span>
          <span class="drawer-badge">explanation only</span>
        </div>
        <div class="drawer-header-actions">
          ${this._statusFeedback ? html`<small class="drawer-feedback">${this._statusFeedback}</small>` : nothing}
          ${this._thread.length > 0 ? html`
            <button
              type="button"
              class="dc-icon-btn"
              @click=${() => this.clearThread()}
              title="Clear thread"
              aria-label="Clear thread"
            >Clear</button>
          ` : nothing}
          <button
            type="button"
            class="dc-overlay-close-x"
            @click=${() => this.closeDrawer()}
            title="Close drawer"
            aria-label="Close drawer"
          >&times;</button>
        </div>
      </div>

      <div class="drawer-body">
        ${this._thread.length === 0 && !this._generating ? html`
          <div class="empty-hint">
            Ask questions about ongoing tasks, tool outputs, or code explanations without interrupting or steering the main agent.
          </div>
        ` : nothing}

        ${this._thread.map(msg => html`
          ${msg.role === 'user' ? html`
            <div class="side-user-msg">${msg.content}</div>
          ` : html`
            <div class="side-assistant-msg">
              <div class="side-content">${unsafeHTML(renderMarkdown(msg.content))}</div>
              <div class="side-actions">
                <button
                  type="button"
                  class="send-to-main-btn outline"
                  @click=${() => this.sendToMain(msg.content)}
                  title="Copy to main chat composer"
                  aria-label="Send to main conversation"
                >&#x21B3; Send to main conversation</button>
              </div>
            </div>
          `}
        `)}

        ${this._streamingText ? html`
          <div class="side-assistant-msg streaming">
            <div class="side-content">${unsafeHTML(renderMarkdown(this._streamingText))}</div>
          </div>
        ` : nothing}

        ${this._generating && !this._streamingText ? html`
          <div class="side-assistant-msg thinking">
            <small>Thinking...</small>
          </div>
        ` : nothing}

        ${this._error ? html`
          <div class="side-error">${this._error}</div>
        ` : nothing}
      </div>

      <div class="drawer-footer">
        <textarea
          placeholder="Ask an explanation question..."
          rows="2"
          ?disabled=${this._generating}
          @keydown=${this.#handleKeydown}
        ></textarea>
        <div class="drawer-footer-actions">
          ${this._generating ? html`
            <button
              type="button"
              class="stop-btn"
              @click=${() => this.stopGeneration()}
              aria-label="Stop generation"
            >&#9632; Stop</button>
          ` : nothing}
          <button
            type="button"
            class="ask-btn"
            ?disabled=${this._generating}
            @click=${this.#handleAskClick}
            aria-label="Ask side question"
          >Ask</button>
        </div>
      </div>
    `;
  }
}

customElements.define('side-question-drawer', SideQuestionDrawer);
