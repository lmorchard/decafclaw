import { afterEach, describe, expect, it, vi } from 'vitest';

await import('../lib/bookmarks-store.js');
await import('./files-sidebar.js');
await import('./vault-sidebar.js');
await import('./file-page.js');
await import('./wiki-page.js');
const { reset } = await import('../lib/bookmarks-store.js');

const jsonResponse = (body) => ({
  ok: true,
  status: 200,
  statusText: 'OK',
  // The generic api-client path branches on Content-Type; without a header
  // the body is dropped (response.headers.get throws inside its try/catch).
  headers: { get: (/** @type {string} */ k) => (k.toLowerCase() === 'content-type' ? 'application/json' : null) },
  json: async () => body,
});

// Stateful fake server for /api/bookmarks plus the listing endpoints the
// sidebars fetch on activation. Mirrors the backend upsert/remove semantics.
function makeServer(initial) {
  const server = { files: [...initial.files], vault: [...initial.vault] };
  const fetchMock = vi.fn(async (url, init = {}) => {
    const u = new URL(url, 'http://t');
    const method = (init?.method ?? 'GET').toUpperCase();
    if (u.pathname === '/api/bookmarks') {
      if (method === 'POST') {
        const body = JSON.parse(init.body);
        const list = server[body.target];
        const entry = {
          path: body.path,
          label: body.label || body.path.split('/').pop(),
          kind: body.kind,
        };
        const i = list.findIndex(b => b.path === body.path);
        if (i >= 0) list[i] = entry; else list.push(entry);
        return jsonResponse({ files: server.files, vault: server.vault });
      }
      if (method === 'DELETE') {
        const target = u.searchParams.get('target');
        const path = u.searchParams.get('path');
        server[target] = server[target].filter(b => b.path !== path);
        return jsonResponse({ files: server.files, vault: server.vault });
      }
      return jsonResponse({ files: server.files, vault: server.vault });
    }
    if (u.pathname === '/api/workspace') {
      return jsonResponse({ folders: WORKSPACE_FOLDERS, files: WORKSPACE_FILES });
    }
    if (u.pathname === '/api/workspace/recent') return jsonResponse({ files: [] });
    if (u.pathname === '/api/vault') {
      return jsonResponse({ folders: VAULT_FOLDERS, pages: VAULT_PAGES });
    }
    if (u.pathname === '/api/vault/recent') return jsonResponse({ pages: [] });
    return jsonResponse({});
  });
  vi.stubGlobal('fetch', fetchMock);
  return { server, fetchMock };
}

const WORKSPACE_FOLDERS = [
  { name: 'blog', path: 'content/blog' },
  { name: 'drafts', path: 'content/blog/drafts' },
  { name: 'notes', path: 'content/notes' },
];
const WORKSPACE_FILES = [
  { path: 'content/notes/todo.md', name: 'todo.md', size: 12, modified: 1, kind: 'text', secret: false, readonly: false },
];
const VAULT_FOLDERS = [
  { name: 'journals', path: 'journals' },
  { name: 'notes', path: 'notes' },
];
const VAULT_PAGES = [
  { path: 'journals/2026-10-20.md', title: 'Today', summary: '' },
];

const INITIAL = {
  files: [
    { path: 'content/blog/drafts', label: 'drafts', kind: 'dir' },
    { path: 'content/notes/todo.md', label: 'todo.md', kind: 'file' },
  ],
  vault: [
    { path: 'journals/2026-10-20.md', label: 'Today', kind: 'page' },
    { path: 'notes', label: 'notes', kind: 'folder' },
  ],
};

async function waitFor(cond) {
  await vi.waitFor(() => expect(Boolean(cond())).toBe(true));
}

function bookmarkSection(el) {
  return el.querySelector('.bookmark-section');
}

function sectionRows(el) {
  return [...bookmarkSection(el).querySelectorAll('.bookmark-row')];
}

function folderStar(el, folderPath) {
  const row = el.querySelector(`.wiki-folder-item[title="${folderPath}"]`);
  return row.querySelector('.folder-bm-btn');
}

afterEach(() => {
  document.body.innerHTML = '';
  vi.unstubAllGlobals();
  reset();
});

describe('files-sidebar bookmarks', () => {
  it('renders the pinned section above the view toggle', async () => {
    makeServer(INITIAL);
    const el = /** @type {any} */ (document.createElement('files-sidebar'));
    document.body.append(el);
    el.active = true;
    await el.updateComplete;
    await waitFor(() => bookmarkSection(el));

    const section = bookmarkSection(el);
    expect(section.querySelector('.bookmark-section-header')?.textContent).toContain('Bookmarks');
    // Pinned above the Browse/Recent toggle.
    const toggle = el.querySelector('.vault-view-toggle');
    expect(section.compareDocumentPosition(toggle) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(sectionRows(el).map(r => r.getAttribute('title'))).toEqual(
      ['content/blog/drafts', 'content/notes/todo.md']
    );
  });

  it('opens a file bookmark with file-open and a folder bookmark by navigating', async () => {
    makeServer(INITIAL);
    const el = /** @type {any} */ (document.createElement('files-sidebar'));
    document.body.append(el);
    el.active = true;
    await el.updateComplete;
    await waitFor(() => bookmarkSection(el));

    const opened = [];
    el.addEventListener('file-open', (/** @type {CustomEvent} */ e) => opened.push(e.detail));
    sectionRows(el).find(r => r.getAttribute('title') === 'content/notes/todo.md').click();
    await el.updateComplete;
    expect(opened).toEqual([{ path: 'content/notes/todo.md' }]);

    sectionRows(el).find(r => r.getAttribute('title') === 'content/blog/drafts').click();
    await el.updateComplete;
    expect(el._currentFolder).toBe('content/blog/drafts');
  });

  it('toggles the folder-row star via POST then DELETE', async () => {
    const { server, fetchMock } = makeServer(INITIAL);
    const el = /** @type {any} */ (document.createElement('files-sidebar'));
    document.body.append(el);
    el.active = true;
    await el.updateComplete;
    await waitFor(() => bookmarkSection(el) && el.querySelector('.wiki-folder-item'));

    // 'content/blog' is not bookmarked initially.
    const star = folderStar(el, 'content/blog');
    expect(star.classList.contains('active')).toBe(false);
    expect(star.textContent.trim()).toBe('\u2606');

    star.click();
    await waitFor(() => folderStar(el, 'content/blog').classList.contains('active'));
    expect(folderStar(el, 'content/blog').textContent.trim()).toBe('\u2605');
    const post = fetchMock.mock.calls.find(([url, init]) =>
      new URL(url, 'http://t').pathname === '/api/bookmarks' && init?.method === 'POST');
    expect(JSON.parse(post[1].body)).toEqual({
      target: 'files', path: 'content/blog', label: 'blog', kind: 'dir',
    });
    expect(server.files.some(b => b.path === 'content/blog')).toBe(true);

    folderStar(el, 'content/blog').click();
    await waitFor(() => !folderStar(el, 'content/blog').classList.contains('active'));
    const del = fetchMock.mock.calls.find(([url, init]) =>
      new URL(url, 'http://t').pathname === '/api/bookmarks' && init?.method === 'DELETE');
    const q = new URL(del[0], 'http://t').searchParams;
    expect(q.get('target')).toBe('files');
    expect(q.get('path')).toBe('content/blog');
    expect(server.files.some(b => b.path === 'content/blog')).toBe(false);
  });
});

describe('vault-sidebar bookmarks', () => {
  it('renders the pinned section and opens bookmarked pages', async () => {
    makeServer(INITIAL);
    const el = /** @type {any} */ (document.createElement('vault-sidebar'));
    document.body.append(el);
    el.active = true;
    await el.updateComplete;
    await waitFor(() => bookmarkSection(el));

    expect(sectionRows(el).map(r => r.getAttribute('title'))).toEqual(
      ['journals/2026-10-20.md', 'notes']
    );

    const opened = [];
    el.addEventListener('wiki-open', (/** @type {CustomEvent} */ e) => opened.push(e.detail));
    sectionRows(el).find(r => r.getAttribute('title') === 'journals/2026-10-20.md').click();
    await el.updateComplete;
    expect(opened).toEqual([{ page: 'journals/2026-10-20.md' }]);

    sectionRows(el).find(r => r.getAttribute('title') === 'notes').click();
    await el.updateComplete;
    expect(el._vaultFolder).toBe('notes');
  });

  it('toggles the folder-row star with kind folder', async () => {
    const { server, fetchMock } = makeServer(INITIAL);
    const el = /** @type {any} */ (document.createElement('vault-sidebar'));
    document.body.append(el);
    el.active = true;
    await el.updateComplete;
    await waitFor(() => bookmarkSection(el) && el.querySelector('.wiki-folder-item'));

    const star = folderStar(el, 'journals');
    expect(star.classList.contains('active')).toBe(false);
    star.click();
    await waitFor(() => folderStar(el, 'journals').classList.contains('active'));
    const post = fetchMock.mock.calls.find(([url, init]) =>
      new URL(url, 'http://t').pathname === '/api/bookmarks' && init?.method === 'POST');
    expect(JSON.parse(post[1].body)).toEqual({
      target: 'vault', path: 'journals', label: 'journals', kind: 'folder',
    });
    expect(server.vault.some(b => b.path === 'journals')).toBe(true);
  });
});

describe('viewer toolbar bookmark toggles', () => {
  it('file-page star reflects state and POSTs/DELETEs on toggle', async () => {
    const { server, fetchMock } = makeServer(INITIAL);
    const el = /** @type {any} */ (document.createElement('file-page'));
    document.body.append(el);
    el.path = 'content/notes/todo.md';
    el.kind = 'text';
    await el.updateComplete;
    await vi.waitFor(() => {
      expect(el.querySelector('.file-bookmark-btn')?.classList.contains('active')).toBe(true);
    });
    expect(el.querySelector('.file-bookmark-btn').textContent.trim()).toBe('\u2605');

    // 'content/notes/todo.md' is bookmarked → first click removes.
    el.querySelector('.file-bookmark-btn').click();
    await vi.waitFor(() => {
      expect(el.querySelector('.file-bookmark-btn')?.classList.contains('active')).toBe(false);
    });
    const del = fetchMock.mock.calls.find(([url, init]) =>
      new URL(url, 'http://t').pathname === '/api/bookmarks' && init?.method === 'DELETE');
    const q = new URL(del[0], 'http://t').searchParams;
    expect(q.get('target')).toBe('files');
    expect(q.get('path')).toBe('content/notes/todo.md');
    expect(server.files.some(b => b.path === 'content/notes/todo.md')).toBe(false);

    el.querySelector('.file-bookmark-btn').click();
    await vi.waitFor(() => {
      expect(el.querySelector('.file-bookmark-btn')?.classList.contains('active')).toBe(true);
    });
    expect(server.files.some(b => b.path === 'content/notes/todo.md')).toBe(true);
  });

  it('wiki-page star reflects state and POSTs/DELETEs on toggle', async () => {
    // View mode keeps the render path light in jsdom.
    localStorage.setItem('wiki-edit-mode', 'false');
    makeServer(INITIAL);
    const el = /** @type {any} */ (document.createElement('wiki-page'));
    document.body.append(el);
    el.page = 'journals/2026-10-20.md';
    await vi.waitFor(() => {
      expect(el.querySelector('.wiki-bookmark-btn')?.classList.contains('active')).toBe(true);
    });

    const btn = el.querySelector('.wiki-bookmark-btn');
    btn.click();
    await vi.waitFor(() => {
      expect(el.querySelector('.wiki-bookmark-btn')?.classList.contains('active')).toBe(false);
    });

    btn.click();
    await vi.waitFor(() => {
      expect(el.querySelector('.wiki-bookmark-btn')?.classList.contains('active')).toBe(true);
    });
  });
});
