/**
 * File page viewer/editor — renders a workspace file as either a text editor
 * (CodeMirror via <file-editor>), an inline image preview, or a binary
 * download link, depending on `kind`.
 *
 * Mirrors the shape of <wiki-page> so styling is parallel (file-page-toolbar,
 * file-page-body, rename bar, breadcrumbs, close button).
 */

import { LitElement, html, nothing } from 'lit';
import { encodePagePath } from '../lib/utils.js';
import './file-editor.js';

/**
 * @param {number} ts — Unix timestamp (seconds)
 * @returns {string}
 */
function formatDate(ts) {
  if (!ts) return '';
  const d = new Date(ts * 1000);
  return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
}

export class FilePage extends LitElement {
  static properties = {
    path: { type: String },
    kind: { type: String },
    readonly: { type: Boolean },
    standalone: { type: Boolean },
    _content: { state: true },
    _modified: { state: true },
    _editing: { state: true },
    _loading: { state: true },
    _error: { state: true },
    _renaming: { state: true },
    _renameValue: { state: true },
    _renameError: { state: true },
    _saveStatus: { state: true },
    _saveError: { state: true },
    _conflict: { state: true },
  };

  createRenderRoot() { return this; }

  constructor() {
    super();
    /** @type {string} */ this.path = '';
    /** @type {string} */ this.kind = 'unknown';
    this.readonly = false;
    this.standalone = false;

    /** @type {string} */ this._content = '';
    /** @type {number} */ this._modified = 0;
    this._editing = true;
    this._loading = false;
    /** @type {string} */ this._error = '';

    this._renaming = false;
    /** @type {string} */ this._renameValue = '';
    /** @type {string} */ this._renameError = '';

    /** @type {'' | 'saving' | 'saved' | 'error'} */ this._saveStatus = '';
    /** @type {string} */ this._saveError = '';
    this._conflict = false;
  }

  /** @param {Map<string, any>} changed */
  willUpdate(changed) {
    if (changed.has('path') && this.path) {
      // Reset per-file state
      this._renaming = false;
      this._renameError = '';
      this._saveStatus = '';
      this._saveError = '';
      this._conflict = false;
      if (this.kind === 'text') {
        this._editing = !this.readonly;
        this.#fetchFile();
      } else {
        this._editing = false;
        this._loading = false;
        this._content = '';
        this._modified = 0;
        this._error = '';
      }
    } else if (changed.has('kind') || changed.has('readonly')) {
      // Kind/readonly toggled for same path — re-evaluate edit mode
      if (this.kind === 'text' && this.readonly) this._editing = false;
    }
  }

  async #fetchFile() {
    this._loading = true;
    this._error = '';
    this._content = '';
    try {
      const res = await fetch('/api/workspace-file/' + encodePagePath(this.path));
      if (!res.ok) {
        if (res.status === 403) this._error = `File "${this.path}" is not readable.`;
        else if (res.status === 404) this._error = `File "${this.path}" not found.`;
        else if (res.status === 415) this._error = `File "${this.path}" is not a text file.`;
        else this._error = `Error loading file (${res.status}).`;
        return;
      }
      const data = await res.json();
      this._content = data.content ?? '';
      this._modified = data.modified ?? 0;
      // Trust server readonly if it tightens ours
      if (data.readonly === true) this.readonly = true;
      // Clear conflict after successful reload; editor remounts via loading swap.
      this._conflict = false;
    } catch {
      this._error = 'Failed to load file.';
    } finally {
      this._loading = false;
    }
  }

  _close() {
    this.dispatchEvent(new CustomEvent('file-close', { bubbles: true, composed: true }));
  }

  /** @param {string} folderPath */
  #navigateFolder(folderPath) {
    this.dispatchEvent(new CustomEvent('file-navigate-folder', {
      detail: { folder: folderPath },
      bubbles: true,
      composed: true,
    }));
  }

  // ── Save status from <file-editor> ─────────────────────────────────────
  #onSaving() {
    this._saveStatus = 'saving';
    this._saveError = '';
  }

  /** @param {CustomEvent} e */
  #onSaved(e) {
    this._saveStatus = 'saved';
    this._saveError = '';
    if (e.detail && typeof e.detail.modified === 'number') {
      this._modified = e.detail.modified;
    }
  }

  /** @param {CustomEvent} _e */
  #onConflict(_e) {
    this._saveStatus = 'error';
    this._saveError = 'File changed on disk.';
    this._conflict = true;
  }

  /** @param {CustomEvent} e */
  #onEditorError(e) {
    this._saveStatus = 'error';
    const d = e.detail || {};
    this._saveError = d.message ? `Save failed: ${d.message}` : `Save failed (${d.status ?? ''}).`;
  }

  async #reloadFromDisk() {
    await this.#fetchFile();
  }

  // ── Rename ─────────────────────────────────────────────────────────────
  #startRename() {
    this._renaming = true;
    this._renameValue = this.path;
    this._renameError = '';
  }

  #cancelRename() {
    this._renaming = false;
    this._renameError = '';
  }

  /** @param {KeyboardEvent} e */
  #handleRenameKey(e) {
    if (e.key === 'Enter') {
      e.preventDefault();
      void this.#submitRename();
    } else if (e.key === 'Escape') {
      this.#cancelRename();
    }
  }

  async #submitRename() {
    const newPath = this._renameValue.trim();
    if (!newPath || newPath === this.path) {
      this.#cancelRename();
      return;
    }
    try {
      // Flush editor before moving the file
      /** @type {import('./file-editor.js').FileEditor|null} */
      const editor = this.querySelector('file-editor');
      if (editor && typeof editor.flushSave === 'function') {
        await editor.flushSave();
      }
      const url = '/api/workspace/' + encodePagePath(this.path)
        + '?rename_to=' + encodeURIComponent(newPath);
      const res = await fetch(url, { method: 'PUT' });
      if (res.status === 409) {
        this._renameError = 'A file already exists at that path.';
        return;
      }
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        this._renameError = data.error || `Rename failed (${res.status})`;
        return;
      }
      this._renaming = false;
      this._renameError = '';
      this.dispatchEvent(new CustomEvent('file-open', {
        detail: { path: newPath },
        bubbles: true,
        composed: true,
      }));
    } catch {
      this._renameError = 'Rename failed.';
    }
  }

  // ── Delete ─────────────────────────────────────────────────────────────
  async #deleteFile() {
    if (!confirm(`Delete "${this.path}"?`)) return;
    try {
      const res = await fetch('/api/workspace/' + encodePagePath(this.path), {
        method: 'DELETE',
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        alert(data.error || `Delete failed (${res.status})`);
        return;
      }
      window.dispatchEvent(new CustomEvent('workspace-file-deleted', {
        detail: { path: this.path },
      }));
      this._close();
    } catch {
      alert('Delete failed.');
    }
  }

  #toggleMode() {
    if (this.kind !== 'text' || this.readonly) return;
    this._editing = !this._editing;
  }

  // ── Render helpers ─────────────────────────────────────────────────────
  #renderBreadcrumbs() {
    const parts = this.path.split('/');
    const fileName = parts[parts.length - 1];
    const folderParts = parts.slice(0, -1);

    if (this._renaming) {
      return html`
        <div class="file-rename-bar">
          <input
            class="file-rename-input"
            aria-label="New file path"
            .value=${this._renameValue}
            @input=${(/** @type {InputEvent} */ e) => { this._renameValue = /** @type {HTMLInputElement} */ (e.target).value; }}
            @keydown=${(/** @type {KeyboardEvent} */ e) => this.#handleRenameKey(e)}
          />
          <button class="file-rename-ok" @click=${() => this.#submitRename()} title="Confirm rename" aria-label="Confirm rename">✓</button>
          <button class="file-rename-cancel" @click=${() => this.#cancelRename()} title="Cancel rename" aria-label="Cancel rename">✗</button>
          ${this._renameError ? html`<span class="file-rename-error">${this._renameError}</span>` : nothing}
        </div>
      `;
    }

    return html`
      <span class="file-page-breadcrumbs">
        ${folderParts.map((seg, i) => {
          const folderPath = folderParts.slice(0, i + 1).join('/');
          return html`
            <button type="button" class="file-bc-segment" @click=${() => this.#navigateFolder(folderPath)}>${seg}</button>
            <span class="file-bc-sep">/</span>
          `;
        })}
        <span class="file-bc-page">${fileName}</span>
        <button class="file-rename-btn" @click=${() => this.#startRename()} title="Rename / move file" aria-label="Rename / move file">\u{270e}</button>
      </span>
    `;
  }

  #renderRightButtons() {
    const canToggle = this.kind === 'text' && !this.readonly;
    const modeIcon = this._editing ? '\u{1f441}' : '\u{270e}';
    const modeTitle = this._editing ? 'Switch to view mode' : 'Switch to edit mode';
    const toggleBtn = canToggle
      ? html`<button class="file-edit-btn" @click=${() => this.#toggleMode()} title=${modeTitle}>${modeIcon}</button>`
      : nothing;

    const canDelete = !this.readonly;
    const deleteBtn = canDelete
      ? html`<button class="file-delete-btn" @click=${() => this.#deleteFile()} title="Delete file" aria-label="Delete file">\u{1F5D1}</button>`
      : nothing;

    const closeBtn = this.standalone
      ? nothing
      : html`<button class="file-close-btn" @click=${() => this._close()} title="Close file pane">&times;</button>`;

    return html`${toggleBtn}${deleteBtn}${closeBtn}`;
  }

  #renderSaveStatus() {
    if (!this._saveStatus) return nothing;
    const text = this._saveStatus === 'saving'
      ? 'Saving…'
      : this._saveStatus === 'saved'
        ? 'Saved'
        : this._saveError || 'Save failed';
    const cls = `file-editor-status ${this._saveStatus}`;
    return html`<span class=${cls}>${text}</span>`;
  }

  #renderConflictBanner() {
    if (!this._conflict) return nothing;
    return html`
      <div class="file-editor-conflict">
        <span>File changed on disk.</span>
        <button @click=${() => this.#reloadFromDisk()}>Reload</button>
      </div>
    `;
  }

  #renderTextBody() {
    if (this._editing) {
      return html`
        <file-editor
          path=${this.path}
          .content=${this._content}
          .modified=${this._modified}
          kind=${this.kind}
          ?readonly=${this.readonly}
          save-endpoint="/api/workspace/"
          @saving=${() => this.#onSaving()}
          @saved=${(/** @type {CustomEvent} */ e) => this.#onSaved(e)}
          @conflict=${(/** @type {CustomEvent} */ e) => this.#onConflict(e)}
          @error=${(/** @type {CustomEvent} */ e) => this.#onEditorError(e)}
        ></file-editor>
      `;
    }
    return html`<pre class="file-page-body">${this._content}</pre>`;
  }

  #renderImageBody() {
    const src = '/api/workspace/' + encodePagePath(this.path);
    return html`
      <div class="file-page-body file-page-image">
        <img src=${src} alt=${this.path} />
      </div>
    `;
  }

  #renderBinaryBody() {
    const href = '/api/workspace/' + encodePagePath(this.path);
    const parts = this.path.split('/');
    const fileName = parts[parts.length - 1];
    const kindLabel = this.kind === 'binary' ? 'Binary file' : 'Unknown file type';
    return html`
      <div class="file-page-body file-page-download">
        <p><strong>${fileName}</strong></p>
        <p class="file-page-meta">${kindLabel}</p>
        <p><a class="file-download-link" href=${href} download>Download</a></p>
      </div>
    `;
  }

  render() {
    if (this._loading) {
      return html`<div class="file-page-loading">Loading…</div>`;
    }
    if (this._error) {
      return html`
        <div class="file-page">
          <div class="file-page-toolbar">
            ${this.#renderBreadcrumbs()}
            <span class="file-editor-spacer"></span>
            ${this.#renderRightButtons()}
          </div>
          <div class="file-page-error">${this._error}</div>
        </div>
      `;
    }
    if (!this.path) return nothing;

    const body = this.kind === 'text'
      ? this.#renderTextBody()
      : this.kind === 'image'
        ? this.#renderImageBody()
        : this.#renderBinaryBody();

    // For text-edit mode, the CodeMirror view is self-contained; still wrap in toolbar.
    return html`
      <div class="file-page">
        <div class="file-page-toolbar">
          ${this.#renderBreadcrumbs()}
          <span class="file-editor-spacer"></span>
          ${this.#renderSaveStatus()}
          ${this._modified ? html`<span class="file-page-date">${formatDate(this._modified)}</span>` : nothing}
          ${this.#renderRightButtons()}
        </div>
        ${this.#renderConflictBanner()}
        ${body}
      </div>
    `;
  }
}

customElements.define('file-page', FilePage);
