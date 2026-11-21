import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import './chat-view.js';
import { STORAGE_KEY, PREFERENCE_EVENT } from '../lib/context-provenance-preference.js';

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

  it('mounts streaming thinking as its own transcript widget (#1627)', async () => {
    const store = createFakeStore({
      currentMessages: [],
      isBusy: true,
      streamingText: '',
      streamingThinking: 'Reasoning through the problem...',
    });
    const el = /** @type {any} */ (document.createElement('chat-view'));
    document.body.appendChild(el);
    el.store = store;
    await el.updateComplete;
    await el.updateComplete;
    const messages = /** @type {any[]} */ ([...el.querySelectorAll('chat-message')]);
    expect(messages).toHaveLength(1);
    const thinkingMsg = messages[0];
    expect(thinkingMsg.role).toBe('thinking');
    expect(thinkingMsg.content).toBe('Reasoning through the problem...');
    expect(thinkingMsg.streaming).toBe(true);
    await thinkingMsg.updateComplete;
    const thinking = thinkingMsg.querySelector('thinking-message');
    expect(thinking).toBeTruthy();
    await thinking.updateComplete;
    expect(thinking.querySelector('.thought-block')).toBeTruthy();
    expect(thinking.querySelector('.thought-spinner')).toBeTruthy();
    expect(el.querySelector('.thinking-indicator')).toBeNull();
  });

  it('streams thinking and answer as separate widgets (#1627)', async () => {
    const store = createFakeStore({
      currentMessages: [],
      isBusy: true,
      streamingText: 'The answer is 42.',
      streamingThinking: 'Reasoning through the problem...',
    });
    const el = /** @type {any} */ (document.createElement('chat-view'));
    document.body.appendChild(el);
    el.store = store;
    await el.updateComplete;
    await el.updateComplete;

    const messages = /** @type {any[]} */ ([...el.querySelectorAll('chat-message')]);
    expect(messages.map((m) => m.role)).toEqual(['thinking', 'assistant']);
    expect(messages[0].content).toBe('Reasoning through the problem...');
    expect(messages[1].content).toBe('The answer is 42.');
    await messages[1].updateComplete;
    const assistant = messages[1].querySelector('assistant-message');
    await assistant.updateComplete;
    expect(assistant.querySelector('.thought-block')).toBeNull();
  });

  it('renders archived thinking as a standalone widget (#1627)', async () => {
    const store = createFakeStore({
      currentMessages: [
        { role: 'user', content: 'hello' },
        { role: 'thinking', content: 'Internal thought with no content', timestamp: 't1' },
        { role: 'assistant', content: '', timestamp: 't1' },
      ],
    });
    const el = /** @type {any} */ (document.createElement('chat-view'));
    document.body.appendChild(el);
    el.store = store;
    await el.updateComplete;
    await el.updateComplete;

    const messages = /** @type {any[]} */ ([...el.querySelectorAll('chat-message')]);
    expect(messages).toHaveLength(2);
    expect(messages[1].role).toBe('thinking');
    expect(messages[1].content).toBe('Internal thought with no content');

    await messages[1].updateComplete;
    const thinking = messages[1].querySelector('thinking-message');
    expect(thinking).toBeTruthy();
    await thinking.updateComplete;
    expect(thinking.querySelector('.thought-block')).toBeTruthy();
  });
});

describe('chat-view context provenance preference (#1563)', () => {
  afterEach(() => {
    document.body.innerHTML = '';
    localStorage.clear();
  });

  function storeWithProvenance(provenance) {
    return Object.assign(new EventTarget(), {
      currentConvId: 'conv-1',
      currentMessages: [
        { role: 'user', content: 'hello' },
        { role: 'assistant', content: 'hi', provenance },
      ],
      streamingText: '',
      streamingThinking: '',
      streamingProvenance: {},
      isBusy: false,
      toolStatus: null,
      pendingConfirms: [],
      hasMore: false,
    });
  }

  async function mount(store = storeWithProvenance({ facts: [{ fact: 'User prefers espresso.' }] })) {
    const el = /** @type {any} */ (document.createElement('chat-view'));
    el.store = store;
    document.body.appendChild(el);
    await el.updateComplete;
    await el.updateComplete;
    return el;
  }

  function assistantMessage(el) {
    return /** @type {any} */ ([...el.querySelectorAll('chat-message')][1]);
  }

  it('passes provenance to assistant messages by default', async () => {
    const el = await mount();
    expect(assistantMessage(el).provenance).toEqual({ facts: [{ fact: 'User prefers espresso.' }] });
  });

  it('derives a facts capsule from legacy recalled_facts history rows', async () => {
    const store = Object.assign(new EventTarget(), {
      currentConvId: 'conv-1',
      currentMessages: [
        { role: 'user', content: 'hello' },
        { role: 'assistant', content: 'hi', recalled_facts: ['User prefers espresso.'] },
      ],
      streamingText: '',
      streamingThinking: '',
      streamingProvenance: {},
      isBusy: false,
      toolStatus: null,
      pendingConfirms: [],
      hasMore: false,
    });
    const el = await mount(store);
    expect(assistantMessage(el).provenance).toEqual({ facts: [{ fact: 'User prefers espresso.' }] });
  });

  it('hides provenance when the stored preference is off', async () => {
    localStorage.setItem(STORAGE_KEY, 'false');
    const el = await mount();
    expect(assistantMessage(el).provenance).toEqual({});
  });

  it('reacts to a preference change without a reload', async () => {
    const el = await mount();
    expect(assistantMessage(el).provenance.facts).toHaveLength(1);

    document.dispatchEvent(new CustomEvent(PREFERENCE_EVENT, { detail: { enabled: false } }));
    await el.updateComplete;

    expect(assistantMessage(el).provenance).toEqual({});
  });

  it('does not mount an empty assistant bubble just to show provenance (#1539 follow-up)', async () => {
    const store = Object.assign(new EventTarget(), {
      currentConvId: 'conv-1',
      currentMessages: [{ role: 'user', content: 'hello' }],
      streamingText: '',
      streamingThinking: '',
      streamingProvenance: { journals: [{ timestamp: '2026-11-19 09:00', text: 'Thinking.' }] },
      isBusy: true,
      toolStatus: null,
      pendingConfirms: [],
      hasMore: false,
    });
    const el = await mount(store);

    expect([...el.querySelectorAll('chat-message')]).toHaveLength(1);
    expect(el.querySelector('.thinking-indicator')).toBeTruthy();
  });

  it('renders streaming provenance once text starts streaming (#1563)', async () => {
    const store = Object.assign(new EventTarget(), {
      currentConvId: 'conv-1',
      currentMessages: [{ role: 'user', content: 'hello' }],
      streamingText: 'Espresso it is.',
      streamingThinking: '',
      streamingProvenance: { journals: [{ timestamp: '2026-11-19 09:00', text: 'Thinking.' }] },
      isBusy: true,
      toolStatus: null,
      pendingConfirms: [],
      hasMore: false,
    });
    const el = await mount(store);

    const messages = [...el.querySelectorAll('chat-message')];
    expect(messages).toHaveLength(2);
    const streamingMsg = messages[1];
    expect(streamingMsg.streaming).toBe(true);
    expect(streamingMsg.provenance).toEqual({ journals: [{ timestamp: '2026-11-19 09:00', text: 'Thinking.' }] });
  });

  it('hides streaming provenance when preference is disabled (#1563)', async () => {
    localStorage.setItem(STORAGE_KEY, 'false');
    const store = Object.assign(new EventTarget(), {
      currentConvId: 'conv-1',
      currentMessages: [{ role: 'user', content: 'hello' }],
      streamingText: 'some text',
      streamingThinking: '',
      streamingProvenance: { facts: [{ fact: 'User prefers espresso.' }] },
      isBusy: true,
      toolStatus: null,
      pendingConfirms: [],
      hasMore: false,
    });
    const el = await mount(store);

    const streamingMsg = [...el.querySelectorAll('chat-message')][1];
    expect(streamingMsg.provenance).toEqual({});
  });
});
