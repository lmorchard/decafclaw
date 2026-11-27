import { afterEach, describe, expect, it, vi } from 'vitest';
import './conversation-controls.js';

vi.mock('../lib/toast.js', () => ({ showToast: vi.fn() }));

describe('conversation-controls model selector (#1407)', () => {
  let controls;

  afterEach(() => {
    controls?.remove();
  });

  async function mount(storeOverrides = {}) {
    const store = Object.assign(new EventTarget(), {
      currentConvId: 'c1',
      conversations: [{ conv_id: 'c1', title: 'Trip planning' }, { conv_id: 'c2', title: 'Coding' }],
      folders: [],
      archivedConversations: [],
      archivedFolders: [],
      systemConversations: [],
      systemFolders: [],
      contextUsage: 0,
      contextLimit: 0,
      activeModel: 'vertex-gemini-flash-3.8',
      availableModels: ['anthropic-claude-3.5-sonnet', 'openai-gpt-4o', 'vertex-gemini-flash-3.8'],
      modelGroups: [
        { name: 'Anthropic', models: ['anthropic-claude-3.5-sonnet'] },
        { name: 'OpenAI', models: ['openai-gpt-4o'] },
        { name: 'Vertex', models: ['vertex-gemini-flash-3.8'] },
      ],
      defaultModel: 'vertex-gemini-flash-3.8',
      selectConversation: vi.fn(),
      listConversations: vi.fn(),
      listSessions: vi.fn(),
      listArchivedConversations: vi.fn(),
      listSystemConversations: vi.fn(),
      setModel: vi.fn(),
      setMode: vi.fn(),
      createConversation: vi.fn(),
      ...storeOverrides,
    });
    controls = /** @type {any} */ (document.createElement('conversation-controls'));
    document.body.append(controls);
    controls.store = store;
    await controls.updateComplete;
    return { controls, store };
  }

  /** Wait for the nested picker and its popup to render, then return the picker. */
  async function pickerOf(el) {
    const picker = /** @type {any} */ (el.querySelector('model-picker'));
    await picker.updateComplete;
    // #2011: the trigger and rows live in the nested <picker-popup>, which is
    // a separate element with its own update cycle.
    await picker.querySelector('picker-popup')?.updateComplete;
    return picker;
  }

  it('renders the model picker and selects the active model', async () => {
    const { controls } = await mount({ activeModel: 'vertex-gemini-flash-3.8' });
    const picker = await pickerOf(controls);
    const trigger = picker.querySelector('.picker-popup-trigger');
    expect(trigger).toBeTruthy();
    expect(trigger.textContent).toContain('vertex-gemini-flash-3.8');
    expect(controls.querySelector('#model-select')).toBeNull();
  });

  it('falls back to defaultModel when activeModel is empty', async () => {
    const { controls } = await mount({
      activeModel: '',
      defaultModel: 'openai-gpt-4o',
    });
    const picker = await pickerOf(controls);
    const trigger = picker.querySelector('.picker-popup-trigger');
    expect(trigger).toBeTruthy();
    expect(trigger.textContent).toContain('openai-gpt-4o');
  });

  it('updates the picker when switching conversations to a different model', async () => {
    const { controls, store } = await mount({ activeModel: 'vertex-gemini-flash-3.8' });
    const picker = await pickerOf(controls);
    const trigger = picker.querySelector('.picker-popup-trigger');
    expect(trigger.textContent).toContain('vertex-gemini-flash-3.8');

    // Simulate switching conversation: activeModel changes to another model
    store.activeModel = 'openai-gpt-4o';
    store.dispatchEvent(new Event('change'));
    await controls.updateComplete;
    await picker.updateComplete;

    expect(trigger.textContent).toContain('openai-gpt-4o');
  });

  it('retains correct model even when temporary reset to empty string happens during switch', async () => {
    const { controls, store } = await mount({ activeModel: 'vertex-gemini-flash-3.8' });
    const picker = await pickerOf(controls);
    const trigger = picker.querySelector('.picker-popup-trigger');
    expect(trigger.textContent).toContain('vertex-gemini-flash-3.8');

    // Simulating selectConversation: activeModel is cleared
    store.activeModel = '';
    store.defaultModel = 'anthropic-claude-3.5-sonnet';
    store.dispatchEvent(new Event('change'));
    await controls.updateComplete;
    await picker.updateComplete;
    expect(trigger.textContent).toContain('anthropic-claude-3.5-sonnet');

    // Now CONV_HISTORY arrives with the conversation's active model
    store.activeModel = 'vertex-gemini-flash-3.8';
    store.dispatchEvent(new Event('change'));
    await controls.updateComplete;
    await picker.updateComplete;

    expect(trigger.textContent).toContain('vertex-gemini-flash-3.8');
  });

  it('flags an unconfigured model', async () => {
    const { controls } = await mount({
      activeModel: 'legacy-model-v1',
      availableModels: ['anthropic-claude-3.5-sonnet', 'openai-gpt-4o'],
    });
    const picker = await pickerOf(controls);
    const trigger = picker.querySelector('.picker-popup-trigger');
    expect(trigger.textContent).toContain('legacy-model-v1');
    expect(trigger.textContent).toContain('not configured');
  });

  it('emits set-model when the user changes selection', async () => {
    const { controls } = await mount({ activeModel: 'vertex-gemini-flash-3.8' });
    const picker = await pickerOf(controls);
    const handler = vi.fn();
    document.addEventListener('set-model', handler);
    picker.querySelector('.picker-popup-trigger').click();
    await picker.updateComplete;
    const option = [...picker.querySelectorAll('.picker-popup-option')]
      .find((row) => row.dataset.value === 'openai-gpt-4o');
    option.click();

    expect(handler).toHaveBeenCalledTimes(1);
    expect(handler.mock.calls[0][0].detail).toEqual({ model: 'openai-gpt-4o' });
    document.removeEventListener('set-model', handler);
  });
});

describe('conversation-controls context meter and inspector (#1407)', () => {
  let controls;

  afterEach(() => {
    controls?.remove();
  });

  async function mount(storeOverrides = {}) {
    const store = Object.assign(new EventTarget(), {
      currentConvId: 'c1',
      conversations: [{ conv_id: 'c1', title: 'Trip planning' }],
      folders: [],
      archivedConversations: [],
      archivedFolders: [],
      systemConversations: [],
      systemFolders: [],
      contextUsage: 45000,
      contextLimit: 100000,
      activeModel: 'vertex-gemini-flash-3.8',
      availableModels: ['vertex-gemini-flash-3.8'],
      defaultModel: 'vertex-gemini-flash-3.8',
      selectConversation: vi.fn(),
      setModel: vi.fn(),
      ...storeOverrides,
    });
    controls = /** @type {any} */ (document.createElement('conversation-controls'));
    document.body.append(controls);
    controls.store = store;
    await controls.updateComplete;
    return { controls, store };
  }

  // The chat-controls context meter dispatches toggle-context-inspector; this
  // element owns the inspector and must open it in response.
  it('opens the context inspector on toggle-context-inspector and closes on the next', async () => {
    const { controls } = await mount();
    const inspector = /** @type {any} */ (controls.querySelector('context-inspector'));
    expect(inspector).toBeTruthy();
    expect(controls._contextInspectorOpen).toBe(false);

    document.dispatchEvent(new CustomEvent('toggle-context-inspector', { detail: { convId: 'c1' } }));
    await controls.updateComplete;
    expect(controls._contextInspectorOpen).toBe(true);
    expect(inspector.open).toBe(true);

    // Let the inspector's deferred outside-click listener register, then
    // toggle closed again.
    await new Promise((r) => requestAnimationFrame(r));
    document.dispatchEvent(new CustomEvent('toggle-context-inspector', { detail: { convId: 'c1' } }));
    await controls.updateComplete;
    expect(controls._contextInspectorOpen).toBe(false);
    expect(inspector.open).toBe(false);
  });

  // The sidebar's meter used to flip the inspector state directly; now it
  // dispatches the same event as chat-controls' meter, so both surfaces share
  // one summon contract.
  it('summons the inspector when its own context meter is clicked', async () => {
    const { controls } = await mount();
    const meter = /** @type {HTMLElement} */ (controls.querySelector('.context-usage'));
    expect(meter).toBeTruthy();

    meter.click();
    await controls.updateComplete;
    const inspector = /** @type {any} */ (controls.querySelector('context-inspector'));
    expect(controls._contextInspectorOpen).toBe(true);
    expect(inspector.open).toBe(true);
  });

  // The composer's control strip hosts its own model picker and context meter;
  // when present, this element renders only the inspector they can summon.
  it('renders only the inspector when the composer control strip is present', async () => {
    const strip = /** @type {any} */ (document.createElement('chat-controls'));
    document.body.append(strip);
    try {
      const { controls } = await mount();
      expect(controls.querySelector('model-picker')).toBeNull();
      expect(controls.querySelector('.context-usage')).toBeNull();
      expect(controls.querySelector('context-inspector')).toBeTruthy();
    } finally {
      strip.remove();
    }
  });

  // #1408 — the controls render straight from store getters on change events;
  // no reactive mirror fields. These pin the re-render contract: a store
  // change must update the DOM even though no reactive property changed.
  it('re-renders the context usage meter from store values on change', async () => {
    const { controls, store } = await mount();
    expect(controls.querySelector('.context-usage')?.textContent).toContain('45%');

    store.contextUsage = 90000;
    store.dispatchEvent(new Event('change'));
    await controls.updateComplete;

    expect(controls.querySelector('.context-usage')?.textContent).toContain('90%');
  });

  it('re-renders the model picker from store values on change', async () => {
    // #2011: the rows live in the nested <picker-popup>, which has its own
    // update cycle, so flush both elements before asserting.
    /** @param {any} el */
    async function flushPicker(el) {
      const picker = /** @type {any} */ (el.querySelector('model-picker'));
      await picker.updateComplete;
      await picker.querySelector('picker-popup')?.updateComplete;
    }

    const { controls, store } = await mount();
    await flushPicker(controls);
    expect(controls.querySelector('.picker-popup-trigger')?.textContent).toContain('vertex-gemini-flash-3.8');

    store.activeModel = 'openai-gpt-4o';
    store.availableModels = ['vertex-gemini-flash-3.8', 'openai-gpt-4o'];
    store.dispatchEvent(new Event('change'));
    await controls.updateComplete;
    await flushPicker(controls);

    expect(controls.querySelector('.picker-popup-trigger')?.textContent).toContain('openai-gpt-4o');
  });
});
