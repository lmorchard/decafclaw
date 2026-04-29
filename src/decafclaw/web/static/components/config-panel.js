/**
 * Config panel — list and edit agent config files using wiki-editor.
 *
 * Dispatches 'close' CustomEvent when the user clicks back from the editor.
 */

import { LitElement, html, nothing } from 'lit';
import './wiki-editor.js';

export class ConfigPanel extends LitElement {
  static properties = {
    _files: { state: true },
    _selectedFile: { state: true },
    _fileContent: { state: true },
    _fileModified: { state: true },
    _loading: { state: true },
  };

  createRenderRoot() { return this; }

  constructor() {
    super();
    /** @type {Array<{name: string, path: string, description: string, scope: string, modified: number|null, exists: boolean}>} */
    this._files = [];
    /** @type {{name: string, path: string, description: string, scope: string}|null} */
    this._selectedFile = null;
    /** @type {string} */
    this._fileContent = '';
    /** @type {number} */
    this._fileModified = 0;
    /** @type {boolean} */
    this._loading = false;
  }

  connectedCallback() {
    super.connectedCallback();
    this.#fetchFiles();
  }

  async #fetchFiles() {
    this._loading = true;
    try {
      const res = await fetch('/api/config/files');
      if (res.ok) {
        this._files = await res.json();
      }
    } catch {
      // silently fail
    } finally {
      this._loading = false;
    }
  }

  /** @param {{name: string, path: string, description: string, scope: string}} file */
  async #selectFile(file) {
    this._loading = true;
    try {
      const res = await fetch(`/api/config/files/${encodeURIComponent(file.path)}`);
      if (res.ok) {
        const data = await res.json();
        this._fileContent = data.content;
        this._fileModified = data.modified || 0;
        this._selectedFile = file;
      }
    } catch {
      // silently fail
    } finally {
      this._loading = false;
    }
  }

  #backToList() {
    this._selectedFile = null;
    this._fileContent = '';
    this._fileModified = 0;
    this.#fetchFiles();
  }

  close() {
    this._selectedFile = null;
    this.dispatchEvent(new CustomEvent('close', { bubbles: true, composed: true }));
  }

  render() {
    if (this._selectedFile) {
      return html`
        <div class="config-panel">
          <div class="config-panel-header">
            <button class="config-back-btn dc-icon-btn" @click=${() => this.#backToList()} title="Back to list" aria-label="Back to list">&larr;</button>
            <span class="config-panel-title">${this._selectedFile.name}</span>
            <span class="config-scope-badge ${this._selectedFile.scope}">${this._selectedFile.scope}</span>
          </div>
          <wiki-editor
            .page=${this._selectedFile.path}
            .content=${this._fileContent}
            .modified=${this._fileModified}
            save-endpoint="/api/config/files/"
            @close=${() => this.#backToList()}
          ></wiki-editor>
        </div>
      `;
    }

    if (this._loading) {
      return html`<div class="config-panel"><p style="padding: 1rem; color: var(--pico-muted-color);">Loading...</p></div>`;
    }

    return html`
      <div class="config-panel">
        <div class="config-panel-header">
          <span class="config-panel-title">Agent Config</span>
          <button class="config-close-btn dc-icon-btn" @click=${() => this.close()} title="Close config panel" aria-label="Close config panel">&#10005;</button>
        </div>
        <div class="config-file-list">
          ${this._files.map(f => html`
            <div class="config-file-item" @click=${() => this.#selectFile(f)}>
              <div class="config-file-info">
                <span class="config-file-name">${f.name}</span>
                <span class="config-scope-badge ${f.scope}">${f.scope}</span>
                ${!f.exists ? html`<span class="config-default-badge">default</span>` : nothing}
              </div>
              <div class="config-file-desc">${f.description}</div>
            </div>
          `)}
        </div>
      </div>
    `;
  }
}

customElements.define('config-panel', ConfigPanel);
