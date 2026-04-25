/**
 * File editor — plain-text CodeMirror 6 editor for workspace files with auto-save.
 *
 * Properties:
 *   path (String) — file path relative to workspace, used for language detection + save URL
 *   content (String) — initial file content
 *   modified (Number) — file mtime for conflict detection
 *   kind (String) — file kind from server (host decides whether to mount us)
 *   readonly (Boolean) — if true, editor is read-only
 *   saveEndpoint (String) — API prefix, default '/api/workspace/'
 *
 * Events (all bubble + composed):
 *   'saving'    — auto-save in flight
 *   'saved'     — { modified, path } save succeeded; caller should refresh cached mtime
 *   'conflict'  — server returned 409; host should remount with fresh server state
 *   'error'     — { status, message } network or non-200 save failure
 */

import { LitElement, html } from 'lit';
import { encodePagePath } from '../lib/utils.js';
import {
  EditorState,
  EditorView,
  keymap,
  lineNumbers,
  highlightActiveLine,
  defaultKeymap,
  history,
  historyKeymap,
  indentWithTab,
  bracketMatching,
  defaultHighlightStyle,
  syntaxHighlighting,
  foldGutter,
  foldKeymap,
  indentOnInput,
  searchKeymap,
  highlightSelectionMatches,
  markdown,
  python,
  json,
  yaml,
  javascript,
} from 'codemirror';

const SAVE_DEBOUNCE_MS = 800;

/**
 * Return a CodeMirror language extension for the given file path, or null for plain text.
 * @param {string} path
 * @returns {import('@codemirror/state').Extension | null}
 */
function languageForPath(path) {
  const lower = (path || '').toLowerCase();
  const dot = lower.lastIndexOf('.');
  if (dot < 0) return null;
  const ext = lower.slice(dot);
  switch (ext) {
    case '.md':
    case '.markdown':
      return markdown();
    case '.py':
      return python();
    case '.json':
      return json();
    case '.yaml':
    case '.yml':
      return yaml();
    case '.js':
    case '.mjs':
    case '.cjs':
    case '.ts':
      return javascript({ typescript: ext === '.ts' });
    default:
      return null;
  }
}

/**
 * Mount contract:
 *   Properties (`path`, `content`, `modified`, `kind`, `readonly`, `saveEndpoint`) are
 *   read once at `firstUpdated()` time. Reassigning them on an already-mounted instance
 *   is a no-op — the editor does NOT observe property changes. To switch files, hosts
 *   MUST remount the component (e.g. Lit `@keyed(...)`, re-render with a different key,
 *   or explicit unmount + mount).
 *
 * Conflict handling:
 *   On `conflict` (HTTP 409 from save), the component does not latch into a "paused"
 *   state. Auto-save will re-fire on the next doc change and, since the client's cached
 *   `modified` mtime hasn't advanced, it will 409 again. There is no in-place resolve
 *   path. Hosts should react to the `conflict` event by refetching the current server
 *   state (via `GET /api/workspace-file/{path}`) and remounting this component with the
 *   fresh `{ content, modified }`.
 */
export class FileEditor extends LitElement {
  static properties = {
    path: { type: String },
    content: { type: String },
    modified: { type: Number },
    kind: { type: String },
    readonly: { type: Boolean },
    saveEndpoint: { type: String, attribute: 'save-endpoint' },
  };

  /** @type {EditorView | null} */
  #view = null;
  /** @type {ReturnType<typeof setTimeout> | null} */
  #saveTimer = null;
  /** @type {string} */
  #lastSavedContent = '';
  /** @type {string} */
  #currentContent = '';
  /** @type {(e: KeyboardEvent) => void} */
  #onKeyDown;

  createRenderRoot() { return this; }

  constructor() {
    super();
    this.path = '';
    this.content = '';
    /** @type {number} */ this.modified = 0;
    this.kind = 'text';
    this.readonly = false;
    this.saveEndpoint = '/api/workspace/';
    this.#onKeyDown = (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 's') {
        e.preventDefault();
        this.#flushSave();
      }
    };
  }

  firstUpdated() {
    const host = this.querySelector('.file-editor-mount');
    if (!(host instanceof HTMLElement)) return;

    const initial = this.content || '';
    this.#currentContent = initial;
    this.#lastSavedContent = initial;

    const extensions = [
      lineNumbers(),
      foldGutter(),
      highlightActiveLine(),
      highlightSelectionMatches(),
      history(),
      indentOnInput(),
      bracketMatching(),
      syntaxHighlighting(defaultHighlightStyle, { fallback: true }),
      keymap.of([
        ...defaultKeymap,
        ...historyKeymap,
        ...foldKeymap,
        ...searchKeymap,
        indentWithTab,
      ]),
      EditorView.lineWrapping,
      EditorView.updateListener.of((update) => {
        if (update.docChanged) this.#onDocChanged(update.state.doc.toString());
      }),
    ];

    const lang = languageForPath(this.path);
    if (lang) extensions.push(lang);

    if (this.readonly) {
      extensions.push(EditorState.readOnly.of(true));
      extensions.push(EditorView.editable.of(false));
    }

    this.#view = new EditorView({
      state: EditorState.create({ doc: initial, extensions }),
      parent: host,
    });

    this.addEventListener('keydown', this.#onKeyDown);
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    this.removeEventListener('keydown', this.#onKeyDown);
    if (this.#saveTimer != null) {
      clearTimeout(this.#saveTimer);
      this.#saveTimer = null;
    }
    if (this.#view) {
      this.#view.destroy();
      this.#view = null;
    }
  }

  /** @param {string} doc */
  #onDocChanged(doc) {
    this.#currentContent = doc;
    if (this.readonly) return;
    if (this.#saveTimer != null) clearTimeout(this.#saveTimer);
    this.#saveTimer = setTimeout(() => { void this.#save(); }, SAVE_DEBOUNCE_MS);
  }

  #flushSave() {
    if (this.#saveTimer != null) {
      clearTimeout(this.#saveTimer);
      this.#saveTimer = null;
    }
    if (this.readonly) return;
    if (this.#currentContent === this.#lastSavedContent) return;
    void this.#save();
  }

  /** Public: force an immediate save (debounce bypass). */
  async flushSave() {
    if (this.#saveTimer != null) {
      clearTimeout(this.#saveTimer);
      this.#saveTimer = null;
    }
    if (this.readonly) return;
    if (this.#currentContent === this.#lastSavedContent) return;
    await this.#save();
  }

  /** Public: true if the editor has unsaved edits or a debounced save queued. */
  hasPendingChanges() {
    if (this.readonly) return false;
    if (this.#saveTimer != null) return true;
    return this.#currentContent !== this.#lastSavedContent;
  }

  async #save() {
    this.#saveTimer = null;
    const content = this.#currentContent;
    this.#dispatch('saving');
    try {
      const url = `${this.saveEndpoint}${encodePagePath(this.path)}`;
      const res = await fetch(url, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content, modified: this.modified }),
      });
      if (res.ok) {
        const data = await res.json().catch(() => ({}));
        const newModified = data.modified ?? this.modified;
        this.modified = newModified;
        this.#lastSavedContent = content;
        this.#dispatch('saved', { modified: newModified, path: this.path });
      } else if (res.status === 409) {
        this.#dispatch('conflict', { status: 409 });
      } else {
        const message = await res.text().catch(() => '');
        this.#dispatch('error', { status: res.status, message });
      }
    } catch (e) {
      this.#dispatch('error', {
        status: 0,
        message: /** @type {Error} */ (e).message || 'network error',
      });
    }
  }

  /**
   * @param {string} name
   * @param {Record<string, unknown>} [detail]
   */
  #dispatch(name, detail) {
    this.dispatchEvent(new CustomEvent(name, {
      detail: detail ?? {},
      bubbles: true,
      composed: true,
    }));
  }

  render() {
    return html`<div class="file-editor"><div class="file-editor-mount"></div></div>`;
  }
}

customElements.define('file-editor', FileEditor);
