// Keyboard access for clickable sidebar list rows (#555). Each row is a
// focusable <div> with an aria-label; Enter and Space do what a click does.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { afterEach, describe, expect, it, vi } from 'vitest';

import './schedules-sidebar.js';
import './vault-sidebar.js';
import './files-sidebar.js';
import './tags-sidebar.js';
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

  // #944: the row buttons are hidden until hover. Focus inside the row must
  // show them too, so a keyboard user can Tab to them. Load the real
  // stylesheet so the computed display comes from sidebar.css.
  describe('action buttons with sidebar.css', () => {
    const stylesDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../styles');
    /** @type {HTMLStyleElement} */
    let styleEl;

    async function mountStyled() {
      styleEl = document.createElement('style');
      styleEl.textContent = fs.readFileSync(path.join(stylesDir, 'sidebar.css'), 'utf8');
      document.head.append(styleEl);
      const mounted = await mount();
      await vi.waitFor(() => expect(mounted.sidebar.querySelector('.conv-list .wiki-folder-item')).toBeTruthy());
      return mounted;
    }

    afterEach(() => styleEl?.remove());

    /**
     * jsdom caches computed styles and drops the cache on attribute changes,
     * not on focus changes. Touch an attribute so each read sees current focus.
     * @param {Element} el
     */
    function display(el) {
      el.toggleAttribute('data-style-read');
      return getComputedStyle(el).display;
    }

    for (const [name, rowSelector, labels] of [
      ['conversation', '.conv-list .conv-item:not(.wiki-folder-item)', ['Archive conversation']],
      ['chat folder', '.conv-list .wiki-folder-item', ['Delete folder']],
    ]) {
      it(`shows the ${name} row buttons while focus is in the row, with their labels`, async () => {
        const { sidebar } = await mountStyled();
        const row = sidebar.querySelector(rowSelector);
        const buttons = [...row.querySelectorAll('button.conv-archive')];
        expect(buttons.map(b => b.getAttribute('aria-label'))).toEqual(labels);

        // Hidden (and so out of the tab order) while the row has no focus.
        for (const b of buttons) expect(display(b)).toBe('none');

        row.focus();
        expect(document.activeElement).toBe(row);
        for (const b of buttons) expect(display(b)).toBe('block');

        // Focus moving from the row to a button keeps the buttons shown.
        // (jsdom has no Tab order; the PR's manual check covers real Tab.)
        buttons[0].focus();
        expect(document.activeElement).toBe(buttons[0]);
        for (const b of buttons) expect(display(b)).toBe('block');

        buttons[0].blur();
        for (const b of buttons) expect(display(b)).toBe('none');
      });
    }
  });
});

describe('tags-sidebar row keyboard access', () => {
  const TAGS = [{ tag: 'alpha', count: 2, pages: ['agent/pages/one.md', 'agent/pages/two.md'] }];

  async function mount() {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () => jsonResponse({ tags: TAGS }));
    const sidebar = /** @type {any} */ (document.createElement('tags-sidebar'));
    document.body.append(sidebar);
    await sidebar.updateComplete;
    sidebar.active = true;
    await vi.waitFor(() => expect(sidebar.querySelector('.wiki-item[title="alpha"]')).toBeTruthy());
    const opened = [];
    sidebar.addEventListener('wiki-open', (/** @type {CustomEvent} */ e) => opened.push(e.detail.page));
    return { sidebar, opened };
  }

  /** @param {any} sidebar */
  const tagRow = (sidebar) => sidebar.querySelector('.wiki-item[title="alpha"]');
  /** @param {any} sidebar */
  const showsTagPages = (sidebar) => Boolean(sidebar.querySelector('.vault-breadcrumb-segment.active'));

  /** @param {any} sidebar */
  async function backToTagList(sidebar) {
    sidebar.querySelector('button.vault-breadcrumb-segment').click();
    await vi.waitFor(() => expect(tagRow(sidebar)).toBeTruthy());
  }

  it('shows a tag\'s pages with click, Enter, or Space', async () => {
    // Activating a tag row replaces the tag list, so each step returns to it.
    const { sidebar } = await mount();
    const row = tagRow(sidebar);
    expect(row.getAttribute('tabindex')).toBe('0');
    expect(row.getAttribute('aria-label')).toBe('Open tag alpha');
    expect(row.hasAttribute('role')).toBe(false);
    expect(row.tagName).toBe('DIV');

    tagRow(sidebar).click();
    await vi.waitFor(() => expect(showsTagPages(sidebar)).toBe(true));
    await backToTagList(sidebar);

    const enter = press(tagRow(sidebar), 'Enter');
    await vi.waitFor(() => expect(showsTagPages(sidebar)).toBe(true));
    expect(enter.defaultPrevented).toBe(false);
    await backToTagList(sidebar);

    const space = press(tagRow(sidebar), ' ');
    await vi.waitFor(() => expect(showsTagPages(sidebar)).toBe(true));
    expect(space.defaultPrevented).toBe(true);
    await backToTagList(sidebar);

    for (const key of ['a', 'Escape', 'Tab', 'ArrowDown', 'Spacebar']) {
      expect(press(tagRow(sidebar), key).defaultPrevented).toBe(false);
    }
    await sidebar.updateComplete;
    expect(showsTagPages(sidebar)).toBe(false);
  });

  it('does not show a tag\'s pages for keys pressed on the row\'s children', async () => {
    const { sidebar } = await mount();
    for (const child of tagRow(sidebar).querySelectorAll('span')) {
      expect(press(child, 'Enter').defaultPrevented).toBe(false);
      expect(press(child, ' ').defaultPrevented).toBe(false);
    }
    await sidebar.updateComplete;
    expect(showsTagPages(sidebar)).toBe(false);
  });

  it('opens a tagged page with click, Enter, or Space', async () => {
    const { sidebar, opened } = await mount();
    tagRow(sidebar).click();
    await vi.waitFor(() => expect(sidebar.querySelector('.wiki-item[title="agent/pages/one.md"]')).toBeTruthy());

    await expectKeyboardRow(
      () => sidebar.querySelector('.wiki-item[title="agent/pages/one.md"]'),
      () => opened.length,
      'Open page agent/pages/one.md',
    );
    expect(opened).toEqual(['agent/pages/one.md', 'agent/pages/one.md', 'agent/pages/one.md']);
  });

  it('does not open a tagged page for keys pressed on the row\'s children', async () => {
    const { sidebar, opened } = await mount();
    tagRow(sidebar).click();
    await vi.waitFor(() => expect(sidebar.querySelector('.wiki-item[title="agent/pages/one.md"]')).toBeTruthy());

    const title = sidebar.querySelector('.wiki-item[title="agent/pages/one.md"] .conv-title');
    expect(press(title, 'Enter').defaultPrevented).toBe(false);
    expect(press(title, ' ').defaultPrevented).toBe(false);
    await new Promise(resolve => setTimeout(resolve, 0));
    expect(opened).toEqual([]);
  });
});
