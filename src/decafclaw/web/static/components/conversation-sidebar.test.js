import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { DefaultService } from '../lib/api-client/index.js';
import './conversation-sidebar.js';

vi.mock('../lib/toast.js', () => ({ showToast: vi.fn() }));

describe('conversation-sidebar section selector', () => {
  let sidebar;

  beforeEach(() => {
    vi.spyOn(DefaultService, 'wrapperApiBookmarksGet').mockResolvedValue({ files: [], vault: [] });
    vi.spyOn(DefaultService, 'wrapperApiVaultGet').mockResolvedValue({ pages: [], folders: [] });
    vi.spyOn(DefaultService, 'wrapperApiWorkspaceGet').mockResolvedValue({ files: [], folders: [] });
    vi.spyOn(DefaultService, 'wrapperApiSchedulesGet').mockResolvedValue({ schedules: [] });
    vi.spyOn(DefaultService, 'wrapperApiCalendarEventsGet').mockResolvedValue({ events: [] });
    vi.spyOn(DefaultService, 'wrapperApiVaultTagsGet').mockResolvedValue({ tags: [] });
  });

  afterEach(() => {
    sidebar?.remove();
    vi.restoreAllMocks();
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
      contextUsage: 0,
      contextLimit: 0,
      activeModel: '',
      availableModels: [],
      defaultModel: '',
      selectConversation: vi.fn(),
      listConversations: vi.fn(),
      listSessions: vi.fn(),
      listArchivedConversations: vi.fn(),
      listSystemConversations: vi.fn(),
      createConversation: vi.fn(),
      ...storeOverrides,
    });
    sidebar = /** @type {any} */ (document.createElement('conversation-sidebar'));
    document.body.append(sidebar);
    sidebar.store = store;
    await sidebar.updateComplete;
    return { sidebar, store };
  }

  it('renders one section select with options in order and defaults to conversations', async () => {
    const { sidebar } = await mount();
    const selects = sidebar.querySelectorAll('select.sidebar-section-select');
    expect(selects).toHaveLength(1);

    const select = /** @type {HTMLSelectElement} */ (selects[0]);
    const options = [...select.querySelectorAll('option')].map((option) => ({
      value: option.value,
      label: option.textContent,
    }));

    expect(options).toEqual([
      { value: 'conversations', label: 'Chats' },
      { value: 'wiki', label: 'Vault' },
      { value: 'files', label: 'Files' },
      { value: 'schedules', label: 'Schedules' },
      { value: 'calendar', label: 'Calendar' },
      { value: 'tags', label: 'Tags' },
    ]);
    expect(select.value).toBe('conversations');
  });

  it('switches to wiki from the select, hides chats, shows vault, and dispatches sidebar-section-change', async () => {
    const { sidebar } = await mount();
    const handler = vi.fn();
    document.addEventListener('sidebar-section-change', handler);

    const select = /** @type {HTMLSelectElement} */ (sidebar.querySelector('.sidebar-section-select'));
    select.value = 'wiki';
    select.dispatchEvent(new Event('change'));
    await sidebar.updateComplete;

    const vaultSidebar = /** @type {any} */ (sidebar.querySelector('vault-sidebar'));
    // The conversation list is now a child component; the sidebar hides it via
    // display:none when another section is active.
    const convList = /** @type {HTMLElement} */ (sidebar.querySelector('conversation-list'));
    expect(vaultSidebar.active).toBe(true);
    expect(vaultSidebar.getAttribute('style') || '').not.toContain('display:none');
    expect(convList.style.display).toBe('none');
    expect(handler).toHaveBeenCalledTimes(1);
    expect(handler.mock.calls[0][0].detail).toEqual({ section: 'wiki' });

    document.removeEventListener('sidebar-section-change', handler);
  });

  it('syncs the select value when switchToSchedules is called programmatically', async () => {
    const { sidebar } = await mount();

    sidebar.switchToSchedules();
    await sidebar.updateComplete;

    const select = /** @type {HTMLSelectElement} */ (sidebar.querySelector('.sidebar-section-select'));
    const schedulesSidebar = /** @type {any} */ (sidebar.querySelector('schedules-sidebar'));
    expect(select.value).toBe('schedules');
    expect(schedulesSidebar.active).toBe(true);
    expect(schedulesSidebar.getAttribute('style') || '').not.toContain('display:none');
  });

  it('renders no sidebar tab buttons', async () => {
    const { sidebar } = await mount();
    expect(sidebar.querySelectorAll('.sidebar-tab')).toHaveLength(0);
  });
});

describe('conversation-sidebar model selector', () => {
  let sidebar;

  afterEach(() => {
    sidebar?.remove();
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
    sidebar = /** @type {any} */ (document.createElement('conversation-sidebar'));
    document.body.append(sidebar);
    sidebar.store = store;
    await sidebar.updateComplete;
    return { sidebar, store };
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
    const { sidebar } = await mount({ activeModel: 'vertex-gemini-flash-3.8' });
    const picker = await pickerOf(sidebar);
    const trigger = picker.querySelector('.picker-popup-trigger');
    expect(trigger).toBeTruthy();
    expect(trigger.textContent).toContain('vertex-gemini-flash-3.8');
    expect(sidebar.querySelector('#model-select')).toBeNull();
  });

  it('falls back to defaultModel when activeModel is empty', async () => {
    const { sidebar } = await mount({
      activeModel: '',
      defaultModel: 'openai-gpt-4o',
    });
    const picker = await pickerOf(sidebar);
    const trigger = picker.querySelector('.picker-popup-trigger');
    expect(trigger).toBeTruthy();
    expect(trigger.textContent).toContain('openai-gpt-4o');
  });

  it('updates the picker when switching conversations to a different model', async () => {
    const { sidebar, store } = await mount({ activeModel: 'vertex-gemini-flash-3.8' });
    const picker = await pickerOf(sidebar);
    const trigger = picker.querySelector('.picker-popup-trigger');
    expect(trigger.textContent).toContain('vertex-gemini-flash-3.8');

    // Simulate switching conversation: activeModel changes to another model
    store.activeModel = 'openai-gpt-4o';
    store.dispatchEvent(new Event('change'));
    await sidebar.updateComplete;
    await picker.updateComplete;

    expect(trigger.textContent).toContain('openai-gpt-4o');
  });

  it('retains correct model even when temporary reset to empty string happens during switch', async () => {
    const { sidebar, store } = await mount({ activeModel: 'vertex-gemini-flash-3.8' });
    const picker = await pickerOf(sidebar);
    const trigger = picker.querySelector('.picker-popup-trigger');
    expect(trigger.textContent).toContain('vertex-gemini-flash-3.8');

    // Simulating selectConversation: activeModel is cleared
    store.activeModel = '';
    store.defaultModel = 'anthropic-claude-3.5-sonnet';
    store.dispatchEvent(new Event('change'));
    await sidebar.updateComplete;
    await picker.updateComplete;
    expect(trigger.textContent).toContain('anthropic-claude-3.5-sonnet');

    // Now CONV_HISTORY arrives with the conversation's active model
    store.activeModel = 'vertex-gemini-flash-3.8';
    store.dispatchEvent(new Event('change'));
    await sidebar.updateComplete;
    await picker.updateComplete;

    expect(trigger.textContent).toContain('vertex-gemini-flash-3.8');
  });

  it('flags an unconfigured model', async () => {
    const { sidebar } = await mount({
      activeModel: 'legacy-model-v1',
      availableModels: ['anthropic-claude-3.5-sonnet', 'openai-gpt-4o'],
    });
    const picker = await pickerOf(sidebar);
    const trigger = picker.querySelector('.picker-popup-trigger');
    expect(trigger.textContent).toContain('legacy-model-v1');
    expect(trigger.textContent).toContain('not configured');
  });

  it('emits set-model when the user changes selection', async () => {
    const { sidebar } = await mount({ activeModel: 'vertex-gemini-flash-3.8' });
    const picker = await pickerOf(sidebar);
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

describe('conversation-sidebar context inspector', () => {
  let sidebar;

  afterEach(() => {
    sidebar?.remove();
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
      activeMode: 'default',
      availableModes: [{ name: 'default', description: 'Standard' }],
      selectConversation: vi.fn(),
      setModel: vi.fn(),
      setMode: vi.fn(),
      ...storeOverrides,
    });
    sidebar = /** @type {any} */ (document.createElement('conversation-sidebar'));
    document.body.append(sidebar);
    sidebar.store = store;
    await sidebar.updateComplete;
    return { sidebar, store };
  }

  // The chat-controls context meter dispatches toggle-context-inspector; the
  // sidebar owns the inspector and must open it in response.
  it('opens the context inspector on toggle-context-inspector and closes on the next', async () => {
    const { sidebar } = await mount();
    const inspector = /** @type {any} */ (sidebar.querySelector('context-inspector'));
    expect(inspector).toBeTruthy();
    expect(sidebar._contextInspectorOpen).toBe(false);

    document.dispatchEvent(new CustomEvent('toggle-context-inspector', { detail: { convId: 'c1' } }));
    await sidebar.updateComplete;
    expect(sidebar._contextInspectorOpen).toBe(true);
    expect(inspector.open).toBe(true);

    // Let the inspector's deferred outside-click listener register, then
    // toggle closed again.
    await new Promise((r) => requestAnimationFrame(r));
    document.dispatchEvent(new CustomEvent('toggle-context-inspector', { detail: { convId: 'c1' } }));
    await sidebar.updateComplete;
    expect(sidebar._contextInspectorOpen).toBe(false);
    expect(inspector.open).toBe(false);
  });
});

describe('conversation-sidebar retired shell approval controls (#1684)', () => {
  let sidebar;

  afterEach(() => {
    sidebar?.remove();
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
      contextUsage: 0,
      contextLimit: 0,
      activeModel: 'vertex-gemini-flash-3.8',
      availableModels: ['vertex-gemini-flash-3.8'],
      modelGroups: [{ name: 'Vertex', models: ['vertex-gemini-flash-3.8'] }],
      defaultModel: 'vertex-gemini-flash-3.8',
      selectConversation: vi.fn(),
      listConversations: vi.fn(),
      listSessions: vi.fn(),
      listArchivedConversations: vi.fn(),
      listSystemConversations: vi.fn(),
      setModel: vi.fn(),
      createConversation: vi.fn(),
      ...storeOverrides,
    });
    sidebar = /** @type {any} */ (document.createElement('conversation-sidebar'));
    document.body.append(sidebar);
    sidebar.store = store;
    await sidebar.updateComplete;
    return { sidebar, store };
  }

  it('has no mode selector or shell approval panel/trigger', async () => {
    const { sidebar } = await mount();
    expect(sidebar.querySelector('#mode-select')).toBeNull();
    expect(sidebar.querySelector('shell-approval-panel')).toBeNull();
    expect(sidebar.querySelector('button.shell-approval-open-btn')).toBeNull();
  });
});

describe('conversation-sidebar collapse state', () => {
  let sidebar;

  beforeEach(() => localStorage.clear());

  afterEach(() => {
    sidebar?.remove();
    localStorage.clear();
  });

  async function mount(storeOverrides = {}) {
    const store = Object.assign(new EventTarget(), {
      currentConvId: 'c1',
      conversations: [{ conv_id: 'c1', title: 'One' }, { conv_id: 'c2', title: 'Two' }],
      folders: [],
      archivedConversations: [],
      archivedFolders: [],
      systemConversations: [],
      systemFolders: [],
      contextUsage: 0,
      contextLimit: 0,
      activeModel: 'm',
      availableModels: ['m'],
      defaultModel: 'm',
      activeMode: 'default',
      availableModes: [{ name: 'default', description: 'Standard' }],
      selectConversation: vi.fn(),
      setModel: vi.fn(),
      setMode: vi.fn(),
      ...storeOverrides,
    });
    sidebar = /** @type {any} */ (document.createElement('conversation-sidebar'));
    document.body.append(sidebar);
    sidebar.store = store;
    await sidebar.updateComplete;
    return { sidebar, store };
  }

  it('stores collapse per conversation and restores it when switching back', async () => {
    const { sidebar, store } = await mount();
    expect(sidebar._collapsed).toBe(false);

    sidebar.querySelector('.collapse-btn').click();
    await sidebar.updateComplete;
    expect(sidebar._collapsed).toBe(true);
    expect(localStorage.getItem('sidebar-collapsed:c1')).toBe('true');
    expect(localStorage.getItem('sidebar-collapsed')).toBeNull();

    store.currentConvId = 'c2';
    store.dispatchEvent(new Event('change'));
    await sidebar.updateComplete;
    expect(sidebar._collapsed).toBe(false);
    expect(sidebar.hasAttribute('collapsed')).toBe(false);

    store.currentConvId = 'c1';
    store.dispatchEvent(new Event('change'));
    await sidebar.updateComplete;
    expect(sidebar._collapsed).toBe(true);
    expect(sidebar.hasAttribute('collapsed')).toBe(true);
  });

  it('restores the stored state when a conversation is opened in a fresh tab', async () => {
    localStorage.setItem('sidebar-collapsed:c1', 'true');

    const { sidebar } = await mount();
    expect(sidebar._collapsed).toBe(true);
    expect(sidebar.querySelector('.collapse-btn')?.title).toBe('Expand sidebar');
  });

  it('migrates the old global collapse preference to the open conversation', async () => {
    localStorage.setItem('sidebar-collapsed', 'true');

    const { sidebar } = await mount();
    expect(sidebar._collapsed).toBe(true);
    expect(localStorage.getItem('sidebar-collapsed:c1')).toBe('true');
    expect(localStorage.getItem('sidebar-collapsed')).toBeNull();
  });
});

// #1408 — the sidebar renders straight from store getters on change events;
// no reactive mirror fields remain. These pin the re-render contract: a store
// change must update the DOM even though no reactive property of the sidebar
// changed.
describe('conversation-sidebar store reads (#1408)', () => {
  let sidebar;

  afterEach(() => {
    sidebar?.remove();
  });

  async function mount(storeOverrides = {}) {
    const store = Object.assign(new EventTarget(), {
      currentConvId: 'c1',
      conversations: [
        { conv_id: 'c1', title: 'Trip planning' },
        { conv_id: 'c2', title: 'Coding' },
      ],
      folders: [],
      archivedConversations: [],
      archivedFolders: [],
      systemConversations: [],
      systemFolders: [],
      contextUsage: 45000,
      contextLimit: 100000,
      activeModel: 'vertex-gemini-flash-3.8',
      availableModels: ['vertex-gemini-flash-3.8'],
      modelGroups: [{ name: 'Vertex', models: ['vertex-gemini-flash-3.8'] }],
      defaultModel: 'vertex-gemini-flash-3.8',
      activeMode: 'default',
      availableModes: [{ name: 'default', description: 'Standard' }],
      selectConversation: vi.fn(),
      ...storeOverrides,
    });
    sidebar = /** @type {any} */ (document.createElement('conversation-sidebar'));
    document.body.append(sidebar);
    sidebar.store = store;
    await sidebar.updateComplete;
    return { sidebar, store };
  }

  it('re-renders the conversation list when the store listing changes', async () => {
    const { sidebar, store } = await mount();
    expect(sidebar.querySelectorAll('.conv-item:not(.wiki-folder-item)')).toHaveLength(2);

    store.conversations = [...store.conversations, { conv_id: 'c3', title: 'New chat' }];
    store.dispatchEvent(new Event('change'));
    await sidebar.updateComplete;

    expect(sidebar.querySelectorAll('.conv-item:not(.wiki-folder-item)')).toHaveLength(3);
  });

  it('re-renders the context usage meter from store values on change', async () => {
    const { sidebar, store } = await mount();
    expect(sidebar.querySelector('.context-usage')?.textContent).toContain('45%');

    store.contextUsage = 90000;
    store.dispatchEvent(new Event('change'));
    await sidebar.updateComplete;

    expect(sidebar.querySelector('.context-usage')?.textContent).toContain('90%');
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

    const { sidebar, store } = await mount();
    await flushPicker(sidebar);
    expect(sidebar.querySelector('.picker-popup-trigger')?.textContent).toContain('vertex-gemini-flash-3.8');

    store.activeModel = 'openai-gpt-4o';
    store.availableModels = ['vertex-gemini-flash-3.8', 'openai-gpt-4o'];
    store.dispatchEvent(new Event('change'));
    await sidebar.updateComplete;
    await flushPicker(sidebar);

    expect(sidebar.querySelector('.picker-popup-trigger')?.textContent).toContain('openai-gpt-4o');
  });
});
