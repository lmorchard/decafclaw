/**
 * Full-screen reconnect overlay (#1683).
 *
 * While the chat WebSocket is down, the rest of the app still renders but
 * every action behind it is dead. The overlay covers the viewport and marks
 * the chat layout `inert`, so clicks, keystrokes, and screen-reader navigation
 * cannot reach that stale UI. The overlay itself is a sibling of the layout,
 * so it stays available for announcement while the layout is inert.
 *
 * It hides as soon as the socket reopens; nothing here decides when to retry.
 */
export class ConnectionOverlay {
  /** @type {HTMLElement} */
  #root;
  /** @type {HTMLElement|null} */
  #layout;
  /** @type {HTMLElement|null} */
  #attempt;

  /**
   * @param {HTMLElement} root - The overlay element (initially `.hidden`).
   * @param {HTMLElement|null} [layout] - Content to make inert while shown.
   */
  constructor(root, layout = null) {
    this.#root = root;
    this.#layout = layout;
    this.#attempt = root.querySelector('[data-role="attempt"]');
  }

  /** @returns {boolean} */
  get visible() {
    return !this.#root.classList.contains('hidden');
  }

  /** Cover the app and block interaction with the dead layout. */
  show() {
    if (this.visible) return;
    this.#root.classList.remove('hidden');
    if (this.#layout) this.#layout.setAttribute('inert', '');
    this.narrate();
  }

  /** Uncover the app and restore interaction. */
  hide() {
    if (!this.visible) return;
    this.#root.classList.add('hidden');
    if (this.#layout) this.#layout.removeAttribute('inert');
    this.narrate();
  }

  /**
   * Describe the next reconnect attempt. Called with no argument on the first
   * drop, before any retry has been scheduled.
   *
   * @param {{attempt?: number, delayMs?: number}} [state]
   */
  narrate({ attempt = 0, delayMs = 0 } = {}) {
    if (!this.#attempt) return;
    if (!attempt) {
      this.#attempt.textContent = '';
      return;
    }
    const seconds = Math.max(1, Math.round(delayMs / 1000));
    this.#attempt.textContent = `Attempt ${attempt} · next try in ${seconds}s`;
  }
}
