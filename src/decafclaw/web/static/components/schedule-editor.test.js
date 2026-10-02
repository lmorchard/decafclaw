import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const state = vi.hoisted(() => ({ editors: [] }));

vi.mock('@milkdown/kit', async (importOriginal) => {
  const actual = await importOriginal();
  const make = () => {
    const editor = {
      actions: [], markdownUpdated: undefined,
      action(fn) { this.actions.push(fn); }, destroy() {},
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
  editor.page = 'Daily #1 日本語';
  editor.content = '# Initial';
  editor.modified = 10;
  editor.saveEndpoint = '/api/schedules/';
  document.body.append(editor);
  await vi.waitFor(() => expect(state.editors).toHaveLength(1));
  return editor;
}

function clickConflictButton(editor, label) {
  return [...editor.querySelectorAll('.wiki-editor-conflict button')]
    .find(button => button.textContent?.trim() === label).click();
}

describe('schedule wiki-editor generated calls', () => {
  beforeEach(() => {
    state.editors.length = 0;
    vi.useFakeTimers();
  });

  afterEach(() => {
    document.body.innerHTML = '';
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('saves content and the existing modified hint at the encoded route', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ schedule: {}, modified: 11 }));
    vi.stubGlobal('fetch', fetchMock);
    const editor = await mountEditor();

    state.editors[0].markdownUpdated(null, '# Edited', '# Initial');
    await vi.runAllTimersAsync();
    await vi.waitFor(() => expect(editor._status).toBe('saved'));

    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toBe('/api/schedules/Daily%20%231%20%E6%97%A5%E6%9C%AC%E8%AA%9E');
    expect(options.credentials).toBe('same-origin');
    expect(options.headers.get('Content-Type')).toContain('application/json');
    expect(JSON.parse(options.body)).toEqual({ content: '# Edited', modified: 10 });
    expect(editor.modified).toBe(11);
  });

  it('reloads from top-level body and force-saves without modified', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ error: 'conflict' }, 409))
      .mockResolvedValueOnce(jsonResponse({ schedule: {}, body: '# Server', modified: 20 }))
      .mockResolvedValueOnce(jsonResponse({ error: 'conflict' }, 409))
      .mockResolvedValueOnce(jsonResponse({ schedule: {}, modified: 21 }));
    vi.stubGlobal('fetch', fetchMock);
    const editor = await mountEditor();

    state.editors[0].markdownUpdated(null, '# Stale', '# Initial');
    await vi.runAllTimersAsync();
    await vi.waitFor(() => expect(editor._status).toBe('conflict'));
    await editor.updateComplete;
    clickConflictButton(editor, 'Reload');
    await vi.waitFor(() => expect(editor.content).toBe('# Server'));
    expect(editor.modified).toBe(20);

    state.editors[0].markdownUpdated(null, '# Forced', '# Server');
    await vi.runAllTimersAsync();
    await vi.waitFor(() => expect(editor._status).toBe('conflict'));
    await editor.updateComplete;
    clickConflictButton(editor, 'Overwrite');
    await vi.waitFor(() => expect(editor._status).toBe('saved'));

    expect(fetchMock.mock.calls.map(call => call[0])).toEqual(Array(4).fill(
      '/api/schedules/Daily%20%231%20%E6%97%A5%E6%9C%AC%E8%AA%9E'));
    expect(JSON.parse(fetchMock.mock.calls[0][1].body))
      .toEqual({ content: '# Stale', modified: 10 });
    expect(JSON.parse(fetchMock.mock.calls[2][1].body))
      .toEqual({ content: '# Forced', modified: 20 });
    expect(JSON.parse(fetchMock.mock.calls[3][1].body)).toEqual({ content: '# Forced' });
    expect(editor.modified).toBe(21);
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
});
