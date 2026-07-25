/**
 * Wiki page viewer/editor — fetches a wiki page and shows it in either
 * read-only (markdown) or edit (Milkdown WYSIWYG) mode.
 *
 * Mode preference is persisted in localStorage. Defaults to edit mode.
 * Dispatches 'wiki-navigate' events when [[wiki-links]] are clicked (panel mode).
 */

import { LitElement, html, nothing } from 'lit';
import { unsafeHTML } from 'lit/directives/unsafe-html.js';
import { renderMarkdown } from '../lib/markdown.js';
import { encodePagePath } from '../lib/utils.js';
import './wiki-editor.js';
import './wiki-metadata.js';

const EDIT_MODE_KEY = 'wiki-edit-mode';

/**
 * An explicit destination for a metadata PUT, used when it must not be read
 * off `this` — i.e. when flushing a pending edit for the page we're leaving.
 * @typedef {{page: string, modified: number}} MetaTarget
 */

/**
 * @param {number} ts — Unix timestamp (seconds)
 * @returns {string}
 */
function formatDate(ts) {
  if (!ts) return '';
  const d = new Date(ts * 1000);
  return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
}

export class WikiPage extends LitElement {
  static properties = {
    page: { type: String },
    standalone: { type: Boolean },
    _body: { state: true },
    _frontmatter: { state: true },
    _frontmatterRaw: { state: true },
    _frontmatterError: { state: true },
    _metaError: { state: true },
    _orphanMetaError: { state: true },
    _loaded: { state: true },
    _title: { state: true },
    _modified: { state: true },
    _loading: { state: true },
    _error: { state: true },
    _editing: { state: true },
    _renaming: { state: true },
    _renameValue: { state: true },
    _renameError: { state: true },
  };

  createRenderRoot() { return this; }

  constructor() {
    super();
    this.page = '';
    this.standalone = false;
    /** @type {string} */ this._body = '';
    /** @type {Record<string, any>} */ this._frontmatter = {};
    /** @type {string} */ this._frontmatterRaw = '';
    /** @type {string} */ this._frontmatterError = '';
    /**
     * Set when a metadata write (typed patch or raw replace) fails. Passed
     * straight through to <wiki-metadata> for display.
     * @type {{status: 'conflict'|'error', message: string} | null}
     */
    this._metaError = null;
    /**
     * Set when a metadata write aimed at a page we have already navigated
     * away from fails. Deliberately *not* `_metaError`: that state drives
     * Reload/Overwrite/Retry buttons which all act on the page currently on
     * screen, so reusing it would re-misdeliver the departed page's fields.
     * Survives navigation and `_fetchPage()` so the failure can't vanish.
     * @type {string}
     */
    this._orphanMetaError = '';
    this._loaded = false;
    /** @type {string} */ this._title = '';
    /** @type {number} */ this._modified = 0;
    this._loading = false;
    /** @type {string} */ this._error = '';
    // Default to edit mode, persisted in localStorage
    this._editing = localStorage.getItem(EDIT_MODE_KEY) !== 'false';
    this._renaming = false;
    /** @type {string} */ this._renameValue = '';
    /** @type {string} */ this._renameError = '';
  }

  /** @type {ReturnType<typeof setTimeout> | null} */
  #metaTimer = null;
  /** @type {Record<string, any>} */
  #pendingFields = {};
  /**
   * Gate serializing every metadata write (typed patch and raw replace)
   * against every other. Whoever wants to write awaits any existing value
   * here first, then installs their own until they're done — so a raw save
   * and a typed-patch flush can never have two PUTs in flight at once, each
   * carrying the same now-stale `modified`.
   * @type {Promise<void> | null}
   */
  #metaInFlight = null;
  /**
   * What to resend for Retry/Overwrite after a metadata write fails. Typed
   * patches don't need their fields stored here — a failed patch is merged
   * back into #pendingFields, so retrying is just flushing again.
   * @type {{kind: 'raw', raw: string} | {kind: 'patch'} | null}
   */
  #lastMetaAttempt = null;

  /** @param {Map<string, any>} changed */
  willUpdate(changed) {
    if (!changed.has('page')) return;
    // Lit has already assigned the NEW page to `this.page` by the time
    // willUpdate runs, so anything that must reach the page we're *leaving*
    // has to be told which page that is. `this._modified` is still the old
    // page's mtime here, so it travels with it.
    /** @type {string} */
    const prev = changed.get('page') || '';
    if (this._editing && prev) {
      void this.#flushMetadata({ page: prev, modified: this._modified });
      // <wiki-editor> needs no target: it holds its own `page` property, and
      // Lit has not re-rendered it yet, so its save still aims at `prev`.
      /** @type {import('./wiki-editor.js').WikiEditor|null} */
      const editor = this.querySelector('wiki-editor');
      if (editor) editor.flushSave();
    }
    // `page` going empty is the wiki pane closing (app.js). Flushing above
    // rather than bailing early is the point: the debounce timer would
    // otherwise fire 600ms later and PUT to whatever page opens next.
    if (this.page) this._fetchPage();
  }

  /** @param {CustomEvent} e */
  _onMetadataChange(e) {
    Object.assign(this.#pendingFields, e.detail.fields);
    // A conflict must be resolved (Reload/Overwrite) before we try again —
    // auto-firing another flush would just carry the same stale `modified`
    // into another silent 409. The edit stays queued in #pendingFields.
    if (this._metaError?.status === 'conflict') return;
    if (this.#metaTimer != null) clearTimeout(this.#metaTimer);
    this.#metaTimer = setTimeout(() => { this.#flushMetadata(); }, 600);
  }

  /**
   * Serialize one metadata PUT against any other in-flight metadata write.
   * Clears `_metaError` on success; leaves error handling to the caller on
   * failure, since typed-patch and raw-replace failures need different
   * retry bookkeeping.
   * @param {Record<string, any>} payload
   * @param {{skipModifiedCheck?: boolean, target?: MetaTarget}} [opts]
   * @returns {Promise<{ok: boolean, status: number, error: string}>}
   */
  async #writeMeta(payload, opts) {
    while (this.#metaInFlight) await this.#metaInFlight;
    let release = () => {};
    this.#metaInFlight = new Promise(r => { release = r; });
    try {
      const res = await this.#putMetadata(payload, opts);
      // Only the page on screen owns `_metaError` / `#lastMetaAttempt`, so a
      // departed page's success must not clear the current page's banner.
      if (res.ok && !opts?.target) {
        this._metaError = null;
        this.#lastMetaAttempt = null;
      }
      return res;
    } finally {
      release();
      this.#metaInFlight = null;
    }
  }

  /**
   * Send any debounced typed patch now. Resolves when the write completes.
   * @param {MetaTarget} [target] Page to write to, when it is no longer the
   *   one `this.page` names — i.e. we're navigating away from it.
   */
  async #flushMetadata(target) {
    if (this.#metaTimer != null) {
      clearTimeout(this.#metaTimer);
      this.#metaTimer = null;
    }
    const fields = this.#pendingFields;
    // Don't auto-send into a known conflict; wait for the user to resolve it.
    if (this._metaError?.status === 'conflict') {
      // Unless we're leaving: _fetchPage() is about to wipe both the banner
      // and the queued fields, so say what was lost instead of dropping it.
      if (target && Object.keys(fields).length) {
        this.#pendingFields = {};
        this.#orphan(target.page, 'an unresolved conflict');
      }
      return;
    }
    this.#pendingFields = {};
    if (!Object.keys(fields).length) return;
    const res = await this.#writeMeta({ frontmatter: fields }, { target });
    if (res.ok) return;
    if (target) {
      // #pendingFields, _metaError and #lastMetaAttempt are all implicitly
      // scoped to whatever page is on screen now, and every retry affordance
      // they drive PUTs to `this.page`. Requeueing the departed page's fields
      // there is exactly the misdelivery we're fixing, so report the loss
      // against the page it belongs to rather than carrying it forward.
      this.#orphan(
        target.page,
        res.status === 409 ? 'it was modified externally' : res.error,
      );
      return;
    }
    // Nothing the user typed gets dropped: merge the failed fields back in
    // (newer pending edits, if any landed during the write, win).
    this.#pendingFields = { ...fields, ...this.#pendingFields };
    this.#lastMetaAttempt = { kind: 'patch' };
    this._metaError = res.status === 409
      ? { status: 'conflict', message: 'Metadata was modified externally.' }
      : { status: 'error', message: res.error };
  }

  /** @param {string} page @param {string} reason */
  #orphan(page, reason) {
    this._orphanMetaError =
      `Metadata edit to "${page}" was not saved (${reason}). `
      + 'Reopen that page to redo it.';
  }

  /** @param {CustomEvent} e */
  async _onMetadataRawSave(e) {
    // A typed patch landing after a raw replace would resurrect a key the
    // raw save just deleted, so flush it first. The two PUT shapes are
    // mutually exclusive server-side.
    await this.#flushMetadata();
    // If the flush left an unresolved conflict/error, don't pile a raw
    // replace on top of it — the user needs to resolve that first, or a
    // later flush of the still-pending typed fields could resurrect a key
    // this raw save is about to remove. The banner above only talks about
    // the typed fields, so give the raw editor its own inline word too
    // (via its existing error channel) rather than a silent no-op — the
    // raw text itself is left untouched.
    if (this._metaError) {
      /** @type {any} */ (this.querySelector('wiki-metadata'))
        ?.setRawError('Resolve the pending metadata conflict above before saving raw YAML.');
      return;
    }
    await this.#doRawSave(e.detail.raw);
  }

  /** @param {string} raw */
  async #doRawSave(raw) {
    const panel = /** @type {any} */ (this.querySelector('wiki-metadata'));
    const res = await this.#writeMeta({ frontmatter_raw: raw });
    if (res.ok) {
      panel?.closeRaw();
      // Anything typed while this write was in flight is still valid —
      // send it now, on top of the just-applied raw content.
      await this.#flushMetadata();
    } else if (res.status === 409) {
      this.#lastMetaAttempt = { kind: 'raw', raw };
      this._metaError = { status: 'conflict', message: 'Metadata was modified externally.' };
    } else {
      // Malformed YAML etc. — keep the raw editor's existing inline error.
      panel?.setRawError(res.error);
    }
  }

  /** Refetch the page from the server, discarding any pending local metadata edit. */
  async _onMetadataReload() {
    this.#pendingFields = {};
    this.#lastMetaAttempt = null;
    this._metaError = null;
    /** @type {any} */ (this.querySelector('wiki-metadata'))?.closeRaw();
    await this._fetchPage();
  }

  /** Resend the last failed write, skipping the server's mtime check. */
  async _onMetadataOverwrite() {
    const attempt = this.#lastMetaAttempt;
    this.#lastMetaAttempt = null;
    if (!attempt) {
      this._metaError = null;
      return;
    }
    if (attempt.kind === 'raw') {
      await this.#doOverwrite({ frontmatter_raw: attempt.raw }, attempt);
      return;
    }
    const fields = this.#pendingFields;
    this.#pendingFields = {};
    if (!Object.keys(fields).length) {
      this._metaError = null;
      return;
    }
    await this.#doOverwrite({ frontmatter: fields }, { kind: 'patch' }, fields);
  }

  /**
   * @param {Record<string, any>} payload
   * @param {{kind: 'raw', raw: string} | {kind: 'patch'}} attempt
   * @param {Record<string, any>} [patchFields] Original fields, to restore on failure
   */
  async #doOverwrite(payload, attempt, patchFields) {
    const panel = /** @type {any} */ (this.querySelector('wiki-metadata'));
    const res = await this.#writeMeta(payload, { skipModifiedCheck: true });
    if (res.ok) {
      if (attempt.kind === 'raw') panel?.closeRaw();
      // Anything typed while conflicted (or while this overwrite was in
      // flight) is still valid — send it now that we're no longer stuck.
      await this.#flushMetadata();
      return;
    }
    if (attempt.kind === 'patch' && patchFields) {
      this.#pendingFields = { ...patchFields, ...this.#pendingFields };
    }
    this.#lastMetaAttempt = attempt;
    this._metaError = { status: 'error', message: res.error };
  }

  /** Retry the last failed write with the normal (checked) path. */
  async _onMetadataRetry() {
    const attempt = this.#lastMetaAttempt;
    this._metaError = null;
    if (attempt?.kind === 'raw') {
      await this.#doRawSave(attempt.raw);
    } else {
      await this.#flushMetadata();
    }
  }

  /**
   * @param {Record<string, any>} payload
   * @param {{skipModifiedCheck?: boolean, target?: MetaTarget}} [opts]
   * @returns {Promise<{ok: boolean, status: number, error: string}>}
   */
  async #putMetadata(payload, opts) {
    // Never read the page or mtime off `this` when an explicit target is
    // given: `this.page` is already the page we navigated *to*, and pairing
    // it with the departed page's `modified` sails past the server's
    // `mtime > modified + 1` guard whenever the new page is the older one.
    const page = opts?.target ? opts.target.page : this.page;
    const modified = opts?.target ? opts.target.modified : this._modified;
    try {
      const body = opts?.skipModifiedCheck
        ? { ...payload }
        : { ...payload, modified };
      const res = await fetch('/api/vault/' + encodePagePath(page), {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        return { ok: false, status: res.status, error: data.error || `Save failed (${res.status})` };
      }
      // Adopt the response into our own state only while we're still showing
      // the page it was written to. This covers the explicit-target case and
      // the narrower race where an untargeted write was already in flight
      // when the user navigated.
      if (page === this.page) {
        this._frontmatter = data.frontmatter ?? {};
        // The response carries the new raw block, so no second GET is needed.
        this._frontmatterRaw = data.frontmatter_raw ?? '';
        this._frontmatterError = data.frontmatter_error ?? '';
        this._modified = data.modified;
        // Push the new mtime into the body editor, or its next autosave 409s.
        /** @type {any} */
        const editor = this.querySelector('wiki-editor');
        if (editor) editor.modified = data.modified;
      }
      return { ok: true, status: res.status, error: '' };
    } catch (err) {
      return { ok: false, status: 0, error: 'Save failed (network error)' };
    }
  }

  async _fetchPage() {
    this.#pendingFields = {};
    this.#lastMetaAttempt = null;
    this._metaError = null;
    this._loading = true;
    this._error = '';
    this._loaded = false;
    this._body = '';
    try {
      const res = await fetch('/api/vault/' + encodePagePath(this.page));
      if (!res.ok) {
        this._error = res.status === 404 ? `Page "${this.page}" not found.` : `Error loading page (${res.status}).`;
        return;
      }
      const data = await res.json();
      this._title = data.title;
      this._body = data.body ?? '';
      this._frontmatter = data.frontmatter ?? {};
      this._frontmatterRaw = data.frontmatter_raw ?? '';
      this._frontmatterError = data.frontmatter_error ?? '';
      this._modified = data.modified;
      this._loaded = true;
    } catch (e) {
      this._error = 'Failed to load page.';
    } finally {
      this._loading = false;
    }
  }

  async _toggleMode() {
    if (this._editing) {
      // Flush editor save before switching to view mode
      await this.#flushMetadata();
      // A failed flush leaves an unresolved _metaError with its
      // Reload/Overwrite/Retry affordance. Switching to view mode now would
      // call _fetchPage(), which unconditionally wipes #pendingFields and
      // _metaError and overwrites _frontmatter from the server — silently
      // discarding the edit and the only visible sign anything went wrong.
      // Stay in edit mode so the conflict banner remains visible instead.
      if (this._metaError) return;
      /** @type {import('./wiki-editor.js').WikiEditor|null} */
      const editor = this.querySelector('wiki-editor');
      if (editor) await editor.flushSave();
      this._editing = false;
      await this._fetchPage();
    } else {
      this._editing = true;
    }
    localStorage.setItem(EDIT_MODE_KEY, String(this._editing));
  }

  /** Public method to force edit mode (called from app.js for new pages). */
  startEditing() {
    this._editing = true;
    localStorage.setItem(EDIT_MODE_KEY, 'true');
  }

  _close() {
    this.dispatchEvent(new CustomEvent('wiki-close', {
      bubbles: true,
      composed: true,
    }));
  }

  /** @param {CustomEvent} e */
  _onSaved(e) {
    this._modified = e.detail.modified;
  }

  /**
   * The editor's conflict Reload refetched the page, so its mtime is fresh
   * and ours is stale — the next metadata write would 409 for no reason.
   * @param {CustomEvent} e
   */
  _onReloaded(e) {
    this._modified = e.detail.modified;
  }

  #startRename() {
    this._renaming = true;
    this._renameValue = this.page;
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
      this.#submitRename();
    } else if (e.key === 'Escape') {
      this.#cancelRename();
    }
  }

  async #submitRename() {
    const newPath = this._renameValue.trim();
    if (!newPath || newPath === this.page) {
      this.#cancelRename();
      return;
    }
    try {
      // Flush any pending editor save before moving the file
      /** @type {import('./wiki-editor.js').WikiEditor|null} */
      const editor = this.querySelector('wiki-editor');
      if (editor) await editor.flushSave();

      const res = await fetch('/api/vault/' + encodePagePath(this.page), {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ rename_to: newPath }),
      });
      if (res.status === 409) {
        this._renameError = 'A page already exists at that path.';
        return;
      }
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        this._renameError = data.error || `Rename failed (${res.status})`;
        return;
      }
      this._renaming = false;
      this._renameError = '';
      // Navigate to the new page
      this.dispatchEvent(new CustomEvent('wiki-open', {
        detail: { page: newPath },
        bubbles: true,
        composed: true,
      }));
    } catch {
      this._renameError = 'Rename failed.';
    }
  }

  async #deletePage() {
    if (!confirm(`Delete "${this.page}"?`)) return;
    try {
      const res = await fetch('/api/vault/' + encodePagePath(this.page), {
        method: 'DELETE',
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        alert(data.error || `Delete failed (${res.status})`);
        return;
      }
      this._close();
    } catch {
      alert('Delete failed.');
    }
  }

  /** @param {string} folderPath */
  #navigateFolder(folderPath) {
    this.dispatchEvent(new CustomEvent('wiki-navigate-folder', {
      detail: { folder: folderPath },
      bubbles: true,
      composed: true,
    }));
  }

  /** @param {Event} e */
  _handleClick(e) {
    const link = /** @type {HTMLElement} */ (e.target).closest('a.wiki-link');
    if (!link) return;
    const page = link.getAttribute('data-wiki-page');
    if (!page) return;

    if (this.standalone) return; // navigate normally via <a> href

    e.preventDefault();
    this.dispatchEvent(new CustomEvent('wiki-navigate', {
      detail: { page },
      bubbles: true,
      composed: true,
    }));
  }

  /**
   * Failure notice for a metadata write aimed at a page that is no longer on
   * screen. Rendered by the host rather than <wiki-metadata> so it survives
   * that panel's readonly/empty gating, and carries no Reload/Overwrite/Retry
   * — those would act on the wrong page.
   */
  #renderOrphanError() {
    if (!this._orphanMetaError) return nothing;
    return html`
      <div class="wiki-page-orphan-error">
        <span>${this._orphanMetaError}</span>
        <button
          type="button"
          class="wiki-page-orphan-dismiss"
          aria-label="Dismiss"
          @click=${() => { this._orphanMetaError = ''; }}
        >&times;</button>
      </div>
    `;
  }

  render() {
    const orphan = this.#renderOrphanError();
    if (this._loading) {
      return html`${orphan}<div class="wiki-page-loading">Loading...</div>`;
    }
    if (this._error) {
      return html`${orphan}<div class="wiki-page-error">${this._error}</div>`;
    }
    // Guard on load state, not on body text: a page with frontmatter but an
    // empty body is legitimate and must still render its metadata.
    if (!this._loaded) {
      return orphan;
    }

    const newTabUrl = '/vault/' + encodePagePath(this.page);
    const modeIcon = this._editing ? '\u{1f441}' : '\u{270e}';
    const modeTitle = this._editing ? 'Switch to view mode' : 'Switch to edit mode';
    const closeBtn = this.standalone ? nothing : html`
      <button class="wiki-close-btn dc-icon-btn" @click=${() => this._close()} title="Close wiki pane" aria-label="Close wiki pane">&times;</button>
    `;
    const rightButtons = html`
      <button class="wiki-edit-btn dc-icon-btn" @click=${() => this._toggleMode()} title=${modeTitle} aria-label=${modeTitle}>${modeIcon}</button>
      <button class="wiki-delete-btn dc-icon-btn" @click=${() => this.#deletePage()} title="Delete page" aria-label="Delete page">\u{1F5D1}</button>
      <a href=${newTabUrl} target="_blank" rel="noopener" class="wiki-open-tab dc-icon-btn" title="Open in new tab" aria-label="Open in new tab">&#8599;</a>
      ${closeBtn}
    `;

    // Breadcrumb content: folder segments (clickable) + page name (not clickable) + rename
    const parts = this.page.split('/');
    const pageName = parts[parts.length - 1];
    const folderParts = parts.slice(0, -1);
    const breadcrumbContent = this._renaming
      ? html`
        <div class="wiki-rename-bar">
          <input
            class="wiki-rename-input"
            aria-label="New page path"
            .value=${this._renameValue}
            @input=${(/** @type {InputEvent} */ e) => { this._renameValue = /** @type {HTMLInputElement} */ (e.target).value; }}
            @keydown=${(/** @type {KeyboardEvent} */ e) => this.#handleRenameKey(e)}
          />
          <button class="wiki-rename-ok" @click=${() => this.#submitRename()} title="Confirm rename" aria-label="Confirm rename">\u2713</button>
          <button class="wiki-rename-cancel" @click=${() => this.#cancelRename()} title="Cancel rename" aria-label="Cancel rename">\u2717</button>
          ${this._renameError ? html`<span class="wiki-rename-error">${this._renameError}</span>` : nothing}
        </div>
      `
      : html`
        <span class="wiki-page-breadcrumbs">
          ${folderParts.map((seg, i) => {
            const folderPath = folderParts.slice(0, i + 1).join('/');
            return html`
              <button type="button" class="wiki-bc-segment" @click=${() => this.#navigateFolder(folderPath)}>${seg}</button>
              <span class="wiki-bc-sep">/</span>
            `;
          })}
          <span class="wiki-bc-page">${pageName}</span>
          <button class="wiki-rename-btn dc-icon-btn" @click=${() => this.#startRename()} title="Rename / move page" aria-label="Rename / move page">\u{270e}</button>
        </span>
      `;

    const metadataPanel = html`
      <wiki-metadata
        ?readonly=${!this._editing}
        .frontmatter=${this._frontmatter}
        .frontmatterRaw=${this._frontmatterRaw}
        .frontmatterError=${this._frontmatterError}
        .metaError=${this._metaError}
        @metadata-change=${this._onMetadataChange}
        @metadata-raw-save=${this._onMetadataRawSave}
        @metadata-reload=${this._onMetadataReload}
        @metadata-overwrite=${this._onMetadataOverwrite}
        @metadata-retry=${this._onMetadataRetry}
      ></wiki-metadata>
    `;

    if (this._editing) {
      return html`
        <div class="wiki-page">
          ${orphan}
          ${metadataPanel}
          <wiki-editor
            page=${this.page}
            .content=${this._body}
            .modified=${this._modified}
            .toolbarLeft=${breadcrumbContent}
            .toolbarExtra=${rightButtons}
            @saved=${this._onSaved}
            @reloaded=${this._onReloaded}
          ></wiki-editor>
        </div>
      `;
    }

    return html`
      <div class="wiki-page">
        ${orphan}
        <div class="wiki-page-toolbar">
          ${breadcrumbContent}
          <span class="wiki-editor-spacer"></span>
          ${this._modified ? html`<span class="wiki-page-date">${formatDate(this._modified)}</span>` : nothing}
          ${rightButtons}
        </div>
        ${metadataPanel}
        <div class="wiki-page-body" @click=${this._handleClick}>
          ${unsafeHTML(renderMarkdown(this._body))}
        </div>
      </div>
    `;
  }
}

customElements.define('wiki-page', WikiPage);
