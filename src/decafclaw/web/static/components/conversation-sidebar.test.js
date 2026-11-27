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

// #1407 — the model picker, context usage meter and inspector live in
// <conversation-controls>; its behavior is covered by conversation-controls.test.js.

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

  // #1407 — the context meter and model picker re-render contracts moved to
  // conversation-controls.test.js with the controls themselves.
});

describe('conversation-sidebar controls delegation (#1407)', () => {
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
      modelGroups: [{ name: 'Vertex', models: ['vertex-gemini-flash-3.8'] }],
      defaultModel: 'vertex-gemini-flash-3.8',
      selectConversation: vi.fn(),
      ...storeOverrides,
    });
    sidebar = /** @type {any} */ (document.createElement('conversation-sidebar'));
    document.body.append(sidebar);
    sidebar.store = store;
    await sidebar.updateComplete;
    return { sidebar, store };
  }

  it('renders conversation-controls and forwards the store', async () => {
    const { sidebar, store } = await mount();
    const controls = /** @type {any} */ (sidebar.querySelector('conversation-controls'));
    expect(controls).toBeTruthy();
    expect(controls.store).toBe(store);
  });

  it('keeps the model picker and context meter inside conversation-controls', async () => {
    const { sidebar } = await mount();
    const controls = /** @type {any} */ (sidebar.querySelector('conversation-controls'));
    expect(controls.querySelector('.model-picker')).toBeTruthy();
    expect(controls.querySelector('.context-usage')?.textContent).toContain('45%');
  });
});
