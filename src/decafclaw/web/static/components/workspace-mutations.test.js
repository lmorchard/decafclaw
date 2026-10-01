import { EditorView } from 'codemirror';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { FileEditor } from './file-editor.js';
import { FilePage } from './file-page.js';

const jsonResponse = (body, status = 200) => new Response(JSON.stringify(body), {
  status,
  headers: { 'Content-Type': 'application/json' },
});

afterEach(() => {
  vi.restoreAllMocks();
  document.body.replaceChildren();
});

/**
 * @param {string} path
 * @param {string} content
 * @param {number} modified
 */
async function mountEditor(path, content = 'initial', modified = 10) {
  const editor = new FileEditor();
  editor.path = path;
  editor.content = content;
  editor.modified = modified;
  editor.kind = 'text';
  document.body.append(editor);
  await editor.updateComplete;
  const mount = editor.querySelector('.cm-editor');
  if (!(mount instanceof HTMLElement)) throw new Error('CodeMirror did not mount');
  return { editor, view: EditorView.findFromDOM(mount) };
}

/** @param {import('./file-editor.js').FileEditor} editor @param {EditorView} view */
async function editAndFlush(editor, view) {
  view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: 'updated' } });
  await editor.flushSave();
}

/** @param {string} path */
async function mountFilePage(path) {
  const page = new FilePage();
  page.path = path;
  page.kind = 'binary';
  document.body.append(page);
  await page.updateComplete;
  return page;
}

/** @param {FilePage} page @param {string} newPath */
async function submitRename(page, newPath) {
  page.querySelector('.file-rename-btn')?.click();
  await page.updateComplete;
  const input = page.querySelector('.file-rename-input');
  if (!(input instanceof HTMLInputElement)) throw new Error('rename input did not render');
  input.value = newPath;
  input.dispatchEvent(new Event('input', { bubbles: true }));
  page.querySelector('.file-rename-ok')?.click();
}

describe('workspace generated mutation callers', () => {
  it('saves encoded nested paths with the typed body and consumes the new mtime', async () => {
    const path = 'notes/日本語 #?.md';
    const fetchSpy = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValue(jsonResponse({ ok: true, modified: 20 }));
    const { editor, view } = await mountEditor(path);
    const events = [];
    editor.addEventListener('saving', () => events.push('saving'));
    editor.addEventListener('saved', (event) => events.push(event.detail));

    await editAndFlush(editor, view);

    expect(fetchSpy).toHaveBeenCalledOnce();
    const [url, options] = fetchSpy.mock.calls[0];
    expect(url).toBe('/api/workspace/notes/%E6%97%A5%E6%9C%AC%E8%AA%9E%20%23%3F.md');
    expect(options).toEqual(expect.objectContaining({
      method: 'PUT', credentials: 'same-origin', body: JSON.stringify({ content: 'updated', modified: 10 }),
    }));
    expect(editor.modified).toBe(20);
    expect(editor.hasPendingChanges()).toBe(false);
    expect(events).toEqual(['saving', { modified: 20, path }]);
  });

  it('keeps a successful save on malformed JSON and retains the previous mtime', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('not-json', { status: 200 }));
    const { editor, view } = await mountEditor('note.md');
    const saved = vi.fn();
    editor.addEventListener('saved', saved);

    await editAndFlush(editor, view);

    expect(editor.modified).toBe(10);
    expect(editor.hasPendingChanges()).toBe(false);
    expect(saved).toHaveBeenCalledOnce();
  });

  it.each([
    [jsonResponse({ error: 'conflict', modified: 30 }, 409), 'conflict', 409, undefined],
    [new Response('{"error":"readonly path"}', { status: 403 }), 'error', 403,
      '{"error":"readonly path"}'],
  ])('preserves failed save state for HTTP responses', async (response, eventName, status, message) => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(response);
    const { editor, view } = await mountEditor('note.md');
    const failure = vi.fn();
    editor.addEventListener(eventName, failure);

    await editAndFlush(editor, view);

    expect(editor.modified).toBe(10);
    expect(editor.hasPendingChanges()).toBe(true);
    expect(failure).toHaveBeenCalledOnce();
    expect(failure.mock.calls[0][0].detail).toEqual(
      message === undefined ? { status } : { status, message },
    );
  });

  it('preserves network save errors and pending content', async () => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('offline'));
    const { editor, view } = await mountEditor('note.md');
    const failure = vi.fn();
    editor.addEventListener('error', failure);

    await editAndFlush(editor, view);

    expect(editor.modified).toBe(10);
    expect(editor.hasPendingChanges()).toBe(true);
    expect(failure.mock.calls[0][0].detail).toEqual({ status: 0, message: 'offline' });
  });

  it('renames with an encoded query, no body, and the existing navigation event', async () => {
    const oldPath = 'notes/old 日本語 #?.md';
    const newPath = 'archive/new 日本語 & #?.md';
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('', { status: 200 }));
    const page = await mountFilePage(oldPath);
    const opened = vi.fn();
    page.addEventListener('file-open', opened);

    await submitRename(page, newPath);
    await vi.waitFor(() => expect(opened).toHaveBeenCalledOnce());

    const [url, options] = fetchSpy.mock.calls[0];
    expect(url).toBe('/api/workspace/notes/old%20%E6%97%A5%E6%9C%AC%E8%AA%9E%20%23%3F.md'
      + '?rename_to=archive%2Fnew%20%E6%97%A5%E6%9C%AC%E8%AA%9E%20%26%20%23%3F.md');
    expect(options).toEqual(expect.objectContaining({ method: 'PUT', credentials: 'same-origin' }));
    expect(options.body).toBeUndefined();
    expect(opened.mock.calls[0][0].detail).toEqual({ path: newPath });
  });

  it.each([
    [jsonResponse({ error: 'target already exists' }, 409), 'A file already exists at that path.'],
    [jsonResponse({ error: 'readonly path' }, 403), 'readonly path'],
    [new Response('not-json', { status: 500 }), 'Rename failed (500)'],
  ])('keeps rename state after an HTTP failure', async (response, message) => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(response);
    const page = await mountFilePage('notes/old.md');
    const opened = vi.fn();
    page.addEventListener('file-open', opened);

    await submitRename(page, 'notes/new.md');
    await vi.waitFor(() => expect(page._renameError).toBe(message));

    expect(page.path).toBe('notes/old.md');
    expect(page._renaming).toBe(true);
    expect(opened).not.toHaveBeenCalled();
  });

  it('preserves the generic rename message after a network failure', async () => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('offline'));
    const page = await mountFilePage('notes/old.md');
    await submitRename(page, 'notes/new.md');
    await vi.waitFor(() => expect(page._renameError).toBe('Rename failed.'));
    expect(page.path).toBe('notes/old.md');
    expect(page._renaming).toBe(true);
  });

  it('deletes an encoded nested path and preserves both success events', async () => {
    const path = 'notes/日本語 #?.md';
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('', { status: 200 }));
    vi.spyOn(globalThis, 'confirm').mockReturnValue(true);
    const page = await mountFilePage(path);
    const deleted = vi.fn();
    const closed = vi.fn();
    window.addEventListener('workspace-file-deleted', deleted, { once: true });
    page.addEventListener('file-close', closed);

    page.querySelector('.file-delete-btn')?.click();
    await vi.waitFor(() => expect(closed).toHaveBeenCalledOnce());

    const [url, options] = fetchSpy.mock.calls[0];
    expect(url).toBe('/api/workspace/notes/%E6%97%A5%E6%9C%AC%E8%AA%9E%20%23%3F.md');
    expect(options).toEqual(expect.objectContaining({ method: 'DELETE', credentials: 'same-origin' }));
    expect(options.body).toBeUndefined();
    expect(deleted.mock.calls[0][0].detail).toEqual({ path });
  });

  it.each([
    [jsonResponse({ error: 'readonly path' }, 403), 'readonly path'],
    [new Response('not-json', { status: 500 }), 'Delete failed (500)'],
  ])('keeps the file open after an HTTP delete failure', async (response, message) => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(response);
    vi.spyOn(globalThis, 'confirm').mockReturnValue(true);
    const alertSpy = vi.spyOn(globalThis, 'alert').mockImplementation(() => {});
    const page = await mountFilePage('notes/keep.md');
    const closed = vi.fn();
    page.addEventListener('file-close', closed);

    page.querySelector('.file-delete-btn')?.click();
    await vi.waitFor(() => expect(alertSpy).toHaveBeenCalledWith(message));

    expect(page.path).toBe('notes/keep.md');
    expect(closed).not.toHaveBeenCalled();
  });

  it('keeps the file open after a network delete failure', async () => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('offline'));
    vi.spyOn(globalThis, 'confirm').mockReturnValue(true);
    const alertSpy = vi.spyOn(globalThis, 'alert').mockImplementation(() => {});
    const page = await mountFilePage('notes/keep.md');

    page.querySelector('.file-delete-btn')?.click();
    await vi.waitFor(() => expect(alertSpy).toHaveBeenCalledWith('Delete failed.'));
    expect(page.path).toBe('notes/keep.md');
  });
});
