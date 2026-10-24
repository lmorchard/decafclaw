import { LitElement, html } from 'lit';
import { THEMES, applyTheme, loadStoredTheme, resolveTheme, storeTheme } from '../lib/theme.js';

/**
 * Theme toggle: consolidated theme selector button + popover menu.
 *
 * A single theme control displays the active theme's icon (☀️/🌙/💻 for base
 * themes, 🎨 for color palettes) and opens a disclosure popover containing both
 * the structural base modes (Light, Dark, System) and the color palettes.
 *
 * The popover is a disclosure (not an ARIA menu): the trigger carries
 * `aria-expanded`, the items are plain buttons, and it closes on outside-click
 * or Escape.
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
    /** @param {KeyboardEvent} e */
    this._onDocKeydown = (e) => {
      if (this._paletteOpen && e.key === 'Escape') {
        this._paletteOpen = false;
        /** @type {HTMLButtonElement | null} */ (this.querySelector('.theme-btn'))?.focus();
      }
    };
  }

  connectedCallback() {
    super.connectedCallback();
    document.addEventListener('click', this._onDocClick);
    document.addEventListener('keydown', this._onDocKeydown);
  }

  disconnectedCallback() {
    document.removeEventListener('click', this._onDocClick);
    document.removeEventListener('keydown', this._onDocKeydown);
    super.disconnectedCallback();
  }

  /** @param {string} name */
  #select(name) {
    this._theme = applyTheme(name);
    storeTheme(this._theme);
    this._paletteOpen = false;
  }

  render() {
    const current = resolveTheme(this._theme);
    const bases = THEMES.filter((t) => t.kind === 'base');
    const palettes = THEMES.filter((t) => t.kind === 'palette');
    const triggerIcon = current.icon ?? '🎨';

    return html`
      <div class="theme-toggle">
        <div class="theme-palette-wrap">
          <button
            type="button"
            class="theme-btn ${this._paletteOpen ? 'active' : ''}"
            @click=${() => { this._paletteOpen = !this._paletteOpen; }}
            title="Theme: ${current.label}"
            aria-label="Select theme"
            aria-expanded=${this._paletteOpen ? 'true' : 'false'}
          >${triggerIcon}</button>
          ${this._paletteOpen ? html`
            <div class="theme-palette-menu">
              <div class="theme-menu-section-header">Base</div>
              ${bases.map((t) => html`
                <button
                  type="button"
                  class="theme-palette-item ${this._theme === t.name ? 'active' : ''}"
                  @click=${() => this.#select(t.name)}
                  aria-label="Switch to ${t.label} theme"
                >
                  <span class="theme-palette-icon">${t.icon}</span>
                  ${t.label}
                </button>
              `)}
              <div class="theme-menu-divider"></div>
              <div class="theme-menu-section-header">Palettes</div>
              ${palettes.map((p) => html`
                <button
                  type="button"
                  class="theme-palette-item ${this._theme === p.name ? 'active' : ''}"
                  @click=${() => this.#select(p.name)}
                  aria-label="Select ${p.label} theme"
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
