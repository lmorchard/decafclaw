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

  // The store populates availableModels/availableModes asynchronously from a
  // WebSocket message (CONV_HISTORY) *after* app.js assigns `store`. The
  // component must re-sync on the store's `change` event, or the model/mode
  // controls (gated on .length > 0) stay hidden.
  it('re-syncs model/mode controls when the store populates after connect', async () => {
    const { el, store } = await mount({
      availableModels: [],
      availableModes: [],
      activeModel: '',
      activeMode: 'default',
    });
    // Before the WS message: no model/mode controls rendered.
    expect(el.querySelector('#cc-model-select')).toBeNull();
    expect(el.querySelector('#cc-mode-select')).toBeNull();

    // Simulate the store receiving available_models/available_modes and
    // emitting change (as #handleMessage does for CONV_HISTORY).
    store.availableModels = ['anthropic-claude-3.5-sonnet', 'vertex-gemini-flash-3.8'];
    store.activeModel = 'vertex-gemini-flash-3.8';
    store.availableModes = [{ name: 'default', description: 'Standard' }];
    store.dispatchEvent(new Event('change'));
    await el.updateComplete;

    expect(el.querySelector('#cc-model-select')).toBeTruthy();
    expect(el.querySelector('#cc-mode-select')).toBeTruthy();
  });

  // On a narrow viewport the strip starts collapsed. The user taps the toggle
  // to expand it, then clicking the chat field raises the mobile keyboard,
  // which fires a `resize` event. The viewport default must NOT clobber the
  // user's explicit choice, or the controls collapse right back.
  it('keeps a user-expanded control expanded across a resize on a narrow viewport', async () => {
    const { el } = await mount({}, { innerWidth: 500 });
    // Narrow viewport: starts collapsed.
    expect(el._collapsed).toBe(true);

    // User taps the toggle to expand.
    el.querySelector('.collapse-toggle').click();
    await el.updateComplete;
    expect(el._collapsed).toBe(false);

    // Clicking the chat field raises the keyboard -> resize event.
    window.dispatchEvent(new Event('resize'));
    await el.updateComplete;
    // The user's explicit expand must stick.
    expect(el._collapsed).toBe(false);
  });

  // The context meter (desktop <progress> / mobile icon) should summon the
  // context inspector. It dispatches a toggle-context-inspector event that
  // the sidebar (which owns the inspector) listens for.
  it('dispatches toggle-context-inspector when the context meter is clicked', async () => {
    const { el } = await mount();
    const handler = vi.fn();
    document.addEventListener('toggle-context-inspector', handler);

    // Desktop: the <progress> bar is the meter.
    const progress = /** @type {HTMLElement} */ (el.querySelector('#cc-context-progress'));
    progress.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    expect(handler).toHaveBeenCalledTimes(1);
    expect(handler.mock.calls[0][0].detail).toEqual({ convId: 'c1' });
    document.removeEventListener('toggle-context-inspector', handler);
  });

  it('dispatches toggle-context-inspector when the mobile context icon is clicked', async () => {
    const { el } = await mount({}, { innerWidth: 500 });
    const handler = vi.fn();
    document.addEventListener('toggle-context-inspector', handler);

    const icon = /** @type {HTMLElement} */ (el.querySelector('.context-icon'));
    expect(icon).toBeTruthy();
    icon.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    expect(handler).toHaveBeenCalledTimes(1);
    expect(handler.mock.calls[0][0].detail).toEqual({ convId: 'c1' });
    document.removeEventListener('toggle-context-inspector', handler);
  });
});
