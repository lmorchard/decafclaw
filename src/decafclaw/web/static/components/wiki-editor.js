/**
 * Wiki editor — WYSIWYG markdown editor wrapping Milkdown with auto-save.
 *
 * Properties:
 *   page (String) — page name for API path
 *   content (String) — initial markdown
 *   modified (Number) — file mtime for conflict detection
 *   saveEndpoint (String) — API prefix, default '/api/vault/'
 *
 * Behavior:
 *   Dispatches a 'close' CustomEvent when the public close() method is called.
 *   Note: This component does not render a Done/Close button; closing is controlled
 *   by the host application.
 */

import { LitElement, html, nothing } from 'lit';
import {
  Editor, rootCtx, defaultValueCtx,
  commonmark, gfm, history, listener, listenerCtx, clipboard,
  callCommand, getMarkdown, replaceAll,
  toggleStrongCommand, toggleEmphasisCommand, toggleStrikethroughCommand,
  toggleInlineCodeCommand, createCodeBlockCommand,
  wrapInBulletListCommand, wrapInOrderedListCommand, wrapInBlockquoteCommand,
  wrapInHeadingCommand, insertHrCommand,
} from '@milkdown/kit';
import { wikiLinkPlugin } from '../lib/milkdown-wiki-link.js';

/**
 * @typedef {Object} ToolbarItem
 * @property {string} label
 * @property {string} title
 * @property {*} cmd
 */

/** @type {Array<ToolbarItem | null>} */
const TOOLBAR = [
  { label: 'B', title: 'Bold', cmd: toggleStrongCommand },
  { label: 'I', title: 'Italic', cmd: toggleEmphasisCommand },
  { label: 'S', title: 'Strikethrough', cmd: toggleStrikethroughCommand },
  null,
  { label: 'H', title: 'Heading', cmd: wrapInHeadingCommand },
  { label: '\u2022', title: 'Bullet List', cmd: wrapInBulletListCommand },
  { label: '1.', title: 'Ordered List', cmd: wrapInOrderedListCommand },
  null,
  { label: '`', title: 'Inline Code', cmd: toggleInlineCodeCommand },
  { label: '```', title: 'Code Block', cmd: createCodeBlockCommand },
  { label: '>', title: 'Blockquote', cmd: wrapInBlockquoteCommand },
  { label: '\u2014', title: 'Horizontal Rule', cmd: insertHrCommand },
];

const STATUS_LABELS = /** @type {Record<string, string>} */ ({
  idle: '',
  editing: 'Editing...',
  saving: 'Saving...',
  saved: 'Saved',
  error: 'Error',
  conflict: 'Conflict',
});

export class WikiEditor extends LitElement {
  static properties = {
    page: { type: String },
    content: { type: String },
    modified: { type: Number },
    saveEndpoint: { type: String, attribute: 'save-endpoint' },
    /** Extra template content rendered at the right end of the toolbar */
    toolbarExtra: { attribute: false },
    _status: { state: true },
    _error: { state: true },
  };

  /** @type {import('@milkdown/kit').Editor | null} */
  #editor = null;
  /** @type {ReturnType<typeof setTimeout> | null} */
  #saveTimer = null;
  /** @type {string} */
  #lastSavedContent = '';
  /** @type {string} */
  #currentMarkdown = '';

  createRenderRoot() { return this; }

  constructor() {
    super();
    this.page = '';
    this.content = '';
    /** @type {number} */ this.modified = 0;
    this.saveEndpoint = '/api/vault/';
    /** @type {import('lit').TemplateResult|null} Extra content for toolbar right side */
    this.toolbarExtra = null;
    /** @type {'idle'|'editing'|'saving'|'saved'|'error'|'conflict'} */
    this._status = 'idle';
    /** @type {string} */ this._error = '';
  }

  async firstUpdated() {
    const root = this.querySelector('.milkdown-root');
    if (!root) return;

    this.#editor = await Editor.make()
      .config(ctx => {
        ctx.set(rootCtx, root);
        ctx.set(defaultValueCtx, this.content || '');
        ctx.get(listenerCtx).markdownUpdated((_ctx, md, prev) => {
          if (md !== prev) this.#onContentChange(md);
        });
      })
      .use(commonmark)
      .use(gfm)
      .use(history)
      .use(listener)
      .use(clipboard)
      .use(wikiLinkPlugin)
      .create();

    this.#lastSavedContent = this.content || '';
    this.#currentMarkdown = this.content || '';

    this.addEventListener('keydown', (/** @type {KeyboardEvent} */ e) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 's') {
        e.preventDefault();
        this.#flushSave();
      }
    });

    root.addEventListener('dblclick', (/** @type {MouseEvent} */ e) => {
      const link = /** @type {HTMLElement} */ (e.target).closest('a.wiki-link');
      if (!link) return;
      const page = link.getAttribute('data-wiki-page') || link.textContent?.trim();
      if (!page) return;
      e.preventDefault();
      this.dispatchEvent(new CustomEvent('wiki-navigate', {
        detail: { page },
        bubbles: true,
        composed: true,
      }));
    });

    this.querySelector('.milkdown-root')?.addEventListener('focusout', (/** @type {FocusEvent} */ e) => {
      if (!this.contains(/** @type {Node|null} */(e.relatedTarget))) {
        this.#flushSave();
      }
    });
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    this.#flushSave();
    if (this.#editor) {
      this.#editor.destroy();
      this.#editor = null;
    }
  }

  /** @param {string} md */
  #onContentChange(md) {
    this.#currentMarkdown = md;
    // Don't resume auto-save while in conflict state — user must resolve first
    if (this._status === 'conflict') return;
    this._status = 'editing';
    this.#scheduleSave();
  }

  #scheduleSave() {
    // Don't schedule auto-save while in conflict state
    if (this._status === 'conflict') return;
    if (this.#saveTimer != null) clearTimeout(this.#saveTimer);
    this.#saveTimer = setTimeout(() => this.#save(), 1000);
  }

  async #save() {
    this.#saveTimer = null;
    const content = this.#currentMarkdown;
    if (content === this.#lastSavedContent) {
      if (this._status === 'editing') this._status = 'idle';
      return;
    }

    this._status = 'saving';
    try {
      const res = await fetch(
        `${this.saveEndpoint}${encodeURIComponent(this.page)}`,
        {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ content, modified: this.modified }),
        },
      );
      if (res.ok) {
        const data = await res.json();
        const newModified = data.modified ?? this.modified;
        this.modified = newModified;
        this.#lastSavedContent = content;
        this._status = 'saved';
        this._error = '';
        this.dispatchEvent(new CustomEvent('saved', {
          detail: { modified: newModified, page: this.page },
          bubbles: true,
          composed: true,
        }));
      } else if (res.status === 409) {
        this._status = 'conflict';
        this._error = 'Page was modified externally.';
      } else {
        this._status = 'error';
        this._error = `Save failed (${res.status})`;
      }
    } catch (e) {
      this._status = 'error';
      this._error = 'Save failed (network error)';
    }
  }

  /** Flush any pending save immediately. Returns a promise that resolves when done. */
  async flushSave() {
    if (this.#saveTimer != null) {
      clearTimeout(this.#saveTimer);
      this.#saveTimer = null;
    }
    if (this.#currentMarkdown !== this.#lastSavedContent) {
      await this.#save();
    }
  }

  /** @deprecated Use flushSave() — kept for internal use */
  #flushSave() {
    if (this.#saveTimer != null) {
      clearTimeout(this.#saveTimer);
      this.#saveTimer = null;
    }
    if (this.#currentMarkdown !== this.#lastSavedContent) {
      this.#save();
    }
  }

  async #reload() {
    try {
      const res = await fetch(`/api/vault/${encodeURIComponent(this.page)}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      this.content = data.content;
      this.modified = data.modified;
      if (this.#editor) {
        this.#editor.action(replaceAll(data.content));
      }
      this.#lastSavedContent = data.content;
      this.#currentMarkdown = data.content;
      this._status = 'saved';
      this._error = '';
    } catch (e) {
      this._error = `Reload failed: ${/** @type {Error} */(e).message}`;
    }
  }

  async #forceSave() {
    // Send with modified=null to skip server-side mtime check
    this._status = 'saving';
    const savedModified = this.modified;
    try {
      const content = this.#currentMarkdown;
      const res = await fetch(
        `${this.saveEndpoint}${encodeURIComponent(this.page)}`,
        {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ content }),  // no modified field — server skips check
        },
      );
      if (res.ok) {
        const data = await res.json();
        this.modified = data.modified ?? savedModified;
        this.#lastSavedContent = content;
        this._status = 'saved';
        this._error = '';
        this.dispatchEvent(new CustomEvent('saved', {
          detail: { modified: this.modified, page: this.page },
          bubbles: true,
          composed: true,
        }));
      } else {
        this._status = 'error';
        this._error = `Force save failed (${res.status})`;
      }
    } catch (e) {
      this._status = 'error';
      this._error = 'Force save failed (network error)';
    }
  }

  /** Flush pending save and dispatch close event. */
  async close() {
    await this.flushSave();
    this.dispatchEvent(new CustomEvent('close', { bubbles: true, composed: true }));
  }

  /**
   * Execute a Milkdown command via callCommand.
   * @param {*} cmd — command slice (e.g. toggleStrongCommand)
   */
  #execCommand(cmd) {
    if (!this.#editor) return;
    this.#editor.action(ctx => {
      callCommand(cmd.key)(ctx);
    });
  }

  render() {
    const statusText = STATUS_LABELS[this._status] || '';

    return html`
      <div class="wiki-editor-container">
        <div class="wiki-editor-toolbar">
          ${TOOLBAR.map(item =>
            item === null
              ? html`<span class="wiki-editor-separator"></span>`
              : html`<button
                  class="wiki-editor-toolbar-btn"
                  title=${item.title}
                  @click=${() => this.#execCommand(item.cmd)}
                >${item.label}</button>`
          )}
          <span class="wiki-editor-spacer"></span>
          <span class="wiki-editor-status ${this._status}">${statusText}</span>
          ${this.toolbarExtra || nothing}
        </div>
        ${this._status === 'conflict'
          ? html`<div class="wiki-editor-conflict">
              <span>Page was modified externally.</span>
              <button @click=${() => this.#reload()}>Reload</button>
              <button @click=${() => this.#forceSave()}>Overwrite</button>
            </div>`
          : this._status === 'error'
            ? html`<div class="wiki-editor-error">${this._error}</div>`
            : nothing
        }
        <div class="milkdown-root"></div>
      </div>
    `;
  }
}

customElements.define('wiki-editor', WikiEditor);
