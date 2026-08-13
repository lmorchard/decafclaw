const fs = require('fs');
let code = fs.readFileSync('components/wiki-page.js', 'utf8');

code = code.replace(
  "import { encodePagePath } from '../lib/utils.js';",
  "import { encodePagePath } from '../lib/utils.js';\nimport { WikiWriteMutex } from '../lib/wiki-page-write-mutex.js';"
);

code = code.replace(
  "  #metaTimer = null;",
  "  #metaTimer = null;\n  /** @type {WikiWriteMutex} */\n  #mutex;"
);

// We need to remove the other private fields
code = code.replace("  /** @type {Record<string, any>} */\n  #pendingFields = {};\n", "");
code = code.replace("  /**\n   * Gate serializing every metadata write (typed patch and raw replace)\n   * against every other. Whoever wants to write awaits any existing value\n   * here first, then installs their own until they're done — so a raw save\n   * and a typed-patch flush can never have two PUTs in flight at once, each\n   * carrying the same now-stale `modified`.\n   * @type {Promise<void> | null}\n   */\n  #metaInFlight = null;\n", "");
code = code.replace("  /**\n   * What to resend for Retry/Overwrite after a metadata write fails. Typed\n   * patches don't need their fields stored here — a failed patch is merged\n   * back into #pendingFields, so retrying is just flushing again.\n   * @type {{kind: 'raw', raw: string} | {kind: 'patch'} | null}\n   */\n  #lastMetaAttempt = null;\n", "");

code = code.replace(
  "    /** @type {string} */ this._renameError = '';\n  }",
  "    /** @type {string} */ this._renameError = '';\n    this.#mutex = new WikiWriteMutex(this.#apiPut.bind(this));\n  }"
);

code = code.replace(
  "  /** @param {CustomEvent} e */\n  _onMetadataChange(e) {\n    Object.assign(this.#pendingFields, e.detail.fields);\n    // A conflict must be resolved (Reload/Overwrite) before we try again —\n    // auto-firing another flush would just carry the same stale `modified`\n    // into another silent 409. The edit stays queued in #pendingFields.\n    if (this._metaError?.status === 'conflict') return;\n    if (this.#metaTimer != null) clearTimeout(this.#metaTimer);\n    this.#metaTimer = setTimeout(() => { this.#flushMetadata(); }, 600);\n  }",
  `  /** @param {CustomEvent} e */
  _onMetadataChange(e) {
    if (!this.#mutex.queueFields(e.detail.fields)) return;
    if (this.#metaTimer != null) clearTimeout(this.#metaTimer);
    this.#metaTimer = setTimeout(() => { this.#flushMetadata(); }, 600);
  }`
);

// We want to replace everything from `async #writeMeta` up to (and including) `async #putMetadata` with our new methods
// Let's find the indices.
const startIdx = code.indexOf("  /**\n   * Serialize one metadata PUT against any other in-flight metadata write.");
const endIdx = code.indexOf("  async _fetchPage() {");

const newMethods = `  /**
   * Send any debounced typed patch now. Resolves when the write completes.
   * @param {import('../lib/wiki-page-write-mutex.js').MetaTarget} [target] Page to write to, when it is no longer the
   *   one \`this.page\` names — i.e. we're navigating away from it.
   */
  async #flushMetadata(target) {
    if (this.#metaTimer != null) {
      clearTimeout(this.#metaTimer);
      this.#metaTimer = null;
    }
    await this.#mutex.flush(this.page, this._modified, target);
    this._syncMutexState();
  }

  /** @param {CustomEvent} e */
  async _onMetadataRawSave(e) {
    const res = await this.#mutex.saveRaw(e.detail.raw, this.page, this._modified);
    if (res?.ok) {
      /** @type {any} */ (this.querySelector('wiki-metadata'))?.closeRaw();
    } else if (res?.error && res.error !== 'Metadata was modified externally.' && res.error !== 'Resolve the pending metadata conflict above before saving raw YAML.') {
      // Malformed YAML etc. — keep the raw editor's existing inline error.
      /** @type {any} */ (this.querySelector('wiki-metadata'))?.setRawError(res.error);
    } else if (res?.error === 'Resolve the pending metadata conflict above before saving raw YAML.') {
      /** @type {any} */ (this.querySelector('wiki-metadata'))?.setRawError(res.error);
    }
    this._syncMutexState();
  }

  /** Refetch the page from the server, discarding any pending local metadata edit. */
  async _onMetadataReload() {
    this.#mutex.reload();
    this._syncMutexState();
    /** @type {any} */ (this.querySelector('wiki-metadata'))?.closeRaw();
    await this._fetchPage();
  }

  /** Resend the last failed write, skipping the server's mtime check. */
  async _onMetadataOverwrite() {
    const wasRaw = this.#mutex.lastMetaAttempt?.kind === 'raw';
    await this.#mutex.overwrite(this.page, this._modified);
    if (wasRaw && !this.#mutex.metaError) {
      /** @type {any} */ (this.querySelector('wiki-metadata'))?.closeRaw();
    }
    this._syncMutexState();
  }

  /** Retry the last failed write with the normal (checked) path. */
  async _onMetadataRetry() {
    await this.#mutex.retry(this.page, this._modified);
    this._syncMutexState();
  }

  _syncMutexState() {
    this._metaError = this.#mutex.metaError;
    this._orphanMetaError = this.#mutex.orphanMetaError;
  }

  /**
   * @param {object} body
   * @param {string} page
   * @param {number} modified
   * @returns {Promise<{ok: boolean, status: number, error: string, data?: any}>}
   */
  async #apiPut(body, page, modified) {
    try {
      const res = await fetch('/api/vault/' + encodePagePath(page), {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        return { ok: false, status: res.status, error: data.error || \`Save failed (\${res.status})\` };
      }
      
      // Adopt the response into our own state only while we're still showing
      // the page it was written to.
      if (page === this.page) {
        this._frontmatter = data.frontmatter ?? {};
        this._frontmatterRaw = data.frontmatter_raw ?? '';
        this._frontmatterError = data.frontmatter_error ?? '';
        this._modified = data.modified;
        /** @type {any} */
        const editor = this.querySelector('wiki-editor');
        if (editor) editor.modified = data.modified;
      }
      return { ok: true, status: res.status, error: '', data };
    } catch (err) {
      return { ok: false, status: 0, error: 'Save failed (network error)' };
    }
  }

`;

code = code.substring(0, startIdx) + newMethods + code.substring(endIdx);

fs.writeFileSync('components/wiki-page.js', code, 'utf8');
