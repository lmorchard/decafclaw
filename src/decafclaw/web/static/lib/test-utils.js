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
  /** Simulates the socket being down; `send()` then queues (returns true). */
  connected = true;

  /** @param {object} message @returns {boolean} true if the frame was queued */
  send(message) {
    this.sent.push(message);
    return !this.connected;
  }

  /** Simulate the underlying socket (re)connecting. */
  fireOpen() {
    this.dispatchEvent(new CustomEvent('open'));
  }

  /** Simulate a server→client frame arriving. @param {object} msg */
  fireMessage(msg) {
    this.dispatchEvent(new CustomEvent('message', { detail: msg }));
  }

  /** Simulate the client failing to deliver an outbound frame (#1416). @param {object} msg */
  fireSendFailed(msg) {
    this.dispatchEvent(new CustomEvent('send-failed', { detail: msg }));
  }
}

/** Build a real ConversationStore on the fake socket. */
export const makeStore = (/** @type {FakeWS} */ ws) =>
  new ConversationStore(/** @type {any} */ (ws));

/** Let the `open` handler's async listConversations() fetch chain settle. */
export const flush = () => new Promise((resolve) => setTimeout(resolve, 0));
