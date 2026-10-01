/**
 * @typedef {{page: string, modified: number}} MetaTarget
 */

export class WikiWriteMutex {
  constructor(apiPut) {
    /** @type {Record<string, import('./api-client/index.js').JsonValue>} */
    this.pendingFields = {};
    /** @type {Promise<void> | null} */
    this.metaInFlight = null;
    /** @type {{kind: 'raw', raw: string} | {kind: 'patch'} | null} */
    this.lastMetaAttempt = null;
    /** @type {{status: 'conflict'|'error', message: string} | null} */
    this.metaError = null;
    /** @type {string} */
    this.orphanMetaError = '';

    // Dependencies
    /** @type {(payload: Parameters<typeof import('./api-client/index.js').DefaultService.wrapperApiVaultPagePut>[1], page: string, modified: number) => Promise<{ok: true, status: number, error: '', data: import('./api-client/index.js').VaultWriteResponse} | {ok: false, status: number, error: string}>} */
    this.apiPut = apiPut;
  }

  /**
   * Queue a metadata typed-patch edit.
   * @param {Record<string, import('./api-client/index.js').JsonValue>} fields
   * @returns {boolean} true if it can be auto-flushed, false if blocked by conflict
   */
  queueFields(fields) {
    Object.assign(this.pendingFields, fields);
    return this.metaError?.status !== 'conflict';
  }

  /**
   * Send debounced typed patch now.
   * @param {string} currentPage
   * @param {number} currentModified
   * @param {MetaTarget} [target]
   * @returns {Promise<{ok: true, data: import('./api-client/index.js').VaultWriteResponse} | void>}
   */
  async flush(currentPage, currentModified, target) {
    const fields = this.pendingFields;
    if (this.metaError?.status === 'conflict') {
      if (target && Object.keys(fields).length) {
        this.pendingFields = {};
        this._orphan(target.page, 'an unresolved conflict');
      }
      return;
    }
    this.pendingFields = {};
    if (!Object.keys(fields).length) return;

    const res = await this._writeMeta({ frontmatter: fields }, currentPage, currentModified, { target });
    if (res.ok) return { ok: true, data: res.data };

    if (target) {
      this._orphan(
        target.page,
        res.status === 409 ? 'it was modified externally' : res.error
      );
      return;
    }

    this.pendingFields = { ...fields, ...this.pendingFields };
    this.lastMetaAttempt = { kind: 'patch' };
    this.metaError = res.status === 409
      ? { status: 'conflict', message: 'Metadata was modified externally.' }
      : { status: 'error', message: res.error };
  }

  /**
   * @param {string} raw
   * @param {string} currentPage
   * @param {number} currentModified
   */
  async saveRaw(raw, currentPage, currentModified) {
    await this.flush(currentPage, currentModified);
    if (this.metaError) {
      return { ok: false, error: 'Resolve the pending metadata conflict above before saving raw YAML.' };
    }
    const res = await this._writeMeta({ frontmatter_raw: raw }, currentPage, currentModified);
    if (res.ok) {
      await this.flush(currentPage, currentModified);
      return { ok: true, data: res.data };
    } else if (res.status === 409) {
      this.lastMetaAttempt = { kind: 'raw', raw };
      this.metaError = { status: 'conflict', message: 'Metadata was modified externally.' };
      return { ok: false, error: 'Metadata was modified externally.' };
    } else {
      return { ok: false, error: res.error };
    }
  }

  reload() {
    this.pendingFields = {};
    this.lastMetaAttempt = null;
    this.metaError = null;
  }

  /**
   * @param {string} currentPage
   * @param {number} currentModified
   */
  async overwrite(currentPage, currentModified) {
    const attempt = this.lastMetaAttempt;
    this.lastMetaAttempt = null;
    if (!attempt) {
      this.metaError = null;
      return;
    }
    if (attempt.kind === 'raw') {
      return await this._doOverwrite({ frontmatter_raw: attempt.raw }, attempt, undefined, currentPage, currentModified);
    }
    const fields = this.pendingFields;
    this.pendingFields = {};
    if (!Object.keys(fields).length) {
      this.metaError = null;
      return;
    }
    return await this._doOverwrite({ frontmatter: fields }, { kind: 'patch' }, fields, currentPage, currentModified);
  }

  /**
   * @param {string} currentPage
   * @param {number} currentModified
   */
  async retry(currentPage, currentModified) {
    const attempt = this.lastMetaAttempt;
    this.metaError = null;
    if (attempt?.kind === 'raw') {
      return await this.saveRaw(attempt.raw, currentPage, currentModified);
    } else {
      return await this.flush(currentPage, currentModified);
    }
  }

  // --- Internal -----------------------------------------------------------

  /** @param {string} page @param {string} reason */
  _orphan(page, reason) {
    this.orphanMetaError = `Metadata edit to "${page}" was not saved (${reason}). Reopen that page to redo it.`;
  }

  /**
   * @param {Parameters<typeof import('./api-client/index.js').DefaultService.wrapperApiVaultPagePut>[1]} payload
   * @param {string} currentPage
   * @param {number} currentModified
   * @param {{skipModifiedCheck?: boolean, target?: MetaTarget}} [opts]
   * @returns {Promise<{ok: true, status: number, error: '', data: import('./api-client/index.js').VaultWriteResponse} | {ok: false, status: number, error: string}>}
   */
  async _writeMeta(payload, currentPage, currentModified, opts) {
    while (this.metaInFlight) await this.metaInFlight;
    let release = () => {};
    this.metaInFlight = new Promise(r => { release = r; });
    try {
      const page = opts?.target ? opts.target.page : currentPage;
      const modified = opts?.target ? opts.target.modified : currentModified;
      const body = opts?.skipModifiedCheck ? { ...payload } : { ...payload, modified };
      
      const res = await this.apiPut(body, page, modified);
      
      if (res.ok && !opts?.target) {
        this.metaError = null;
        this.lastMetaAttempt = null;
      }
      return res;
    } finally {
      release();
      this.metaInFlight = null;
    }
  }

  /**
   * @param {Parameters<typeof import('./api-client/index.js').DefaultService.wrapperApiVaultPagePut>[1]} payload
   * @param {{kind: 'raw', raw: string} | {kind: 'patch'}} attempt
   * @param {Record<string, import('./api-client/index.js').JsonValue> | undefined} patchFields
   * @param {string} currentPage
   * @param {number} currentModified
   */
  async _doOverwrite(payload, attempt, patchFields, currentPage, currentModified) {
    const res = await this._writeMeta(payload, currentPage, currentModified, { skipModifiedCheck: true });
    if (res.ok) {
      const flushRes = await this.flush(currentPage, currentModified);
      return { ok: true, data: res.data, flushData: flushRes ? flushRes.data : undefined };
    }
    if (attempt.kind === 'patch' && patchFields) {
      this.pendingFields = { ...patchFields, ...this.pendingFields };
    }
    this.lastMetaAttempt = attempt;
    this.metaError = { status: 'error', message: res.error };
    return { ok: false, error: res.error };
  }
}
