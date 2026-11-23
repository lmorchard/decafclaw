import { ConversationStore } from './conversation-store.js';

/**
 * Shared stand-in for WebSocketClient in store tests: same event surface (it is
 * an EventTarget that dispatches `open` / `message`), but `send()` records
 * instead of writing to a socket. `open` is dispatched by hand to simulate a
 * reconnect.
 */
export class FakeWS extends EventTarget {
  /** @type {object[]} */
  sent = [];

  /** @param {object} message */
  send(message) {
    this.sent.push(message);
  }

  /** Simulate the underlying socket (re)connecting. */
  fireOpen() {
    this.dispatchEvent(new CustomEvent('open'));
  }

  /** Simulate a server→client frame arriving. @param {object} msg */
  fireMessage(msg) {
    this.dispatchEvent(new CustomEvent('message', { detail: msg }));
  }
}

/** Build a real ConversationStore on the fake socket. */
export const makeStore = (/** @type {FakeWS} */ ws) =>
  new ConversationStore(/** @type {any} */ (ws));

/** Let the `open` handler's async listConversations() fetch chain settle. */
export const flush = () => new Promise((resolve) => setTimeout(resolve, 0));
