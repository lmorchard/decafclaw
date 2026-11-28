/**
 * WebSocket connection manager with auto-reconnect.
 * @fires WebSocketClient#message - Parsed JSON message from server
 * @fires WebSocketClient#open - Connection opened
 * @fires WebSocketClient#close - Connection closed
 * @fires WebSocketClient#reconnecting - A retry was scheduled; detail is
 *   `{attempt, delayMs}` with a 1-based attempt number
 * @fires WebSocketClient#error - Connection error
 * @fires WebSocketClient#send-failed - An outbound message could not be
 *   delivered (a queued send failed, or the queue was discarded on a
 *   deliberate disconnect); detail is the message object (#1416)
 */
export class WebSocketClient extends EventTarget {
  /** @type {string} */
  #url;
  /** @type {WebSocket|null} */
  #ws = null;
  /** @type {boolean} */
  #shouldReconnect = false;
  /** @type {number} */
  #reconnectAttempts = 0;
  /** @type {number} */
  #maxReconnectDelay = 30000;
  /** @type {number|null} */
  #reconnectTimer = null;
  /** Outbound messages buffered while the socket is not open (#1416). */
  /** @type {object[]} */
  #queue = [];

  /**
   * @param {string} url - WebSocket URL (ws:// or wss://)
   */
  constructor(url) {
    super();
    this.#url = url;
  }

  /** @returns {boolean} */
  get connected() {
    return this.#ws?.readyState === WebSocket.OPEN;
  }

  /** Number of outbound messages buffered while the socket is not open. */
  get pendingCount() {
    return this.#queue.length;
  }

  /**
   * Open the WebSocket connection.
   */
  connect() {
    this.#shouldReconnect = true;
    this.#reconnectAttempts = 0;
    this.#doConnect();
  }

  /**
   * Close the connection (no auto-reconnect). A deliberate disconnect is not
   * followed by an `open`, so buffered messages can never be delivered: each
   * one is surfaced as `send-failed` instead of being dropped (#1416).
   */
  disconnect() {
    this.#shouldReconnect = false;
    if (this.#reconnectTimer) {
      clearTimeout(this.#reconnectTimer);
      this.#reconnectTimer = null;
    }
    for (const message of this.#queue) {
      this.#failMessage(message);
    }
    this.#queue = [];
    if (this.#ws) {
      this.#ws.close();
      this.#ws = null;
    }
  }

  /**
   * Send a JSON message. While the socket is not open, the message is queued
   * and flushed on the next `open` instead of being dropped (#1416).
   * @param {object} message
   * @returns {boolean} true if the message was queued, false if sent directly
   */
  send(message) {
    if (this.#ws && this.#ws.readyState === WebSocket.OPEN) {
      try {
        this.#ws.send(JSON.stringify(message));
        return false;
      } catch (e) {
        // The socket died between the readyState check and send(): surface it.
        console.error('WebSocket send failed:', e);
        this.#failMessage(message);
        return false;
      }
    }
    this.#queue.push(message);
    return true;
  }

  /** Surface a message that cannot be delivered (#1416). @param {object} message */
  #failMessage(message) {
    this.dispatchEvent(new CustomEvent('send-failed', { detail: message }));
  }

  /** Deliver buffered messages in order on a fresh socket (#1416). */
  #flushQueue() {
    if (!this.#queue.length) return;
    const pending = this.#queue;
    this.#queue = [];
    for (const message of pending) {
      try {
        this.#ws.send(JSON.stringify(message));
      } catch (e) {
        console.error('WebSocket send failed:', e);
        this.#failMessage(message);
      }
    }
  }

  #doConnect() {
    if (this.#ws) {
      this.#ws.close();
    }
    this.#ws = new WebSocket(this.#url);

    this.#ws.onopen = () => {
      this.#reconnectAttempts = 0;
      // Deliver buffered outbound messages before announcing the connection,
      // so a queued send reaches the wire ahead of reconnect housekeeping.
      this.#flushQueue();
      this.dispatchEvent(new CustomEvent('open'));
    };

    this.#ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        this.dispatchEvent(new CustomEvent('message', { detail: data }));
      } catch (e) {
        console.error('Failed to parse WebSocket message:', e);
      }
    };

    this.#ws.onclose = () => {
      this.dispatchEvent(new CustomEvent('close'));
      if (this.#shouldReconnect) {
        this.#scheduleReconnect();
      }
    };

    this.#ws.onerror = (event) => {
      this.dispatchEvent(new CustomEvent('error', { detail: event }));
    };
  }

  #scheduleReconnect() {
    const delay = Math.min(1000 * Math.pow(2, this.#reconnectAttempts), this.#maxReconnectDelay);
    this.#reconnectAttempts++;
    console.log(`WebSocket reconnecting in ${delay}ms (attempt ${this.#reconnectAttempts})`);
    this.dispatchEvent(new CustomEvent('reconnecting', {
      detail: { attempt: this.#reconnectAttempts, delayMs: delay },
    }));
    this.#reconnectTimer = setTimeout(() => {
      this.#reconnectTimer = null;
      this.#doConnect();
    }, delay);
  }
}
