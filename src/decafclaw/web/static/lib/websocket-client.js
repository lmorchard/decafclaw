/**
 * WebSocket connection manager with auto-reconnect.
 * @fires WebSocketClient#message - Parsed JSON message from server
 * @fires WebSocketClient#open - Connection opened
 * @fires WebSocketClient#close - Connection closed
 * @fires WebSocketClient#error - Connection error
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

  /**
   * Open the WebSocket connection.
   */
  connect() {
    this.#shouldReconnect = true;
    this.#reconnectAttempts = 0;
    this.#doConnect();
  }

  /**
   * Close the connection (no auto-reconnect).
   */
  disconnect() {
    this.#shouldReconnect = false;
    if (this.#reconnectTimer) {
      clearTimeout(this.#reconnectTimer);
      this.#reconnectTimer = null;
    }
    if (this.#ws) {
      this.#ws.close();
      this.#ws = null;
    }
  }

  /**
   * Send a JSON message.
   * @param {object} message
   */
  send(message) {
    if (!this.#ws || this.#ws.readyState !== WebSocket.OPEN) {
      console.warn('WebSocket not connected, dropping message:', message);
      return;
    }
    this.#ws.send(JSON.stringify(message));
  }

  #doConnect() {
    if (this.#ws) {
      this.#ws.close();
    }
    this.#ws = new WebSocket(this.#url);

    this.#ws.onopen = () => {
      this.#reconnectAttempts = 0;
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
    this.#reconnectTimer = setTimeout(() => {
      this.#reconnectTimer = null;
      this.#doConnect();
    }, delay);
  }
}
