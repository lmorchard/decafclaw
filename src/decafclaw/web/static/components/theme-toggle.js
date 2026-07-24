import { LitElement, html } from 'lit';
import { THEMES, applyTheme, loadStoredTheme, storeTheme } from '../lib/theme.js';

/**
 * Theme toggle: base light/dark/system buttons + a color-palette popover.
 *
 * A single active theme name is chosen either via a base button (which clears
 * any custom palette) or via a palette item (which supersedes the base and
 * drives its own base mode). Persists via lib/theme.js.
 */
export class ThemeToggle extends LitElement {
  static properties = {
    _theme: { type: String, state: true },
    _paletteOpen: { type: Boolean, state: true },
  };

  createRenderRoot() { return this; }

  constructor() {
    super();
    this._theme = loadStoredTheme();
    this._paletteOpen = false;
    applyTheme(this._theme);
    /** @param {MouseEvent} e */
    this._onDocClick = (e) => {
      if (this._paletteOpen && !this.contains(/** @type {Node} */ (e.target))) {
        this._paletteOpen = false;
      }
    };
  }

  connectedCallback() {
    super.connectedCallback();
    document.addEventListener('click', this._onDocClick);
  }

  disconnectedCallback() {
    document.removeEventListener('click', this._onDocClick);
    super.disconnectedCallback();
  }

  /** @param {string} name */
  #select(name) {
    this._theme = applyTheme(name);
    storeTheme(this._theme);
    this._paletteOpen = false;
  }

  render() {
    const bases = THEMES.filter((t) => t.kind === 'base');
    const palettes = THEMES.filter((t) => t.kind === 'palette');
    const paletteActive = palettes.some((p) => p.name === this._theme);
    return html`
      <div class="theme-toggle">
        ${bases.map((t) => html`
          <button
            class="theme-btn ${this._theme === t.name ? 'active' : ''}"
            @click=${() => this.#select(t.name)}
            title=${t.label}
          >${t.icon}</button>
        `)}
        <div class="theme-palette-wrap">
          <button
            class="theme-btn ${paletteActive ? 'active' : ''}"
            @click=${() => { this._paletteOpen = !this._paletteOpen; }}
            title="Color palette"
            aria-haspopup="true"
            aria-expanded=${this._paletteOpen ? 'true' : 'false'}
          >🎨</button>
          ${this._paletteOpen ? html`
            <div class="theme-palette-menu" role="menu">
              ${palettes.map((p) => html`
                <button
                  class="theme-palette-item ${this._theme === p.name ? 'active' : ''}"
                  role="menuitem"
                  @click=${() => this.#select(p.name)}
                >
                  <span class="theme-palette-dot" style="background:${p.dot}"></span>
                  ${p.label}
                </button>
              `)}
            </div>
          ` : ''}
        </div>
      </div>
    `;
  }
}

customElements.define('theme-toggle', ThemeToggle);
