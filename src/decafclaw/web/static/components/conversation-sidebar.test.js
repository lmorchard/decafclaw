import { afterEach, describe, expect, it, vi } from 'vitest';
import './conversation-sidebar.js';

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
      defaultModel: 'vertex-gemini-flash-3.8',
      selectConversation: vi.fn(),
      listConversations: vi.fn(),
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

  it('renders model selector and selects the active model', async () => {
    const { sidebar } = await mount({ activeModel: 'vertex-gemini-flash-3.8' });
    const select = /** @type {HTMLSelectElement} */ (sidebar.querySelector('#model-select'));
    expect(select).toBeTruthy();
    expect(select.value).toBe('vertex-gemini-flash-3.8');
  });

  it('falls back to defaultModel when activeModel is empty', async () => {
    const { sidebar } = await mount({
      activeModel: '',
      defaultModel: 'openai-gpt-4o',
    });
    const select = /** @type {HTMLSelectElement} */ (sidebar.querySelector('#model-select'));
    expect(select).toBeTruthy();
    expect(select.value).toBe('openai-gpt-4o');
  });

  it('updates model selector when switching conversations to a different model', async () => {
    const { sidebar, store } = await mount({ activeModel: 'vertex-gemini-flash-3.8' });
    const select = /** @type {HTMLSelectElement} */ (sidebar.querySelector('#model-select'));
    expect(select.value).toBe('vertex-gemini-flash-3.8');

    // Simulate switching conversation: activeModel changes to another model
    store.activeModel = 'openai-gpt-4o';
    store.dispatchEvent(new Event('change'));
    await sidebar.updateComplete;

    expect(select.value).toBe('openai-gpt-4o');
  });

  it('retains correct model even when temporary reset to empty string happens during switch', async () => {
    const { sidebar, store } = await mount({ activeModel: 'vertex-gemini-flash-3.8' });
    const select = /** @type {HTMLSelectElement} */ (sidebar.querySelector('#model-select'));
    expect(select.value).toBe('vertex-gemini-flash-3.8');

    // Simulating selectConversation: activeModel is cleared
    store.activeModel = '';
    store.defaultModel = 'anthropic-claude-3.5-sonnet';
    store.dispatchEvent(new Event('change'));
    await sidebar.updateComplete;
    expect(select.value).toBe('anthropic-claude-3.5-sonnet');

    // Now CONV_HISTORY arrives with the conversation's active model
    store.activeModel = 'vertex-gemini-flash-3.8';
    store.dispatchEvent(new Event('change'));
    await sidebar.updateComplete;

    expect(select.value).toBe('vertex-gemini-flash-3.8');
  });

  it('flags and selects an unconfigured model', async () => {
    const { sidebar } = await mount({
      activeModel: 'legacy-model-v1',
      availableModels: ['anthropic-claude-3.5-sonnet', 'openai-gpt-4o'],
    });
    const select = /** @type {HTMLSelectElement} */ (sidebar.querySelector('#model-select'));
    expect(select.value).toBe('legacy-model-v1');
    expect(select.textContent).toContain('⚠ legacy-model-v1 (not configured)');
  });

  it('calls store.setModel when the user changes selection', async () => {
    const { sidebar, store } = await mount({ activeModel: 'vertex-gemini-flash-3.8' });
    const select = /** @type {HTMLSelectElement} */ (sidebar.querySelector('#model-select'));
    select.value = 'openai-gpt-4o';
    select.dispatchEvent(new Event('change'));

    expect(store.setModel).toHaveBeenCalledWith('openai-gpt-4o');
  });
});
