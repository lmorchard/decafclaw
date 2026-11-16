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
   it('applies warn and full classes to context control bar', async () => {
     const { el } = await mount({ contextUsage: 85000, contextLimit: 100000 });
     const contextControl = el.querySelector('.context-control');
     expect(contextControl.classList.contains('warn')).toBe(true);

     el._contextUsage = 100000;
     await el.updateComplete;
     expect(contextControl.classList.contains('full')).toBe(true);
   });

   it('renders model, mode, and context controls inline across viewports', async () => {
     const { el } = await mount({}, { innerWidth: 375 });
     expect(el.querySelector('#cc-model-select')).toBeTruthy();
     expect(el.querySelector('#cc-mode-select')).toBeTruthy();
     expect(el.querySelector('#cc-context-progress')).toBeTruthy();
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

  it('selects activeModel and falls back to defaultModel when activeModel is empty', async () => {
    const { el, store } = await mount({
      activeModel: '',
      defaultModel: 'vertex-gemini-flash-3.8',
      availableModels: ['anthropic-claude-3.5-sonnet', 'vertex-gemini-flash-3.8'],
    });
    const select = /** @type {HTMLSelectElement} */ (el.querySelector('#cc-model-select'));
    expect(select.value).toBe('vertex-gemini-flash-3.8');

    // Setting an explicit activeModel selects it
    store.activeModel = 'anthropic-claude-3.5-sonnet';
    store.dispatchEvent(new Event('change'));
    await el.updateComplete;
    expect(select.value).toBe('anthropic-claude-3.5-sonnet');

    // Switching to a session without an explicit model resets to defaultModel
    store.activeModel = '';
    store.dispatchEvent(new Event('change'));
    await el.updateComplete;
    expect(select.value).toBe('vertex-gemini-flash-3.8');
  });

  it('flags and selects an unconfigured model', async () => {
    const { el } = await mount({
      activeModel: 'legacy-model-v1',
      availableModels: ['anthropic-claude-3.5-sonnet', 'vertex-gemini-flash-3.8'],
    });
    const select = /** @type {HTMLSelectElement} */ (el.querySelector('#cc-model-select'));
    expect(select.value).toBe('legacy-model-v1');
    expect(select.textContent).toContain('⚠ legacy-model-v1 (not configured)');
  });

  it('syncs mode selector value when store activeMode changes', async () => {
    const { el, store } = await mount({ activeMode: 'default' });
    const select = /** @type {HTMLSelectElement} */ (el.querySelector('#cc-mode-select'));
    expect(select.value).toBe('default');

    store.activeMode = 'dev';
    store.dispatchEvent(new Event('change'));
    await el.updateComplete;
    expect(select.value).toBe('dev');
  });
   it('dispatches toggle-context-inspector when the context meter is clicked', async () => {
     const { el } = await mount();
     const handler = vi.fn();
     document.addEventListener('toggle-context-inspector', handler);

     const contextControl = /** @type {HTMLElement} */ (el.querySelector('.context-control'));
     contextControl.dispatchEvent(new MouseEvent('click', { bubbles: true }));
     expect(handler).toHaveBeenCalledTimes(1);
     expect(handler.mock.calls[0][0].detail).toEqual({ convId: 'c1' });
     document.removeEventListener('toggle-context-inspector', handler);
   });

  // Issue #1192: the copy-conversation-menu moves out of the chat-control
  // strip and into the context inspector raw pane. The strip must not mount it.
  it('no longer mounts the copy conversation menu in the control strip', async () => {
    const { el } = await mount();
    expect(el.querySelector('copy-conversation-menu')).toBeNull();
  });

  // Issue #1263: the shell-approval gear must sit in the compact actions
  // cluster. In the mode control it kept the 44px floating-btn floor and
  // rendered larger than the buttons beside it.
  it('places the shell-approval trigger in the compact actions cluster', async () => {
    const { el } = await mount();
    const trigger = el.querySelector('button.shell-approval-open-btn');
    expect(trigger).toBeTruthy();
    expect(trigger.closest('.actions-control')).toBeTruthy();
    expect(trigger.closest('.mode-control')).toBeNull();
    // Same cluster as the other compact conversation controls.
    for (const cls of ['.terminal-open-btn', '.side-question-action-btn']) {
      expect(trigger.closest('.actions-control')).toBe(el.querySelector(cls).closest('.actions-control'));
    }
  });

  it('keeps the shell-approval trigger available without configured modes', async () => {
    const { el } = await mount({ availableModes: [] });
    expect(el.querySelector('button.shell-approval-open-btn')).toBeTruthy();
  });
});
