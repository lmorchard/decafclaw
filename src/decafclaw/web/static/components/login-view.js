import { LitElement, html } from 'lit';

export class LoginView extends LitElement {
  static properties = {
    authClient: { type: Object, attribute: false },
    error: { type: String, state: true },
    loading: { type: Boolean, state: true },
  };

  // Use light DOM so Pico CSS applies
  createRenderRoot() { return this; }

  constructor() {
    super();
    this.authClient = null;
    this.error = '';
    this.loading = false;
  }

  async #handleSubmit(e) {
    e.preventDefault();
    const input = /** @type {HTMLInputElement|null} */ (this.querySelector('#token-input'));
    const token = input?.value?.trim();
    if (!token) return;

    this.loading = true;
    this.error = '';
    try {
      await this.authClient.login(token);
    } catch (err) {
      this.error = 'Invalid token. Please try again.';
    } finally {
      this.loading = false;
    }
  }

  render() {
    return html`
      <div class="login-card">
        <h2>DecafClaw</h2>
        <p>Enter your access token to continue.</p>
        <form @submit=${this.#handleSubmit}>
          <input
            id="token-input"
            type="password"
            placeholder="dfc_..."
            ?disabled=${this.loading}
            autocomplete="off"
          >
          <button type="submit" ?disabled=${this.loading}>
            ${this.loading ? 'Signing in...' : 'Sign in'}
          </button>
        </form>
        ${this.error ? html`<p class="error">${this.error}</p>` : ''}
        <div style="margin-top: 1rem;"><theme-toggle></theme-toggle></div>
      </div>
    `;
  }
}

customElements.define('login-view', LoginView);
