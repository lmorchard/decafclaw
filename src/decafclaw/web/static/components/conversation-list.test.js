import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { DefaultService } from '../lib/api-client/index.js';
import { showToast } from '../lib/toast.js';
import './conversation-list.js';

vi.mock('../lib/toast.js', () => ({ showToast: vi.fn() }));

// #1115 — the list renders the server-grouped session hierarchy
// (`GET /api/sessions`) with subagent rows, status badges, and cancel.
describe('conversation-list session hierarchy (#1115)', () => {
  const parent = { conv_id: 'web-alice-r1', title: 'Root chat', updated_at: '2026-01-02T00:00:00+00:00' };
  const child = { conv_id: 'web-alice-r1--child-abcdef12', title: 'Subtask abcdef12', kind: 'subagent', status: 'busy' };
  let list;

  afterEach(() => {
    list?.remove();
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
    list = /** @type {any} */ (document.createElement('conversation-list'));
    document.body.append(list);
    list.store = store;
    list.active = true;
    await list.updateComplete;
    return { list, store };
  }

  it('renders server-grouped sessions with subagent rows, badges, and cancel', async () => {
    const { list } = await mountSessions();
    const subagentRow = list.querySelector('.conv-subagent');
    expect(subagentRow).toBeTruthy();
    expect(subagentRow.textContent).toContain('Subtask abcdef12');
    expect(subagentRow.textContent).toContain('Running');
    expect(list.querySelector('.conv-subagent-cancel')).toBeTruthy();
  });

  it('cancel button calls store.cancelSubagent for the child conv', async () => {
    const { list, store } = await mountSessions();
    /** @type {HTMLElement} */ (list.querySelector('.conv-subagent-cancel')).click();
    expect(store.cancelSubagent).toHaveBeenCalledWith('web-alice-r1--child-abcdef12');
  });

  it('falls back to client-side grouping of the flat list when sessions are empty', async () => {
    const { list } = await mountSessions({
      sessions: [],
      conversations: [
        { conv_id: 'web-alice-r1', title: 'Root chat' },
        { conv_id: 'web-alice-r1--child-abcdef12', title: 'Subtask abcdef12' },
      ],
    });
    expect(list.querySelector('.conv-subagent')).toBeTruthy();
  });

  it('navigating chat folders refreshes the session listing', async () => {
    const { list, store } = await mountSessions({ folders: [{ name: 'Work', path: 'Work' }] });
    /** @type {HTMLElement} */ (list.querySelector('.wiki-folder-item')).click();
    await list.updateComplete;
    expect(store.listSessions).toHaveBeenCalledWith('Work');
  });

  it('collapses and re-expands subagent rows via the session chevron', async () => {
    const { list } = await mountSessions();
    const toggle = /** @type {HTMLElement} */ (list.querySelector('button.conv-session-toggle'));
    expect(toggle).toBeTruthy();
    // The mounted conversation belongs to this session, so it starts expanded.
    expect(list.querySelector('.conv-subagent')).toBeTruthy();
    expect(toggle.getAttribute('aria-expanded')).toBe('true');

    toggle.click();
    await list.updateComplete;
    expect(list.querySelector('.conv-subagent')).toBeFalsy();
    expect(toggle.getAttribute('aria-expanded')).toBe('false');

    toggle.click();
    await list.updateComplete;
    expect(list.querySelector('.conv-subagent')).toBeTruthy();
    expect(toggle.getAttribute('aria-expanded')).toBe('true');
  });

  it('starts every session collapsed when none of their conversations is selected', async () => {
    const { list } = await mountSessions({ currentConvId: '' });
    expect(list.querySelector('.conv-subagent')).toBeFalsy();
    const toggle = /** @type {HTMLElement} */ (list.querySelector('button.conv-session-toggle'));
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
    const { list, store } = await mountSessions();
    store.sessions = [...store.sessions, sibling];
    store.dispatchEvent(new Event('change'));
    await list.updateComplete;

    const groups = [...list.querySelectorAll('.conv-session')];
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
    const { list, store } = await mountSessions();
    store.sessions = [...store.sessions, sibling];
    store.dispatchEvent(new Event('change'));
    await list.updateComplete;

    const other = [...list.querySelectorAll('.conv-session')][1];
    /** @type {HTMLElement} */ (other.querySelector('button.conv-session-toggle')).click();
    await list.updateComplete;
    expect(other.querySelector('.conv-subagent')).toBeTruthy();

    // An unrelated store change re-renders the list; the explicit expansion stays.
    store.contextUsage = 1234;
    store.dispatchEvent(new Event('change'));
    await list.updateComplete;
    const otherAgain = [...list.querySelectorAll('.conv-session')][1];
    expect(otherAgain.querySelector('.conv-subagent')).toBeTruthy();
    expect(otherAgain.querySelector('button.conv-session-toggle').getAttribute('aria-expanded')).toBe('true');
    // The selected session is still expanded too.
    expect([...list.querySelectorAll('.conv-session')][0].querySelector('.conv-subagent')).toBeTruthy();
  });

  it('renders no chevron for sessions without subagent rows', async () => {
    const { list } = await mountSessions({
      sessions: [
        {
          session_id: 'web-alice-r1',
          title: 'Root chat',
          conversations: [{ ...parent, kind: 'parent' }],
        },
      ],
    });
    expect(list.querySelector('button.conv-session-toggle')).toBeFalsy();
  });

  it('auto-expands a collapsed session when one of its conversations is selected', async () => {
    const { list, store } = await mountSessions();
    /** @type {HTMLElement} */ (list.querySelector('button.conv-session-toggle')).click();
    await list.updateComplete;
    expect(list.querySelector('.conv-subagent')).toBeFalsy();

    // Selecting the child conversation (store change with a new currentConvId)
    // re-expands the group.
    store.currentConvId = 'web-alice-r1--child-abcdef12';
    store.dispatchEvent(new Event('change'));
    await list.updateComplete;
    expect(list.querySelector('.conv-subagent')).toBeTruthy();
  });
});

// #1277 — moving a session root into a folder by drag and drop, or through
// the keyboard/mobile "Move to…" picker.
describe('conversation-list session move (#1277)', () => {
  const root = { conv_id: 'web-alice-r1', title: 'Root chat', updated_at: '2026-01-02T00:00:00+00:00' };
  const child = { conv_id: 'web-alice-r1--child-abcdef12', title: 'Subtask abcdef12', kind: 'subagent' };
  let list;

  afterEach(() => {
    list?.remove();
    vi.clearAllMocks();
  });

  async function flush() {
    await new Promise((r) => setTimeout(r, 0));
    await /** @type {any} */ (list).updateComplete;
  }

  /** Open the session menu and click its Move item. */
  async function openMovePicker(el) {
    const menu = el.querySelector('.conv-session .conv-menu');
    menu.open = true;
    await flush();
    const item = [...el.querySelectorAll('.conv-menu-item')].find((i) => i.textContent.includes('Move to folder'));
    item.click();
    await flush();
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
    list = /** @type {any} */ (document.createElement('conversation-list'));
    document.body.append(list);
    list.store = store;
    list.active = true;
    await list.updateComplete;
    return { list, store };
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
    return [...list.querySelectorAll('.wiki-folder-item')].find((el) => el.title === path);
  }

  it('drags a root row onto a folder row and moves the session there', async () => {
    const { list: el, store } = await mountMove();
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
    const { list: el, store } = await mountMove({ currentFolder: 'Work' });
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
    const { list: el } = await mountMove({
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
    const { list: el, store } = await mountMove();
    folderRow('_archived').dispatchEvent(dropEvent('web-alice-r1'));
    await flush();
    expect(store.moveConversation).not.toHaveBeenCalled();
  });

  it('does not drag from a subagent child row', async () => {
    const { list: el, store } = await mountMove();
    const childRow = el.querySelector('.conv-subagent');
    expect(childRow.getAttribute('draggable')).toBeNull();
    childRow.dispatchEvent(new Event('dragstart', { bubbles: true, cancelable: true }));
    folderRow('Work').dispatchEvent(dropEvent('web-alice-r1--child-abcdef12'));
    await flush();
    expect(store.moveConversation).not.toHaveBeenCalled();
  });

  it('does nothing when dropping into the folder the session already occupies', async () => {
    const { list: el, store } = await mountMove({
      currentFolder: 'Work',
      folders: [{ name: 'Work', path: 'Work' }],
    });
    el.querySelector('.conv-session .conv-item').dispatchEvent(dragStartEvent('web-alice-r1'));
    folderRow('Work').dispatchEvent(dropEvent('web-alice-r1'));
    await flush();
    expect(store.moveConversation).not.toHaveBeenCalled();
  });

  it('opens the picker with Top level and every full folder path, excluding virtual folders', async () => {
    const { list: el, store } = await mountMove({
      listAllFolders: vi.fn(async () => ['Work', 'Work/Plans', 'a/b']),
    });
    await openMovePicker(el);
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
    const { list: el } = await mountMove({ currentFolder: 'Work' });
    await openMovePicker(el);
    const work = [...el.querySelectorAll('.conv-move-option')].find((o) => o.textContent.includes('Work'));
    expect(work.disabled).toBe(true);
    expect(work.textContent).toContain('(current)');
    expect(document.activeElement).toBe(el.querySelector('.conv-move-option:not([disabled])'));
  });

  it('closes with Escape and returns focus to the move action', async () => {
    const { list: el } = await mountMove();
    const trigger = el.querySelector('.conv-menu-btn');
    await openMovePicker(el);
    expect(el.querySelector('.conv-move-picker')).toBeTruthy();

    el.querySelector('.conv-move-picker').dispatchEvent(
      new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }),
    );
    await flush();
    expect(el.querySelector('.conv-move-picker')).toBeFalsy();
    expect(document.activeElement).toBe(trigger);
  });

  it('focuses the close button when no destination is enabled, so Escape still closes', async () => {
    const { list: el } = await mountMove({
      currentFolder: '',
      listAllFolders: vi.fn(async () => []),
    });
    const trigger = el.querySelector('.conv-menu-btn');
    await openMovePicker(el);
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
    const { list: el } = await mountMove({
      listAllFolders: vi.fn(() => pending),
    });
    const trigger = el.querySelector('.conv-menu-btn');
    await openMovePicker(el);

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
    const { list: el, store } = await mountMove({
      archivedConversations: [{ conv_id: 'web-alice-a1', title: 'Old chat' }],
    });
    folderRow('_archived').click();
    store.dispatchEvent(new Event('change'));
    await flush();
    const row = el.querySelector('.conv-item.archived');
    expect(row).toBeTruthy();
    expect(row.getAttribute('draggable')).toBeNull();
    expect([...row.querySelectorAll('.conv-menu-item')].some((i) => i.textContent.includes('Move to folder'))).toBe(false);
  });

  it('offers no move action and no drag source on system rows', async () => {
    const { list: el, store } = await mountMove({
      folders: [{ name: 'System', path: '_system', virtual: true }],
      systemConversations: [{ conv_id: 'web-system-1', title: 'System chat', conv_type: 'scheduler' }],
    });
    folderRow('_system').click();
    store.dispatchEvent(new Event('change'));
    await flush();
    const row = el.querySelector('.conv-item.system');
    expect(row).toBeTruthy();
    expect(row.getAttribute('draggable')).toBeNull();
    expect([...row.querySelectorAll('.conv-menu-item')].some((i) => i.textContent.includes('Move to folder'))).toBe(false);
  });

  it('clears the drop-target highlight on dragend, dragleave, and drop', async () => {
    const { list: el } = await mountMove();
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
    const { list: el, store } = await mountMove({ moveConversation: vi.fn(async () => false) });
    el.querySelector('.conv-session .conv-item').dispatchEvent(dragStartEvent('web-alice-r1'));
    folderRow('Work').dispatchEvent(dropEvent('web-alice-r1'));
    await flush();
    expect(store.moveConversation).toHaveBeenCalled();
    expect(showToast).toHaveBeenCalledWith('Failed to move conversation');
  });
});

// #1515 — open a session in a new browser tab from the list.
// #1524 — the create actions sit at the bottom of the list.
describe('conversation-list row actions', () => {
  let list;
  let store;

  afterEach(() => {
    list?.remove();
    vi.restoreAllMocks();
  });

  async function mount() {
    store = Object.assign(new EventTarget(), {
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
      activeMode: 'default',
      availableModes: [],
      selectConversation: vi.fn(),
      listConversations: vi.fn(),
      listSessions: vi.fn(),
    });
    list = /** @type {any} */ (document.createElement('conversation-list'));
    document.body.append(list);
    list.store = store;
    list.active = true;
    // Assigning the store triggers a second (store-driven) update.
    await list.updateComplete;
    await list.updateComplete;
    return list.querySelector('.conv-item[title="Coding task"]');
  }

  it('selects the conversation on a plain click', async () => {
    const open = vi.spyOn(window, 'open').mockImplementation(() => null);
    const row = await mount();
    row.click();
    expect(store.selectConversation).toHaveBeenCalledWith('c2');
    expect(open).not.toHaveBeenCalled();
  });

  for (const modifier of ['metaKey', 'ctrlKey']) {
    it(`opens a ${modifier} click in a new tab without changing the selection`, async () => {
      const open = vi.spyOn(window, 'open').mockImplementation(() => null);
      const row = await mount();
      const event = new MouseEvent('click', { bubbles: true, cancelable: true, [modifier]: true });
      row.dispatchEvent(event);
      expect(event.defaultPrevented).toBe(true);
      expect(store.selectConversation).not.toHaveBeenCalled();
      expect(open).toHaveBeenCalledTimes(1);
      const [url, target, features] = open.mock.calls[0];
      expect(new URL(url, location.href).searchParams.get('conv')).toBe('c2');
      expect(target).toBe('_blank');
      expect(features).toBe('noopener');
    });
  }

  it('opens a middle click in a new tab without changing the selection', async () => {
    const open = vi.spyOn(window, 'open').mockImplementation(() => null);
    const row = await mount();
    const event = new MouseEvent('auxclick', { bubbles: true, cancelable: true, button: 1 });
    row.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(true);
    expect(store.selectConversation).not.toHaveBeenCalled();
    expect(open).toHaveBeenCalledTimes(1);
  });

  it('ignores non-middle aux clicks', async () => {
    const open = vi.spyOn(window, 'open').mockImplementation(() => null);
    const row = await mount();
    row.dispatchEvent(new MouseEvent('auxclick', { bubbles: true, cancelable: true, button: 2 }));
    expect(open).not.toHaveBeenCalled();
    expect(store.selectConversation).not.toHaveBeenCalled();
  });

  it('renders the create actions below the scrollable list', async () => {
    await mount();
    const actions = [...list.querySelectorAll('.vault-action-btns')]
      .find((el) => el.parentElement === list);
    expect(actions).toBeTruthy();
    expect(actions.previousElementSibling.classList.contains('conv-list')).toBe(true);
    expect(actions.querySelector('.new-conv-btn')).toBeTruthy();
    expect(actions.querySelector('.wiki-new-folder-btn')).toBeTruthy();
    expect(actions.previousElementSibling.querySelector('.vault-action-btns')).toBeNull();
  });
});

describe('conversation-list activity indicators', () => {
  let list;

  afterEach(() => {
    list?.remove();
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
    list = /** @type {any} */ (document.createElement('conversation-list'));
    document.body.append(list);
    list.store = store;
    list.active = true;
    await list.updateComplete;
    return { list, store };
  }

  it('renders busy spinner indicator on busy conversation rows', async () => {
    const { list } = await mountWithStatuses({ c2: 'busy' });
    const row = list.querySelector('.conv-item[title*="Coding task"]');
    expect(row).toBeTruthy();
    expect(row.classList.contains('status-busy')).toBe(true);
    expect(row.getAttribute('title')).toContain('(busy)');
    const indicator = row.querySelector('.conv-status.busy');
    expect(indicator).toBeTruthy();
    expect(indicator.querySelector('.conv-status-spinner')).toBeTruthy();
  });

  it('renders waiting indicator on waiting conversation rows', async () => {
    const { list } = await mountWithStatuses({ c3: 'waiting' });
    const row = list.querySelector('.conv-item[title*="Refactoring"]');
    expect(row).toBeTruthy();
    expect(row.classList.contains('status-waiting')).toBe(true);
    expect(row.getAttribute('title')).toContain('(waiting for answer)');
    const indicator = row.querySelector('.conv-status.waiting');
    expect(indicator).toBeTruthy();
    expect(indicator.textContent).toContain('●');
  });

  it('renders finished checkmark indicator on finished background conversation rows', async () => {
    const { list } = await mountWithStatuses({ c2: 'finished' });
    const row = list.querySelector('.conv-item[title*="Coding task"]');
    expect(row).toBeTruthy();
    expect(row.classList.contains('status-finished')).toBe(true);
    expect(row.getAttribute('title')).toContain('(finished)');
    const indicator = row.querySelector('.conv-status.finished');
    expect(indicator).toBeTruthy();
    expect(indicator.textContent).toContain('✓');
  });

  it('omits status indicator on idle conversation rows', async () => {
    const { list } = await mountWithStatuses({});
    const row = list.querySelector('.conv-item[title="Idle chat"]');
    expect(row).toBeTruthy();
    expect(row.querySelector('.conv-status')).toBeNull();
  });
});

describe('conversation-list on-demand retitle action', () => {
  let list;

  afterEach(() => {
    list?.remove();
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
    list = /** @type {any} */ (document.createElement('conversation-list'));
    document.body.append(list);
    list.store = store;
    list.active = true;
    await list.updateComplete;
    await list.updateComplete;

    const row = list.querySelector('.conv-item[title*="Trip planning"]');
    expect(row).toBeTruthy();
    const menu = row.querySelector('details.conv-menu');
    menu.open = true;
    await list.updateComplete;
    const retitleBtn = [...row.querySelectorAll('.conv-menu-item')].find((b) => b.textContent.includes('Regenerate title'));
    expect(retitleBtn).toBeTruthy();

    retitleBtn.click();
    expect(store.retitleConversation).toHaveBeenCalledWith('c1');
  });
});

describe('conversation-list session search (#1451)', () => {
  let list;

  afterEach(() => {
    list?.remove();
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  async function mount() {
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
      activeModel: '',
      availableModels: [],
      modelGroups: [],
      defaultModel: '',
      activeMode: 'default',
      availableModes: [],
      selectConversation: vi.fn(),
      listConversations: vi.fn(),
      listSessions: vi.fn(),
      listArchivedConversations: vi.fn(),
      listSystemConversations: vi.fn(),
    });
    list = /** @type {any} */ (document.createElement('conversation-list'));
    document.body.append(list);
    list.store = store;
    list.active = true;
    await list.updateComplete;
    return { list, store };
  }

  function type(input, value) {
    input.value = value;
    input.dispatchEvent(new Event('input', { bubbles: true }));
  }

  const match = {
    conv_id: 'web-testuser-c9',
    title: 'Homelab Edge Routing',
    updated_at: '2026-11-20T12:00:00+00:00',
    snippet: '…fails on CORS headers; allowlist the origin…',
    matched_role: 'user',
    archived: false,
  };

  it('debounces the query, shows content snippets, and replaces the listing', async () => {
    vi.useFakeTimers();
    const request = vi
      .spyOn(DefaultService, 'searchConversationsApiConversationsSearchGet')
      .mockResolvedValue({ query: 'cors', matches: [match] });
    const { list } = await mount();
    const input = list.querySelector('.conv-search-input');

    type(input, 'cors');
    await list.updateComplete;
    expect(request).not.toHaveBeenCalled();
    expect(list.querySelector('.conv-search-status')?.textContent).toContain('Searching');

    await vi.advanceTimersByTimeAsync(200);
    await list.updateComplete;

    expect(request).toHaveBeenCalledWith('cors', 20, true);
    expect(list.querySelector('.conv-search-snippet')?.textContent).toContain('CORS headers');
    expect(list.querySelector('.conv-search-meta')?.textContent).toContain('matched user message');
    // The normal folder/session listing is replaced while a query is active.
    expect(list.querySelector('.conv-session')).toBeNull();
  });

  it('selects a result, clears the query, and restores the listing', async () => {
    vi.useFakeTimers();
    vi.spyOn(DefaultService, 'searchConversationsApiConversationsSearchGet').mockResolvedValue({
      query: 'cors',
      matches: [match],
    });
    const { list, store } = await mount();
    type(list.querySelector('.conv-search-input'), 'cors');
    await vi.advanceTimersByTimeAsync(200);
    await list.updateComplete;

    list.querySelector('.conv-search-result').click();
    await list.updateComplete;

    expect(store.selectConversation).toHaveBeenCalledWith(match.conv_id, match.title);
    expect(list._searchQuery).toBe('');
    expect(list.querySelector('.conv-search-results')).toBeNull();
    expect(list.querySelector('.conv-search-input').value).toBe('');
  });

  it('shows a no-match message and clears on Escape', async () => {
    vi.useFakeTimers();
    vi.spyOn(DefaultService, 'searchConversationsApiConversationsSearchGet').mockResolvedValue({
      query: 'zzz',
      matches: [],
    });
    const { list } = await mount();
    const input = list.querySelector('.conv-search-input');
    type(input, 'zzz');
    await vi.advanceTimersByTimeAsync(200);
    await list.updateComplete;
    expect(list.querySelector('.conv-search-status')?.textContent).toContain('No matching sessions');

    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    await list.updateComplete;
    expect(list._searchQuery).toBe('');
    expect(list.querySelector('.conv-search-results')).toBeNull();
  });

  it('drops a slow response for a query the user already replaced', async () => {
    vi.useFakeTimers();
    /** @type {Array<(value: any) => void>} */
    const pending = [];
    vi.spyOn(DefaultService, 'searchConversationsApiConversationsSearchGet').mockImplementation(
      () => new Promise((resolve) => pending.push(resolve)),
    );
    const { list } = await mount();
    const input = list.querySelector('.conv-search-input');

    type(input, 'first');
    await vi.advanceTimersByTimeAsync(200);
    type(input, 'second');
    await vi.advanceTimersByTimeAsync(200);
    expect(pending).toHaveLength(2);

    pending[1]({ query: 'second', matches: [match] });
    await vi.advanceTimersByTimeAsync(0);
    await list.updateComplete;
    expect(list.querySelector('.conv-search-snippet')?.textContent).toContain('CORS headers');

    pending[0]({ query: 'first', matches: [] });
    await vi.advanceTimersByTimeAsync(0);
    await list.updateComplete;
    expect(list.querySelector('.conv-search-snippet')?.textContent).toContain('CORS headers');
    expect(list.querySelector('.conv-search-status')).toBeNull();
  });
});
