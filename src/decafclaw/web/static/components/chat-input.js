import { LitElement, html } from 'lit';

export class ChatInput extends LitElement {
  static properties = {
    disabled: { type: Boolean },
    busy: { type: Boolean },
  };

  createRenderRoot() { return this; }

  constructor() {
    super();
    this.disabled = false;
    this.busy = false;
  }

  /** Focus the textarea. */
  focus() {
    this.querySelector('textarea')?.focus();
  }

  /** @param {KeyboardEvent} e */
  #handleKeydown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      this.#handleSend();
    } else if (e.key === 'Escape' && this.busy) {
      e.preventDefault();
      this.#handleStop();
    }
  }

  #handleSend() {
    const textarea = this.querySelector('textarea');
    const text = textarea?.value?.trim();
    if (!text || this.disabled) return;

    this.dispatchEvent(new CustomEvent('send', {
      detail: { text },
      bubbles: true,
      composed: true,
    }));
    textarea.value = '';
    textarea.style.height = 'auto';
  }

  /** Auto-resize textarea */
  #handleInput(e) {
    const textarea = e.target;
    textarea.style.height = 'auto';
    textarea.style.height = Math.min(textarea.scrollHeight, 128) + 'px';
  }

  #handleStop() {
    this.dispatchEvent(new CustomEvent('stop', { bubbles: true, composed: true }));
  }

  render() {
    return html`
      <div class="input-row">
        <textarea
          placeholder="Type a message..."
          rows="1"
          ?disabled=${this.disabled}
          @keydown=${this.#handleKeydown}
          @input=${this.#handleInput}
        ></textarea>
        ${this.busy ? html`
          <button class="stop-btn" @click=${this.#handleStop}>&#9632; Stop</button>
        ` : ''}
        <button @click=${this.#handleSend} ?disabled=${this.disabled}>Send</button>
      </div>
    `;
  }
}

customElements.define('chat-input', ChatInput);
