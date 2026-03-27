import { LitElement, html, nothing } from 'lit';
import { uploadFile } from '../lib/upload-client.js';

export class ChatInput extends LitElement {
  static properties = {
    disabled: { type: Boolean },
    busy: { type: Boolean },
    placeholder: { type: String },
    convId: { type: String, attribute: 'conv-id' },
    _pendingAttachments: { type: Array, state: true },
    _dragOver: { type: Boolean, state: true },
  };

  createRenderRoot() { return this; }

  constructor() {
    super();
    this.disabled = false;
    this.busy = false;
    this.placeholder = 'Type a message...';
    this.convId = '';
    this._pendingAttachments = [];
    this._dragOver = false;
  }

  /** Focus the textarea. */
  focus() {
    this.querySelector('textarea')?.focus();
  }

  /** Add files from an external drop target. @param {FileList|File[]} files */
  addFiles(files) {
    this.#handleFiles(files);
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
    const hasAttachments = this._pendingAttachments.length > 0;
    if ((!text && !hasAttachments) || this.disabled) return;

    const attachments = this._pendingAttachments.map(a => {
      const att = { filename: a.filename, mime_type: a.mime_type };
      if (a.path) att.path = a.path;      // already uploaded
      if (a.file) att.file = a.file;       // needs upload after conv creation
      return att;
    });

    this.dispatchEvent(new CustomEvent('send', {
      detail: { text: text || '', attachments },
      bubbles: true,
      composed: true,
    }));
    textarea.value = '';
    textarea.style.height = 'auto';
    this.#clearAttachments();
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

  // -- File handling ----------------------------------------------------------

  /** @param {FileList|File[]} files */
  async #handleFiles(files) {
    if (this.disabled) return;
    for (const file of Array.from(files)) {
      const previewUrl = file.type.startsWith('image/') ? URL.createObjectURL(file) : null;
      if (this.convId) {
        // Upload immediately when conversation exists
        try {
          const result = await uploadFile(this.convId, file);
          this._pendingAttachments = [...this._pendingAttachments, { ...result, previewUrl }];
        } catch (err) {
          console.warn('Upload failed:', err.message);
          if (previewUrl) URL.revokeObjectURL(previewUrl);
        }
      } else {
        // No conversation yet — hold the File object for later upload
        this._pendingAttachments = [...this._pendingAttachments, {
          filename: file.name,
          mime_type: file.type || 'application/octet-stream',
          file,
          previewUrl,
        }];
      }
    }
  }

  /** @param {number} index */
  #removeAttachment(index) {
    const removed = this._pendingAttachments[index];
    if (removed?.previewUrl) URL.revokeObjectURL(removed.previewUrl);
    this._pendingAttachments = this._pendingAttachments.filter((_, i) => i !== index);
  }

  #clearAttachments() {
    for (const a of this._pendingAttachments) {
      if (a.previewUrl) URL.revokeObjectURL(a.previewUrl);
    }
    this._pendingAttachments = [];
  }

  #handleFileInput(e) {
    const input = /** @type {HTMLInputElement} */ (e.target);
    if (input.files?.length) this.#handleFiles(input.files);
    input.value = ''; // reset so re-selecting same file works
  }

  #openFilePicker() {
    /** @type {HTMLInputElement|null} */ (this.querySelector('#file-input'))?.click();
  }

  // -- Drag and drop ----------------------------------------------------------

  /** @param {DragEvent} e */
  #handleDragOver(e) {
    e.preventDefault();
    this._dragOver = true;
  }

  #handleDragLeave() {
    this._dragOver = false;
  }

  /** @param {DragEvent} e */
  #handleDrop(e) {
    e.preventDefault();
    this._dragOver = false;
    if (e.dataTransfer?.files?.length) this.#handleFiles(e.dataTransfer.files);
  }

  // -- Clipboard paste --------------------------------------------------------

  /** @param {ClipboardEvent} e */
  #handlePaste(e) {
    const files = e.clipboardData?.files;
    if (files?.length) {
      e.preventDefault();
      this.#handleFiles(files);
    }
    // Otherwise let normal text paste proceed
  }

  render() {
    const hasAttachments = this._pendingAttachments.length > 0;
    return html`
      ${hasAttachments ? html`
        <div class="attachment-preview-strip">
          ${this._pendingAttachments.map((a, i) => html`
            <div class="attachment-preview">
              ${a.previewUrl
                ? html`<img src=${a.previewUrl} class="attachment-thumb" alt=${a.filename}>`
                : html`<span class="attachment-file-icon">${a.filename}</span>`
              }
              <button class="attachment-remove" @click=${() => this.#removeAttachment(i)}
                title="Remove">&times;</button>
            </div>
          `)}
        </div>
      ` : nothing}
      <div class="input-row ${this._dragOver ? 'drag-over' : ''}"
        @dragover=${this.#handleDragOver}
        @dragleave=${this.#handleDragLeave}
        @drop=${this.#handleDrop}>
        <input type="file" id="file-input" multiple hidden
          @change=${this.#handleFileInput}>
        ${!this.disabled ? html`
          <button class="attach-btn" @click=${this.#openFilePicker}
            title="Attach file">&#128206;</button>
        ` : nothing}
        <textarea
          placeholder=${this.placeholder}
          rows="1"
          ?disabled=${this.disabled}
          @keydown=${this.#handleKeydown}
          @input=${this.#handleInput}
          @paste=${this.#handlePaste}
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
