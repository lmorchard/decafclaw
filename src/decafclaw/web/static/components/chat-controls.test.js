import { afterEach, describe, expect, it, vi } from 'vitest';
import './chat-controls.js';

function makeStore(overrides = {}) {
  return Object.assign(new EventTarget(), {
    currentConvId: 'c1',
    contextUsage: 0,
    contextLimit: 100000,
    activeModel: 'vertex-gemini-flash-3.8',
    availableModels: ['anthropic-claude-3.5-sonnet', 'vertex-gemini-flash-3.8'],
    modelGroups: [
      { name: 'Anthropic', models: ['anthropic-claude-3.5-sonnet'] },
      { name: 'Vertex', models: ['vertex-gemini-flash-3.8'] },
    ],
    defaultModel: 'vertex-gemini-flash-3.8',
    isBusy: false,
    setModel: vi.fn(),
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

   it('renders model picker and context controls inline across viewports', async () => {
     const { el } = await mount({}, { innerWidth: 375 });
     expect(el.querySelector('model-picker')).toBeTruthy();
     expect(el.querySelector('#cc-model-select')).toBeNull();
     expect(el.querySelector('#cc-mode-select')).toBeNull();
     expect(el.querySelector('#cc-context-progress')).toBeTruthy();
   });

  /** Wait for the nested picker to render, then return it. */
  async function pickerOf(el) {
    const picker = /** @type {any} */ (el.querySelector('model-picker'));
    await picker.updateComplete;
    return picker;
  }

  it('dispatches set-model with the chosen model through the picker', async () => {
    const { el } = await mount();
    const picker = await pickerOf(el);
    const handler = vi.fn();
    document.addEventListener('set-model', handler);
    picker.querySelector('.model-picker-trigger').click();
    await picker.updateComplete;
    const option = [...picker.querySelectorAll('.model-picker-option')]
      .find((row) => row.dataset.model === 'anthropic-claude-3.5-sonnet');
    option.click();
    expect(handler).toHaveBeenCalledTimes(1);
    expect(handler.mock.calls[0][0].detail).toEqual({ model: 'anthropic-claude-3.5-sonnet' });
    document.removeEventListener('set-model', handler);
  });

  // The store populates availableModels asynchronously from a WebSocket
  // message (CONV_HISTORY) *after* app.js assigns `store`. The component must
  // re-sync on the store's `change` event, or the model controls (gated on
  // .length > 0) stay hidden.
  it('re-syncs model controls when the store populates after connect', async () => {
    const { el, store } = await mount({
      modelGroups: [],
      availableModels: [],
      activeModel: '',
    });
    // Before the WS message: no model controls rendered.
    expect(el.querySelector('model-picker')).toBeNull();

    // Simulate the store receiving available_models and emitting change (as
    // #handleMessage does for CONV_HISTORY).
    store.availableModels = ['anthropic-claude-3.5-sonnet', 'vertex-gemini-flash-3.8'];
    store.modelGroups = [
      { name: 'Anthropic', models: ['anthropic-claude-3.5-sonnet'] },
      { name: 'Vertex', models: ['vertex-gemini-flash-3.8'] },
    ];
    store.activeModel = 'vertex-gemini-flash-3.8';
    store.dispatchEvent(new Event('change'));
    await el.updateComplete;

    expect(el.querySelector('model-picker')).toBeTruthy();
  });

  it('selects activeModel and falls back to defaultModel when activeModel is empty', async () => {
    const { el, store } = await mount({
      activeModel: '',
      defaultModel: 'vertex-gemini-flash-3.8',
      availableModels: ['anthropic-claude-3.5-sonnet', 'vertex-gemini-flash-3.8'],
    });
    const trigger = (await pickerOf(el)).querySelector('.model-picker-trigger');
    expect(trigger.textContent).toContain('vertex-gemini-flash-3.8');

    // Setting an explicit activeModel selects it
    store.activeModel = 'anthropic-claude-3.5-sonnet';
    store.dispatchEvent(new Event('change'));
    await el.updateComplete;
    await el.querySelector('model-picker').updateComplete;
    expect(trigger.textContent).toContain('anthropic-claude-3.5-sonnet');

    // Switching to a session without an explicit model resets to defaultModel
    store.activeModel = '';
    store.dispatchEvent(new Event('change'));
    await el.updateComplete;
    await el.querySelector('model-picker').updateComplete;
    expect(trigger.textContent).toContain('vertex-gemini-flash-3.8');
  });

  it('flags an unconfigured active model', async () => {
    const { el } = await mount({
      activeModel: 'legacy-model-v1',
      availableModels: ['anthropic-claude-3.5-sonnet', 'vertex-gemini-flash-3.8'],
    });
    const trigger = (await pickerOf(el)).querySelector('.model-picker-trigger');
    expect(trigger.textContent).toContain('legacy-model-v1');
    expect(trigger.textContent).toContain('not configured');
  });

  it('disables the picker while busy or read-only', async () => {
    const { el, store } = await mount();
    const picker = await pickerOf(el);
    expect(picker.querySelector('.model-picker-trigger').disabled).toBe(false);

    store.isBusy = true;
    store.dispatchEvent(new Event('change'));
    await el.updateComplete;
    await picker.updateComplete;
    expect(picker.querySelector('.model-picker-trigger').disabled).toBe(true);

    store.isBusy = false;
    store.isReadOnly = true;
    store.dispatchEvent(new Event('change'));
    await el.updateComplete;
    await picker.updateComplete;
    expect(picker.querySelector('.model-picker-trigger').disabled).toBe(true);
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

  // #1684: the per-conversation shell approval panel and its trigger are
  // retired; only the model, conversation actions, and context controls remain.
  it('has no shell-approval trigger', async () => {
    const { el } = await mount();
    expect(el.querySelector('button.shell-approval-open-btn')).toBeNull();
  });
});
