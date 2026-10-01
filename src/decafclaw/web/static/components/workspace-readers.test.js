import { afterEach, describe, expect, it, vi } from 'vitest';

import './files-sidebar.js';
import './file-page.js';

const jsonResponse = (body, status = 200) => new Response(JSON.stringify(body), {
  status,
  headers: { 'Content-Type': 'application/json' },
});

afterEach(() => {
  vi.restoreAllMocks();
  document.body.replaceChildren();
  localStorage.clear();
});

describe('workspace generated read callers', () => {
  it('loads an encoded nested folder and recent files through generated calls', async () => {
    const nested = 'notes/日本語 #?';
    const file = {
      name: 'résumé #1.md', path: `${nested}/résumé #1.md`, size: 12,
      modified: 123, kind: 'text', readonly: true, secret: false,
    };
    const folder = { name: 'child', path: `${nested}/child` };
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation((url) => {
      if (String(url) === '/api/workspace/recent') {
        return Promise.resolve(jsonResponse({ files: [file] }));
      }
      if (String(url) === '/api/workspace') {
        return Promise.resolve(jsonResponse({
          folder: '', folders: [{ name: 'notes', path: 'notes' }], files: [],
        }));
      }
      return Promise.resolve(jsonResponse({ folder: nested, folders: [folder], files: [file] }));
    });

    const el = document.createElement('files-sidebar');
    document.body.append(el);
    await el.updateComplete;
    el.active = true;

    await vi.waitFor(() => expect(el._folders).toEqual([{ name: 'notes', path: 'notes' }]));
    expect(fetchSpy).toHaveBeenNthCalledWith(
      1,
      '/api/workspace',
      expect.objectContaining({ method: 'GET', credentials: 'same-origin' }),
    );

    el.navigateToFolder(nested);
    await vi.waitFor(() => expect(el._files).toEqual([file]));
    expect(el._folders).toEqual([folder]);
    expect(fetchSpy).toHaveBeenNthCalledWith(
      2,
      '/api/workspace?folder=notes%2F%E6%97%A5%E6%9C%AC%E8%AA%9E%20%23%3F',
      expect.objectContaining({ method: 'GET', credentials: 'same-origin' }),
    );

    const recent = [...el.querySelectorAll('button')].find(button => button.textContent === 'Recent');
    expect(recent).toBeDefined();
    recent.click();
    await vi.waitFor(() => expect(el._recentFiles).toEqual([file]));
    expect(fetchSpy).toHaveBeenNthCalledWith(
      3,
      '/api/workspace/recent',
      expect.objectContaining({ method: 'GET', credentials: 'same-origin' }),
    );
  });

  it('loads and reloads encoded text paths through the generated operation', async () => {
    const path = 'notes/日本語 #?.md';
    const fetchSpy = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(jsonResponse({ content: 'first', modified: 10, readonly: true }))
      .mockResolvedValueOnce(jsonResponse({ content: 'second', modified: 20, readonly: true }));

    const el = document.createElement('file-page');
    el.path = path;
    el.kind = 'text';
    el.readonly = true;
    document.body.append(el);

    await vi.waitFor(() => expect(el._content).toBe('first'));
    await el.reload();
    expect(el._content).toBe('second');
    expect(el._modified).toBe(20);
    expect(fetchSpy).toHaveBeenCalledTimes(2);
    for (const call of fetchSpy.mock.calls) {
      expect(call).toEqual([
        '/api/workspace-file/notes/%E6%97%A5%E6%9C%AC%E8%AA%9E%20%23%3F.md',
        expect.objectContaining({ method: 'GET', credentials: 'same-origin' }),
      ]);
    }
  });

  it.each([
    [403, 'is not readable'],
    [404, 'not found'],
    [415, 'is not a text file'],
    [500, 'Error loading file (500)'],
  ])('preserves the file-page message for HTTP %s', async (status, message) => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse({ error: 'failure' }, status));
    const el = document.createElement('file-page');
    el.path = 'note.md';
    el.kind = 'text';
    el.readonly = true;
    document.body.append(el);
    await vi.waitFor(() => expect(el._error).toContain(message));
  });
});
