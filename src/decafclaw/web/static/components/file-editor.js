/**
 * File editor — plain-text CodeMirror 6 editor for workspace files with auto-save.
 *
 * Properties:
 *   path (String) — file path relative to workspace, used for language detection + save URL
 *   content (String) — initial file content
 *   modified (Number) — file mtime for conflict detection
 *   kind (String) — file kind from server (host decides whether to mount us)
 *   readonly (Boolean) — if true, editor is read-only
 *
 * Events (all bubble + composed):
 *   'saving'    — auto-save in flight
 *   'saved'     — { modified, path } save succeeded; caller should refresh cached mtime
 *   'conflict'  — server returned 409; host should remount with fresh server state
 *   'error'     — { status, message } network or non-200 save failure
 */

import { LitElement, html } from 'lit';
import { ApiError, DefaultService } from '../lib/api-client/index.js';
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
  HighlightStyle,
  syntaxHighlighting,
  tags,
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

/**
 * Custom HighlightStyle backed by CSS variables (--cm-*) that adapt to the
 * active application theme (light, dark, Dracula, Solarized Light).
 */
export const editorHighlightStyle = HighlightStyle.define([
  { tag: tags.link, textDecoration: 'underline' },
  { tag: tags.heading, textDecoration: 'underline', fontWeight: 'bold' },
  { tag: tags.emphasis, fontStyle: 'italic' },
  { tag: tags.strong, fontWeight: 'bold' },
  { tag: tags.strikethrough, textDecoration: 'line-through' },
  { tag: tags.keyword, color: 'var(--cm-keyword, #c678dd)' },
  { tag: [tags.atom, tags.bool, tags.url, tags.contentSeparator, tags.labelName], color: 'var(--cm-atom, #56b6c2)' },
  { tag: [tags.literal, tags.inserted], color: 'var(--cm-literal, #56b6c2)' },
  { tag: [tags.string, tags.deleted], color: 'var(--cm-string, #98c379)' },
  { tag: [tags.regexp, tags.escape, tags.special(tags.string)], color: 'var(--cm-regexp, #56b6c2)' },
  { tag: tags.definition(tags.variableName), color: 'var(--cm-variable, #e06c75)' },
  { tag: tags.local(tags.variableName), color: 'var(--cm-variable, #e06c75)' },
  { tag: [tags.typeName, tags.namespace], color: 'var(--cm-type, #e6c07b)' },
  { tag: tags.className, color: 'var(--cm-class, #e6c07b)' },
  { tag: [tags.special(tags.variableName), tags.macroName], color: 'var(--cm-special, #61aeee)' },
  { tag: tags.definition(tags.propertyName), color: 'var(--cm-property, #61aeee)' },
  { tag: tags.propertyName, color: 'var(--cm-property, #61aeee)' },
  { tag: tags.comment, color: 'var(--cm-comment, #5c6370)', fontStyle: 'italic' },
  { tag: tags.meta, color: 'var(--cm-meta, #abb2bf)' },
  { tag: tags.number, color: 'var(--cm-number, #d19a66)' },
  { tag: tags.invalid, color: 'var(--cm-invalid, #e06c75)' },
]);

const SAVE_DEBOUNCE_MS = 800;

/**
 * Preserve the existing raw HTTP error text while keeping response data unknown.
 * @param {unknown} body
 * @returns {string}
 */
function errorText(body) {
  return typeof body === 'string' ? body : '';
}

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
 *   Properties (`path`, `content`, `modified`, `kind`, `readonly`) are
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
      syntaxHighlighting(editorHighlightStyle, { fallback: true }),
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
      const data = await DefaultService.wrapperApiWorkspacePathPut(
        this.path,
        undefined,
        { content, modified: this.modified },
      );
      const newModified = data.modified ?? this.modified;
      this.modified = newModified;
      this.#lastSavedContent = content;
      this.#dispatch('saved', { modified: newModified, path: this.path });
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        this.#dispatch('conflict', { status: 409 });
      } else if (error instanceof ApiError) {
        /** @type {unknown} */
        const body = error.body;
        this.#dispatch('error', {
          status: error.status,
          message: errorText(body),
        });
      } else {
        this.#dispatch('error', {
          status: 0,
          message: error instanceof Error ? error.message : 'network error',
        });
      }
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
