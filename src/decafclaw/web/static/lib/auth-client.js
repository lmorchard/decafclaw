/**
 * REST client for authentication.
 * @fires AuthClient#login
 * @fires AuthClient#logout
 */
export class AuthClient extends EventTarget {
  /** @type {string|null} */
  #currentUser = null;

  /** @returns {string|null} */
  get currentUser() {
    return this.#currentUser;
  }

  /**
   * Check if there's an active session.
   * @returns {Promise<string|null>} username or null
   */
  async checkSession() {
    try {
      const resp = await fetch('/api/auth/me');
      if (resp.ok) {
        const data = await resp.json();
        this.#currentUser = data.username;
        return data.username;
      }
    } catch (e) {
      // not authenticated
    }
    this.#currentUser = null;
    return null;
  }

  /**
   * Login with a token.
   * @param {string} token
   * @returns {Promise<string>} username
   * @throws {Error} on invalid token
   */
  async login(token) {
    const resp = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token }),
    });
    if (!resp.ok) {
      throw new Error('Invalid token');
    }
    const data = await resp.json();
    this.#currentUser = data.username;
    this.dispatchEvent(new CustomEvent('login', { detail: { username: data.username } }));
    return data.username;
  }

  /**
   * Logout and clear session.
   * @returns {Promise<void>}
   */
  async logout() {
    await fetch('/api/auth/logout', { method: 'POST' });
    this.#currentUser = null;
    this.dispatchEvent(new CustomEvent('logout'));
  }
}
