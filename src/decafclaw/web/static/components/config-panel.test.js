import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('@milkdown/kit', async (importOriginal) => {
  const actual = await importOriginal();
  const chainable = {
    config() { return chainable; },
    use() { return chainable; },
    async create() { return { action() {}, destroy() {} }; },
  };
  return { ...actual, Editor: { make: () => chainable } };
});

await import('./config-panel.js');

const jsonResponse = (body, status = 200) => ({
  ok: status >= 200 && status < 300,
  status,
  statusText: status === 503 ? 'Unavailable' : 'OK',
  json: async () => body,
});

const file = {
  name: 'USER #1.md',
  path: 'workspace/USER #1.md',
  description: 'User context',
  scope: 'workspace',
  modified: null,
  exists: false,
};

async function mountPanel() {
  const panel = document.createElement('config-panel');
  document.body.append(panel);
  await panel.updateComplete;
  return panel;
}

describe('config-panel generated reads', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn());
  });

  afterEach(() => {
    document.body.innerHTML = '';
    vi.unstubAllGlobals();
  });

  it('lists and selects an encoded nested config path with session credentials', async () => {
    const fetchMock = vi.mocked(fetch)
      .mockResolvedValueOnce(jsonResponse([file]))
      .mockResolvedValueOnce(jsonResponse({
        content: '# Bundled default', modified: null,
        name: 'USER #1.md', default: true,
      }));
    const panel = await mountPanel();
    await vi.waitFor(() => expect(panel._files).toHaveLength(1));

    expect(fetchMock.mock.calls[0][0]).toBe('/api/config/files');
    expect(fetchMock.mock.calls[0][1].credentials).toBe('same-origin');
    expect(panel.textContent).toContain('User context');
    expect(panel.textContent).toContain('default');

    panel.querySelector('.config-file-item').click();
    await vi.waitFor(() => expect(panel._selectedFile).toEqual(file));

    expect(fetchMock.mock.calls[1][0])
      .toBe('/api/config/files/workspace/USER%20%231.md');
    expect(fetchMock.mock.calls[1][1].credentials).toBe('same-origin');
    expect(panel._fileContent).toBe('# Bundled default');
    expect(panel._fileModified).toBe(0);
    const editor = panel.querySelector('wiki-editor');
    expect(editor.page).toBe(file.path);
    expect(editor.content).toBe('# Bundled default');
    expect(editor.modified).toBe(0);
  });

  it.each([
    ['HTTP errors', () => jsonResponse({ error: 'denied' }, 503)],
    ['network errors', () => Promise.reject(new TypeError('offline'))],
    ['successful-response parse errors', () => ({
      ...jsonResponse({}, 200), json: async () => { throw new SyntaxError('bad json'); },
    })],
  ])('keeps the list empty on %s', async (_label, failure) => {
    vi.mocked(fetch).mockImplementationOnce(failure);
    const panel = await mountPanel();
    await vi.waitFor(() => expect(panel._loading).toBe(false));
    expect(panel._files).toEqual([]);
  });

  it.each([
    ['HTTP errors', () => jsonResponse({ error: 'denied' }, 503)],
    ['network errors', () => Promise.reject(new TypeError('offline'))],
    ['successful-response parse errors', () => ({
      ...jsonResponse({}, 200), json: async () => { throw new SyntaxError('bad json'); },
    })],
  ])('keeps the file list open when selection hits %s', async (_label, failure) => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(jsonResponse([file]))
      .mockImplementationOnce(failure);
    const panel = await mountPanel();
    await vi.waitFor(() => expect(panel._files).toHaveLength(1));

    panel.querySelector('.config-file-item').click();
    await vi.waitFor(() => expect(panel._loading).toBe(false));

    expect(panel._selectedFile).toBeNull();
    expect(panel._fileContent).toBe('');
    expect(panel.querySelector('.config-file-list')).not.toBeNull();
  });
});
