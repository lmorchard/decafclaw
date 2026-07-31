import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

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
