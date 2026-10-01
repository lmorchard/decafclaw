import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const state = vi.hoisted(() => ({ editors: [] }));

vi.mock('@milkdown/kit', async (importOriginal) => {
  const actual = await importOriginal();
  const make = () => {
    const editor = {
      actions: [],
      markdownUpdated: undefined,
      action(fn) { this.actions.push(fn); },
      destroy() {},
    };
    const chainable = {
      config(configure) {
        configure({
          set() {},
          get(key) {
            if (key === actual.listenerCtx) {
              return { markdownUpdated(callback) { editor.markdownUpdated = callback; } };
            }
            return undefined;
          },
        });
        return chainable;
      },
      use() { return chainable; },
      async create() { state.editors.push(editor); return editor; },
    };
    return chainable;
  };
  return { ...actual, Editor: { make } };
});

await import('./wiki-editor.js');

const jsonResponse = (body, status = 200) => ({
  ok: status >= 200 && status < 300,
  status,
  statusText: status === 409 ? 'Conflict' : 'OK',
  json: async () => body,
});

async function mountEditor() {
  const editor = document.createElement('wiki-editor');
  editor.page = 'agent/pages/Edit #1';
  editor.content = '# Initial';
  editor.modified = 10;
  document.body.append(editor);
  await vi.waitFor(() => expect(state.editors).toHaveLength(1));
  return editor;
}

describe('vault wiki-editor generated writes', () => {
  beforeEach(() => {
    state.editors.length = 0;
    vi.useFakeTimers();
  });

  afterEach(() => {
    document.body.innerHTML = '';
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('saves nested pages with content, modified time, and session credentials', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({
      ok: true, modified: 11, frontmatter: {}, frontmatter_raw: '', frontmatter_error: '',
    }));
    vi.stubGlobal('fetch', fetchMock);
    const element = await mountEditor();

    state.editors[0].markdownUpdated(null, '# Edited', '# Initial');
    await vi.runAllTimersAsync();
    await vi.waitFor(() => expect(element._status).toBe('saved'));

    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toBe('/api/vault/agent/pages/Edit%20%231');
    expect(options.credentials).toBe('same-origin');
    expect(JSON.parse(options.body)).toEqual({ content: '# Edited', modified: 10 });
    expect(element.modified).toBe(11);
  });

  it('keeps 409 conflict handling and omits modified on force-save', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ error: 'conflict', server_modified: 20 }, 409))
      .mockResolvedValueOnce(jsonResponse({
        ok: true, modified: 21, frontmatter: {}, frontmatter_raw: '', frontmatter_error: '',
      }));
    vi.stubGlobal('fetch', fetchMock);
    const element = await mountEditor();

    state.editors[0].markdownUpdated(null, '# Stale edit', '# Initial');
    await vi.runAllTimersAsync();
    await vi.waitFor(() => expect(element._status).toBe('conflict'));
    await element.updateComplete;
    [...element.querySelectorAll('.wiki-editor-conflict button')]
      .find(button => button.textContent?.trim() === 'Overwrite').click();
    await vi.waitFor(() => expect(element._status).toBe('saved'));

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
      content: '# Stale edit', modified: 10,
    });
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({ content: '# Stale edit' });
    expect(fetchMock.mock.calls[1][1].credentials).toBe('same-origin');
    expect(element.modified).toBe(21);
  });

  it('preserves HTTP, network, and success-parse failure outcomes', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ error: 'denied' }, 403))
      .mockRejectedValueOnce(new TypeError('offline'))
      .mockResolvedValueOnce({ ...jsonResponse({}, 200), json: async () => { throw new SyntaxError('bad json'); } });
    vi.stubGlobal('fetch', fetchMock);
    const element = await mountEditor();

    for (const [content, expected] of [
      ['# HTTP', 'Save failed (403)'],
      ['# Network', 'Save failed (network error)'],
      ['# Parse', 'Save failed (network error)'],
    ]) {
      state.editors[0].markdownUpdated(null, content, '# Initial');
      await vi.runAllTimersAsync();
      await vi.waitFor(() => expect(element._error).toBe(expected));
    }
  });
});
