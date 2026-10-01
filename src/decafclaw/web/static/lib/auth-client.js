import { ApiError, DefaultService } from './api-client/index.js';

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
      const data = await DefaultService.authMeApiAuthMeGet();
      this.#currentUser = data.username;
      return data.username;
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
    const data = await DefaultService.authLoginApiAuthLoginPost({ token }).catch(error => {
      if (error instanceof ApiError) throw new Error('Invalid token');
      throw error;
    });
    this.#currentUser = data.username;
    this.dispatchEvent(new CustomEvent('login', { detail: { username: data.username } }));
    return data.username;
  }

  /**
   * Logout and clear session.
   * @returns {Promise<void>}
   */
  async logout() {
    await DefaultService.authLogoutApiAuthLogoutPost(true);
    this.#currentUser = null;
    this.dispatchEvent(new CustomEvent('logout'));
  }
}
