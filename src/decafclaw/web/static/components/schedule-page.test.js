import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// These tests read wiki-editor's properties, not Milkdown's rendering. A real
// Milkdown editor arms 3-second timers that call the global
// removeEventListener even after the editor is destroyed; when one fires
// after jsdom teardown, vitest reports an unhandled ReferenceError and exits
// 1. Override only `Editor`, as wiki-editor.test.js does.
vi.mock('@milkdown/kit', async (importOriginal) => {
  const actual = /** @type {object} */ (await importOriginal());
  const make = () => {
    const editor = { action: () => {}, destroy: () => {} };
    /** @type {Record<string, Function>} */
    const chainable = {
      config: () => chainable,
      use: () => chainable,
      create: async () => editor,
    };
    return chainable;
  };
  return { ...actual, Editor: { make } };
});

await import('./schedule-page.js');

const SCHEDULE = {
  name: 'dream', schedule: '0 3 * * *', channel: '', model: '',
  enabled: true, pre_script: '', required_skills: [], allowed_tools: [],
  shell_patterns: [], email_recipients: [], unknown_keys: [],
  frontmatter_raw: '', source_tier: 'bundled', has_overlay: false,
  body: 'Body.', modified: 1,
};

describe('schedule-page', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn(async (/** @type {string} */ url) => {
      if (url.startsWith('/api/models')) {
        return { ok: true, json: async () => ({ models: ['a', 'b'], default: 'a' }) };
      }
      return { ok: true, json: async () => ({ schedule: SCHEDULE }) };
    }));
  });

  afterEach(() => {
    document.body.innerHTML = '';
    vi.unstubAllGlobals();
  });

  it('renders the metadata panel and feeds it the model list', async () => {
    const el = /** @type {any} */ (document.createElement('schedule-page'));
    el.name = 'dream';
    document.body.appendChild(el);
    await el.updateComplete;
    await new Promise(r => setTimeout(r, 0));
    await el.updateComplete;

    const panel = /** @type {any} */ (el.querySelector('schedule-metadata'));
    expect(panel).toBeTruthy();
    expect(panel.models).toEqual(['a', 'b']);
  });

  it('tells the panel the model list is unavailable after an HTTP failure', async () => {
    /** @type {any} */ (globalThis.fetch).mockImplementation(async (/** @type {string} */ url) => {
      if (url.startsWith('/api/models')) return { ok: false, status: 500, json: async () => ({}) };
      return { ok: true, json: async () => ({ schedule: SCHEDULE }) };
    });

    const el = /** @type {any} */ (document.createElement('schedule-page'));
    el.name = 'dream';
    document.body.appendChild(el);
    await el.updateComplete;
    await new Promise(r => setTimeout(r, 0));
    await el.updateComplete;

    const panel = /** @type {any} */ (el.querySelector('schedule-metadata'));
    expect(panel.modelsUnavailable).toBe(true);
  });

  it('tells the panel the model list is unavailable after a network error', async () => {
    /** @type {any} */ (globalThis.fetch).mockImplementation(async (/** @type {string} */ url) => {
      if (url.startsWith('/api/models')) throw new TypeError('Failed to fetch');
      return { ok: true, json: async () => ({ schedule: SCHEDULE }) };
    });

    const el = /** @type {any} */ (document.createElement('schedule-page'));
    el.name = 'dream';
    document.body.appendChild(el);
    await el.updateComplete;
    await new Promise(r => setTimeout(r, 0));
    await el.updateComplete;

    const panel = /** @type {any} */ (el.querySelector('schedule-metadata'));
    expect(panel.modelsUnavailable).toBe(true);
  });

  it('does not flag an empty-but-successful model list as unavailable', async () => {
    /** @type {any} */ (globalThis.fetch).mockImplementation(async (/** @type {string} */ url) => {
      if (url.startsWith('/api/models')) {
        return { ok: true, json: async () => ({ models: [], default: '' }) };
      }
      return { ok: true, json: async () => ({ schedule: SCHEDULE }) };
    });

    const el = /** @type {any} */ (document.createElement('schedule-page'));
    el.name = 'dream';
    document.body.appendChild(el);
    await el.updateComplete;
    await new Promise(r => setTimeout(r, 0));
    await el.updateComplete;

    const panel = /** @type {any} */ (el.querySelector('schedule-metadata'));
    expect(panel.models).toEqual([]);
    expect(panel.modelsUnavailable).toBe(false);
  });

  it('PUTs the patch when the panel emits metadata-change', async () => {
    const el = /** @type {any} */ (document.createElement('schedule-page'));
    el.name = 'dream';
    document.body.appendChild(el);
    await el.updateComplete;
    await new Promise(r => setTimeout(r, 0));
    await el.updateComplete;

    /** @type {any} */ (globalThis.fetch).mockClear();
    const panel = /** @type {any} */ (el.querySelector('schedule-metadata'));
    panel.dispatchEvent(new CustomEvent('metadata-change', {
      detail: { fields: { model: 'b' } }, bubbles: true, composed: true,
    }));
    await new Promise(r => setTimeout(r, 0));

    const [url, init] = /** @type {any} */ (globalThis.fetch).mock.calls[0];
    expect(url).toBe('/api/schedules/dream');
    expect(init.method).toBe('PUT');
    expect(JSON.parse(init.body)).toEqual({ model: 'b' });
  });

  it('surfaces a 400 on the panel instead of only logging it', async () => {
    const el = /** @type {any} */ (document.createElement('schedule-page'));
    el.name = 'dream';
    document.body.appendChild(el);
    await el.updateComplete;
    await new Promise(r => setTimeout(r, 0));
    await el.updateComplete;

    /** @type {any} */ (globalThis.fetch).mockImplementation(async () => ({
      ok: false,
      status: 400,
      json: async () => ({ error: "invalid cron expression: 'nope'" }),
    }));

    const panel = /** @type {any} */ (el.querySelector('schedule-metadata'));
    panel.dispatchEvent(new CustomEvent('metadata-change', {
      detail: { fields: { schedule: 'nope' } }, bubbles: true, composed: true,
    }));
    await new Promise(r => setTimeout(r, 0));
    await el.updateComplete;

    expect(/** @type {any} */ (el.querySelector('schedule-metadata')).error)
      .toContain('invalid cron');
  });

  it('surfaces a network-level PUT failure instead of only logging it', async () => {
    const el = /** @type {any} */ (document.createElement('schedule-page'));
    el.name = 'dream';
    document.body.appendChild(el);
    await el.updateComplete;
    await new Promise(r => setTimeout(r, 0));
    await el.updateComplete;

    // fetch rejects outright (offline, server restarting) — never
    // reaches the res.ok branch.
    /** @type {any} */ (globalThis.fetch).mockImplementation(async () => {
      throw new TypeError('Failed to fetch');
    });

    const panel = /** @type {any} */ (el.querySelector('schedule-metadata'));
    panel.dispatchEvent(new CustomEvent('metadata-change', {
      detail: { fields: { channel: 'abc' } }, bubbles: true, composed: true,
    }));
    await new Promise(r => setTimeout(r, 0));
    await el.updateComplete;

    expect(/** @type {any} */ (el.querySelector('schedule-metadata')).error)
      .toContain('could not reach the server');
  });

  it('clears the previous save error when switching to a different schedule', async () => {
    const el = /** @type {any} */ (document.createElement('schedule-page'));
    el.name = 'dream';
    document.body.appendChild(el);
    await el.updateComplete;
    await new Promise(r => setTimeout(r, 0));
    await el.updateComplete;

    // Induce a failed patch on schedule "dream".
    /** @type {any} */ (globalThis.fetch).mockImplementation(async (/** @type {string} */ url) => {
      if (url.startsWith('/api/schedules/dream')) {
        return { ok: false, status: 400, json: async () => ({ error: "invalid cron expression: 'nope'" }) };
      }
      return { ok: true, json: async () => ({ schedule: SCHEDULE }) };
    });

    let panel = /** @type {any} */ (el.querySelector('schedule-metadata'));
    panel.dispatchEvent(new CustomEvent('metadata-change', {
      detail: { fields: { schedule: 'nope' } }, bubbles: true, composed: true,
    }));
    await new Promise(r => setTimeout(r, 0));
    await el.updateComplete;

    panel = /** @type {any} */ (el.querySelector('schedule-metadata'));
    expect(panel.error).toContain('invalid cron');

    // Switch to a different schedule; its own GET succeeds.
    /** @type {any} */ (globalThis.fetch).mockImplementation(async (/** @type {string} */ url) => {
      if (url.startsWith('/api/models')) {
        return { ok: true, json: async () => ({ models: ['a', 'b'], default: 'a' }) };
      }
      return { ok: true, json: async () => ({ schedule: { ...SCHEDULE, name: 'garden' } }) };
    });
    el.name = 'garden';
    await el.updateComplete;
    await new Promise(r => setTimeout(r, 0));
    await el.updateComplete;

    panel = /** @type {any} */ (el.querySelector('schedule-metadata'));
    expect(panel.error).toBe('');
  });

  it('does not refetch the model list when it loaded successfully but empty', async () => {
    // A fresh agent with no model_configs is a real state. Gating the
    // fetch on `_models.length` refetched on every schedule selection,
    // because an empty list is indistinguishable from "never loaded".
    vi.stubGlobal('fetch', vi.fn(async (/** @type {string} */ url) => {
      if (url.startsWith('/api/models')) {
        return { ok: true, json: async () => ({ models: [], default: '' }) };
      }
      return { ok: true, json: async () => ({ schedule: SCHEDULE }) };
    }));

    const el = /** @type {any} */ (document.createElement('schedule-page'));
    el.name = 'dream';
    document.body.appendChild(el);
    await el.updateComplete;
    await new Promise(r => setTimeout(r, 0));

    el.name = 'garden';
    await el.updateComplete;
    await new Promise(r => setTimeout(r, 0));

    const modelCalls = /** @type {any} */ (globalThis.fetch).mock.calls
      .filter((/** @type {any[]} */ c) => String(c[0]).startsWith('/api/models'));
    expect(modelCalls).toHaveLength(1);
  });

  it('retries the model list on the next schedule after a failed fetch', async () => {
    // The flip side: a real failure must not be latched, or a transient
    // 500 would leave the dropdown degraded for the rest of the session.
    let modelAttempts = 0;
    vi.stubGlobal('fetch', vi.fn(async (/** @type {string} */ url) => {
      if (url.startsWith('/api/models')) {
        modelAttempts += 1;
        if (modelAttempts === 1) return { ok: false, status: 500, json: async () => ({}) };
        return { ok: true, json: async () => ({ models: ['a'], default: 'a' }) };
      }
      return { ok: true, json: async () => ({ schedule: SCHEDULE }) };
    }));

    const el = /** @type {any} */ (document.createElement('schedule-page'));
    el.name = 'dream';
    document.body.appendChild(el);
    await el.updateComplete;
    await new Promise(r => setTimeout(r, 0));

    el.name = 'garden';
    await el.updateComplete;
    await new Promise(r => setTimeout(r, 0));
    await el.updateComplete;

    expect(modelAttempts).toBe(2);
    const panel = /** @type {any} */ (el.querySelector('schedule-metadata'));
    expect(panel.models).toEqual(['a']);
  });
});

/**
 * A fetch stub whose schedule requests stay pending until the test
 * resolves them, so a test controls the order in which responses arrive.
 * The model list resolves at once; it is not part of the race.
 */
function controlledFetch() {
  /** @type {{url: string, method: string, resolve: (body: unknown, status?: number) => void}[]} */
  const pending = [];
  const fetch = vi.fn((/** @type {string} */ url, /** @type {any} */ init) => {
    if (url.startsWith('/api/models')) {
      return Promise.resolve({ ok: true, json: async () => ({ models: ['a'], default: 'a' }) });
    }
    return new Promise(resolve => {
      pending.push({
        url,
        method: init?.method ?? 'GET',
        resolve: (body, status = 200) => resolve({
          ok: status < 400, status, json: async () => body,
        }),
      });
    });
  });
  /**
   * Resolve the oldest pending request for this method and URL, or the
   * newest one when `newest` is set.
   * @param {string} method
   * @param {string} url
   * @param {unknown} body
   * @param {number} [status]
   * @param {{newest?: boolean}} [options]
   */
  async function respond(method, url, body, status, { newest = false } = {}) {
    // The generated client reaches fetch() a few microtasks after the call.
    await settle();
    const matches = (/** @type {{method: string, url: string}} */ p) =>
      p.method === method && p.url === url;
    const index = newest ? pending.findLastIndex(matches) : pending.findIndex(matches);
    if (index < 0) {
      const open = pending.map(p => `${p.method} ${p.url}`).join(', ');
      throw new Error(`no pending ${method} ${url}; pending: ${open}`);
    }
    const [request] = pending.splice(index, 1);
    request.resolve(body, status);
    await settle();
  }
  return { fetch, pending, respond };
}

async function settle() {
  for (let i = 0; i < 3; i++) await new Promise(r => setTimeout(r, 0));
}

const DREAM = { ...SCHEDULE, name: 'dream', body: 'Dream body.', modified: 1 };
const GARDEN = { ...SCHEDULE, name: 'garden', body: 'Garden body.', modified: 2 };
// What a late response for dream looks like: different badges and
// metadata from garden, so a stale assignment is visible in the panel.
const STALE_DREAM = {
  ...DREAM, source_tier: 'admin', has_overlay: true, channel: 'dream-channel',
};

/** @param {any} el */
function expectPanelShowsGarden(el) {
  expect(el._data.name).toBe('garden');
  expect(el.querySelector('.schedule-page-title').textContent).toBe('garden');
  expect(el.querySelector('.schedule-tier-badge').textContent).toBe('bundled');
  expect(el.querySelector('.schedule-overlay-badge')).toBeNull();
  expect(el.querySelector('schedule-metadata').data.name).toBe('garden');
  expect(el.querySelector('schedule-metadata').data.channel).toBe('');
  expect(el.querySelector('wiki-editor').page).toBe('garden');
}

describe('schedule-page responses after a selection change', () => {
  /** @type {ReturnType<typeof controlledFetch>} */
  let server;

  beforeEach(() => {
    server = controlledFetch();
    vi.stubGlobal('fetch', server.fetch);
  });

  afterEach(() => {
    document.body.innerHTML = '';
    vi.unstubAllGlobals();
  });

  /** Mount the page with dream loaded. */
  async function mountDream() {
    const el = /** @type {any} */ (document.createElement('schedule-page'));
    el.name = 'dream';
    document.body.appendChild(el);
    await el.updateComplete;
    await server.respond('GET', '/api/schedules/dream', { schedule: DREAM });
    await el.updateComplete;
    expect(el._data.name).toBe('dream');
    return el;
  }

  /** @param {any} el */
  async function selectGarden(el) {
    el.name = 'garden';
    await el.updateComplete;
    await settle();
  }

  it('ignores a dream detail response that arrives after garden (rapid selection)', async () => {
    const el = /** @type {any} */ (document.createElement('schedule-page'));
    el.name = 'dream';
    document.body.appendChild(el);
    await el.updateComplete;
    await selectGarden(el);

    await server.respond('GET', '/api/schedules/garden', { schedule: GARDEN });
    await server.respond('GET', '/api/schedules/dream', { schedule: STALE_DREAM });
    await el.updateComplete;

    expect(el._loading).toBe(false);
    expectPanelShowsGarden(el);
  });

  it('keeps loading garden when an earlier dream detail response arrives first', async () => {
    const el = /** @type {any} */ (document.createElement('schedule-page'));
    el.name = 'dream';
    document.body.appendChild(el);
    await el.updateComplete;
    await selectGarden(el);

    await server.respond('GET', '/api/schedules/dream', { schedule: STALE_DREAM });
    await el.updateComplete;
    expect(el._loading).toBe(true);
    expect(el.querySelector('schedule-metadata')).toBeNull();

    await server.respond('GET', '/api/schedules/garden', { schedule: GARDEN });
    await el.updateComplete;
    expect(el._loading).toBe(false);
    expectPanelShowsGarden(el);
  });

  it('ignores a post-save dream refresh that arrives after garden', async () => {
    const el = await mountDream();

    el.querySelector('wiki-editor').dispatchEvent(new CustomEvent('saved', {
      detail: { modified: 5, page: 'dream' }, bubbles: true, composed: true,
    }));
    await settle();
    await selectGarden(el);

    await server.respond('GET', '/api/schedules/garden', { schedule: GARDEN });
    await server.respond('GET', '/api/schedules/dream', { schedule: STALE_DREAM });
    await el.updateComplete;

    expectPanelShowsGarden(el);
    expect(el.querySelector('wiki-editor').content).toBe('Garden body.');
    expect(el._data.modified).toBe(2);
  });

  it('ignores a post-save dream refresh after selecting garden and then dream again', async () => {
    const el = await mountDream();

    el.querySelector('wiki-editor').dispatchEvent(new CustomEvent('saved', {
      detail: { modified: 5, page: 'dream' }, bubbles: true, composed: true,
    }));
    await settle();
    await selectGarden(el);
    await server.respond('GET', '/api/schedules/garden', { schedule: GARDEN });
    el.name = 'dream';
    await el.updateComplete;
    await settle();
    // The second dream selection loads newer data than the old refresh saw.
    const freshDream = { ...DREAM, body: 'Fresh dream body.', modified: 9 };
    await server.respond('GET', '/api/schedules/dream', { schedule: freshDream }, 200,
      { newest: true });
    await el.updateComplete;
    expect(el._data.modified).toBe(9);

    // The refresh from the first dream selection arrives last.
    await server.respond('GET', '/api/schedules/dream', { schedule: STALE_DREAM });
    await el.updateComplete;

    expect(el._data).toEqual(freshDream);
    expect(el.querySelector('.schedule-overlay-badge')).toBeNull();
  });

  it('ignores a dream editor save that finishes after garden is selected', async () => {
    const el = await mountDream();
    const dreamEditor = el.querySelector('wiki-editor');
    await selectGarden(el);
    await server.respond('GET', '/api/schedules/garden', { schedule: GARDEN });
    await el.updateComplete;

    // Focus loss saves dream; the selection click lands before the PUT
    // returns, and the detached dream editor then reports its save.
    const saved = vi.fn();
    window.addEventListener('schedule-saved', saved);
    dreamEditor.dispatchEvent(new CustomEvent('saved', {
      detail: { modified: 5, page: 'dream' }, bubbles: true, composed: true,
    }));
    await settle();
    await el.updateComplete;
    window.removeEventListener('schedule-saved', saved);

    expectPanelShowsGarden(el);
    expect(el._data.modified).toBe(2);
    expect(server.pending).toEqual([]);
    expect(saved).toHaveBeenCalledTimes(1);
  });

  it('ignores a dream metadata save response that arrives after garden', async () => {
    const el = await mountDream();
    const saved = vi.fn();
    window.addEventListener('schedule-saved', saved);

    el.querySelector('schedule-metadata').dispatchEvent(new CustomEvent('metadata-change', {
      detail: { fields: { channel: 'dream-channel' } }, bubbles: true, composed: true,
    }));
    await settle();
    await selectGarden(el);

    await server.respond('GET', '/api/schedules/garden', { schedule: GARDEN });
    await server.respond('PUT', '/api/schedules/dream', { schedule: STALE_DREAM });
    await el.updateComplete;
    window.removeEventListener('schedule-saved', saved);

    expectPanelShowsGarden(el);
    // The dream save itself completed, so listeners such as the sidebar
    // still hear about it.
    expect(saved).toHaveBeenCalledTimes(1);
  });

  it('does not show a late dream save error on garden', async () => {
    const el = await mountDream();

    el.querySelector('schedule-metadata').dispatchEvent(new CustomEvent('metadata-change', {
      detail: { fields: { schedule: 'nope' } }, bubbles: true, composed: true,
    }));
    await settle();
    await selectGarden(el);

    await server.respond('GET', '/api/schedules/garden', { schedule: GARDEN });
    await server.respond('PUT', '/api/schedules/dream',
      { error: "invalid cron expression: 'nope'" }, 400);
    await el.updateComplete;

    expectPanelShowsGarden(el);
    expect(el.querySelector('schedule-metadata').error).toBe('');
  });

  it('ignores a dream reset reload that arrives after garden', async () => {
    vi.stubGlobal('confirm', () => true);
    const el = /** @type {any} */ (document.createElement('schedule-page'));
    el.name = 'dream';
    document.body.appendChild(el);
    await el.updateComplete;
    await server.respond('GET', '/api/schedules/dream', { schedule: STALE_DREAM });
    await el.updateComplete;

    el.querySelector('.schedule-reset-btn').click();
    await settle();
    await server.respond('DELETE', '/api/schedules/dream/overlay', {});
    await selectGarden(el);

    await server.respond('GET', '/api/schedules/garden', { schedule: GARDEN });
    await server.respond('GET', '/api/schedules/dream', { schedule: DREAM });
    await el.updateComplete;

    expect(el._loading).toBe(false);
    expectPanelShowsGarden(el);
  });

  it('does not reload or remount garden when a dream reset finishes after garden', async () => {
    vi.stubGlobal('confirm', () => true);
    const el = /** @type {any} */ (document.createElement('schedule-page'));
    el.name = 'dream';
    document.body.appendChild(el);
    await el.updateComplete;
    await server.respond('GET', '/api/schedules/dream', { schedule: STALE_DREAM });
    await el.updateComplete;

    el.querySelector('.schedule-reset-btn').click();
    await selectGarden(el);
    await server.respond('GET', '/api/schedules/garden', { schedule: GARDEN });
    await el.updateComplete;
    const gardenEditor = el.querySelector('wiki-editor');

    await server.respond('DELETE', '/api/schedules/dream/overlay', {});
    await el.updateComplete;

    expect(server.pending).toEqual([]);
    expect(el.querySelector('wiki-editor')).toBe(gardenEditor);
    expectPanelShowsGarden(el);
  });
});

describe('schedule-page without a selection change', () => {
  /** @type {ReturnType<typeof controlledFetch>} */
  let server;

  beforeEach(() => {
    server = controlledFetch();
    vi.stubGlobal('fetch', server.fetch);
  });

  afterEach(() => {
    document.body.innerHTML = '';
    vi.unstubAllGlobals();
  });

  it('post-save refresh keeps the editor body and updates badges', async () => {
    const el = /** @type {any} */ (document.createElement('schedule-page'));
    el.name = 'dream';
    document.body.appendChild(el);
    await el.updateComplete;
    await server.respond('GET', '/api/schedules/dream', { schedule: DREAM });
    await el.updateComplete;
    const editor = el.querySelector('wiki-editor');

    editor.dispatchEvent(new CustomEvent('saved', {
      detail: { modified: 5, page: 'dream' }, bubbles: true, composed: true,
    }));
    await settle();
    await server.respond('GET', '/api/schedules/dream', {
      schedule: { ...DREAM, body: 'Server body.', source_tier: 'admin', has_overlay: true, modified: 5 },
    });
    await el.updateComplete;

    expect(el.querySelector('wiki-editor')).toBe(editor);
    expect(el._data.body).toBe('Dream body.');
    expect(el._data.modified).toBe(5);
    expect(el.querySelector('.schedule-tier-badge').textContent).toBe('admin');
    expect(el.querySelector('.schedule-overlay-badge')).not.toBeNull();
  });

  it('reset remounts the editor with the default body', async () => {
    vi.stubGlobal('confirm', () => true);
    const el = /** @type {any} */ (document.createElement('schedule-page'));
    el.name = 'dream';
    document.body.appendChild(el);
    await el.updateComplete;
    await server.respond('GET', '/api/schedules/dream', { schedule: STALE_DREAM });
    await el.updateComplete;
    const editor = el.querySelector('wiki-editor');

    el.querySelector('.schedule-reset-btn').click();
    await settle();
    await server.respond('DELETE', '/api/schedules/dream/overlay', {});
    await server.respond('GET', '/api/schedules/dream', { schedule: DREAM });
    await el.updateComplete;

    const remounted = el.querySelector('wiki-editor');
    expect(remounted).not.toBe(editor);
    expect(remounted.content).toBe('Dream body.');
    expect(el.querySelector('.schedule-overlay-badge')).toBeNull();
  });
});
