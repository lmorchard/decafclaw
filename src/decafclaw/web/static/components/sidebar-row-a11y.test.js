// Keyboard access for clickable sidebar list rows (#555). Each row is a
// focusable <div> with an aria-label; Enter and Space do what a click does.
import { afterEach, describe, expect, it, vi } from 'vitest';

import './schedules-sidebar.js';
import './vault-sidebar.js';
import './files-sidebar.js';
import './conversation-sidebar.js';

const jsonResponse = (body, status = 200) => new Response(JSON.stringify(body), {
  status,
  headers: { 'Content-Type': 'application/json' },
});

/** @param {Element} row @param {string} key */
function press(row, key) {
  const ev = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true });
  row.dispatchEvent(ev);
  return ev;
}

/**
 * Assert that a row is focusable, labelled, and activates on click, Enter,
 * and Space, and on no other key.
 * @param {() => HTMLElement} getRow - re-queried each step; activation can re-render
 * @param {() => number} activations - how many times the row's action ran
 * @param {string} label - expected aria-label
 */
async function expectKeyboardRow(getRow, activations, label) {
  const row = getRow();
  expect(row.getAttribute('tabindex')).toBe('0');
  expect(row.getAttribute('aria-label')).toBe(label);
  expect(row.hasAttribute('role')).toBe(false);
  expect(row.tagName).toBe('DIV');
  const start = activations();

  getRow().click();
  await vi.waitFor(() => expect(activations()).toBe(start + 1));

  const enter = press(getRow(), 'Enter');
  await vi.waitFor(() => expect(activations()).toBe(start + 2));
  expect(enter.defaultPrevented).toBe(false);

  const space = press(getRow(), ' ');
  await vi.waitFor(() => expect(activations()).toBe(start + 3));
  // Space would otherwise scroll the sidebar list.
  expect(space.defaultPrevented).toBe(true);

  for (const key of ['a', 'Escape', 'Tab', 'ArrowDown', 'Spacebar']) {
    expect(press(getRow(), key).defaultPrevented).toBe(false);
  }
  await new Promise(resolve => setTimeout(resolve, 0));
  expect(activations()).toBe(start + 3);
}

afterEach(() => {
  vi.restoreAllMocks();
  document.body.replaceChildren();
  localStorage.clear();
});

describe('schedules-sidebar row keyboard access', () => {
  const SCHEDULE = {
    name: 'Daily digest', source_tier: 'admin', source_path: 'schedules/Daily digest.md',
    has_overlay: false, enabled: true, schedule: '0 3 * * *', channel: '', model: '',
    allowed_tools: [], disallowed_tools: [], required_skills: [], shell_patterns: [],
    email_recipients: [], pre_script: '', unknown_keys: [], frontmatter_raw: '',
    body: 'Body.', modified: 1, next_run_iso: null, last_run_iso: null,
  };

  async function mount() {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (url) =>
      String(url).endsWith('/run') ? jsonResponse({}, 202) : jsonResponse({ schedules: [SCHEDULE] }));
    const sidebar = /** @type {any} */ (document.createElement('schedules-sidebar'));
    document.body.append(sidebar);
    await sidebar.updateComplete;
    sidebar.active = true;
    await vi.waitFor(() => expect(sidebar.querySelector('.schedule-row-header')).toBeTruthy());
    return { sidebar, fetchSpy };
  }

  it('opens the schedule from the row header with click, Enter, or Space', async () => {
    const { sidebar } = await mount();
    const opened = [];
    sidebar.addEventListener('schedule-open', (/** @type {CustomEvent} */ e) => opened.push(e.detail.name));

    await expectKeyboardRow(
      () => sidebar.querySelector('.schedule-row-header'),
      () => opened.length,
      'Open schedule Daily digest',
    );
    expect(opened).toEqual(['Daily digest', 'Daily digest', 'Daily digest']);
  });

  it('does not open the schedule for keys pressed on its nested controls', async () => {
    const { sidebar, fetchSpy } = await mount();
    const opened = [];
    sidebar.addEventListener('schedule-open', () => opened.push(1));

    const run = sidebar.querySelector('.schedule-row-run');
    const toggle = sidebar.querySelector('.schedule-enabled-toggle');
    for (const control of [run, toggle]) {
      expect(press(control, 'Enter').defaultPrevented).toBe(false);
      expect(press(control, ' ').defaultPrevented).toBe(false);
    }
    await new Promise(resolve => setTimeout(resolve, 0));
    expect(opened).toEqual([]);
    // Only the initial list fetch; keydown on the run button does not run it.
    expect(fetchSpy).toHaveBeenCalledTimes(1);
  });
});

describe('vault-sidebar row keyboard access', () => {
  const PAGE = { title: 'Résumé', path: 'agent/pages/notes/Résumé', folder: 'agent/pages/notes', modified: 1, summary: '' };
  const FOLDER = { name: 'notes', path: 'agent/pages/notes' };

  async function mount() {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (url) =>
      String(url) === '/api/vault/recent'
        ? jsonResponse({ pages: [PAGE] })
        : jsonResponse({ folder: '', folders: [FOLDER], pages: [PAGE] }));
    const sidebar = /** @type {any} */ (document.createElement('vault-sidebar'));
    document.body.append(sidebar);
    await sidebar.updateComplete;
    sidebar.active = true;
    await vi.waitFor(() => expect(sidebar.querySelector('.wiki-folder-item')).toBeTruthy());
    const opened = [];
    sidebar.addEventListener('wiki-open', (/** @type {CustomEvent} */ e) => opened.push(e.detail.page));
    return { sidebar, fetchSpy, opened };
  }

  it('opens a folder with click, Enter, or Space', async () => {
    const { sidebar, fetchSpy } = await mount();
    const folderFetches = () => fetchSpy.mock.calls
      .filter(([url]) => String(url).includes('folder=agent%2Fpages%2Fnotes')).length;

    await expectKeyboardRow(
      () => sidebar.querySelector('.wiki-folder-item'),
      folderFetches,
      'Open folder notes',
    );
  });

  it('opens a page with click, Enter, or Space', async () => {
    const { sidebar, opened } = await mount();
    await expectKeyboardRow(
      () => sidebar.querySelector('.wiki-item:not(.wiki-folder-item)'),
      () => opened.length,
      'Open page Résumé',
    );
    expect(opened).toEqual([PAGE.path, PAGE.path, PAGE.path]);
  });

  it('opens a recent page with click, Enter, or Space', async () => {
    const { sidebar, opened } = await mount();
    [...sidebar.querySelectorAll('button')].find(b => b.textContent === 'Recent').click();
    await vi.waitFor(() => expect(sidebar.querySelector('.recent-item')).toBeTruthy());

    await expectKeyboardRow(
      () => sidebar.querySelector('.recent-item'),
      () => opened.length,
      'Open page agent/pages/notes/Résumé',
    );
    expect(opened).toEqual([PAGE.path, PAGE.path, PAGE.path]);
  });
});

describe('files-sidebar row keyboard access', () => {
  const FILE = { name: 'todo.md', path: 'notes/todo.md', size: 12, modified: 1, kind: 'text', readonly: false, secret: false };
  const SECRET = { name: 'key.pem', path: 'notes/key.pem', size: 12, modified: 1, kind: 'binary', readonly: true, secret: true };
  const FOLDER = { name: 'drafts', path: 'notes/drafts' };

  async function mount() {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (url) =>
      String(url) === '/api/workspace/recent'
        ? jsonResponse({ files: [FILE] })
        : jsonResponse({ folder: '', folders: [FOLDER], files: [FILE, SECRET] }));
    const sidebar = /** @type {any} */ (document.createElement('files-sidebar'));
    document.body.append(sidebar);
    await sidebar.updateComplete;
    sidebar.active = true;
    await vi.waitFor(() => expect(sidebar.querySelector('.wiki-folder-item')).toBeTruthy());
    const opened = [];
    sidebar.addEventListener('file-open', (/** @type {CustomEvent} */ e) => opened.push(e.detail.path));
    return { sidebar, fetchSpy, opened };
  }

  it('opens a folder with click, Enter, or Space', async () => {
    const { sidebar, fetchSpy } = await mount();
    const folderFetches = () => fetchSpy.mock.calls
      .filter(([url]) => String(url).includes('folder=notes%2Fdrafts')).length;

    await expectKeyboardRow(
      () => sidebar.querySelector('.wiki-folder-item'),
      folderFetches,
      'Open folder drafts',
    );
  });

  it('opens a file with click, Enter, or Space', async () => {
    const { sidebar, opened } = await mount();
    await expectKeyboardRow(
      () => sidebar.querySelector('.wiki-item:not(.wiki-folder-item):not(.secret)'),
      () => opened.length,
      'Open file todo.md',
    );
    expect(opened).toEqual([FILE.path, FILE.path, FILE.path]);
  });

  it('labels a secret file row with the reason and does not open it from the keyboard', async () => {
    const { sidebar, opened } = await mount();
    const row = sidebar.querySelector('.wiki-item.secret');
    expect(row.getAttribute('tabindex')).toBe('0');
    expect(row.getAttribute('aria-label')).toBe('key.pem, hidden from the UI by policy');
    press(row, 'Enter');
    press(row, ' ');
    row.click();
    await new Promise(resolve => setTimeout(resolve, 0));
    expect(opened).toEqual([]);
  });

  it('opens a recent file with click, Enter, or Space', async () => {
    const { sidebar, opened } = await mount();
    [...sidebar.querySelectorAll('button')].find(b => b.textContent === 'Recent').click();
    await vi.waitFor(() => expect(sidebar.querySelector('.recent-item')).toBeTruthy());

    await expectKeyboardRow(
      () => sidebar.querySelector('.recent-item'),
      () => opened.length,
      'Open file notes/todo.md',
    );
    expect(opened).toEqual([FILE.path, FILE.path, FILE.path]);
  });
});

describe('conversation-sidebar row keyboard access', () => {
  async function mount() {
    // Child sidebars mount inactive; answer any stray request harmlessly.
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () => jsonResponse({}));
    const store = Object.assign(new EventTarget(), {
      currentConvId: null,
      conversations: [{ conv_id: 'c1', title: 'Trip planning' }],
      folders: [{ name: 'Work', path: 'Work' }],
      selectConversation: vi.fn(),
      listConversations: vi.fn(),
      listArchivedConversations: vi.fn(),
      listSystemConversations: vi.fn(),
    });
    const sidebar = /** @type {any} */ (document.createElement('conversation-sidebar'));
    document.body.append(sidebar);
    sidebar.store = store;
    await vi.waitFor(() => expect(sidebar.querySelector('.conv-list .conv-item:not(.wiki-folder-item)')).toBeTruthy());
    return { sidebar, store };
  }

  it('opens a conversation with click, Enter, or Space', async () => {
    const { sidebar, store } = await mount();
    await expectKeyboardRow(
      () => sidebar.querySelector('.conv-list .conv-item:not(.wiki-folder-item)'),
      () => store.selectConversation.mock.calls.length,
      'Open conversation Trip planning',
    );
    expect(store.selectConversation.mock.calls).toEqual([['c1'], ['c1'], ['c1']]);
  });

  it('opens a chat folder with click, Enter, or Space', async () => {
    const { sidebar, store } = await mount();
    await expectKeyboardRow(
      () => sidebar.querySelector('.conv-list .wiki-folder-item'),
      () => store.listConversations.mock.calls.length,
      'Open folder Work',
    );
    expect(store.listConversations.mock.calls).toEqual([['Work'], ['Work'], ['Work']]);
  });

  it('does not open the conversation for keys pressed on its action buttons', async () => {
    const { sidebar, store } = await mount();
    const buttons = sidebar.querySelectorAll('.conv-list .conv-item:not(.wiki-folder-item) .conv-archive');
    expect(buttons.length).toBeGreaterThan(0);
    for (const button of buttons) {
      press(button, 'Enter');
      press(button, ' ');
    }
    await new Promise(resolve => setTimeout(resolve, 0));
    expect(store.selectConversation).not.toHaveBeenCalled();
  });
});
