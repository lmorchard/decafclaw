/**
 * Model catalog manager (#1211).
 *
 * Lists models auto-discovered from configured providers with
 * enable/disable toggles, a discovery refresh button, and the global
 * default-model selector. Enabled discovered models join hand-authored
 * configs in the conversation model picker; the default pointer lives in
 * the model_catalog.json sidecar — config.json is never written here.
 *
 * Dispatches nothing; changes push `models_available` updates to all
 * open tabs over WebSocket.
 */

import { LitElement, html, nothing } from 'lit';
import { ApiError, DefaultService } from '../lib/api-client/index.js';

export class ModelCatalog extends LitElement {
  static properties = {
    _catalog: { state: true },
    _loading: { state: true },
    _busy: { state: true },
    _error: { state: true },
  };
  createRenderRoot() { return this; }

  constructor() {
    super();
    /** @type {import('../lib/api-client/index.js').ModelCatalogResponse|null} */
    this._catalog = null;
    this._loading = false;
    this._busy = false;
    this._error = '';
  }

  connectedCallback() {
    super.connectedCallback();
    void this.#fetchCatalog();
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

  render() {
    const catalog = this._catalog;
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
        ${catalog ? Object.entries(catalog.providers).map(([name, p]) => this.#renderProvider(name, p)) : nothing}
        ${catalog && Object.keys(catalog.providers).length === 0
          ? html`<p class="model-catalog-empty" style="padding: 1rem; color: var(--pico-muted-color);">
              No providers configured, or discovery has not run yet.
            </p>`
          : nothing}
      </div>
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
          ${catalog.picker_models.map(m => html`
            <option value="${m}" ?selected=${m === catalog.default_pointer}>${m}</option>
          `)}
        </select>
        <span class="model-catalog-default-hint" title="Used for conversations with no model selected">
          effective: ${catalog.default_model || '(none)'}
        </span>
      </div>
    `;
  }

  /**
   * @param {string} name
   * @param {import('../lib/api-client/index.js').CatalogProviderEntry} provider
   */
  #renderProvider(name, provider) {
    const enabledCount = provider.models.filter(m => m.enabled).length;
    return html`
      <details class="model-catalog-provider">
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
                ${provider.models.map(m => this.#renderModel(name, m))}
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
