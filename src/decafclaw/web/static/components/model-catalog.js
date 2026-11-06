/**
 * Model catalog manager (#1211).
 *
 * Lists models auto-discovered from configured providers with
 * enable/disable toggles, a discovery refresh button, a client-side
 * search filter, and the global default-model selector. Enabled
 * discovered models join hand-authored
 * configs in the conversation model picker; the default pointer lives in
 * the model_catalog.json sidecar — config.json is never written here.
 *
 * Dispatches nothing; changes push `models_available` updates to all
 * open tabs over WebSocket.
 */

import { LitElement, html, nothing } from 'lit';
import { repeat } from 'lit/directives/repeat.js';
import { ApiError, DefaultService } from '../lib/api-client/index.js';

export class ModelCatalog extends LitElement {
  static properties = {
    _catalog: { state: true },
    _loading: { state: true },
    _busy: { state: true },
    _error: { state: true },
    _filter: { state: true },
    store: { type: Object, attribute: false },
    _activeModel: { type: String, state: true },
  };
  createRenderRoot() { return this; }

  constructor() {
    super();
    /** @type {import('../lib/api-client/index.js').ModelCatalogResponse|null} */
    this._catalog = null;
    this._loading = false;
    this._busy = false;
    this._error = '';
    /** Client-side search text for the model list; only the input mutates it. */
    this._filter = '';
    this.store = null;
    this._activeModel = '';
  }

  connectedCallback() {
    super.connectedCallback();
    if (this.store) {
      this.store.addEventListener('change', this.#onStoreChange);
      this.#syncFromStore();
    }
    void this.#fetchCatalog();
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    this.store?.removeEventListener('change', this.#onStoreChange);
  }

  #onStoreChange = () => {
    this.#syncFromStore();
  };

  #syncFromStore() {
    if (!this.store) return;
    this._activeModel = this.store.activeModel || '';
  }

  updated(changedProps) {
    if (changedProps.has('store')) {
      const oldStore = changedProps.get('store');
      if (oldStore) oldStore.removeEventListener('change', this.#onStoreChange);
      if (this.store) {
        // app.js assigns the shared conversation store after connect, so the
        // listener must be (re)registered here — same contract as ChatControls.
        this.store.addEventListener('change', this.#onStoreChange);
        this.#syncFromStore();
      }
    }
  }

  async #fetchCatalog() {
    this._loading = true;
    this._error = '';
    try {
      this._catalog = await DefaultService.wrapperApiModelsCatalogGet();
    } catch (e) {
      this._error = this.#errorText(e, 'Failed to load model catalog');
    } finally {
      this._loading = false;
    }
  }

  async #discover() {
    this._busy = true;
    this._error = '';
    try {
      this._catalog = await DefaultService.wrapperApiModelsCatalogDiscoverPost();
    } catch (e) {
      this._error = this.#errorText(e, 'Discovery failed');
    } finally {
      this._busy = false;
    }
  }

  /**
   * @param {string} key
   * @param {boolean} enabled
   */
  async #setEnabled(key, enabled) {
    this._busy = true;
    this._error = '';
    try {
      this._catalog = await DefaultService.wrapperApiModelsCatalogModelPost({ key, enabled });
    } catch (e) {
      this._error = this.#errorText(e, 'Failed to update model');
      void this.#fetchCatalog();
    } finally {
      this._busy = false;
    }
  }

  /** @param {string} model */
  async #setDefault(model) {
    this._busy = true;
    this._error = '';
    try {
      this._catalog = await DefaultService.wrapperApiModelsCatalogDefaultPost({ model });
    } catch (e) {
      this._error = this.#errorText(e, 'Failed to set default model');
      void this.#fetchCatalog();
    } finally {
      this._busy = false;
    }
  }

  /** @param {string} model */
  async #setAuxApproval(model) {
    this._busy = true;
    this._error = '';
    try {
      this._catalog = await DefaultService.wrapperApiModelsCatalogAuxApprovalPost({ model });
    } catch (e) {
      this._error = this.#errorText(e, 'Failed to set auto-approval model');
      void this.#fetchCatalog();
    } finally {
      this._busy = false;
    }
  }

  /** @param {string} model */
  async #setReflection(model) {
    this._busy = true;
    this._error = '';
    try {
      this._catalog = await DefaultService.wrapperApiModelsCatalogReflectionPost({ model });
    } catch (e) {
      this._error = this.#errorText(e, 'Failed to set reflection model');
      void this.#fetchCatalog();
    } finally {
      this._busy = false;
    }
  }

  /** @param {string} model */
  async #setVerifier(model) {
    this._busy = true;
    this._error = '';
    try {
      this._catalog = await DefaultService.wrapperApiModelsCatalogVerifierPost({ model });
    } catch (e) {
      this._error = this.#errorText(e, 'Failed to set verifier model');
      void this.#fetchCatalog();
    } finally {
      this._busy = false;
    }
  }

  /**
   * Bulk enable/disable every discovered model of one provider. Catalog
   * entries only; hand-authored configs are never touched server-side.
   * @param {string} provider
   * @param {boolean} enabled
   */
  async #setProviderEnabled(provider, enabled) {
    this._busy = true;
    this._error = '';
    try {
      this._catalog = await DefaultService.wrapperApiModelsCatalogProviderPost({ provider, enabled });
    } catch (e) {
      this._error = this.#errorText(e, 'Failed to update provider models');
      void this.#fetchCatalog();
    } finally {
      this._busy = false;
    }
  }

  /**
   * @param {unknown} err
   * @param {string} fallback
   * @returns {string}
   */
  #errorText(err, fallback) {
    if (err instanceof ApiError && err.body?.error) return err.body.error;
    return err instanceof Error && err.message ? `${fallback}: ${err.message}` : fallback;
  }

  /** Trimmed, lowercased filter text; empty string means no filter. */
  #filterQuery() {
    return this._filter.trim().toLowerCase();
  }

  /**
   * Models of one provider visible under the current filter. An empty
   * query matches everything; otherwise the query is a case-insensitive
   * substring of the model id, the catalog key, or the provider name —
   * a provider-name match shows all of that provider's models.
   * @param {string} name
   * @param {import('../lib/api-client/index.js').CatalogProviderEntry} provider
   * @param {string} query
   * @returns {import('../lib/api-client/index.js').CatalogModelEntry[]}
   */
  #matchedModels(name, provider, query) {
    if (!query) return provider.models;
    return provider.models.filter(m => this.#modelMatches(name, m, query));
  }

  /**
   * @param {string} name
   * @param {import('../lib/api-client/index.js').CatalogModelEntry} m
   * @param {string} query
   */
  #modelMatches(name, m, query) {
    return m.id.toLowerCase().includes(query)
      || m.key.toLowerCase().includes(query)
      || name.toLowerCase().includes(query);
  }

  render() {
    const catalog = this._catalog;
    const query = this.#filterQuery();
    const filtering = query !== '';
    const providers = catalog ? Object.entries(catalog.providers) : [];
    const sections = providers.map(([name, p]) => ({
      name,
      provider: p,
      models: this.#matchedModels(name, p, query),
    }));
    const totalMatches = sections.reduce((n, s) => n + s.models.length, 0);
    return html`
      <div class="model-catalog">
        <div class="model-catalog-header">
          <span class="model-catalog-title">Models</span>
          <button
            class="model-catalog-refresh-btn outline"
            ?disabled=${this._busy}
            @click=${() => void this.#discover()}
            title="Re-discover models from configured providers"
          >${this._busy ? 'Working…' : 'Refresh'}</button>
        </div>

        ${this._error ? html`<div class="model-catalog-error">${this._error}</div>` : nothing}
        ${this._loading && !catalog ? html`<p class="model-catalog-loading">Loading…</p>` : nothing}

        ${catalog ? this.#renderDefaultPicker(catalog) : nothing}
        ${catalog ? this.#renderAuxApprovalPicker(catalog) : nothing}
        ${catalog ? this.#renderReflectionPicker(catalog) : nothing}
        ${catalog ? this.#renderVerifierPicker(catalog) : nothing}
        ${catalog ? this.#renderFilter() : nothing}
        ${catalog
          ? repeat(sections, s => s.name, s => this.#renderProvider(s.name, s.provider, s.models, filtering))
          : nothing}
        ${catalog && filtering && providers.length > 0 && totalMatches === 0
          ? html`<p class="model-catalog-no-match">No models match '${this._filter.trim()}'.</p>`
          : nothing}
        ${catalog && providers.length === 0
          ? html`<p class="model-catalog-empty" style="padding: 1rem; color: var(--pico-muted-color);">
              No providers configured, or discovery has not run yet.
            </p>`
          : nothing}
      </div>
    `;
  }

  /** Dynamic search field filtering the model list client-side. */
  #renderFilter() {
    return html`
      <input
        type="search"
        class="model-catalog-filter-input"
        placeholder="Filter models…"
        title="Type to filter the model list; Esc clears"
        aria-label="Filter models"
        .value=${this._filter}
        @input=${(e) => { this._filter = e.target.value; }}
        @keydown=${(e) => {
          if (e.key === 'Escape' && this._filter) this._filter = '';
        }}
      />
    `;
  }

  /** @param {import('../lib/api-client/index.js').ModelCatalogResponse} catalog */
  #renderDefaultPicker(catalog) {
    return html`
      <div class="model-catalog-default">
        <label class="model-catalog-default-label" for="catalog-default-select">Default model</label>
        <select
          id="catalog-default-select"
          class="model-catalog-default-select"
          ?disabled=${this._busy}
          @change=${(e) => void this.#setDefault(e.target.value)}
        >
          <option value="" ?selected=${catalog.default_pointer === ''}>
            (config.json default)
          </option>
          ${repeat(catalog.picker_models, m => m, m => html`
            <option value="${m}" ?selected=${m === catalog.default_pointer}>${m}</option>
          `)}
        </select>
        <span class="model-catalog-default-hint" title="Used for conversations with no model selected">
          effective: ${catalog.default_model || '(none)'}
        </span>
      </div>
    `;
  }

  /** Auto-approval-model picker (#1213): enabled catalog models + explicit "use conversation model". */
  #renderAuxApprovalPicker(catalog) {
    return html`
      <div class="model-catalog-judge">
        <label class="model-catalog-default-label" for="catalog-aux-approval-select">Auto-approval model</label>
        <select
          id="catalog-aux-approval-select"
          class="model-catalog-default-select"
          ?disabled=${this._busy}
          @change=${(e) => void this.#setAuxApproval(e.target.value)}
        >
          <option value="" ?selected=${!catalog.aux_approval_pointer}>${'(use conversation model)'}</option>
          ${repeat(catalog.picker_models, m => m, m => html`
            <option value="${m}" ?selected=${m === catalog.aux_approval_pointer}>${m}</option>
          `)}
        </select>
        <span class="model-catalog-default-hint" title="Judge that grades shell auto-approvals; empty = ride on the active conversation model">${catalog.aux_approval_model || '(use conversation model)'}</span>
      </div>
    `;
  }

  /** Reflection-model picker (#1212): enabled catalog models + explicit inherit. */
  #renderReflectionPicker(catalog) {
    // #1212: an explicitly selected judge equal to the active conversation
    // model means the author grades its own homework — allowed, but surfaced
    // as a warning (#591).
    const judgeWarning = Boolean(catalog.reflection_pointer) && catalog.reflection_pointer === this._activeModel;
    return html`
      <div class="model-catalog-judge">
        <label class="model-catalog-default-label" for="catalog-reflection-select">Reflection model</label>
        <select
          id="catalog-reflection-select"
          class="model-catalog-default-select"
          ?disabled=${this._busy}
          @change=${(e) => void this.#setReflection(e.target.value)}
        >
          <option value="" ?selected=${!catalog.reflection_pointer}>${'(config.json reflection / inherit)'}</option>
          ${repeat(catalog.picker_models, m => m, m => html`
            <option value="${m}" ?selected=${m === catalog.reflection_pointer}>${m}</option>
          `)}
        </select>
        <span class="model-catalog-default-hint" title="Judge that grades each turn; empty = inherit">${catalog.reflection_model || '(inherit)'}</span>
        ${judgeWarning ? html`
          <div class="model-catalog-judge-warning" role="status">
            Reflection model is the active conversation model — the author would grade its own homework.
          </div>
        ` : nothing}
      </div>
    `;
  }

  /** Verifier-model picker (#1212): enabled catalog models + explicit inherit. */
  #renderVerifierPicker(catalog) {
    return html`
      <div class="model-catalog-judge">
        <label class="model-catalog-default-label" for="catalog-verifier-select">Verifier model</label>
        <select
          id="catalog-verifier-select"
          class="model-catalog-default-select"
          ?disabled=${this._busy}
          @change=${(e) => void this.#setVerifier(e.target.value)}
        >
          <option value="" ?selected=${!catalog.verifier_pointer}>${'(config.json verifier / inherit)'}</option>
          ${repeat(catalog.picker_models, m => m, m => html`
            <option value="${m}" ?selected=${m === catalog.verifier_pointer}>${m}</option>
          `)}
        </select>
        <span class="model-catalog-default-hint" title="Second-opinion judge; empty = inherit">${catalog.verifier_model || '(inherit)'}</span>
      </div>
    `;
  }

  /**
   * @param {string} name
   * @param {import('../lib/api-client/index.js').CatalogProviderEntry} provider
   * @param {import('../lib/api-client/index.js').CatalogModelEntry[]} shown models after the filter
   * @param {boolean} filtering whether a filter is active
   */
  #renderProvider(name, provider, shown, filtering) {
    if (filtering && shown.length === 0) return nothing;
    // Summary counts are real totals, not filtered counts.
    const enabledCount = provider.models.filter(m => m.enabled).length;
    return html`
      <details class="model-catalog-provider" ?open=${filtering || nothing}>
        <summary class="model-catalog-provider-header">
          <span class="model-catalog-provider-name">${name}</span>
          ${provider.error ? html`<span class="model-catalog-provider-error" title="${provider.error}">⚠ discovery failed</span>` : nothing}
          <span class="model-catalog-provider-tools">
            ${provider.models.length > 0
              ? html`
                  <button
                    class="model-catalog-bulk-btn outline"
                    ?disabled=${this._busy}
                    @click=${(e) => {
                      // A button inside <summary> toggles the disclosure by
                      // default; keep the bulk action click open-state-neutral.
                      e.preventDefault();
                      void this.#setProviderEnabled(name, true);
                    }}
                    title="Enable every discovered model of this provider"
                  >Select all</button>
                  <button
                    class="model-catalog-bulk-btn outline"
                    ?disabled=${this._busy}
                    @click=${(e) => {
                      e.preventDefault();
                      void this.#setProviderEnabled(name, false);
                    }}
                    title="Disable every discovered model of this provider"
                  >Deselect all</button>
                `
              : nothing}
            <span class="model-catalog-provider-count" title="Enabled / discovered models">${enabledCount}/${provider.models.length} enabled</span>
          </span>
        </summary>
        ${provider.models.length === 0
          ? html`<p class="model-catalog-provider-empty">No models discovered.</p>`
          : html`
              <ul class="model-catalog-model-list">
                ${repeat(shown, m => m.key, m => this.#renderModel(name, m))}
              </ul>
            `}
      </details>
    `;
  }

  /**
   * @param {string} providerName
   * @param {import('../lib/api-client/index.js').CatalogModelEntry} m
   */
  #renderModel(providerName, m) {
    const isDefault = this._catalog?.default_model === m.key;
    return html`
      <li class="model-catalog-model" data-model-key=${m.key}>
        <label class="model-catalog-model-toggle">
          <input
            type="checkbox"
            ?checked=${m.enabled}
            ?disabled=${this._busy}
            @change=${(e) => void this.#setEnabled(m.key, e.target.checked)}
          />
          <span class="model-catalog-model-id">${m.id}</span>
        </label>
        ${isDefault ? html`<span class="model-catalog-default-badge">default</span>` : nothing}
        <span class="model-catalog-model-meta" title="Reported capabilities">${this.#metaText(m)}</span>
      </li>
    `;
  }

  /**
   * Capability summary; unknown capabilities say so rather than implying support.
   * @param {import('../lib/api-client/index.js').CatalogModelEntry} m
   */
  #metaText(m) {
    const parts = [];
    parts.push(m.chat === null ? 'chat?' : m.chat ? 'chat' : 'no chat');
    parts.push(m.tools === null ? 'tools?' : m.tools ? 'tools' : 'no tools');
    parts.push(m.context_window_size ? `${m.context_window_size.toLocaleString()} ctx` : 'ctx?');
    return parts.join(' · ');
  }
}

customElements.define('model-catalog', ModelCatalog);
