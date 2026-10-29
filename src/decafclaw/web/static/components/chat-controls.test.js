import { afterEach, describe, expect, it, vi } from 'vitest';
import './chat-controls.js';

function makeStore(overrides = {}) {
  return Object.assign(new EventTarget(), {
    currentConvId: 'c1',
    contextUsage: 0,
    contextLimit: 100000,
    activeModel: 'vertex-gemini-flash-3.8',
    availableModels: ['anthropic-claude-3.5-sonnet', 'vertex-gemini-flash-3.8'],
    defaultModel: 'vertex-gemini-flash-3.8',
    activeMode: 'default',
    availableModes: [
      { name: 'default', description: 'Standard assistance' },
      { name: 'dev', description: 'Software engineering' },
    ],
    isBusy: false,
    setModel: vi.fn(),
    setMode: vi.fn(),
    ...overrides,
  });
}

describe('chat-controls', () => {
  let el;

  afterEach(() => {
    el?.remove();
    vi.unstubAllGlobals();
  });

  // Mirrors the conversation-sidebar test mount: append, then assign store,
  // then await one update. The component syncs from the store in updated()
  // when `store` lands in changedProps.
  async function mount(storeOverrides = {}, { innerWidth = 1024 } = {}) {
    vi.stubGlobal('window', Object.assign(globalThis.window, { innerWidth }));
    const store = makeStore(storeOverrides);
    el = /** @type {any} */ (document.createElement('chat-controls'));
    document.body.append(el);
    el.store = store;
    await el.updateComplete;
    return { el, store };
  }

  // The context icon (with its danger class) only renders in the collapsed
  // mobile state; desktop shows a <progress> bar instead. Mount at mobile
  // width to exercise the icon.
  it('flags the context icon red at >=90% usage and not below', async () => {
    const { el } = await mount({ contextUsage: 95000, contextLimit: 100000 }, { innerWidth: 375 });
    const icon = el.querySelector('.context-icon');
    expect(icon).toBeTruthy();
    expect(icon.classList.contains('danger')).toBe(true);

    el._contextUsage = 50000;
    await el.updateComplete;
    expect(el.querySelector('.context-icon').classList.contains('danger')).toBe(false);
  });

  it('does not flag danger when no context limit is known', async () => {
    const { el } = await mount({ contextUsage: 95000, contextLimit: 0 }, { innerWidth: 375 });
    expect(el.querySelector('.context-icon').classList.contains('danger')).toBe(false);
  });

  it('shows an expand button in the collapsed (mobile) state', async () => {
    const { el } = await mount({}, { innerWidth: 375 });
    const toggle = el.querySelector('.collapse-toggle');
    expect(toggle).toBeTruthy();
    expect(toggle.title).toBe('Expand controls');
  });

  it('shows a collapse button in the expanded (desktop) state', async () => {
    const { el } = await mount({}, { innerWidth: 1024 });
    const toggle = el.querySelector('.collapse-toggle');
    expect(toggle).toBeTruthy();
    expect(toggle.title).toBe('Collapse controls');
  });

  it('dispatches set-model with the chosen model', async () => {
    const { el } = await mount();
    const handler = vi.fn();
    document.addEventListener('set-model', handler);
    const select = /** @type {HTMLSelectElement} */ (el.querySelector('#cc-model-select'));
    select.value = 'anthropic-claude-3.5-sonnet';
    select.dispatchEvent(new Event('change', { bubbles: true }));
    expect(handler).toHaveBeenCalledTimes(1);
    expect(handler.mock.calls[0][0].detail).toEqual({ model: 'anthropic-claude-3.5-sonnet' });
    document.removeEventListener('set-model', handler);
  });

  it('dispatches set-mode with the chosen mode', async () => {
    const { el } = await mount();
    const handler = vi.fn();
    document.addEventListener('set-mode', handler);
    const select = /** @type {HTMLSelectElement} */ (el.querySelector('#cc-mode-select'));
    select.value = 'dev';
    select.dispatchEvent(new Event('change', { bubbles: true }));
    expect(handler).toHaveBeenCalledTimes(1);
    expect(handler.mock.calls[0][0].detail).toEqual({ mode: 'dev' });
    document.removeEventListener('set-mode', handler);
  });
});
