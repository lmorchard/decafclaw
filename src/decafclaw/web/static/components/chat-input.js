import { LitElement, html, nothing } from 'lit';
import { uploadFile } from '../lib/upload-client.js';

/** A `/` or `!` command token filling the current line up to the caret. */
const TRIGGER_RE = /^([/!])(\S*)$/;

/**
 * Score `name` as an ordered-subsequence match for `query`.
 *
 * Returns `null` when the query's characters do not appear in `name` in
 * order — "in order" is the point: every character of `eod` occurs in
 * `mcp__demo__summarize`, but not left to right, so it is not a match.
 * Higher scores rank first; runs of adjacent characters and matches near the
 * start of the name are what earn them.
 *
 * @param {string} query @param {string} name @returns {number|null}
 */
export function commandMatchScore(query, name) {
  const q = query.toLowerCase();
  const n = name.toLowerCase();
  let score = 0;
  let from = 0;
  let prev = -1;
  for (const ch of q) {
    const at = n.indexOf(ch, from);
    if (at === -1) return null;
    if (at === prev + 1) score += 2;   // contiguous run
    if (at === 0) score += 2;          // anchored at the name's start
    score -= at - from;                // how much had to be skipped
    prev = at;
    from = at + 1;
  }
  return score;
}

export class ChatInput extends LitElement {
  static properties = {
    disabled: { type: Boolean },
    busy: { type: Boolean },
    placeholder: { type: String },
    convId: { type: String, attribute: 'conv-id' },
    // {name, description, argument_hint} entries from the server's command_list.
    commands: { type: Array },
    _pendingAttachments: { type: Array, state: true },
    _dragOver: { type: Boolean, state: true },
    _trigger: { type: Object, state: true },
    _highlight: { type: Number, state: true },
  };

  createRenderRoot() { return this; }

  updated(changedProperties) {
    super.updated(changedProperties);
    const triggerOpened = changedProperties.has('_trigger') && !changedProperties.get('_trigger') && Boolean(this._trigger);
    if (changedProperties.has('_highlight') || triggerOpened) {
      const highlighted = this.querySelector('.command-menu-item.highlighted');
      highlighted?.scrollIntoView?.({ block: 'nearest' });
    }
  }

  /** Escape keeps the menu shut for the token it dismissed. */
  #dismissed = false;

  constructor() {
    super();
    this.disabled = false;
    this.busy = false;
    this.placeholder = 'Type a message...';
    this.convId = '';
    /** @type {{name: string, description: string, argument_hint: string}[]} */
    this.commands = [];
    this._pendingAttachments = [];
    this._dragOver = false;
    /** @type {{prefix: string, query: string}|null} */
    this._trigger = null;
    this._highlight = 0;
  }

  /** Focus the textarea. */
  focus() {
    this.querySelector('textarea')?.focus();
  }

  /** Add files from an external drop target. @param {FileList|File[]} files */
  addFiles(files) {
    this.#handleFiles(files);
  }

  // -- Command autocomplete -----------------------------------------------------

  /**
   * The command token the caret sits in, read from the live textarea.
   *
   * Scoped to the current *line*, not the whole value: `hello\n/mc` is a
   * trigger, `see /mc` is not. Recomputed on demand rather than cached so a
   * commit always rewrites what is actually in the box.
   *
   * @returns {{textarea: HTMLTextAreaElement, caret: number, lineStart: number,
   *            prefix: string, query: string}|null}
   */
  #triggerContext() {
    const ta = /** @type {HTMLTextAreaElement|null} */ (this.querySelector('textarea'));
    if (!ta) return null;
    const caret = ta.selectionStart ?? ta.value.length;
    const before = ta.value.slice(0, caret);
    const lineStart = before.lastIndexOf('\n') + 1;
    const match = TRIGGER_RE.exec(before.slice(lineStart));
    if (!match) return null;
    return { textarea: ta, caret, lineStart, prefix: match[1], query: match[2] };
  }

  /**
   * Recompute the menu's open/closed state from the live caret.
   *
   * Runs on `input` and also on `keyup` / `click`, so a caret moved without
   * typing (arrow keys, a mouse click) closes a menu that no longer belongs to
   * where the caret is. Resets the highlight ONLY when the token actually
   * changed: keyup fires after every ArrowDown/ArrowUp keydown, so resetting
   * unconditionally would snap the highlight back to 0 on each press and break
   * arrow navigation in a real browser — invisibly, since a synthetic `press()`
   * in a test dispatches no keyup.
   */
  #syncMenu() {
    const ctx = this.#triggerContext();
    if (!ctx) {
      // Left the token entirely — a later `/` gets a fresh menu.
      this.#dismissed = false;
      this._trigger = null;
      return;
    }
    if (this.#dismissed) return;
    const changed = this._trigger?.prefix !== ctx.prefix
      || this._trigger?.query !== ctx.query;
    this._trigger = { prefix: ctx.prefix, query: ctx.query };
    if (changed) this._highlight = 0;
  }

  /** Commands matching the open trigger, best first. @returns {any[]} */
  #matchingCommands() {
    if (!this._trigger) return [];
    const scored = [];
    for (const cmd of this.commands || []) {
      const score = commandMatchScore(this._trigger.query, cmd?.name || '');
      if (score !== null) scored.push({ cmd, score });
    }
    // Array.prototype.sort is stable, so an empty query (every score 0)
    // preserves the server's ordering.
    scored.sort((a, b) => b.score - a.score);
    return scored.map((s) => s.cmd);
  }

  /** Replace the trigger token with the chosen command. @param {any} cmd */
  #commitCommand(cmd) {
    const ctx = this.#triggerContext();
    if (!ctx || !cmd) return;
    const { textarea } = ctx;
    const insert = `${ctx.prefix}${cmd.name} `;
    textarea.value = textarea.value.slice(0, ctx.lineStart)
      + insert + textarea.value.slice(ctx.caret);
    const caret = ctx.lineStart + insert.length;
    textarea.selectionStart = textarea.selectionEnd = caret;
    this.#closeMenu();
    textarea.focus();
  }

  #closeMenu() {
    this.#dismissed = false;
    this._trigger = null;
  }

  /** @param {KeyboardEvent} e */
  #handleKeydown(e) {
    // Gate on the LIVE caret, not on `_trigger` alone. `_trigger` is only
    // recomputed on `input`, so a caret moved by ArrowLeft/ArrowRight or a
    // mouse click leaves it stale: with `hello /mc` and the caret clicked back
    // to after `hello`, the cache still says a `/mc` token is open, so Tab
    // would be swallowed here and then do nothing (`#commitCommand`
    // recomputes the context and early-returns) — you could not tab out of the
    // composer. Asking `#triggerContext()` makes the interception decision use
    // the same source of truth the commit does.
    const matches = this.#triggerContext() ? this.#matchingCommands() : [];
    if (matches.length) {
      const highlight = Math.min(this._highlight, matches.length - 1);
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        this._highlight = (highlight + 1) % matches.length;
        return;
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault();
        this._highlight = (highlight - 1 + matches.length) % matches.length;
        return;
      }
      if (e.key === 'Tab') {
        e.preventDefault();
        this.#commitCommand(matches[highlight]);
        return;
      }
      if (e.key === 'Escape') {
        e.preventDefault();
        this.#dismissed = true;
        this._trigger = null;
        return;
      }
      // Enter deliberately falls through: it sends, it never commits.
    }

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
    this.#closeMenu();
    this.#clearAttachments();
  }

  /** Auto-resize textarea and re-evaluate the command menu */
  #handleInput(e) {
    const textarea = e.target;
    textarea.style.height = 'auto';
    textarea.style.height = Math.min(textarea.scrollHeight, 128) + 'px';
    this.#syncMenu();
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
    const matches = this.#matchingCommands();
    const highlight = Math.min(this._highlight, matches.length - 1);
    return html`
      ${matches.length ? html`
        <div class="command-menu" role="listbox">
          ${matches.map((cmd, i) => html`
            <div class="command-menu-item ${i === highlight ? 'highlighted' : ''}"
              role="option"
              aria-selected=${i === highlight}
              data-command=${cmd.name}
              @mousedown=${(/** @type {MouseEvent} */ e) => {
                e.preventDefault();  // keep focus in the textarea
                this.#commitCommand(cmd);
              }}>
              <span class="command-menu-name">${cmd.name}</span>
              ${cmd.argument_hint
                ? html`<span class="command-menu-hint">${cmd.argument_hint}</span>`
                : nothing}
              ${cmd.description
                ? html`<span class="command-menu-desc">${cmd.description}</span>`
                : nothing}
            </div>
          `)}
        </div>
      ` : nothing}
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
          <button type="button" class="attach-btn" @click=${this.#openFilePicker}
            title="Attach file">&#128206;</button>
        ` : nothing}
        <textarea
          placeholder=${this.placeholder}
          rows="1"
          ?disabled=${this.disabled}
          @keydown=${this.#handleKeydown}
          @input=${this.#handleInput}
          @keyup=${this.#syncMenu}
          @click=${this.#syncMenu}
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
