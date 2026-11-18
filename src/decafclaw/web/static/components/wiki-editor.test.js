import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest';
/** Real listener slice, re-exported by the mock below — used to drive content changes. */
import { listenerCtx } from '@milkdown/kit';

// Milkdown needs a real contenteditable surface and contributes nothing to the
// fetch paths under test. Override only `Editor` with a recording stub — the
// rest of the kit stays real, since the wiki-link plugin imports from it.
vi.mock('@milkdown/kit', async (importOriginal) => {
  const actual = /** @type {object} */ (await importOriginal());
  /** @type {{actions: unknown[], configCb?: Function, destroyCalls: number}[]} */
  const editors = [];
  const make = () => {
    const editor = {
      /** @type {unknown[]} */ actions: [],
      /** @param {unknown} fn */ action: (fn) => editor.actions.push(fn),
      /** @type {number} */ destroyCalls: 0,
      destroy: () => { editor.destroyCalls++; },
    };
    /** @type {Record<string, Function>} */
    const chainable = {
      config: (cb) => { editor.configCb = cb; return chainable; },
      use: () => chainable,
      create: async () => { editors.push(editor); return editor; },
    };
    return chainable;
  };
  return { ...actual, Editor: { make }, __editors: editors };
});

await import('./wiki-editor.js');
/** Editors built by the stub above, in mount order. */
const { __editors: editors } = /** @type {any} */ (await import('@milkdown/kit'));

/**
 * Mount a `wiki-editor` in its default idle state.
 * @param {{page: string, content?: string, saveEndpoint?: string}} opts
 */
async function mount({ page, content = '', saveEndpoint }) {
  const el = /** @type {import('./wiki-editor.js').WikiEditor} */ (
    document.createElement('wiki-editor')
  );
  el.page = page;
  if (content !== undefined) el.content = content;
  if (saveEndpoint !== undefined) el.saveEndpoint = saveEndpoint;
  document.body.appendChild(el);
  await el.updateComplete;
  // firstUpdated builds the editor asynchronously, outside the update cycle.
  await vi.waitFor(() => expect(editors).toHaveLength(1));
  return el;
}

/**
 * Mount a `wiki-editor` already sitting in the conflict state, so the
 * conflict banner (and its Reload button) is rendered.
 * @param {{page: string, saveEndpoint?: string}} opts
 */
async function mountInConflict(opts) {
  const el = await mount(/** @type {{page: string, content?: string, saveEndpoint?: string}} */ (opts));
  el._status = 'conflict';
  await el.updateComplete;
  return el;
}

/**
 * Drive a content change through the editor's markdownUpdated listener — the
 * same path Milkdown uses when the user types. The stubbed Editor never
 * invokes its config callback, so call it here with a fake ctx.
 * @param {any} editor stub editor from the mock above
 * @param {string} md new markdown
 */
function typeInto(editor, md) {
  /** @type {((ctx: unknown, next: string, prev: string) => void) | null} */
  let handler = null;
  editor.configCb({
    set: () => {},
    get: (key) => key === listenerCtx ? { markdownUpdated: cb => { handler = /** @type {any} */ (cb); } } : {},
  });
  if (!handler) throw new Error('markdownUpdated not registered');
  handler(null, md, '');
}

/** @param {HTMLElement} el */
function clickReload(el) {
  const buttons = [...el.querySelectorAll('.wiki-editor-conflict button')];
  const reload = buttons.find(b => b.textContent?.trim() === 'Reload');
  if (!reload) throw new Error('Reload button not rendered');
  /** @type {HTMLButtonElement} */ (reload).click();
}

describe('wiki-editor #reload', () => {
  /** @type {ReturnType<typeof vi.fn>} */
  let fetchMock;

  beforeEach(() => {
    editors.length = 0;
    fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
  });

  afterEach(() => {
    document.body.innerHTML = '';
    vi.unstubAllGlobals();
  });

  /**
   * Serve `payload` at exactly one URL and 404 everywhere else — the real
   * failure mode in #666 is a request landing on the wrong endpoint, so a
   * mock that answers any URL would let the bug pass.
   * @param {string} url
   * @param {object} payload
   */
  const respondAt = (url, payload) => fetchMock.mockImplementation(
    async (/** @type {string} */ requested) => requested === url
      ? { ok: true, status: 200, json: async () => payload }
      : { ok: false, status: 404, json: async () => ({ error: 'not found' }) },
  );

  // Each host wires wiki-editor to a different endpoint. Reload must follow
  // `saveEndpoint`, not the vault default (#666), and must read the body field
  // that endpoint actually returns.
  const HOSTS = [
    {
      host: 'wiki-page',
      saveEndpoint: undefined,  // default
      page: 'Some Page',
      expectedUrl: '/api/vault/Some%20Page',
      payload: { body: 'vault body', modified: 111 },
    },
    {
      host: 'schedule-page',
      saveEndpoint: '/api/schedules/',
      page: 'dream',
      expectedUrl: '/api/schedules/dream',
      payload: { body: 'schedule body', modified: 222 },
    },
    {
      host: 'config-panel',
      saveEndpoint: '/api/config/files/',
      page: 'AGENT.md',
      expectedUrl: '/api/config/files/AGENT.md',
      payload: { content: 'config body', modified: 333 },
    },
  ];

  for (const { host, saveEndpoint, page, expectedUrl, payload } of HOSTS) {
    it(`reloads from saveEndpoint for ${host}`, async () => {
      respondAt(expectedUrl, payload);
      const el = await mountInConflict({ page, saveEndpoint });

      clickReload(el);
      await vi.waitFor(() => expect(fetchMock).toHaveBeenCalled());

      expect(fetchMock.mock.calls[0][0]).toBe(expectedUrl);
    });

    it(`applies the reloaded content for ${host}`, async () => {
      respondAt(expectedUrl, payload);
      const el = await mountInConflict({ page, saveEndpoint });

      clickReload(el);
      await vi.waitFor(() => expect(el._status).toBe('saved'));

      expect(el.content).toBe(payload.body ?? payload.content);
      expect(el.modified).toBe(payload.modified);
      expect(el._error).toBe('');
      // Setting `.content` alone doesn't move Milkdown — it only reads that
      // once, at init. The visible refresh is this replaceAll action.
      expect(editors[0].actions).toHaveLength(1);
    });
  }

  it('preserves the vault reload HTTP status through the generated call', async () => {
    fetchMock.mockResolvedValue({
      ok: false, status: 409, statusText: 'Conflict',
      json: async () => ({ error: 'conflict' }),
    });
    const el = await mountInConflict({ page: 'Some Page' });

    clickReload(el);
    await vi.waitFor(() => expect(el._error).toBe('Reload failed: HTTP 409'));
  });

  it('surfaces a failed reload instead of blanking the editor', async () => {
    fetchMock.mockResolvedValue({ ok: false, status: 404, json: async () => ({}) });
    const el = await mountInConflict({ page: 'dream', saveEndpoint: '/api/schedules/' });
    el.content = 'unsaved work';

    clickReload(el);
    await vi.waitFor(() => expect(el._error).toContain('Reload failed'));

    expect(el.content).toBe('unsaved work');
  });
});

describe('wiki-editor save flush', () => {
  /** @type {ReturnType<typeof vi.fn>} */
  let fetchMock;

  beforeEach(() => {
    editors.length = 0;
    fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
  });

  afterEach(() => {
    document.body.innerHTML = '';
    vi.unstubAllGlobals();
  });

  it('waits for an in-flight PUT before destroying the editor on disconnect', async () => {
    // Gate the PUT open so the test can observe the window between the save
    // starting and it settling.
    /** @type {() => void} */
    let resolvePut;
    const putGate = new Promise((resolve) => { resolvePut = /** @type {() => void} */ (resolve); });
    fetchMock.mockImplementation(async (/** @type {string} */ url, /** @type {RequestInit | undefined} */ init) =>
      init?.method === 'PUT'
        ? putGate.then(() => ({ ok: true, status: 200, json: async () => ({ modified: 42 }) }))
        : { ok: false, status: 404, json: async () => ({ error: 'not found' }) });

    const el = await mount({ page: 'Some Page', content: 'A' });
    typeInto(editors[0], 'B');

    el.remove();

    // The generated client awaits header setup before calling fetch, so the PUT
    // lands a microtask after remove(); wait for it to be in flight. While it
    // is, the editor must not be destroyed — that was the teardown race.
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(editors[0].destroyCalls).toBe(0);

    const [putUrl, putInit] = fetchMock.mock.calls[0];
    expect(putUrl).toBe('/api/vault/Some%20Page');
    expect(JSON.parse(/** @type {string} */ (putInit.body)).content).toBe('B');

    resolvePut();
    await vi.waitFor(() => expect(editors[0].destroyCalls).toBe(1));

    expect(el._status).toBe('saved');
    expect(el.modified).toBe(42);
  });
});
