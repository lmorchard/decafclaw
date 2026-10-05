import { afterEach, describe, expect, it, vi } from 'vitest';

await import('./tags-sidebar.js');

const TAGS = [
  { tag: 'alpha', count: 2, pages: ['notes/one', 'notes/two'] },
  { tag: 'beta', count: 1, pages: ['notes/three'] },
];

const jsonResponse = (body) => ({
  ok: true,
  status: 200,
  statusText: 'OK',
  json: async () => body,
});

async function mount() {
  vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({ tags: TAGS })));
  const sidebar = /** @type {any} */ (document.createElement('tags-sidebar'));
  document.body.append(sidebar);
  sidebar.active = true;
  await sidebar.updateComplete;
  await vi.waitFor(() => expect(sidebar.querySelectorAll('.wiki-item').length).toBe(2));
  return sidebar;
}

/** @param {any} sidebar @param {string} tag */
async function selectTag(sidebar, tag) {
  const row = [...sidebar.querySelectorAll('.wiki-item')]
    .find(el => el.getAttribute('title') === tag);
  row.click();
  await sidebar.updateComplete;
}

/** @param {any} sidebar */
async function clearSelection(sidebar) {
  /** @type {HTMLButtonElement} */ (sidebar.querySelector('.vault-breadcrumb-segment')).click();
  await sidebar.updateComplete;
}

/** @param {any} sidebar */
function activeTitles(sidebar) {
  return [...sidebar.querySelectorAll('.wiki-item.active')].map(el => el.getAttribute('title'));
}

describe('tags-sidebar open-page highlight', () => {
  afterEach(() => {
    document.body.innerHTML = '';
    vi.unstubAllGlobals();
  });

  it('marks only the row for the open page as active', async () => {
    const sidebar = await mount();
    sidebar.openPage = 'notes/two';
    await selectTag(sidebar, 'alpha');

    expect(activeTitles(sidebar)).toEqual(['notes/two']);
    const other = sidebar.querySelector('.wiki-item[title="notes/one"]');
    expect(other.classList.contains('active')).toBe(false);
    expect(other.classList.contains('conv-item')).toBe(true);
  });

  it('updates the highlight when the open page changes while a list is shown', async () => {
    const sidebar = await mount();
    await selectTag(sidebar, 'alpha');
    expect(activeTitles(sidebar)).toEqual([]);

    sidebar.openPage = 'notes/one';
    await sidebar.updateComplete;
    expect(activeTitles(sidebar)).toEqual(['notes/one']);

    sidebar.openPage = null;
    await sidebar.updateComplete;
    expect(activeTitles(sidebar)).toEqual([]);
  });

  it('keeps the open page across clearing and switching the selected tag', async () => {
    const sidebar = await mount();
    sidebar.openPage = 'notes/two';
    await selectTag(sidebar, 'alpha');
    expect(activeTitles(sidebar)).toEqual(['notes/two']);

    await clearSelection(sidebar);
    // The tag list itself never shows a page highlight.
    expect(activeTitles(sidebar)).toEqual([]);

    await selectTag(sidebar, 'beta');
    expect(activeTitles(sidebar)).toEqual([]);

    await clearSelection(sidebar);
    await selectTag(sidebar, 'alpha');
    expect(activeTitles(sidebar)).toEqual(['notes/two']);
  });
});

describe('conversation-sidebar forwards the open page to tags-sidebar', () => {
  afterEach(() => {
    document.body.innerHTML = '';
    vi.unstubAllGlobals();
  });

  it('sets the page on navigateToPageFolder and clears it on clearOpenPage', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({ tags: [], pages: [], folders: [] })));
    await import('./conversation-sidebar.js');
    const sidebar = /** @type {any} */ (document.createElement('conversation-sidebar'));
    document.body.append(sidebar);
    await sidebar.updateComplete;
    const tags = /** @type {any} */ (sidebar.querySelector('tags-sidebar'));

    sidebar.navigateToPageFolder('notes/two');
    expect(tags.openPage).toBe('notes/two');

    sidebar.clearOpenPage();
    expect(tags.openPage).toBe(null);
  });
});
