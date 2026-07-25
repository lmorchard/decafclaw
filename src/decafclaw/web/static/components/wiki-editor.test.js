import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest';

// Milkdown needs a real contenteditable surface and contributes nothing to the
// fetch paths under test. Override only `Editor` with a recording stub — the
// rest of the kit stays real, since the wiki-link plugin imports from it.
vi.mock('@milkdown/kit', async (importOriginal) => {
  const actual = /** @type {object} */ (await importOriginal());
  /** @type {{actions: unknown[]}[]} */
  const editors = [];
  const make = () => {
    const editor = {
      /** @type {unknown[]} */ actions: [],
      /** @param {unknown} fn */ action: (fn) => editor.actions.push(fn),
      destroy: () => {},
    };
    /** @type {Record<string, Function>} */
    const chainable = {
      config: () => chainable,
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
 * Mount a `wiki-editor` already sitting in the conflict state, so the
 * conflict banner (and its Reload button) is rendered.
 * @param {{page: string, saveEndpoint?: string}} opts
 */
async function mountInConflict({ page, saveEndpoint }) {
  const el = /** @type {import('./wiki-editor.js').WikiEditor} */ (
    document.createElement('wiki-editor')
  );
  el.page = page;
  if (saveEndpoint !== undefined) el.saveEndpoint = saveEndpoint;
  document.body.appendChild(el);
  await el.updateComplete;
  // firstUpdated builds the editor asynchronously, outside the update cycle.
  await vi.waitFor(() => expect(editors).toHaveLength(1));
  el._status = 'conflict';
  await el.updateComplete;
  return el;
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

  it('surfaces a failed reload instead of blanking the editor', async () => {
    fetchMock.mockResolvedValue({ ok: false, status: 404, json: async () => ({}) });
    const el = await mountInConflict({ page: 'dream', saveEndpoint: '/api/schedules/' });
    el.content = 'unsaved work';

    clickReload(el);
    await vi.waitFor(() => expect(el._error).toContain('Reload failed'));

    expect(el.content).toBe('unsaved work');
  });
});
