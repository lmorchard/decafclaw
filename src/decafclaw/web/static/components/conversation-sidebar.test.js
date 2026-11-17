import { afterEach, describe, expect, it, vi } from 'vitest';
import { showToast } from '../lib/toast.js';
import './conversation-sidebar.js';

vi.mock('../lib/toast.js', () => ({ showToast: vi.fn() }));

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
      activeMode: 'default',
      availableModes: [
        { name: 'default', description: 'Standard assistance', presets: [], promoted_tools: [] },
        { name: 'dev', description: 'Software engineering', presets: ['developer', 'github'], promoted_tools: ['workspace_diff'] },
        { name: 'research', description: 'Deep research', presets: [], promoted_tools: ['web_fetch'] },
      ],
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

  /** Wait for the nested picker to render its children, then return it. */
  async function pickerOf(el) {
    const picker = /** @type {any} */ (el.querySelector('model-picker'));
    await picker.updateComplete;
    return picker;
  }

  it('renders the model picker and selects the active model', async () => {
    const { sidebar } = await mount({ activeModel: 'vertex-gemini-flash-3.8' });
    const picker = await pickerOf(sidebar);
    const trigger = picker.querySelector('.model-picker-trigger');
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
    const trigger = picker.querySelector('.model-picker-trigger');
    expect(trigger).toBeTruthy();
    expect(trigger.textContent).toContain('openai-gpt-4o');
  });

  it('updates the picker when switching conversations to a different model', async () => {
    const { sidebar, store } = await mount({ activeModel: 'vertex-gemini-flash-3.8' });
    const picker = await pickerOf(sidebar);
    const trigger = picker.querySelector('.model-picker-trigger');
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
    const trigger = picker.querySelector('.model-picker-trigger');
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
    const trigger = picker.querySelector('.model-picker-trigger');
    expect(trigger.textContent).toContain('legacy-model-v1');
    expect(trigger.textContent).toContain('not configured');
  });

  it('emits set-model when the user changes selection', async () => {
    const { sidebar } = await mount({ activeModel: 'vertex-gemini-flash-3.8' });
    const picker = await pickerOf(sidebar);
    const handler = vi.fn();
    document.addEventListener('set-model', handler);
    picker.querySelector('.model-picker-trigger').click();
    await picker.updateComplete;
    const option = [...picker.querySelectorAll('.model-picker-option')]
      .find((row) => row.dataset.model === 'openai-gpt-4o');
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

describe('conversation-sidebar mode selector', () => {
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
      activeMode: 'default',
      availableModes: [
        { name: 'default', description: 'Standard assistance', presets: [], promoted_tools: [] },
        { name: 'dev', description: 'Software engineering', presets: ['developer', 'github'], promoted_tools: ['workspace_diff'] },
        { name: 'research', description: 'Deep research', presets: [], promoted_tools: ['web_fetch'] },
      ],
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

  it('renders mode selector and selects the active mode', async () => {
    const { sidebar } = await mount({ activeMode: 'dev' });
    const select = /** @type {HTMLSelectElement} */ (sidebar.querySelector('#mode-select'));
    expect(select).toBeTruthy();
    expect(select.value).toBe('dev');
  });

  it('renders disabled Custom option when activeMode is custom', async () => {
    const { sidebar } = await mount({ activeMode: 'custom' });
    const select = /** @type {HTMLSelectElement} */ (sidebar.querySelector('#mode-select'));
    expect(select).toBeTruthy();
    expect(select.value).toBe('custom');
    const customOpt = /** @type {HTMLOptionElement} */ (select.querySelector('option[value="custom"]'));
    expect(customOpt).toBeTruthy();
    expect(customOpt.disabled).toBe(true);
  });

  it('calls store.setMode when the user changes mode selection', async () => {
    const { sidebar, store } = await mount({ activeMode: 'default' });
    const select = /** @type {HTMLSelectElement} */ (sidebar.querySelector('#mode-select'));
    select.value = 'dev';
    select.dispatchEvent(new Event('change'));

    expect(store.setMode).toHaveBeenCalledWith('dev');
  });

  it('updates mode selector when store activeMode changes', async () => {
    const { sidebar, store } = await mount({ activeMode: 'default' });
    const select = /** @type {HTMLSelectElement} */ (sidebar.querySelector('#mode-select'));
    expect(select.value).toBe('default');

    store.activeMode = 'research';
    store.dispatchEvent(new Event('change'));
    await sidebar.updateComplete;

    expect(select.value).toBe('research');
  });

  it('disables mode selector when store is busy or readOnly', async () => {
    const { sidebar, store } = await mount({ isBusy: true });
    const select = /** @type {HTMLSelectElement} */ (sidebar.querySelector('#mode-select'));
    expect(select.disabled).toBe(true);

    store.isBusy = false;
    store.isReadOnly = true;
    store.dispatchEvent(new Event('change'));
    await sidebar.updateComplete;
    expect(select.disabled).toBe(true);

    store.isReadOnly = false;
    store.dispatchEvent(new Event('change'));
    await sidebar.updateComplete;
    expect(select.disabled).toBe(false);
  });
});

describe('conversation-sidebar activity indicators', () => {
  let sidebar;

  afterEach(() => {
    sidebar?.remove();
  });

  async function mountWithStatuses(statuses = {}) {
    const store = Object.assign(new EventTarget(), {
      currentConvId: 'c1',
      conversations: [
        { conv_id: 'c1', title: 'Trip planning' },
        { conv_id: 'c2', title: 'Coding task' },
        { conv_id: 'c3', title: 'Refactoring' },
        { conv_id: 'c4', title: 'Idle chat' },
      ],
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
      getConversationStatus: (id) => statuses[id] || 'idle',
    });
    sidebar = /** @type {any} */ (document.createElement('conversation-sidebar'));
    document.body.append(sidebar);
    sidebar.store = store;
    await sidebar.updateComplete;
    return { sidebar, store };
  }

  it('renders busy spinner indicator on busy conversation rows', async () => {
    const { sidebar } = await mountWithStatuses({ c2: 'busy' });
    const row = sidebar.querySelector('.conv-item[title*="Coding task"]');
    expect(row).toBeTruthy();
    expect(row.classList.contains('status-busy')).toBe(true);
    expect(row.getAttribute('title')).toContain('(busy)');
    const indicator = row.querySelector('.conv-status.busy');
    expect(indicator).toBeTruthy();
    expect(indicator.querySelector('.conv-status-spinner')).toBeTruthy();
  });

  it('renders waiting indicator on waiting conversation rows', async () => {
    const { sidebar } = await mountWithStatuses({ c3: 'waiting' });
    const row = sidebar.querySelector('.conv-item[title*="Refactoring"]');
    expect(row).toBeTruthy();
    expect(row.classList.contains('status-waiting')).toBe(true);
    expect(row.getAttribute('title')).toContain('(waiting for answer)');
    const indicator = row.querySelector('.conv-status.waiting');
    expect(indicator).toBeTruthy();
    expect(indicator.textContent).toContain('●');
  });

  it('renders finished checkmark indicator on finished background conversation rows', async () => {
    const { sidebar } = await mountWithStatuses({ c2: 'finished' });
    const row = sidebar.querySelector('.conv-item[title*="Coding task"]');
    expect(row).toBeTruthy();
    expect(row.classList.contains('status-finished')).toBe(true);
    expect(row.getAttribute('title')).toContain('(finished)');
    const indicator = row.querySelector('.conv-status.finished');
    expect(indicator).toBeTruthy();
    expect(indicator.textContent).toContain('✓');
  });

  it('omits status indicator on idle conversation rows', async () => {
    const { sidebar } = await mountWithStatuses({});
    const row = sidebar.querySelector('.conv-item[title="Idle chat"]');
    expect(row).toBeTruthy();
    expect(row.querySelector('.conv-status')).toBeNull();
  });
});

describe('conversation-sidebar on-demand retitle action', () => {
  let sidebar;

  afterEach(() => {
    sidebar?.remove();
  });

  it('renders a Regenerate title button on active conversation rows and invokes store.retitleConversation', async () => {
    const store = Object.assign(new EventTarget(), {
      currentConvId: 'c1',
      conversations: [
        { conv_id: 'c1', title: 'Trip planning' },
        { conv_id: 'c2', title: 'Coding task' },
      ],
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
      retitleConversation: vi.fn(),
      selectConversation: vi.fn(),
      listConversations: vi.fn(),
      listSessions: vi.fn(),
    });
    sidebar = /** @type {any} */ (document.createElement('conversation-sidebar'));
    document.body.append(sidebar);
    sidebar.store = store;
    await sidebar.updateComplete;
    await sidebar.updateComplete;

    const row = sidebar.querySelector('.conv-item[title*="Trip planning"]');
    expect(row).toBeTruthy();
    const retitleBtn = row.querySelector('button[title="Regenerate title"]');
    expect(retitleBtn).toBeTruthy();
    expect(retitleBtn.textContent).toBe('✨');

    retitleBtn.click();
    expect(store.retitleConversation).toHaveBeenCalledWith('c1');
  });
});

// #1115 — the sidebar renders the server-grouped session hierarchy
// (`GET /api/sessions`) with subagent rows, status badges, and cancel.
describe('conversation-sidebar session hierarchy (#1115)', () => {
  const parent = { conv_id: 'web-alice-r1', title: 'Root chat', updated_at: '2026-01-02T00:00:00+00:00' };
  const child = { conv_id: 'web-alice-r1--child-abcdef12', title: 'Subtask abcdef12', kind: 'subagent', status: 'busy' };
  let sidebar;

  afterEach(() => {
    sidebar?.remove();
  });

  async function mountSessions(storeOverrides = {}) {
    const store = Object.assign(new EventTarget(), {
      currentConvId: 'web-alice-r1',
      conversations: [parent],
      sessions: [
        {
          session_id: 'web-alice-r1',
          title: 'Root chat',
          updated_at: '2026-01-02T00:00:00+00:00',
          status: 'idle',
          conversations: [{ ...parent, kind: 'parent' }, { ...child }],
        },
      ],
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
      activeMode: 'default',
      availableModes: [],
      commands: [],
      getConversationStatus: vi.fn(() => 'busy'),
      selectConversation: vi.fn(),
      cancelSubagent: vi.fn(),
      listConversations: vi.fn(),
      listSessions: vi.fn(),
      ...storeOverrides,
    });
    sidebar = /** @type {any} */ (document.createElement('conversation-sidebar'));
    document.body.append(sidebar);
    sidebar.store = store;
    await sidebar.updateComplete;
    return { sidebar, store };
  }

  it('renders server-grouped sessions with subagent rows, badges, and cancel', async () => {
    const { sidebar } = await mountSessions();
    const subagentRow = sidebar.querySelector('.conv-subagent');
    expect(subagentRow).toBeTruthy();
    expect(subagentRow.textContent).toContain('Subtask abcdef12');
    expect(subagentRow.textContent).toContain('Running');
    expect(sidebar.querySelector('.conv-subagent-cancel')).toBeTruthy();
  });

  it('cancel button calls store.cancelSubagent for the child conv', async () => {
    const { sidebar, store } = await mountSessions();
    /** @type {HTMLElement} */ (sidebar.querySelector('.conv-subagent-cancel')).click();
    expect(store.cancelSubagent).toHaveBeenCalledWith('web-alice-r1--child-abcdef12');
  });

  it('falls back to client-side grouping of the flat list when sessions are empty', async () => {
    const { sidebar } = await mountSessions({
      sessions: [],
      conversations: [
        { conv_id: 'web-alice-r1', title: 'Root chat' },
        { conv_id: 'web-alice-r1--child-abcdef12', title: 'Subtask abcdef12' },
      ],
    });
    expect(sidebar.querySelector('.conv-subagent')).toBeTruthy();
  });

  it('navigating chat folders refreshes the session listing', async () => {
    const { sidebar, store } = await mountSessions({ folders: [{ name: 'Work', path: 'Work' }] });
    /** @type {HTMLElement} */ (sidebar.querySelector('.wiki-folder-item')).click();
    await sidebar.updateComplete;
    expect(store.listSessions).toHaveBeenCalledWith('Work');
  });

  it('collapses and re-expands subagent rows via the session chevron', async () => {
    const { sidebar } = await mountSessions();
    const toggle = /** @type {HTMLElement} */ (sidebar.querySelector('button.conv-session-toggle'));
    expect(toggle).toBeTruthy();
    // The mounted conversation belongs to this session, so it starts expanded.
    expect(sidebar.querySelector('.conv-subagent')).toBeTruthy();
    expect(toggle.getAttribute('aria-expanded')).toBe('true');

    toggle.click();
    await sidebar.updateComplete;
    expect(sidebar.querySelector('.conv-subagent')).toBeFalsy();
    expect(toggle.getAttribute('aria-expanded')).toBe('false');

    toggle.click();
    await sidebar.updateComplete;
    expect(sidebar.querySelector('.conv-subagent')).toBeTruthy();
    expect(toggle.getAttribute('aria-expanded')).toBe('true');
  });

  it('starts every session collapsed when none of their conversations is selected', async () => {
    const { sidebar } = await mountSessions({ currentConvId: '' });
    expect(sidebar.querySelector('.conv-subagent')).toBeFalsy();
    const toggle = /** @type {HTMLElement} */ (sidebar.querySelector('button.conv-session-toggle'));
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    expect(toggle.getAttribute('aria-label')).toBe('Expand subagents');
  });

  it('expands the selected session and collapses its siblings (#1262)', async () => {
    const sibling = {
      session_id: 'web-alice-r2',
      title: 'Other chat',
      updated_at: '2026-01-01T00:00:00+00:00',
      status: 'idle',
      conversations: [
        { conv_id: 'web-alice-r2', title: 'Other chat', kind: 'parent' },
        { conv_id: 'web-alice-r2--child-99999999', title: 'Other subtask', kind: 'subagent', status: 'idle' },
      ],
    };
    const { sidebar, store } = await mountSessions();
    store.sessions = [...store.sessions, sibling];
    store.dispatchEvent(new Event('change'));
    await sidebar.updateComplete;

    const groups = [...sidebar.querySelectorAll('.conv-session')];
    expect(groups.length).toBe(2);
    const [selected, other] = groups;
    // The selected session shows its subagent; the unselected one does not.
    expect(selected.querySelector('.conv-subagent')).toBeTruthy();
    expect(selected.querySelector('button.conv-session-toggle').getAttribute('aria-expanded')).toBe('true');
    expect(other.querySelector('.conv-subagent')).toBeFalsy();
    expect(other.querySelector('button.conv-session-toggle').getAttribute('aria-expanded')).toBe('false');
  });

  it('keeps a manual expansion of an unselected session across re-render (#1262)', async () => {
    const sibling = {
      session_id: 'web-alice-r2',
      title: 'Other chat',
      updated_at: '2026-01-01T00:00:00+00:00',
      status: 'idle',
      conversations: [
        { conv_id: 'web-alice-r2', title: 'Other chat', kind: 'parent' },
        { conv_id: 'web-alice-r2--child-99999999', title: 'Other subtask', kind: 'subagent', status: 'idle' },
      ],
    };
    const { sidebar, store } = await mountSessions();
    store.sessions = [...store.sessions, sibling];
    store.dispatchEvent(new Event('change'));
    await sidebar.updateComplete;

    const other = [...sidebar.querySelectorAll('.conv-session')][1];
    /** @type {HTMLElement} */ (other.querySelector('button.conv-session-toggle')).click();
    await sidebar.updateComplete;
    expect(other.querySelector('.conv-subagent')).toBeTruthy();

    // An unrelated store change re-renders the list; the explicit expansion stays.
    store.contextUsage = 1234;
    store.dispatchEvent(new Event('change'));
    await sidebar.updateComplete;
    const otherAgain = [...sidebar.querySelectorAll('.conv-session')][1];
    expect(otherAgain.querySelector('.conv-subagent')).toBeTruthy();
    expect(otherAgain.querySelector('button.conv-session-toggle').getAttribute('aria-expanded')).toBe('true');
    // The selected session is still expanded too.
    expect([...sidebar.querySelectorAll('.conv-session')][0].querySelector('.conv-subagent')).toBeTruthy();
  });

  it('renders no chevron for sessions without subagent rows', async () => {
    const { sidebar } = await mountSessions({
      sessions: [
        {
          session_id: 'web-alice-r1',
          title: 'Root chat',
          conversations: [{ ...parent, kind: 'parent' }],
        },
      ],
    });
    expect(sidebar.querySelector('button.conv-session-toggle')).toBeFalsy();
  });

  it('auto-expands a collapsed session when one of its conversations is selected', async () => {
    const { sidebar, store } = await mountSessions();
    /** @type {HTMLElement} */ (sidebar.querySelector('button.conv-session-toggle')).click();
    await sidebar.updateComplete;
    expect(sidebar.querySelector('.conv-subagent')).toBeFalsy();

    // Selecting the child conversation (store change with a new currentConvId)
    // re-expands the group.
    store.currentConvId = 'web-alice-r1--child-abcdef12';
    store.dispatchEvent(new Event('change'));
    await sidebar.updateComplete;
    expect(sidebar.querySelector('.conv-subagent')).toBeTruthy();
  });
});

// #1277 — moving a session root into a folder by drag and drop, or through
// the keyboard/mobile "Move to…" picker.
describe('conversation-sidebar session move (#1277)', () => {
  const root = { conv_id: 'web-alice-r1', title: 'Root chat', updated_at: '2026-01-02T00:00:00+00:00' };
  const child = { conv_id: 'web-alice-r1--child-abcdef12', title: 'Subtask abcdef12', kind: 'subagent' };
  let sidebar;

  afterEach(() => {
    sidebar?.remove();
    vi.clearAllMocks();
  });

  async function flush() {
    await new Promise((r) => setTimeout(r, 0));
    await /** @type {any} */ (sidebar).updateComplete;
  }

  async function mountMove(storeOverrides = {}) {
    const store = Object.assign(new EventTarget(), {
      currentConvId: 'web-alice-r1',
      currentFolder: '',
      conversations: [root],
      sessions: [{
        session_id: 'web-alice-r1',
        title: 'Root chat',
        conversations: [{ ...root, kind: 'parent' }, { ...child }],
      }],
      folders: [
        { name: 'Work', path: 'Work' },
        { name: 'Archived', path: '_archived', virtual: true },
      ],
      archivedConversations: [],
      archivedFolders: [],
      systemConversations: [],
      systemFolders: [],
      contextUsage: 0,
      contextLimit: 0,
      activeModel: '',
      availableModels: [],
      defaultModel: '',
      activeMode: 'default',
      availableModes: [],
      getConversationStatus: vi.fn(() => 'idle'),
      selectConversation: vi.fn(),
      cancelSubagent: vi.fn(),
      listConversations: vi.fn(),
      listSessions: vi.fn(),
      listArchivedConversations: vi.fn(),
      listSystemConversations: vi.fn(),
      listAllFolders: vi.fn(async () => ['Work', 'Work/Plans']),
      moveConversation: vi.fn(async () => true),
      ...storeOverrides,
    });
    sidebar = /** @type {any} */ (document.createElement('conversation-sidebar'));
    document.body.append(sidebar);
    sidebar.store = store;
    await sidebar.updateComplete;
    return { sidebar, store };
  }

  /** @param {string} convId */
  function dragStartEvent(convId) {
    const ev = new Event('dragstart', { bubbles: true, cancelable: true });
    // @ts-ignore test shim — jsdom has no DataTransfer
    ev.dataTransfer = { setData: vi.fn(), effectAllowed: '' };
    ev.__convId = convId;
    return ev;
  }

  /** @param {string} [convId] */
  function dropEvent(convId) {
    const ev = new Event('drop', { bubbles: true, cancelable: true });
    // @ts-ignore test shim — jsdom has no DataTransfer
    ev.dataTransfer = { getData: () => convId || '', dropEffect: '' };
    return ev;
  }

  function folderRow(path) {
    return [...sidebar.querySelectorAll('.wiki-folder-item')].find((el) => el.title === path);
  }

  it('drags a root row onto a folder row and moves the session there', async () => {
    const { sidebar: el, store } = await mountMove();
    const rootRow = el.querySelector('.conv-session .conv-item');
    expect(rootRow.getAttribute('draggable')).toBe('true');

    rootRow.dispatchEvent(dragStartEvent('web-alice-r1'));
    const target = folderRow('Work');
    expect(target).toBeTruthy();
    target.dispatchEvent(new Event('dragover', { bubbles: true, cancelable: true }));
    await el.updateComplete;
    // Visible drop highlight while hovered.
    expect(target.classList.contains('drop-target')).toBe(true);

    target.dispatchEvent(dropEvent('web-alice-r1'));
    await flush();
    expect(store.moveConversation).toHaveBeenCalledWith('web-alice-r1', 'Work');
  });

  it('drops on the Chats breadcrumb to move back to the top level', async () => {
    const { sidebar: el, store } = await mountMove({ currentFolder: 'Work' });
    folderRow('Work').click();
    await flush();
    const rootRow = el.querySelector('.conv-session .conv-item');
    rootRow.dispatchEvent(dragStartEvent('web-alice-r1'));
    const chats = [...el.querySelectorAll('.vault-breadcrumb-segment')].find((b) => b.textContent.trim() === 'Chats');
    expect(chats).toBeTruthy();
    chats.dispatchEvent(dropEvent('web-alice-r1'));
    await flush();
    expect(store.moveConversation).toHaveBeenCalledWith('web-alice-r1', '');
  });

  it('highlighting an ancestor breadcrumb segment tracks the drag', async () => {
    const { sidebar: el } = await mountMove({
      currentFolder: 'Work/Plans',
      folders: [{ name: 'Plans', path: 'Work/Plans' }],
    });
    folderRow('Work/Plans').click();
    await flush();
    el.querySelector('.conv-session .conv-item').dispatchEvent(dragStartEvent('web-alice-r1'));
    const work = [...el.querySelectorAll('.vault-breadcrumb-segment')].find((b) => b.textContent.trim() === 'Work');
    work.dispatchEvent(new Event('dragover', { bubbles: true, cancelable: true }));
    await el.updateComplete;
    expect(work.classList.contains('drop-target')).toBe(true);
  });

  it('ignores drops on virtual folders', async () => {
    const { sidebar: el, store } = await mountMove();
    folderRow('_archived').dispatchEvent(dropEvent('web-alice-r1'));
    await flush();
    expect(store.moveConversation).not.toHaveBeenCalled();
  });

  it('does not drag from a subagent child row', async () => {
    const { sidebar: el, store } = await mountMove();
    const childRow = el.querySelector('.conv-subagent');
    expect(childRow.getAttribute('draggable')).toBeNull();
    childRow.dispatchEvent(new Event('dragstart', { bubbles: true, cancelable: true }));
    folderRow('Work').dispatchEvent(dropEvent('web-alice-r1--child-abcdef12'));
    await flush();
    expect(store.moveConversation).not.toHaveBeenCalled();
  });

  it('does nothing when dropping into the folder the session already occupies', async () => {
    const { sidebar: el, store } = await mountMove({
      currentFolder: 'Work',
      folders: [{ name: 'Work', path: 'Work' }],
    });
    el.querySelector('.conv-session .conv-item').dispatchEvent(dragStartEvent('web-alice-r1'));
    folderRow('Work').dispatchEvent(dropEvent('web-alice-r1'));
    await flush();
    expect(store.moveConversation).not.toHaveBeenCalled();
  });

  it('opens the picker with Top level and every full folder path, excluding virtual folders', async () => {
    const { sidebar: el, store } = await mountMove({
      listAllFolders: vi.fn(async () => ['Work', 'Work/Plans', 'a/b']),
    });
    el.querySelector('.conv-move-btn').click();
    await flush();
    const options = [...el.querySelectorAll('.conv-move-option')].map((o) => o.textContent.trim());
    expect(options[0]).toBe('Top level (current)');
    expect(options).toContain('Work');
    expect(options).toContain('Work/Plans');
    expect(options).toContain('a/b');
    expect(options.some((t) => t.includes('_archived') || t.includes('_system'))).toBe(false);

    const nested = [...el.querySelectorAll('.conv-move-option')].find((o) => o.textContent.trim() === 'a/b');
    nested.click();
    await flush();
    expect(store.moveConversation).toHaveBeenCalledWith('web-alice-r1', 'a/b');
    expect(el.querySelector('.conv-move-picker')).toBeFalsy();
  });

  it('marks the current folder disabled and focuses the first option', async () => {
    const { sidebar: el } = await mountMove({ currentFolder: 'Work' });
    el.querySelector('.conv-move-btn').click();
    await flush();
    const work = [...el.querySelectorAll('.conv-move-option')].find((o) => o.textContent.includes('Work'));
    expect(work.disabled).toBe(true);
    expect(work.textContent).toContain('(current)');
    expect(document.activeElement).toBe(el.querySelector('.conv-move-option:not([disabled])'));
  });

  it('closes with Escape and returns focus to the move action', async () => {
    const { sidebar: el } = await mountMove();
    const trigger = el.querySelector('.conv-move-btn');
    trigger.click();
    await flush();
    expect(el.querySelector('.conv-move-picker')).toBeTruthy();

    el.querySelector('.conv-move-picker').dispatchEvent(
      new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }),
    );
    await flush();
    expect(el.querySelector('.conv-move-picker')).toBeFalsy();
    expect(document.activeElement).toBe(trigger);
  });

  it('focuses the close button when no destination is enabled, so Escape still closes', async () => {
    const { sidebar: el } = await mountMove({
      currentFolder: '',
      listAllFolders: vi.fn(async () => []),
    });
    const trigger = el.querySelector('.conv-move-btn');
    trigger.click();
    await flush();
    const close = el.querySelector('.conv-move-picker-close');
    expect(close).toBeTruthy();
    expect(document.activeElement).toBe(close);

    el.querySelector('.conv-move-picker').dispatchEvent(
      new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }),
    );
    await flush();
    expect(el.querySelector('.conv-move-picker')).toBeFalsy();
    expect(document.activeElement).toBe(trigger);
  });

  it('focuses inside the picker while folders are still loading, so Escape closes it', async () => {
    /** @type {(paths: string[]) => void} */
    let resolveFolders = () => {};
    const pending = new Promise((resolve) => { resolveFolders = /** @type {any} */ (resolve); });
    const { sidebar: el } = await mountMove({
      listAllFolders: vi.fn(() => pending),
    });
    const trigger = el.querySelector('.conv-move-btn');
    trigger.click();
    await flush();

    const picker = el.querySelector('.conv-move-picker');
    expect(picker).toBeTruthy();
    expect(picker.textContent).toContain('Loading folders');
    const close = el.querySelector('.conv-move-picker-close');
    expect(document.activeElement).toBe(close);
    expect(picker.contains(document.activeElement)).toBe(true);

    // Keydown only reaches the dialog's Escape handler through the focused
    // element, so this also proves the dialog is keyboard-reachable mid-walk.
    document.activeElement.dispatchEvent(
      new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }),
    );
    await flush();
    expect(el.querySelector('.conv-move-picker')).toBeFalsy();
    expect(document.activeElement).toBe(trigger);

    // The late folder list must not reopen the picker or steal focus back.
    resolveFolders(['Work']);
    await flush();
    expect(el.querySelector('.conv-move-picker')).toBeFalsy();
    expect(document.activeElement).toBe(trigger);
  });

  it('offers no move action and no drag source on archived rows', async () => {
    const { sidebar: el, store } = await mountMove({
      archivedConversations: [{ conv_id: 'web-alice-a1', title: 'Old chat' }],
    });
    folderRow('_archived').click();
    store.dispatchEvent(new Event('change'));
    await flush();
    const row = el.querySelector('.conv-item.archived');
    expect(row).toBeTruthy();
    expect(row.getAttribute('draggable')).toBeNull();
    expect(row.querySelector('.conv-move-btn')).toBeNull();
  });

  it('offers no move action and no drag source on system rows', async () => {
    const { sidebar: el, store } = await mountMove({
      folders: [{ name: 'System', path: '_system', virtual: true }],
      systemConversations: [{ conv_id: 'web-system-1', title: 'System chat', conv_type: 'scheduler' }],
    });
    folderRow('_system').click();
    store.dispatchEvent(new Event('change'));
    await flush();
    const row = el.querySelector('.conv-item.system');
    expect(row).toBeTruthy();
    expect(row.getAttribute('draggable')).toBeNull();
    expect(row.querySelector('.conv-move-btn')).toBeNull();
  });

  it('clears the drop-target highlight on dragend, dragleave, and drop', async () => {
    const { sidebar: el } = await mountMove();
    const rootRow = el.querySelector('.conv-session .conv-item');
    const target = folderRow('Work');

    rootRow.dispatchEvent(dragStartEvent('web-alice-r1'));
    target.dispatchEvent(new Event('dragover', { bubbles: true, cancelable: true }));
    await el.updateComplete;
    expect(target.classList.contains('drop-target')).toBe(true);
    rootRow.dispatchEvent(new Event('dragend', { bubbles: true }));
    await el.updateComplete;
    expect(target.classList.contains('drop-target')).toBe(false);

    rootRow.dispatchEvent(dragStartEvent('web-alice-r1'));
    target.dispatchEvent(new Event('dragover', { bubbles: true, cancelable: true }));
    await el.updateComplete;
    expect(target.classList.contains('drop-target')).toBe(true);
    const leave = new Event('dragleave', { bubbles: true, cancelable: true });
    // @ts-ignore test shim — jsdom Event has no relatedTarget
    leave.relatedTarget = document.body;
    target.dispatchEvent(leave);
    await el.updateComplete;
    expect(target.classList.contains('drop-target')).toBe(false);

    rootRow.dispatchEvent(dragStartEvent('web-alice-r1'));
    target.dispatchEvent(new Event('dragover', { bubbles: true, cancelable: true }));
    await el.updateComplete;
    expect(target.classList.contains('drop-target')).toBe(true);
    target.dispatchEvent(dropEvent('web-alice-r1'));
    await flush();
    expect(target.classList.contains('drop-target')).toBe(false);
  });

  it('shows a toast when the move fails', async () => {
    const { sidebar: el, store } = await mountMove({ moveConversation: vi.fn(async () => false) });
    el.querySelector('.conv-session .conv-item').dispatchEvent(dragStartEvent('web-alice-r1'));
    folderRow('Work').dispatchEvent(dropEvent('web-alice-r1'));
    await flush();
    expect(store.moveConversation).toHaveBeenCalled();
    expect(showToast).toHaveBeenCalledWith('Failed to move conversation');
  });
});
