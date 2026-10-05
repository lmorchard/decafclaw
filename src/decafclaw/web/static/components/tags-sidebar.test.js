import { afterEach, describe, expect, it, vi } from 'vitest';

await import('./tags-sidebar.js');

// /api/vault/tags returns vault-relative paths that keep `.md`
// (collect_all_tags in src/decafclaw/tags.py), including journal files.
const TAGS = [
  { tag: 'alpha', count: 3, pages: ['agent/pages/one.md', 'agent/pages/two.md', 'agent/journal/2026/2026-10-05.md'] },
  { tag: 'beta', count: 1, pages: ['agent/pages/three.md'] },
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
    sidebar.openPage = 'agent/pages/two.md';
    await selectTag(sidebar, 'alpha');

    expect(activeTitles(sidebar)).toEqual(['agent/pages/two.md']);
    const other = sidebar.querySelector('.wiki-item[title="agent/pages/one.md"]');
    expect(other.classList.contains('active')).toBe(false);
    expect(other.classList.contains('conv-item')).toBe(true);
  });

  it('updates the highlight when the open page changes while a list is shown', async () => {
    const sidebar = await mount();
    await selectTag(sidebar, 'alpha');
    expect(activeTitles(sidebar)).toEqual([]);

    sidebar.openPage = 'agent/pages/one.md';
    await sidebar.updateComplete;
    expect(activeTitles(sidebar)).toEqual(['agent/pages/one.md']);

    sidebar.openPage = null;
    await sidebar.updateComplete;
    expect(activeTitles(sidebar)).toEqual([]);
  });

  it('keeps the open page across clearing and switching the selected tag', async () => {
    const sidebar = await mount();
    sidebar.openPage = 'agent/pages/two.md';
    await selectTag(sidebar, 'alpha');
    expect(activeTitles(sidebar)).toEqual(['agent/pages/two.md']);

    await clearSelection(sidebar);
    // The tag list itself never shows a page highlight.
    expect(activeTitles(sidebar)).toEqual([]);

    await selectTag(sidebar, 'beta');
    expect(activeTitles(sidebar)).toEqual([]);

    await clearSelection(sidebar);
    await selectTag(sidebar, 'alpha');
    expect(activeTitles(sidebar)).toEqual(['agent/pages/two.md']);
  });
});

describe('tags-sidebar open-page path formats', () => {
  afterEach(() => {
    document.body.innerHTML = '';
    vi.unstubAllGlobals();
  });

  it('highlights a .md row when the open page is extensionless', async () => {
    // Vault tab, wiki-links, ?vault= URLs, and the agent open pages by
    // extensionless path (http_server uses rel.with_suffix("")).
    const sidebar = await mount();
    sidebar.openPage = 'agent/pages/two';
    await selectTag(sidebar, 'alpha');
    expect(activeTitles(sidebar)).toEqual(['agent/pages/two.md']);

    sidebar.openPage = 'agent/journal/2026/2026-10-05';
    await sidebar.updateComplete;
    expect(activeTitles(sidebar)).toEqual(['agent/journal/2026/2026-10-05.md']);
  });

  it('does not match a page whose name only shares a prefix', async () => {
    const sidebar = await mount();
    sidebar.openPage = 'agent/pages/tw';
    await selectTag(sidebar, 'alpha');
    expect(activeTitles(sidebar)).toEqual([]);
  });

  it('highlights the row after a Tags-row click opens its .md path', async () => {
    const sidebar = await mount();
    // app.js handles wiki-open by calling navigateToPageFolder(page), which
    // conversation-sidebar forwards as openPage. Mirror that here.
    sidebar.addEventListener('wiki-open', (/** @type {CustomEvent} */ e) => {
      sidebar.openPage = e.detail.page;
    });
    await selectTag(sidebar, 'alpha');
    sidebar.querySelector('.wiki-item[title="agent/pages/one.md"]').click();
    await sidebar.updateComplete;
    expect(sidebar.openPage).toBe('agent/pages/one.md');
    expect(activeTitles(sidebar)).toEqual(['agent/pages/one.md']);
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

    sidebar.navigateToPageFolder('agent/pages/two.md');
    expect(tags.openPage).toBe('agent/pages/two.md');

    sidebar.clearOpenPage();
    expect(tags.openPage).toBe(null);
  });
});
