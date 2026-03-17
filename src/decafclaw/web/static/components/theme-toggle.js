import { LitElement, html } from 'lit';

/**
 * Theme toggle: light / dark / system.
 * Persists choice in localStorage.
 */
export class ThemeToggle extends LitElement {
  static properties = {
    _theme: { type: String, state: true },
  };

  createRenderRoot() { return this; }

  constructor() {
    super();
    this._theme = localStorage.getItem('decafclaw-theme') || 'system';
    this.#applyTheme();
  }

  #applyTheme() {
    const html = document.documentElement;
    if (this._theme === 'system') {
      html.removeAttribute('data-theme');
    } else {
      html.setAttribute('data-theme', this._theme);
    }
  }

  /** @param {string} theme */
  #setTheme(theme) {
    this._theme = theme;
    localStorage.setItem('decafclaw-theme', theme);
    this.#applyTheme();
  }

  render() {
    return html`
      <div class="theme-toggle">
        <button
          class="theme-btn ${this._theme === 'light' ? 'active' : ''}"
          @click=${() => this.#setTheme('light')}
          title="Light"
        >\u2600\ufe0f</button>
        <button
          class="theme-btn ${this._theme === 'dark' ? 'active' : ''}"
          @click=${() => this.#setTheme('dark')}
          title="Dark"
        >\u{1f319}</button>
        <button
          class="theme-btn ${this._theme === 'system' ? 'active' : ''}"
          @click=${() => this.#setTheme('system')}
          title="System"
        >\u{1f4bb}</button>
      </div>
    `;
  }
}

customElements.define('theme-toggle', ThemeToggle);
