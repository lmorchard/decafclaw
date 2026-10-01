import { afterEach, describe, expect, it, vi } from 'vitest';

import './vault-sidebar.js';
import './tags-sidebar.js';
import './wiki-page.js';

const jsonResponse = (body, status = 200) => new Response(JSON.stringify(body), {
  status,
  headers: { 'Content-Type': 'application/json' },
});

afterEach(() => {
  vi.restoreAllMocks();
  document.body.replaceChildren();
  localStorage.clear();
});

describe('vault generated read callers', () => {
  it('loads an encoded nested folder and recent pages with session credentials', async () => {
    const nested = 'agent/pages/日本語 #?';
    const page = {
      title: 'Résumé #1', path: `${nested}/Résumé #1`, folder: nested,
      modified: 123, summary: 'ordered summary',
    };
    const folder = { name: 'child', path: `${nested}/child` };
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation((url) => {
      if (String(url) === '/api/vault/recent') {
        return Promise.resolve(jsonResponse({ pages: [page] }));
      }
      if (String(url) === '/api/vault') {
        return Promise.resolve(jsonResponse({ folder: '', folders: [], pages: [] }));
      }
      return Promise.resolve(jsonResponse({ folder: nested, folders: [folder], pages: [page] }));
    });

    const el = document.createElement('vault-sidebar');
    document.body.append(el);
    await el.updateComplete;
    el.active = true;
    await vi.waitFor(() => expect(fetchSpy).toHaveBeenCalled());

    el.navigateToFolder(nested);
    await vi.waitFor(() => expect(el._wikiPages).toEqual([page]));
    expect(el._vaultFolders).toEqual([folder]);
    expect(fetchSpy).toHaveBeenNthCalledWith(
      2,
      '/api/vault?folder=agent%2Fpages%2F%E6%97%A5%E6%9C%AC%E8%AA%9E%20%23%3F',
      expect.objectContaining({ method: 'GET', credentials: 'same-origin' }),
    );

    const recent = [...el.querySelectorAll('button')].find(button => button.textContent === 'Recent');
    expect(recent).toBeDefined();
    recent.click();
    await vi.waitFor(() => expect(el._recentPages).toEqual([page]));
    expect(fetchSpy).toHaveBeenNthCalledWith(
      3,
      '/api/vault/recent',
      expect.objectContaining({ method: 'GET', credentials: 'same-origin' }),
    );
  });

  it('keeps tag and page ordering from generated responses', async () => {
    const tags = [
      { tag: 'Rust', count: 2, pages: ['agent/pages/B.md', 'agent/pages/A.md'] },
      { tag: 'async', count: 1, pages: ['agent/pages/C.md'] },
    ];
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse({ tags }));
    const el = document.createElement('tags-sidebar');
    document.body.append(el);
    await el.updateComplete;
    el.active = true;
    await vi.waitFor(() => expect(el._tags).toEqual(tags));
    expect(fetchSpy).toHaveBeenCalledWith(
      '/api/vault/tags',
      expect.objectContaining({ method: 'GET', credentials: 'same-origin' }),
    );
  });

  it('preserves empty/error fallbacks for list, recent, and tag reads', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(jsonResponse({ error: 'missing' }, 404))
      .mockResolvedValueOnce(jsonResponse({ error: 'failed' }, 500))
      .mockRejectedValueOnce(new TypeError('offline'));
    const sidebar = document.createElement('vault-sidebar');
    sidebar._vaultFolder = 'missing/folder';
    document.body.append(sidebar);
    await sidebar.updateComplete;
    sidebar.active = true;
    await vi.waitFor(() => expect(sidebar._vaultFolder).toBe(''));
    expect(sidebar._vaultFolders).toEqual([]);
    expect(sidebar._wikiPages).toEqual([]);

    const recent = [...sidebar.querySelectorAll('button')]
      .find(button => button.textContent === 'Recent');
    recent.click();
    await vi.waitFor(() => expect(fetchSpy).toHaveBeenCalledTimes(2));
    expect(sidebar._recentPages).toEqual([]);

    const tags = document.createElement('tags-sidebar');
    document.body.append(tags);
    await tags.updateComplete;
    tags.active = true;
    await vi.waitFor(() => expect(fetchSpy).toHaveBeenCalledTimes(3));
    expect(tags._tags).toEqual([]);
  });

  it('loads an encoded page and preserves arbitrary and malformed metadata', async () => {
    localStorage.setItem('wiki-edit-mode', 'false');
    const payload = {
      title: 'Résumé', path: 'agent/pages/日本語 #?/Résumé', body: '# Body', modified: 42,
      frontmatter: { nested: { rows: [1, true, null] }, date: '2026-10-01' },
      frontmatter_raw: 'nested:\n  rows: [1, true, null]\nbroken: [',
      frontmatter_error: 'invalid YAML',
    };
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse(payload));
    const el = document.createElement('wiki-page');
    el.page = payload.path;
    document.body.append(el);

    await vi.waitFor(() => expect(el._loaded).toBe(true));
    expect(el._frontmatter).toEqual(payload.frontmatter);
    expect(el._frontmatterRaw).toBe(payload.frontmatter_raw);
    expect(el._frontmatterError).toBe(payload.frontmatter_error);
    expect(fetchSpy).toHaveBeenCalledWith(
      '/api/vault/agent/pages/%E6%97%A5%E6%9C%AC%E8%AA%9E%20%23%3F/R%C3%A9sum%C3%A9',
      expect.objectContaining({ method: 'GET', credentials: 'same-origin' }),
    );
  });

  it.each([
    [404, 'Page "Missing" not found.'],
    [500, 'Error loading page (500).'],
  ])('preserves wiki-page HTTP %s behavior', async (status, message) => {
    localStorage.setItem('wiki-edit-mode', 'false');
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse({ error: 'failure' }, status));
    const el = document.createElement('wiki-page');
    el.page = 'Missing';
    document.body.append(el);
    await vi.waitFor(() => expect(el._error).toBe(message));
  });

  it('preserves wiki-page network and parse failure behavior', async () => {
    localStorage.setItem('wiki-edit-mode', 'false');
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new TypeError('offline'));
    const network = document.createElement('wiki-page');
    network.page = 'Offline';
    document.body.append(network);
    await vi.waitFor(() => expect(network._error).toBe('Failed to load page.'));

    vi.restoreAllMocks();
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('not json', { status: 200 }));
    const parse = document.createElement('wiki-page');
    parse.page = 'Malformed';
    document.body.append(parse);
    await vi.waitFor(() => expect(parse._error).toBe('Failed to load page.'));
  });
});
