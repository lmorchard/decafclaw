import { afterEach, describe, expect, it, vi } from 'vitest';
import './vault-sidebar.js';
import './wiki-page.js';

const jsonResponse = (body, status = 200) => ({
  ok: status >= 200 && status < 300,
  status,
  statusText: status === 409 ? 'Conflict' : status === 400 ? 'Bad Request' : 'OK',
  json: async () => body,
});

describe('vault generated write callers', () => {
  afterEach(() => {
    document.body.innerHTML = '';
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('creates nested pages and folders with JSON and session credentials', async () => {
    const fetchMock = vi.fn().mockImplementation(async (_url, options = {}) =>
      (options.method ?? 'GET') === 'GET'
        ? jsonResponse({ folder: 'agent/日本語', folders: [], pages: [] })
        : jsonResponse({ ok: true }));
    vi.stubGlobal('fetch', fetchMock);
    vi.stubGlobal('prompt', vi.fn()
      .mockReturnValueOnce('Page #1')
      .mockReturnValueOnce('Folder #1'));
    const sidebar = document.createElement('vault-sidebar');
    sidebar._vaultFolder = 'agent/日本語';
    document.body.append(sidebar);
    await sidebar.updateComplete;

    sidebar.querySelector('.wiki-new-page-btn').click();
    await vi.waitFor(() => expect(fetchMock.mock.calls.some(
      ([, options]) => options.method === 'POST')).toBe(true));
    sidebar.querySelector('.wiki-new-folder-btn').click();
    await vi.waitFor(() => expect(fetchMock.mock.calls.filter(
      ([, options]) => options.method === 'POST')).toHaveLength(2));

    expect(fetchMock.mock.calls.filter(([, options]) => options.method === 'POST').map(([url, options]) => ({
      url, method: options.method, credentials: options.credentials,
      body: JSON.parse(options.body),
    }))).toEqual([
      { url: '/api/vault', method: 'POST', credentials: 'same-origin',
        body: { name: 'agent/日本語/Page #1' } },
      { url: '/api/vault/folders', method: 'POST', credentials: 'same-origin',
        body: { folder: 'agent/日本語/Folder #1' } },
    ]);
  });

  it('writes metadata, renames, and deletes nested pages through generated routes', async () => {
    vi.useFakeTimers();
    const requests = [];
    const fetchMock = vi.fn().mockImplementation(async (url, options = {}) => {
      const body = options.body ? JSON.parse(options.body) : undefined;
      requests.push({ url, method: options.method ?? 'GET',
        credentials: options.credentials, body });
      if ((options.method ?? 'GET') === 'GET') {
        return jsonResponse({ title: 'Old', path: 'agent/pages/Old #1', body: '# Old',
          modified: 10, frontmatter: {}, frontmatter_raw: '' });
      }
      if (body?.frontmatter_raw === 'bad: [') {
        return jsonResponse({ error: 'invalid frontmatter YAML' }, 400);
      }
      return jsonResponse({ ok: true, modified: 11, frontmatter: { nested: [1, true, null] },
        frontmatter_raw: 'nested: [1, true, null]', frontmatter_error: '' });
    });
    vi.stubGlobal('fetch', fetchMock);
    vi.stubGlobal('confirm', vi.fn(() => true));
    vi.stubGlobal('alert', vi.fn());
    const page = document.createElement('wiki-page');
    page.page = 'agent/pages/Old #1';
    page._editing = false;
    document.body.append(page);
    await vi.runAllTimersAsync();
    await vi.waitFor(() => expect(page._loaded).toBe(true));

    page._onMetadataChange(new CustomEvent('metadata-change', {
      detail: { fields: { nested: [1, true, null] } },
    }));
    await vi.runAllTimersAsync();
    await vi.waitFor(() => expect(page._modified).toBe(11));
    await page._onMetadataRawSave(new CustomEvent('metadata-raw-save', {
      detail: { raw: 'bad: [' },
    }));

    page.querySelector('.wiki-rename-btn').click();
    await page.updateComplete;
    const rename = page.querySelector('.wiki-rename-input');
    rename.value = 'agent/archive/New 日本語';
    rename.dispatchEvent(new InputEvent('input', { bubbles: true }));
    page.querySelector('.wiki-rename-ok').click();
    await vi.waitFor(() => expect(requests.some(item => item.body?.rename_to)).toBe(true));
    page.querySelector('.wiki-delete-btn').click();
    await vi.waitFor(() => expect(requests.some(item => item.method === 'DELETE')).toBe(true));

    const writes = requests.filter(item => item.method !== 'GET');
    expect(writes).toEqual([
      { url: '/api/vault/agent/pages/Old%20%231', method: 'PUT', credentials: 'same-origin',
        body: { frontmatter: { nested: [1, true, null] }, modified: 10 } },
      { url: '/api/vault/agent/pages/Old%20%231', method: 'PUT', credentials: 'same-origin',
        body: { frontmatter_raw: 'bad: [', modified: 11 } },
      { url: '/api/vault/agent/pages/Old%20%231', method: 'PUT', credentials: 'same-origin',
        body: { rename_to: 'agent/archive/New 日本語' } },
      { url: '/api/vault/agent/pages/Old%20%231', method: 'DELETE', credentials: 'same-origin',
        body: undefined },
    ]);
  });

  it('preserves conflict and malformed-error messages', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ title: 'Conflict', path: 'Conflict', body: '',
        modified: 10, frontmatter: {}, frontmatter_raw: '' }))
      .mockResolvedValueOnce(jsonResponse({ error: 'conflict' }, 409));
    vi.stubGlobal('fetch', fetchMock);
    const page = document.createElement('wiki-page');
    page.page = 'Conflict';
    page._editing = false;
    document.body.append(page);
    await vi.waitFor(() => expect(page._loaded).toBe(true));

    await page._onMetadataRawSave(new CustomEvent('metadata-raw-save', {
      detail: { raw: 'summary: stale' },
    }));
    expect(page._metaError).toEqual({
      status: 'conflict', message: 'Metadata was modified externally.',
    });
  });
});
