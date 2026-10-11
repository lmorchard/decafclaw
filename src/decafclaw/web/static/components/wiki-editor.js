/**
 * Wiki editor — WYSIWYG markdown editor wrapping Milkdown with auto-save.
 *
 * Properties:
 *   page (String) — page name for API path
 *   content (String) — initial markdown
 *   modified (Number) — file mtime for conflict detection
 *   saveEndpoint (String) — API prefix, default '/api/vault/'. Used by every
 *     request the editor makes — save, force-save, and conflict reload. A host
 *     that sets it must serve GET and PUT at `{saveEndpoint}{page}`; see
 *     docs/web-ui.md for the shared request/response contract.
 *
 * Behavior:
 *   Dispatches a 'close' CustomEvent when the public close() method is called.
 *   Note: This component does not render a Done/Close button; closing is controlled
 *   by the host application.
 *
 *   A single click on a [[wiki-link]] in the editor opens a small popover
 *   (portaled to document.body) with editable Target and Display fields;
 *   Apply commits both via a ProseMirror transaction, Cancel/Escape/outside
 *   click discard. Double-click keeps navigating (dispatches 'wiki-navigate').
 */

import { LitElement, html, nothing, render } from 'lit';
import { ApiError, DefaultService } from '../lib/api-client/index.js';
import {
  Editor, rootCtx, defaultValueCtx, editorViewCtx,
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

/** @typedef {'/api/vault/'|'/api/config/files/'|'/api/schedules/'} EditorSaveEndpoint */

// ── Wiki-link edit popover ─────────────────────────────────────────────

/** Single- vs double-click disambiguation before the popover opens. */
const LINK_EDIT_OPEN_DELAY_MS = 250;
/** Popover size assumptions, for viewport clamping (see #showLinkEdit). */
const LINK_EDIT_WIDTH = 240;
const LINK_EDIT_ESTIMATED_HEIGHT = 180;
const LINK_EDIT_GAP = 6;
const LINK_EDIT_MARGIN = 8;

/**
 * Find the contiguous span of inline nodes carrying the wiki_link mark that
 * contains `pos`. A marked run may span several text nodes (nested marks
 * split it), so the span extends across adjacent marked nodes in both
 * directions — transitive, so clicking any run of a multi-run link yields
 * the whole run.
 * @param {any} doc ProseMirror document
 * @param {number} pos position at or inside the marked run
 * @returns {{from: number, to: number} | null}
 */
function findWikiLinkSpan(doc, pos) {
  /** @type {{from: number, to: number}[]} */
  const runs = [];
  doc.descendants((/** @type {any} */ node, /** @type {number} */ nodePos) => {
    if (node.isInline && node.marks.some((/** @type {any} */ m) => m.type.name === 'wiki_link')) {
      runs.push({ from: nodePos, to: nodePos + node.nodeSize });
    }
  });
  let i = -1;
  for (let k = 0; k < runs.length; k++) {
    if (runs[k].from <= pos && pos <= runs[k].to) {
      i = k;
      break;
    }
  }
  if (i < 0) return null;
  let from = runs[i].from;
  let to = runs[i].to;
  for (let k = i + 1; k < runs.length && runs[k].from === to; k++) to = runs[k].to;
  for (let k = i - 1; k >= 0 && runs[k].to === from; k--) from = runs[k].from;
  return { from, to };
}

/**
 * @typedef {Object} LinkEditState
 * @property {number} from — marked span start in the doc
 * @property {number} to — marked span end in the doc
 * @property {number} left — popover fixed x (px)
 * @property {number} top — popover fixed y (px)
 * @property {string} target — Target field value; mutated in place on input
 * @property {string} display — Display field value; mutated in place on input
 * @property {string} initTarget — target when the popover opened
 * @property {string} initDisplay — display text when the popover opened
 */

export class WikiEditor extends LitElement {
  static properties = {
    page: { type: String },
    content: { type: String },
    modified: { type: Number },
    saveEndpoint: { type: String, attribute: 'save-endpoint' },
    /** Extra template content rendered at the right end of the toolbar */
    toolbarExtra: { attribute: false },
    /** Template content rendered at the left of the toolbar (replaces format buttons) */
    toolbarLeft: { attribute: false },
    _status: { state: true },
    _error: { state: true },
    /** Wiki-link edit popover model; null when the popover is closed. */
    _linkEdit: { state: true },
  };

  /** @type {import('@milkdown/kit').Editor | null} */
  #editor = null;
  /** @type {ReturnType<typeof setTimeout> | null} */
  #saveTimer = null;
  /** @type {string} */
  #lastSavedContent = '';
  /** @type {string} */
  #currentMarkdown = '';
  /** Delayed popover open, so a double-click (navigate) can cancel it. */
  #linkEditTimer = /** @type {ReturnType<typeof setTimeout> | null} */ (null);
  /** The <a> element the open popover edits, or null. */
  #linkEditLink = /** @type {HTMLElement | null} */ (null);
  /**
   * Document-level outside-click handler while the popover is open.
   * @type {(e: MouseEvent) => void}
   */
  #onLinkEditDocClick = (/** @type {MouseEvent} */ e) => {
    if (!this._linkEdit) return;
    /** @type {Element | null} */
    const t = /** @type {Element | null} */ (e.target);
    if (t?.closest('.wiki-link-popover')) return;
    // A (re)click on the very link being edited keeps the popover open —
    // through closest() so nested elements inside the link also count.
    if (this.#linkEditLink && t?.closest('a.wiki-link') === this.#linkEditLink) return;
    this.#closeLinkEdit();
  };

  createRenderRoot() { return this; }

  constructor() {
    super();
    this.page = '';
    this.content = '';
    /** @type {number} */ this.modified = 0;
    /** @type {EditorSaveEndpoint} */ this.saveEndpoint = '/api/vault/';
    /** @type {import('lit').TemplateResult|null} Extra content for toolbar right side */
    this.toolbarExtra = null;
    /** @type {import('lit').TemplateResult|null} Content for toolbar left side (replaces format buttons) */
    this.toolbarLeft = null;
    /** @type {'idle'|'editing'|'saving'|'saved'|'error'|'conflict'} */
    this._status = 'idle';
    /** @type {string} */ this._error = '';
    /** @type {LinkEditState | null} */
    this._linkEdit = null;
    /** @type {HTMLElement | null} */
    this._popoverMount = null;
  }

  connectedCallback() {
    super.connectedCallback();
    // Body-level mount so the popover escapes the side panel's overflow and
    // any transformed ancestor (a transform would become the containing
    // block for position:fixed). Mirrors context-inspector.js (#1057).
    if (!this._popoverMount) {
      this._popoverMount = document.createElement('div');
      this._popoverMount.className = 'wiki-link-popover-mount';
      document.body.appendChild(this._popoverMount);
    }
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

    // A single click on a wiki link opens the edit popover (delayed, so a
    // double-click can still mean "navigate"). Every current host sits inside
    // #wiki-main, which the document-level .wiki-link interceptor in app.js
    // already skips; preventDefault/stopPropagation keep the editor
    // self-contained so a future host placed outside #wiki-main cannot
    // navigate on single click underneath the popover.
    root.addEventListener('click', (/** @type {MouseEvent} */ e) => {
      const link = /** @type {HTMLElement | null} */ (/** @type {HTMLElement} */ (e.target).closest('a.wiki-link'));
      if (!link) {
        if (this.#linkEditTimer !== null) this.#cancelLinkEditOpen();
        return;
      }
      e.preventDefault();
      e.stopPropagation();
      this.#scheduleLinkEditOpen(link);
    });

    root.addEventListener('dblclick', (/** @type {MouseEvent} */ e) => {
      const link = /** @type {HTMLElement} */ (e.target).closest('a.wiki-link');
      if (!link) return;
      // Double-click navigates — drop any pending or open popover first.
      this.#closeLinkEdit();
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
    this.#closeLinkEdit();
    if (this._popoverMount) {
      render(nothing, this._popoverMount);
      this._popoverMount.remove();
      this._popoverMount = null;
    }
    this.#flushSave();
    if (this.#editor) {
      this.#editor.destroy();
      this.#editor = null;
    }
  }

  updated(changed) {
    if (this._popoverMount) {
      render(this._linkEdit ? this.#linkEditPopover() : nothing, this._popoverMount);
    }
    if (changed.has('_linkEdit') && this._linkEdit) {
      // Popover (re)rendered — move focus to the Target field and select
      // its text, so typing replaces it. (select() alone does not focus.)
      /** @type {HTMLInputElement | null} */
      const input = /** @type {HTMLInputElement | null} */ (
        this._popoverMount?.querySelector('input[data-field="target"]')
      );
      input?.focus();
      input?.select();
    }
  }

  /** @param {string} md */
  #onContentChange(md) {
    this.#currentMarkdown = md;
    // The document changed under an open (or pending) link editor — the
    // captured span is now stale, so drop the edit rather than apply it to
    // a shifted or out-of-bounds range.
    if (this._linkEdit !== null || this.#linkEditTimer !== null) this.#closeLinkEdit();
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
    if (this.saveEndpoint === '/api/vault/') {
      try {
        const data = await DefaultService.wrapperApiVaultPagePut(
          this.page, { content, modified: this.modified },
        );
        const newModified = data.modified;
        this.modified = newModified;
        this.#lastSavedContent = content;
        this._status = 'saved';
        this._error = '';
        this.dispatchEvent(new CustomEvent('saved', {
          detail: { modified: newModified, page: this.page },
          bubbles: true,
          composed: true,
        }));
      } catch (error) {
        if (error instanceof ApiError && error.status === 409) {
          this._status = 'conflict';
          this._error = 'Page was modified externally.';
        } else if (error instanceof ApiError) {
          this._status = 'error';
          this._error = `Save failed (${error.status})`;
        } else {
          this._status = 'error';
          this._error = 'Save failed (network error)';
        }
      }
      return;
    }

    if (this.saveEndpoint === '/api/config/files/') {
      try {
        const data = await DefaultService.wrapperApiConfigFilesPathPut(
          this.page, { content, modified: this.modified },
        );
        const newModified = data.modified;
        this.modified = newModified;
        this.#lastSavedContent = content;
        this._status = 'saved';
        this._error = '';
        this.dispatchEvent(new CustomEvent('saved', {
          detail: { modified: newModified, page: this.page },
          bubbles: true,
          composed: true,
        }));
      } catch (error) {
        if (error instanceof ApiError && error.status === 409) {
          this._status = 'conflict';
          this._error = 'Page was modified externally.';
        } else if (error instanceof ApiError) {
          this._status = 'error';
          this._error = `Save failed (${error.status})`;
        } else {
          this._status = 'error';
          this._error = 'Save failed (network error)';
        }
      }
      return;
    }

    try {
      const data = await DefaultService.wrapperApiSchedulesNamePut(
        this.page, { content, modified: this.modified },
      );
      const newModified = data.modified;
      this.modified = newModified;
      this.#lastSavedContent = content;
      this._status = 'saved';
      this._error = '';
      this.dispatchEvent(new CustomEvent('saved', {
        detail: { modified: newModified, page: this.page },
        bubbles: true,
        composed: true,
      }));
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        this._status = 'conflict';
        this._error = 'Page was modified externally.';
      } else if (error instanceof ApiError) {
        this._status = 'error';
        this._error = `Save failed (${error.status})`;
      } else {
        this._status = 'error';
        this._error = 'Save failed (network error)';
      }
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
      /** @type {string} */
      let newContent;
      /** @type {number} */
      let newModified;
      if (this.saveEndpoint === '/api/vault/') {
        try {
          const data = await DefaultService.wrapperApiVaultPageGet(this.page);
          newContent = data.body;
          newModified = data.modified;
        } catch (e) {
          if (e instanceof ApiError) throw new Error(`HTTP ${e.status}`);
          throw e;
        }
      } else if (this.saveEndpoint === '/api/config/files/') {
        try {
          const data = await DefaultService.wrapperApiConfigFilesPathGet(this.page);
          newContent = data.content;
          newModified = data.modified ?? 0;
        } catch (e) {
          if (e instanceof ApiError) throw new Error(`HTTP ${e.status}`);
          throw e;
        }
      } else {
        try {
          const data = await DefaultService.wrapperApiSchedulesNameGet(this.page);
          newContent = data.body;
          newModified = data.modified;
        } catch (e) {
          if (e instanceof ApiError) throw new Error(`HTTP ${e.status}`);
          throw e;
        }
      }
      this.content = newContent;
      this.modified = newModified;
      if (this.#editor) {
        this.#editor.action(replaceAll(newContent));
      }
      this.#lastSavedContent = newContent;
      this.#currentMarkdown = newContent;
      this._status = 'saved';
      this._error = '';
      // The host tracks its own mtime for metadata writes; without this its
      // copy stays stale and the next metadata edit 409s spuriously.
      this.dispatchEvent(new CustomEvent('reloaded', {
        detail: { modified: this.modified, page: this.page },
        bubbles: true,
        composed: true,
      }));
    } catch (e) {
      this._error = `Reload failed: ${/** @type {Error} */(e).message}`;
    }
  }

  async #forceSave() {
    // Send with modified=null to skip server-side mtime check
    this._status = 'saving';
    const savedModified = this.modified;
    const content = this.#currentMarkdown;
    if (this.saveEndpoint === '/api/vault/') {
      try {
        const data = await DefaultService.wrapperApiVaultPagePut(
          this.page, { content },
        );
        this.modified = data.modified;
        this.#lastSavedContent = content;
        this._status = 'saved';
        this._error = '';
        this.dispatchEvent(new CustomEvent('saved', {
          detail: { modified: this.modified, page: this.page },
          bubbles: true,
          composed: true,
        }));
      } catch (error) {
        this._status = 'error';
        this._error = error instanceof ApiError
          ? `Force save failed (${error.status})`
          : 'Force save failed (network error)';
      }
      return;
    }

    if (this.saveEndpoint === '/api/config/files/') {
      try {
        const data = await DefaultService.wrapperApiConfigFilesPathPut(
          this.page, { content },
        );
        this.modified = data.modified;
        this.#lastSavedContent = content;
        this._status = 'saved';
        this._error = '';
        this.dispatchEvent(new CustomEvent('saved', {
          detail: { modified: this.modified, page: this.page },
          bubbles: true,
          composed: true,
        }));
      } catch (error) {
        this.modified = savedModified;
        this._status = 'error';
        this._error = error instanceof ApiError
          ? `Force save failed (${error.status})`
          : 'Force save failed (network error)';
      }
      return;
    }

    try {
      const data = await DefaultService.wrapperApiSchedulesNamePut(
        this.page, { content },
      );
      this.modified = data.modified;
      this.#lastSavedContent = content;
      this._status = 'saved';
      this._error = '';
      this.dispatchEvent(new CustomEvent('saved', {
        detail: { modified: this.modified, page: this.page },
        bubbles: true,
        composed: true,
      }));
    } catch (error) {
      this.modified = savedModified;
      this._status = 'error';
      if (error instanceof ApiError) {
        this._error = `Force save failed (${error.status})`;
      } else {
        this._error = 'Force save failed (network error)';
      }
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

  // ── Wiki-link edit popover ───────────────────────────────────────────

  /**
   * Fetch the ProseMirror view from the Milkdown editor, or null if the
   * editor is not ready yet.
   * @returns {any | null}
   */
  #getEditorView() {
    if (!this.#editor) return null;
    /** @type {any | null} */
    let view = null;
    this.#editor.action(ctx => {
      view = ctx.get(editorViewCtx);
    });
    return view;
  }

  /**
   * Open the popover for `link`, delayed so a double-click (which
   * navigates) can cancel it. A popover for a different link is already
   * closed by the document-level outside-click handler (capture phase,
   * before this handler runs), so every open here takes the delay path.
   * @param {HTMLElement} link
   */
  #scheduleLinkEditOpen(link) {
    // The open popover is for this same link — keep it open.
    if (this._linkEdit && this.#linkEditLink === link) return;
    this.#cancelLinkEditOpen();
    this.#linkEditTimer = setTimeout(() => {
      this.#linkEditTimer = null;
      this.#showLinkEdit(link);
    }, LINK_EDIT_OPEN_DELAY_MS);
  }

  /** Drop a pending (not yet shown) popover open. */
  #cancelLinkEditOpen() {
    if (this.#linkEditTimer !== null) {
      clearTimeout(this.#linkEditTimer);
      this.#linkEditTimer = null;
    }
  }

  /**
   * Resolve the clicked link to its marked span and show the popover
   * anchored below the link (flipping above when near the viewport edge).
   * @param {HTMLElement} link
   */
  #showLinkEdit(link) {
    if (!link.isConnected) return;
    const view = this.#getEditorView();
    if (!view) return;
    let pos;
    try {
      pos = view.posAtDOM(link, 0);
    } catch {
      return;
    }
    const span = findWikiLinkSpan(view.state.doc, pos);
    if (!span) return;
    const display = (link.textContent || '').trim();
    if (!display) return;
    // A plain [[text]] link carries no data-wiki-page; its target is the
    // text itself, which is what the field should show.
    const target = link.getAttribute('data-wiki-page') || display;
    const rect = link.getBoundingClientRect();
    const left = Math.max(LINK_EDIT_MARGIN,
      Math.min(rect.left, window.innerWidth - LINK_EDIT_WIDTH - LINK_EDIT_MARGIN));
    let top = rect.bottom + LINK_EDIT_GAP;
    if (top + LINK_EDIT_ESTIMATED_HEIGHT > window.innerHeight) {
      top = Math.max(LINK_EDIT_MARGIN, rect.top - LINK_EDIT_ESTIMATED_HEIGHT - LINK_EDIT_GAP);
    }
    this.#linkEditLink = link;
    this._linkEdit = { ...span, left, top, target, display, initTarget: target, initDisplay: display };
    document.addEventListener('click', this.#onLinkEditDocClick, true);
  }

  /** Close the popover (and drop any pending open). Safe to call when closed. */
  #closeLinkEdit() {
    this.#cancelLinkEditOpen();
    this.#linkEditLink = null;
    this._linkEdit = null;
    document.removeEventListener('click', this.#onLinkEditDocClick, true);
  }

  /** @param {KeyboardEvent} e */
  #onLinkEditKeydown(e) {
    if (e.key === 'Escape') {
      e.preventDefault();
      this.#closeLinkEdit();
    } else if (e.key === 'Enter' && e.target instanceof HTMLInputElement) {
      e.preventDefault();
      this.#applyLinkEdit();
    }
  }

  /** @param {Event} e */
  #onLinkEditInput(e) {
    const edit = this._linkEdit;
    /** @type {HTMLInputElement | null} */
    const input = /** @type {HTMLInputElement | null} */ (e.target);
    if (!edit || !input) return;
    const field = input.dataset.field;
    if (field === 'target') edit.target = input.value;
    else if (field === 'display') edit.display = input.value;
  }

  /**
   * Commit the edit. When the display text is unchanged, swap the
   * wiki_link mark's attrs in place across the span so other marks (bold,
   * italic) on the display text survive; when it changed, the field is
   * plain text, so the run is replaced by plain text carrying only the
   * wiki_link mark. The previous selection is mapped through the
   * transaction, landing the cursor inside the new link text.
   */
  #applyLinkEdit() {
    const edit = this._linkEdit;
    if (!edit) return;
    const view = this.#getEditorView();
    if (!view) return;
    const target = edit.target.trim();
    const display = edit.display.trim();
    // An empty display would leave a zero-length link; refuse to apply.
    if (!display) return;
    const markType = view.state.schema.marks.wiki_link;
    // Omit the target attr when it is empty or redundant, matching the
    // [[text]] form the serializer and input rule produce.
    const attrs = target && target !== display ? { target } : {};
    let tr = view.state.tr;
    if (display === edit.initDisplay) {
      // Display unchanged: update just the wiki mark's attrs in place.
      if (target !== edit.initTarget) {
        tr = tr.removeMark(edit.from, edit.to, markType)
          .addMark(edit.from, edit.to, markType.create(attrs));
      }
    } else {
      // Display edited: replace the whole run (flattening any nested
      // formatting — the fields are plain text by design).
      tr = tr.replaceWith(edit.from, edit.to,
        view.state.schema.text(display, [markType.create(attrs)]));
    }
    if (tr.steps.length) view.dispatch(tr);
    view.focus?.();
    this.#closeLinkEdit();
  }

  /** Popover template, rendered into the body-level mount in updated(). */
  #linkEditPopover() {
    const edit = /** @type {LinkEditState} */ (this._linkEdit);
    return html`
      <div class="wiki-link-popover"
        style="left: ${edit.left}px; top: ${edit.top}px"
        role="dialog"
        aria-label="Edit wiki link"
        @keydown=${(e) => this.#onLinkEditKeydown(e)}>
        <label class="wiki-link-popover-field">
          <span>Target</span>
          <input type="text" data-field="target"
            .value=${edit.target}
            @input=${(e) => this.#onLinkEditInput(e)}>
        </label>
        <label class="wiki-link-popover-field">
          <span>Display text</span>
          <input type="text" data-field="display"
            .value=${edit.display}
            @input=${(e) => this.#onLinkEditInput(e)}>
        </label>
        <div class="wiki-link-popover-actions">
          <button type="button" class="secondary"
            @click=${() => this.#closeLinkEdit()}>Cancel</button>
          <button type="button" class="wiki-link-popover-apply"
            @click=${() => this.#applyLinkEdit()}>Apply</button>
        </div>
      </div>
    `;
  }

  render() {
    const statusText = STATUS_LABELS[this._status] || '';

    return html`
      <div class="wiki-editor-container">
        <div class="wiki-editor-toolbar">
          ${this.toolbarLeft
            ? this.toolbarLeft
            : TOOLBAR.map(item =>
                item === null
                  ? html`<span class="wiki-editor-separator"></span>`
                  : html`<button
                      class="wiki-editor-toolbar-btn"
                      title=${item.title}
                      @click=${() => this.#execCommand(item.cmd)}
                    >${item.label}</button>`
              )
          }
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
