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
  editor.page = 'workspace/schedules/Daily #1.md';
  editor.content = '# Initial';
  editor.modified = 10;
  editor.saveEndpoint = '/api/config/files/';
  document.body.append(editor);
  await vi.waitFor(() => expect(state.editors).toHaveLength(1));
  return editor;
}

function clickConflictButton(editor, label) {
  return [...editor.querySelectorAll('.wiki-editor-conflict button')]
    .find(button => button.textContent?.trim() === label).click();
}

describe('config wiki-editor generated calls', () => {
  beforeEach(() => {
    state.editors.length = 0;
    vi.useFakeTimers();
  });

  afterEach(() => {
    document.body.innerHTML = '';
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('saves content and modification time at the encoded route', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true, modified: 11 }));
    vi.stubGlobal('fetch', fetchMock);
    const editor = await mountEditor();
    const savedEvents = [];
    editor.addEventListener('saved', event => savedEvents.push(event.detail));

    state.editors[0].markdownUpdated(null, '# Edited', '# Initial');
    await vi.runAllTimersAsync();
    await vi.waitFor(() => expect(editor._status).toBe('saved'));

    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toBe('/api/config/files/workspace/schedules/Daily%20%231.md');
    expect(options.credentials).toBe('same-origin');
    expect(options.headers.get('Content-Type')).toContain('application/json');
    expect(JSON.parse(options.body)).toEqual({ content: '# Edited', modified: 10 });
    expect(editor.modified).toBe(11);
    expect(savedEvents).toEqual([{
      modified: 11, page: 'workspace/schedules/Daily #1.md',
    }]);
  });

  it('reloads a conflict from content and force-saves without modified', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ error: 'conflict' }, 409))
      .mockResolvedValueOnce(jsonResponse({
        content: '# Server', modified: 20, name: 'Daily #1.md', default: false,
      }))
      .mockResolvedValueOnce(jsonResponse({ error: 'conflict' }, 409))
      .mockResolvedValueOnce(jsonResponse({ ok: true, modified: 21 }));
    vi.stubGlobal('fetch', fetchMock);
    const editor = await mountEditor();
    const reloadedEvents = [];
    const savedEvents = [];
    editor.addEventListener('reloaded', event => reloadedEvents.push(event.detail));
    editor.addEventListener('saved', event => savedEvents.push(event.detail));

    state.editors[0].markdownUpdated(null, '# Stale', '# Initial');
    await vi.runAllTimersAsync();
    await vi.waitFor(() => expect(editor._status).toBe('conflict'));
    await editor.updateComplete;
    clickConflictButton(editor, 'Reload');
    await vi.waitFor(() => expect(editor.content).toBe('# Server'));
    expect(editor.modified).toBe(20);
    expect(reloadedEvents).toEqual([{
      modified: 20, page: 'workspace/schedules/Daily #1.md',
    }]);

    state.editors[0].markdownUpdated(null, '# Forced', '# Server');
    await vi.runAllTimersAsync();
    await vi.waitFor(() => expect(editor._status).toBe('conflict'));
    await editor.updateComplete;
    clickConflictButton(editor, 'Overwrite');
    await vi.waitFor(() => expect(editor._status).toBe('saved'));

    expect(fetchMock.mock.calls.map(call => call[0])).toEqual([
      '/api/config/files/workspace/schedules/Daily%20%231.md',
      '/api/config/files/workspace/schedules/Daily%20%231.md',
      '/api/config/files/workspace/schedules/Daily%20%231.md',
      '/api/config/files/workspace/schedules/Daily%20%231.md',
    ]);
    expect(JSON.parse(fetchMock.mock.calls[0][1].body))
      .toEqual({ content: '# Stale', modified: 10 });
    expect(fetchMock.mock.calls[1][1].credentials).toBe('same-origin');
    expect(JSON.parse(fetchMock.mock.calls[2][1].body))
      .toEqual({ content: '# Forced', modified: 20 });
    expect(JSON.parse(fetchMock.mock.calls[3][1].body)).toEqual({ content: '# Forced' });
    expect(editor.modified).toBe(21);
    expect(savedEvents).toEqual([{
      modified: 21, page: 'workspace/schedules/Daily #1.md',
    }]);
  });

  it('preserves HTTP, network, and successful-response parse failures', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ error: 'denied' }, 403))
      .mockRejectedValueOnce(new TypeError('offline'))
      .mockResolvedValueOnce({
        ...jsonResponse({}, 200), json: async () => { throw new SyntaxError('bad json'); },
      });
    vi.stubGlobal('fetch', fetchMock);
    const editor = await mountEditor();

    for (const [content, expected] of [
      ['# HTTP', 'Save failed (403)'],
      ['# Network', 'Save failed (network error)'],
      ['# Parse', 'Save failed (network error)'],
    ]) {
      state.editors[0].markdownUpdated(null, content, '# Initial');
      await vi.runAllTimersAsync();
      await vi.waitFor(() => expect(editor._error).toBe(expected));
    }
  });

  it.each([
    ['HTTP errors', () => jsonResponse({ error: 'denied' }, 503), 'Reload failed: HTTP 503'],
    ['network errors', () => Promise.reject(new TypeError('offline')), 'Reload failed: offline'],
    ['successful-response parse errors', () => ({
      ...jsonResponse({}, 200), json: async () => { throw new SyntaxError('bad json'); },
    }), 'Reload failed: bad json'],
  ])('preserves config reload behavior for %s', async (_label, failure, expected) => {
    vi.stubGlobal('fetch', vi.fn().mockImplementationOnce(failure));
    const editor = await mountEditor();
    editor._status = 'conflict';
    await editor.updateComplete;

    clickConflictButton(editor, 'Reload');
    await vi.waitFor(() => expect(editor._error).toBe(expected));

    expect(editor.content).toBe('# Initial');
    expect(editor.modified).toBe(10);
  });
});
