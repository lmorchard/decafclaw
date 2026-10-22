import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import './chat-view.js';

/**
 * Tests for #1090: transcript scroll following and resize behavior.
 */

describe('chat-view scroll following on resize (#1090)', () => {
  let fakeROCallbacks = [];
  let observedElements = [];
  let disconnected = false;

  class TestResizeObserver {
    constructor(callback) {
      this.callback = callback;
      fakeROCallbacks.push(callback);
    }
    observe(el) {
      observedElements.push(el);
    }
    disconnect() {
      disconnected = true;
    }
  }

  const originalRO = globalThis.ResizeObserver;

  beforeEach(() => {
    fakeROCallbacks = [];
    observedElements = [];
    disconnected = false;
    globalThis.ResizeObserver = TestResizeObserver;
  });

  afterEach(() => {
    globalThis.ResizeObserver = originalRO;
    document.body.innerHTML = '';
    vi.restoreAllMocks();
  });

  function createFakeStore(overrides = {}) {
    return Object.assign(new EventTarget(), {
      currentConvId: 'conv-1',
      currentMessages: [
        { role: 'user', content: 'hello' },
        { role: 'assistant', content: 'hi there' },
      ],
      streamingText: '',
      isBusy: false,
      toolStatus: null,
      pendingConfirms: [],
      hasMore: false,
      ...overrides,
    });
  }

  it('observes the element with ResizeObserver and disconnects on unmount', async () => {
    const el = /** @type {any} */ (document.createElement('chat-view'));
    document.body.appendChild(el);
    await el.updateComplete;

    expect(observedElements).toContain(el);

    el.remove();
    expect(disconnected).toBe(true);
  });

  it('adjusts scroll to bottom on resize when following new messages', async () => {
    const store = createFakeStore();
    const el = /** @type {any} */ (document.createElement('chat-view'));
    el.store = store;
    document.body.appendChild(el);
    await el.updateComplete;

    // Simulate geometry: initially 500px tall, content is 1000px, scrolled to bottom (500)
    Object.defineProperty(el, 'clientHeight', { value: 500, configurable: true });
    Object.defineProperty(el, 'scrollHeight', { value: 1000, configurable: true });
    el.scrollTop = 500;
    el._showScrollBtn = false;

    // Now sticky slot appears: clientHeight shrinks to 350px
    Object.defineProperty(el, 'clientHeight', { value: 350, configurable: true });

    // Trigger the ResizeObserver callback
    expect(fakeROCallbacks.length).toBeGreaterThan(0);
    for (const cb of fakeROCallbacks) {
      cb([{ target: el }]);
    }

    // Scroll should be adjusted to bottom (scrollHeight = 1000) and button not shown
    expect(el.scrollTop).toBe(1000);
    expect(el._showScrollBtn).toBe(false);
  });

  it('does not scroll to bottom on resize if user has scrolled up', async () => {
    const store = createFakeStore();
    const el = /** @type {any} */ (document.createElement('chat-view'));
    el.store = store;
    document.body.appendChild(el);
    await el.updateComplete;

    // Simulate geometry: user scrolled up
    Object.defineProperty(el, 'clientHeight', { value: 500, configurable: true });
    Object.defineProperty(el, 'scrollHeight', { value: 2000, configurable: true });
    el.scrollTop = 200;
    el._showScrollBtn = true;

    // Resize occurs
    Object.defineProperty(el, 'clientHeight', { value: 350, configurable: true });
    for (const cb of fakeROCallbacks) {
      cb([{ target: el }]);
    }

    // Should NOT force scroll to bottom
    expect(el.scrollTop).toBe(200);
    expect(el._showScrollBtn).toBe(true);
  });
});
